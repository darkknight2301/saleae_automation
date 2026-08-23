"""
validator.py - Executes a TestCase's validations against CommandResults.

Design intent (Phase 2):
- All validations always run, even after an earlier one fails (per spec:
  "All validations must execute even if one fails"). There is no
  short-circuiting.
- A single failed validation makes the overall test FAIL.
- Binary validations (EXPECT_BYTE / EXPECT_HEX) operate on the raw
  `CommandResult.stdout` bytes captured by the executor -- never on the
  formatted hex-dump text that ends up in the .log. The hex dump is a
  human-readable rendering produced afterwards by logger.py; it is not
  parsed back for validation.
- Text validations (EXPECT ... CONTAINS/NOT_CONTAINS/NOT_EMPTY) work on a
  simple, deterministic "find the line containing <field>" model -- see
  module docstring notes below each helper for the exact semantics.
"""

from dataclasses import dataclass
from typing import List, Tuple

from .parser import (
    TestCase, EXIT, TEXT_CONTAINS, TEXT_NOT_CONTAINS, TEXT_NOT_EMPTY, BYTE, HEX,
)


@dataclass
class ValidationResult:
    """A single validation's outcome. Replaces the bare (bool, str) tuple
    with a named, self-documenting result."""
    passed: bool
    message: str


def _find_field_line(text: str, field_name: str):
    """Return the first line of `text` containing `field_name` as a
    substring, or None if no such line exists."""
    for line in text.splitlines():
        if field_name in line:
            return line
    return None


def _extract_value_after_field(line: str, field_name: str) -> str:
    """Given a line known to contain `field_name`, return whatever comes
    after it with common key/value separators (':', '=') and surrounding
    whitespace stripped. Used for NOT_EMPTY checks, e.g.:

        "Firmware Revision : 2B2QEXM7"  ->  "2B2QEXM7"
        "Firmware Revision :"           ->  ""
    """
    idx = line.find(field_name)
    remainder = line[idx + len(field_name):]
    return remainder.strip().lstrip(":=").strip()


def check_validation(v, result, variable_manager=None) -> Tuple[bool, str]:
    """Check one Validation against one CommandResult. Returns (passed, message).

    Extracted from validate() so a looped RUN (LOOP > 1) can call this once
    per iteration without re-implementing the per-kind logic -- validate()
    itself is now a thin loop over this function for the LOOP=1 case, which
    is the overwhelming majority of existing .nvtest files and produces
    byte-for-byte identical messages to before this refactor.
    """
    value = v.value
    if variable_manager is not None and value is not None:
        value = variable_manager.substitute(value)

    if v.kind == EXIT:
        passed = result.exit_code == v.expected_exit
        message = f"Exit code == {v.expected_exit}"
        if not passed:
            message += f" (got {result.exit_code})"

    elif v.kind in (TEXT_CONTAINS, TEXT_NOT_CONTAINS, TEXT_NOT_EMPTY):
        text = result.stderr_text() if v.stream == "stderr" else result.stdout_text()
        stream_suffix = " (stderr)" if v.stream == "stderr" else ""

        if v.kind == TEXT_CONTAINS:
            line = _find_field_line(text, v.field)
            passed = line is not None and value in line
            message = f'"{v.field}" contains "{value}"{stream_suffix}'
            if line is None:
                message += " (field not found in output)"

        elif v.kind == TEXT_NOT_CONTAINS:
            passed = v.field not in text
            message = f'"{v.field}" not present in output{stream_suffix}'

        else:  # TEXT_NOT_EMPTY
            line = _find_field_line(text, v.field)
            value_part = _extract_value_after_field(line, v.field) if line is not None else ""
            passed = bool(value_part)
            message = f'"{v.field}" is not empty{stream_suffix}'
            if line is None:
                message += " (field not found in output)"

    elif v.kind == BYTE:
        data = result.stdout
        in_range = v.offset < len(data)
        passed = in_range and data[v.offset] == v.expected_byte
        message = f"Byte at offset 0x{v.offset:02x} == 0x{v.expected_byte:02x}"
        if not in_range:
            message += f" (offset beyond {len(data)} captured bytes)"
        elif not passed:
            message += f" (got 0x{data[v.offset]:02x})"

    elif v.kind == HEX:
        data = result.stdout
        expected = bytes.fromhex(v.hex_string)
        actual = data[v.offset:v.offset + len(expected)]
        passed = actual == expected
        message = f"Bytes at offset 0x{v.offset:02x} == {v.hex_string}"
        if not passed:
            message += f" (got {actual.hex()})"

    else:  # pragma: no cover - parser never produces unknown kinds
        passed = False
        message = f"Unknown validation kind: {v.kind}"

    return passed, message


def validate(test_case: TestCase, results: List, variable_manager=None) -> Tuple[List[Tuple[bool, str]], bool]:
    """Run every validation in `test_case` against the matching CommandResult
    in `results` (results[i] is the output of test_case.commands[i]).

    If `variable_manager` is given, {{name}} placeholders in EXPECT values
    are substituted before comparison (VariableError propagates uncaught).

    This is the LOOP=1 path: each validation is checked exactly once,
    against the single CommandResult for its bound RUN. A looped RUN
    (LOOP > 1) is validated differently, once per iteration -- see
    TestRunner, which calls check_validation() directly for that case.

    Returns:
        (validation_lines, all_passed)
        validation_lines: list of (passed: bool, message: str), in the same
                           order validations were declared.
        all_passed:        True only if every validation passed.
    """
    validation_lines: List[Tuple[bool, str]] = []
    all_passed = True

    for v in test_case.validations:
        result = results[v.run_index]
        passed, message = check_validation(v, result, variable_manager)
        all_passed = all_passed and passed
        validation_lines.append((passed, message))

    return validation_lines, all_passed


def describe_validation(v, variable_manager=None) -> str:
    """Human-readable label for a Validation, independent of any particular
    CommandResult -- e.g. 'Exit code == 0', '"Model Number" contains
    "KIOXIA"'. Used by TestRunner to build an aggregate PASS/FAIL summary
    for a looped RUN (LOOP > 1), where no single CommandResult represents
    the whole validation the way it does for an ordinary LOOP=1 RUN.

    Deliberately duplicates the small "base description" fragments already
    built inline inside check_validation() -- kept separate rather than
    threading a "give me just the label" mode through check_validation(),
    to avoid complicating that function's simple, single-purpose signature
    for a need that only the loop-reporting path has.
    """
    value = v.value
    if variable_manager is not None and value is not None:
        value = variable_manager.substitute(value)
    stream_suffix = " (stderr)" if v.stream == "stderr" else ""

    if v.kind == EXIT:
        return f"Exit code == {v.expected_exit}"
    elif v.kind == TEXT_CONTAINS:
        return f'"{v.field}" contains "{value}"{stream_suffix}'
    elif v.kind == TEXT_NOT_CONTAINS:
        return f'"{v.field}" not present in output{stream_suffix}'
    elif v.kind == TEXT_NOT_EMPTY:
        return f'"{v.field}" is not empty{stream_suffix}'
    elif v.kind == BYTE:
        return f"Byte at offset 0x{v.offset:02x} == 0x{v.expected_byte:02x}"
    elif v.kind == HEX:
        return f"Bytes at offset 0x{v.offset:02x} == {v.hex_string}"
    else:  # pragma: no cover
        return f"Unknown validation kind: {v.kind}"


class Validator:
    """Thin class wrapper around validate(), holding an optional
    VariableManager so callers don't have to pass it on every call."""

    def __init__(self, variable_manager=None):
        self.variable_manager = variable_manager

    def validate(self, test_case: TestCase, results: List) -> Tuple[List[ValidationResult], bool]:
        raw_lines, all_passed = validate(test_case, results, self.variable_manager)
        return [ValidationResult(passed=p, message=m) for p, m in raw_lines], all_passed
