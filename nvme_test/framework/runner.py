"""
runner.py - Ties parser + executor + validator + logger together.

Critical rule enforced here: `.nvtest` is the ONLY supported test-case
format. This module rejects any other extension outright, before ever
touching the parser, so unsupported formats are never silently
interpreted (as Python, YAML, JSON, etc.) -- they are refused with a clear
error.

TestRunner owns one run directory (logs/{timestamp}/) for its entire
lifetime -- every test executed through the same TestRunner instance
lands in that same directory, per the "timestamp belongs to the run, not
the test" rule.
"""

import os
from concurrent.futures import ThreadPoolExecutor

from .config_manager import ConfigManager
from .executor import CommandExecutor
from .framework_log import FrameworkLogger
from .logger import ResultLogger
from .parser import TestParser
from .utility import new_run_id
from .validator import Validator, check_validation, describe_validation
from .variable_manager import VariableManager

SUPPORTED_EXTENSION = ".nvtest"


class UnsupportedFileTypeError(Exception):
    """Raised when asked to run a test file that isn't `.nvtest`."""
    pass


class NvtestResult:
    """Outcome of running one .nvtest file."""

    def __init__(self, name, status, log_path, validation_lines, source_path):
        self.name = name
        self.status = status  # "PASS" or "FAIL"
        self.log_path = log_path
        self.validation_lines = validation_lines  # list[(bool, str)]
        self.source_path = source_path

    @property
    def passed(self) -> bool:
        return self.status == "PASS"


def check_extension(path: str):
    _, ext = os.path.splitext(path)
    if ext != SUPPORTED_EXTENSION:
        raise UnsupportedFileTypeError(
            f"Unsupported test file type '{ext or '(none)'}' for {path!r}. "
            f"Only {SUPPORTED_EXTENSION} files are supported test cases."
        )


class TestRunner:
    """One instance == one framework execution == one logs/{timestamp}/
    directory shared by every .nvtest file it runs.

    Owns ConfigManager, VariableManager, CommandExecutor, Validator,
    TestParser, ResultLogger, and FrameworkLogger -- constructed once here
    and reused across every run() call.
    """

    def __init__(self, config: ConfigManager = None, variables_path: str = None):
        self.config = config or ConfigManager()
        self.run_id = new_run_id()
        self.run_dir = os.path.join(self.config.log_directory, self.run_id)
        os.makedirs(self.run_dir, exist_ok=True)

        self.log = FrameworkLogger(level=self.config.log_level, run_dir=self.run_dir)

        variables_path = variables_path or self.config.variables_file
        if variables_path and os.path.exists(variables_path):
            self.variable_manager = VariableManager(variables_path)
            self.log.debug(f"Loaded variables from {variables_path}")
        else:
            # Always construct a VariableManager, even with nothing loaded from
            # disk, so RUN ... CAPTURE <name> has somewhere to store captured
            # values regardless of whether a variables file exists at all.
            self.variable_manager = VariableManager(None)
            self.log.debug(f"No variables file loaded (looked for {variables_path})")

        self.parser = TestParser()
        self.executor = CommandExecutor(default_timeout=self.config.command_timeout)
        # Still constructed and available as a public attribute for direct use,
        # even though run() below calls check_validation()/describe_validation()
        # directly (needed for per-iteration LOOP/PARALLEL aggregation, which
        # Validator.validate()'s single-CommandResult-per-command shape doesn't
        # support).
        self.validator = Validator(variable_manager=self.variable_manager)
        self.result_logger = ResultLogger(log_dir=self.run_dir)

    def run(self, path: str) -> NvtestResult:
        """Parse, execute, validate, and log a single `.nvtest` file into
        this TestRunner's shared run directory.

        Every RUN executes `loop_counts[i]` times in sequence (1 unless the
        file used `LOOP <n>`); every validation bound to that RUN is then
        checked against EVERY iteration, not just one -- the run/test fails
        if any iteration fails any bound validation. RUN statements sharing
        a `parallel_group_id` (from a PARALLEL block) execute concurrently,
        one Python thread per RUN, via ThreadPoolExecutor; the framework
        waits for the whole group to finish before continuing to whatever
        follows the PARALLEL block. This is what lets one .nvtest file
        drive e.g. "id-ctrl looped 1000x" concurrently with "reset looped
        100x" (see USER_GUIDE.md's Parallel/Loop Execution section).

        A RUN using `CAPTURE <name>` stores its (last iteration's) stdout,
        stripped, as a runtime variable -- available to any LATER RUN or
        EXPECT ... CONTAINS via {{name}}, exactly like a variable loaded
        from common_variables.json. Substitution happens per-command,
        immediately before that command's own iterations run (not all
        upfront for every command at once), specifically so this works.

        Raises:
            UnsupportedFileTypeError: if `path` does not end in .nvtest.
            ParseError:               if the file's syntax/structure is invalid.
            VariableError:            if a {{name}} placeholder is unresolved.
            (All raised before any .log file is written for this test --
            except a VariableError from a RUN referencing a variable
            CAPTUREd by an earlier RUN in the same file, which can only be
            detected once execution reaches that RUN; see RUN ... CAPTURE
            below.)
        """
        check_extension(path)
        self.log.debug(f"Parsing {path}")
        test_case = self.parser.parse(path)

        self.log.info(f"Running: {test_case.name}")

        n = len(test_case.commands)
        validations_by_index = {}
        for v in test_case.validations:
            validations_by_index.setdefault(v.run_index, []).append(v)

        binary_flags = [False] * n
        for v in test_case.validations:
            if v.kind in ("BYTE", "HEX"):
                binary_flags[v.run_index] = True

        results = [None] * n
        loop_infos = [None] * n
        # per index: (bound_validations, pass_counts, fail_counts, last_message,
        #             first_failure_by_vid) -- all keyed by id(validation)
        per_index_validation_data = [None] * n

        def execute_slot(i):
            # Substituted here, immediately before this command's own
            # iterations run -- NOT all upfront for every command at once
            # -- so a RUN can reference {{name}} captured by an EARLIER
            # RUN (lower index, already executed) via RUN ... CAPTURE
            # <name>. This does mean an unresolved {{name}} in a later RUN
            # is only discovered once execution reaches that RUN, not
            # before any command runs -- any earlier RUNs' real-world
            # side effects will already have happened by then.
            cmd = self.variable_manager.substitute(test_case.commands[i])
            loop_count = test_case.loop_counts[i]
            bound = validations_by_index.get(i, [])
            pass_counts = {id(v): 0 for v in bound}
            fail_counts = {id(v): 0 for v in bound}
            last_message = {}
            first_failure_by_vid = {}
            last_result = None
            iterations_run = 0

            for iteration in range(1, loop_count + 1):
                result = self.executor.run(cmd)
                last_result = result
                iterations_run += 1
                for v in bound:
                    passed, message = check_validation(v, result, self.variable_manager)
                    last_message[id(v)] = message
                    if passed:
                        pass_counts[id(v)] += 1
                    else:
                        fail_counts[id(v)] += 1
                        first_failure_by_vid.setdefault(id(v), (iteration, message))

            results[i] = last_result
            capture_name = test_case.capture_names[i]
            if capture_name:
                self.variable_manager.set(capture_name, last_result.stdout_text().strip())
                self.log.debug(f"Captured {{{{{capture_name}}}}} from RUN #{i}")

            parallel_group = test_case.parallel_group_id[i]
            if loop_count > 1 or parallel_group is not None:
                overall_first_failure = min(first_failure_by_vid.values(), default=None,
                                             key=lambda pair: pair[0])
                loop_infos[i] = {
                    "loop_count": loop_count,
                    "parallel_group": parallel_group,
                    "iterations_run": iterations_run,
                    "first_failure": overall_first_failure,
                }
            return bound, pass_counts, fail_counts, last_message, first_failure_by_vid

        executed = [False] * n
        for i in range(n):
            if executed[i]:
                continue
            group = test_case.parallel_group_id[i]
            if group is None:
                per_index_validation_data[i] = execute_slot(i)
                executed[i] = True
            else:
                group_indices = [
                    j for j in range(n)
                    if test_case.parallel_group_id[j] == group and not executed[j]
                ]
                self.log.info(
                    f"Running PARALLEL group {group} ({len(group_indices)} commands concurrently)"
                )
                with ThreadPoolExecutor(max_workers=len(group_indices)) as pool:
                    futures = {pool.submit(execute_slot, j): j for j in group_indices}
                    for future, j in futures.items():
                        per_index_validation_data[j] = future.result()
                        executed[j] = True

        validation_lines = []
        validation_bool_pairs = []
        all_passed = True

        for v in test_case.validations:
            loop_count = test_case.loop_counts[v.run_index]
            _, pass_counts, fail_counts, last_message, first_failure_by_vid = \
                per_index_validation_data[v.run_index]
            failed_count = fail_counts[id(v)]
            passed = failed_count == 0

            if loop_count == 1:
                # Byte-for-byte identical to the pre-LOOP/PARALLEL behavior:
                # exactly one iteration ran, so its message IS the result.
                message = last_message[id(v)]
            else:
                message = (
                    f"{describe_validation(v, self.variable_manager)} across "
                    f"{loop_count} iterations: {pass_counts[id(v)]} passed, {failed_count} failed"
                )
                if not passed:
                    fail_iter, fail_msg = first_failure_by_vid[id(v)]
                    message += f" (first failure at iteration {fail_iter}: {fail_msg})"

            all_passed = all_passed and passed
            validation_bool_pairs.append((passed, message))
            validation_lines.append(f"[{'PASS' if passed else 'FAIL'}] {message}")

        status = "PASS" if all_passed else "FAIL"
        self.log.info(f"{test_case.name}: {status}")

        log_path = self.result_logger.write_nvtest_log(
            test_case.name, results, binary_flags, validation_lines, status,
            filename_stem=os.path.splitext(os.path.basename(path))[0],
            loop_infos=loop_infos,
        )

        return NvtestResult(
            name=test_case.name,
            status=status,
            log_path=log_path,
            validation_lines=validation_bool_pairs,
            source_path=path,
        )

    def close(self):
        """Release this run's FrameworkLogger handlers (file descriptors).
        Safe to call multiple times; a no-op if never explicitly closed
        (review F-1 secondary defect)."""
        self.log.close()


def run_nvtest_file(path: str) -> NvtestResult:
    """Backward-compatible single-file entry point (pre-TestRunner API).
    Creates its own one-off TestRunner (its own run directory). Prefer
    constructing a TestRunner directly for anything running more than one
    file, so they share a single run directory -- see cli.py.
    """
    return TestRunner().run(path)
