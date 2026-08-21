"""
cli.py - Command-line entry point logic for running `.nvtest` files.

Two invocation shapes, per spec:
      python3 run.py tests/TC001.nvtest      (single file)
      python3 run.py tests/                  (directory)
plus --config, --log-level, --dry-run.

`.nvtest` is the only extension ever executed. A directory scan discovers
only `*.nvtest` and silently ignores everything else. A single file path
that doesn't end in `.nvtest` is rejected (UnsupportedFileTypeError) --
reported, not crashed, and not executed.

One TestRunner is constructed per CLI invocation and shared across every
discovered target, so every test in this invocation writes into the same
logs/{timestamp}/ run directory. All status/summary output goes through
FrameworkLogger, not print() -- the one exception is a bare usage message
on a missing path argument, before any logger can exist yet (documented
at that call site).
"""

import argparse
import glob
import os

from .config_manager import ConfigManager
from .parser import ParseError
from .runner import TestRunner, UnsupportedFileTypeError, SUPPORTED_EXTENSION, check_extension
from .variable_manager import VariableError


class CliTarget:
    """One file that will be run and reported on."""

    def __init__(self, path):
        self.path = path
        self.label = os.path.splitext(os.path.basename(path))[0]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Run one .nvtest file or every .nvtest file in a directory.",
    )
    parser.add_argument("path", help="Path to a .nvtest file or a directory of .nvtest files")
    parser.add_argument("--config", default=None, help="Path to a YAML config file")
    parser.add_argument(
        "--log-level", default=None, choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Console log level (overrides config.yaml framework.log_level)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Discover and parse target(s) only -- no commands executed, no .log files written",
    )
    return parser


def discover_targets(path):
    """Resolve a CLI path argument into a list of CliTarget to run.

    - A directory: only *.nvtest files inside it, sorted, non-recursive.
      Every other file extension is ignored -- never opened, never parsed.
    - A file: that single path, whatever its extension (non-.nvtest paths
      are still returned here; run_targets() reports them as rejected via
      the same UnsupportedFileTypeError the runner already raises).

    Raises:
        FileNotFoundError: if `path` does not exist at all.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"No such file or directory: {path!r}")

    if os.path.isdir(path):
        pattern = os.path.join(path, f"*{SUPPORTED_EXTENSION}")
        return [CliTarget(p) for p in sorted(glob.glob(pattern))]

    return [CliTarget(path)]


def run_targets(targets, test_runner: TestRunner):
    """Run each target through the given (shared) TestRunner and collect
    (label, status, detail) rows.

    status is one of "PASS", "FAIL", or "ERROR":
      - PASS/FAIL come from the validator.
      - ERROR means the file was never executed -- wrong extension, invalid
        .nvtest syntax, an unresolved {{variable}}, or (review F-4) any
        other unexpected exception raised while running this one target.
        No .log file is produced for it.

    A failure in one target must never prevent the rest of the batch from
    being reported (review F-4): the known, specific exceptions are caught
    first for a precise message, and a final broad `except Exception` ropes
    in anything unanticipated so one bad test can't silently drop every
    result after it. Only Exception (not BaseException) is caught, so
    KeyboardInterrupt/SystemExit still propagate normally.
    """
    rows = []
    for target in targets:
        try:
            result = test_runner.run(target.path)
            rows.append((target.label, result.status, result.log_path))
        except UnsupportedFileTypeError as exc:
            rows.append((target.label, "ERROR", str(exc)))
        except ParseError as exc:
            rows.append((target.label, "ERROR", str(exc).splitlines()[0]))
        except VariableError as exc:
            rows.append((target.label, "ERROR", str(exc)))
        except Exception as exc:
            test_runner.log.error(f"Unexpected error running {target.path}: {exc}")
            rows.append((target.label, "ERROR", f"Unexpected error: {exc}"))
    return rows


def dry_run_targets(targets, test_runner: TestRunner):
    """--dry-run: parse only, report what WOULD run, execute nothing."""
    rows = []
    for target in targets:
        try:
            check_extension(target.path)
            test_case = test_runner.parser.parse(target.path)
            rows.append((target.label, "DRY-RUN", f"{len(test_case.commands)} command(s)"))
        except UnsupportedFileTypeError as exc:
            rows.append((target.label, "ERROR", str(exc)))
        except ParseError as exc:
            rows.append((target.label, "ERROR", str(exc).splitlines()[0]))
    return rows


def print_summary(rows, log):
    """Emit the concise PASS/FAIL/ERROR table + Total/Passed/Failed summary
    through the FrameworkLogger (not print())."""
    if not rows:
        log.result_line("No .nvtest files found.")
        return

    width = max(len(label) for label, _, _ in rows) + 4
    passed = 0
    failed = 0
    is_dry_run = any(status == "DRY-RUN" for _, status, _ in rows)

    for label, status, detail in rows:
        log.result_line(f"{label:<{width}}{status}")
        if status == "PASS":
            passed += 1
        elif status != "DRY-RUN":
            failed += 1
            if status == "ERROR":
                log.result_line(f"{'':<{width}}  -> {detail}")

    log.result_line("")
    if is_dry_run:
        log.result_line(f"Total: {len(rows)} (dry-run, nothing executed)")
        return

    log.result_line(f"Total: {len(rows)}")
    log.result_line(f"Passed: {passed}")
    log.result_line(f"Failed: {failed}")


def main(argv):
    """CLI entry point. `argv` is sys.argv[1:].

    Returns a process exit code: 0 if every discovered test PASSed
    (or none were discovered), 1 otherwise, 2 for a usage/path error.
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        config = ConfigManager(args.config)
    except (FileNotFoundError, RuntimeError) as exc:
        # No FrameworkLogger exists yet (config failed before TestRunner
        # could build one) -- this is the one unavoidable bootstrap print().
        print(f"Error: {exc}")
        return 2

    test_runner = TestRunner(config=config)
    if args.log_level:
        test_runner.log.set_level(args.log_level)

    try:
        return _run_cli(args, test_runner)
    finally:
        test_runner.close()


def _run_cli(args, test_runner: TestRunner) -> int:
    try:
        targets = discover_targets(args.path)
    except FileNotFoundError as exc:
        test_runner.log.error(str(exc))
        return 2

    if args.dry_run:
        rows = dry_run_targets(targets, test_runner)
        print_summary(rows, test_runner.log)
        return 0

    rows = run_targets(targets, test_runner)
    print_summary(rows, test_runner.log)

    failed = any(status != "PASS" for _, status, _ in rows)
    return 1 if failed else 0
