"""
runner.py - Ties parser + executor + validator + logger together for a
single `.nvtest` file.

Critical rule enforced here: `.nvtest` is the ONLY supported test-case
format. This module rejects any other extension outright, before ever
touching the parser, so unsupported formats are never silently
interpreted (as Python, YAML, JSON, etc.) -- they are refused with a clear
error.
"""

import os

from .executor import Executor
from .logger import Logger
from .parser import parse_file, ParseError
from .validator import validate

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


def _check_extension(path: str):
    _, ext = os.path.splitext(path)
    if ext != SUPPORTED_EXTENSION:
        raise UnsupportedFileTypeError(
            f"Unsupported test file type '{ext or '(none)'}' for {path!r}. "
            f"Only {SUPPORTED_EXTENSION} files are supported test cases."
        )


def run_nvtest_file(path: str, executor: Executor = None, logger: Logger = None) -> NvtestResult:
    """Parse, execute, validate, and log a single `.nvtest` file.

    Raises:
        UnsupportedFileTypeError: if `path` does not end in .nvtest.
        ParseError:               if the file's syntax/structure is invalid.
        (Both are raised *before* any command is executed and *before* any
        .log file is written -- a rejected file leaves no trace in logs/.)
    """
    _check_extension(path)  # raises before parsing -- wrong format is never interpreted

    test_case = parse_file(path)  # raises ParseError on invalid .nvtest syntax

    executor = executor or Executor()
    logger = logger or Logger()

    results = [executor.run(cmd) for cmd in test_case.commands]

    # A command's output is logged as a hex dump if any binary validation
    # (EXPECT_BYTE / EXPECT_HEX) checks that command's raw bytes; otherwise
    # it's logged as text. This is derived automatically from the
    # validations rather than requiring the test writer to say so.
    binary_flags = [False] * len(results)
    for v in test_case.validations:
        if v.kind in ("BYTE", "HEX"):
            binary_flags[v.run_index] = True

    validation_results, all_passed = validate(test_case, results)
    validation_lines = [f"[{'PASS' if p else 'FAIL'}] {msg}" for p, msg in validation_results]
    status = "PASS" if all_passed else "FAIL"

    log_path = logger.write_nvtest_log(
        test_case.name, results, binary_flags, validation_lines, status,
    )

    return NvtestResult(
        name=test_case.name,
        status=status,
        log_path=log_path,
        validation_lines=validation_results,
        source_path=path,
    )
