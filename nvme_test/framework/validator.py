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

from typing import List, Tuple

from .parser import (
    TestCase, EXIT, TEXT_CONTAINS, TEXT_NOT_CONTAINS, TEXT_NOT_EMPTY, BYTE, HEX,
)


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


def validate(test_case: TestCase, results: List) -> Tuple[List[Tuple[bool, str]], bool]:
    """Run every validation in `test_case` against the matching CommandResult
    in `results` (results[i] is the output of test_case.commands[i]).

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

        if v.kind == EXIT:
            passed = result.exit_code == v.expected_exit
            message = f"Exit code == {v.expected_exit}"
            if not passed:
                message += f" (got {result.exit_code})"

        elif v.kind == TEXT_CONTAINS:
            text = result.stdout_text()
            line = _find_field_line(text, v.field)
            passed = line is not None and v.value in line
            message = f'"{v.field}" contains "{v.value}"'
            if line is None:
                message += " (field not found in output)"

        elif v.kind == TEXT_NOT_CONTAINS:
            text = result.stdout_text()
            passed = v.field not in text
            message = f'"{v.field}" not present in output'

        elif v.kind == TEXT_NOT_EMPTY:
            text = result.stdout_text()
            line = _find_field_line(text, v.field)
            value_part = _extract_value_after_field(line, v.field) if line is not None else ""
            passed = bool(value_part)
            message = f'"{v.field}" is not empty'
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

        all_passed = all_passed and passed
        validation_lines.append((passed, message))

    return validation_lines, all_passed
