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

from .config_manager import ConfigManager
from .executor import CommandExecutor
from .framework_log import FrameworkLogger
from .logger import ResultLogger
from .parser import TestParser
from .utility import new_run_id
from .validator import Validator
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
        self.variable_manager = None
        if variables_path and os.path.exists(variables_path):
            self.variable_manager = VariableManager(variables_path)
            self.log.debug(f"Loaded variables from {variables_path}")
        else:
            self.log.debug(f"No variables file loaded (looked for {variables_path})")

        self.parser = TestParser()
        self.executor = CommandExecutor(default_timeout=self.config.command_timeout)
        self.validator = Validator(variable_manager=self.variable_manager)
        self.result_logger = ResultLogger(log_dir=self.run_dir)

    def run(self, path: str) -> NvtestResult:
        """Parse, execute, validate, and log a single `.nvtest` file into
        this TestRunner's shared run directory.

        Raises:
            UnsupportedFileTypeError: if `path` does not end in .nvtest.
            ParseError:               if the file's syntax/structure is invalid.
            VariableError:            if a {{name}} placeholder is unresolved.
            (All raised before any .log file is written for this test.)
        """
        check_extension(path)
        self.log.debug(f"Parsing {path}")
        test_case = self.parser.parse(path)

        commands = test_case.commands
        if self.variable_manager is not None:
            commands = [self.variable_manager.substitute(c) for c in commands]

        self.log.info(f"Running: {test_case.name}")
        results = [self.executor.run(cmd) for cmd in commands]

        binary_flags = [False] * len(results)
        for v in test_case.validations:
            if v.kind in ("BYTE", "HEX"):
                binary_flags[v.run_index] = True

        validation_results, all_passed = self.validator.validate(test_case, results)
        validation_lines = [
            f"[{'PASS' if r.passed else 'FAIL'}] {r.message}" for r in validation_results
        ]
        status = "PASS" if all_passed else "FAIL"
        self.log.info(f"{test_case.name}: {status}")

        log_path = self.result_logger.write_nvtest_log(
            test_case.name, results, binary_flags, validation_lines, status,
            filename_stem=os.path.splitext(os.path.basename(path))[0],
        )

        return NvtestResult(
            name=test_case.name,
            status=status,
            log_path=log_path,
            validation_lines=[(r.passed, r.message) for r in validation_results],
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
