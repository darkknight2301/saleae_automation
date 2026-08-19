"""
parser.py - Parses the `.nvtest` DSL into a small internal representation.

Design intent (Phase 2):
- `.nvtest` is the ONLY supported test-case format. This module never reads
  or interprets .py/.yaml/.yml/.json/.xml files; extension enforcement lives
  in runner.py, which refuses to even call this parser on the wrong file type.
- The DSL is intentionally tiny and line-oriented: one statement per line,
  each line starts with a keyword (TEST, RUN, EXPECT_EXIT, EXPECT,
  EXPECT_BYTE, EXPECT_HEX, END). There is no branching, looping, variables,
  or expression language -- this is a manual-test-case format, not a
  programming language.
- Parsing is strict and deterministic: unknown keywords, wrong argument
  counts, and structural mistakes (missing TEST/END, validation with no
  preceding RUN, etc.) all raise ParseError with a line number, so bad
  syntax is rejected loudly rather than silently misinterpreted.
- Lines whose first non-whitespace character is `#` are comments and are
  skipped entirely, same as blank lines (Phase 3 addition -- purely
  additive, does not change how any existing statement is parsed). This
  lets realistic `.nvtest` examples document prerequisites (e.g. "requires
  real NVMe hardware") without inventing a new keyword.
- The output is a plain, framework-internal TestCase / Validation model
  (dataclasses). Nothing here exposes Python objects, syntax, or semantics
  to the test writer -- they only ever see the DSL.
"""

import shlex
from dataclasses import dataclass, field
from typing import List, Optional


class ParseError(Exception):
    """Raised for any structurally or syntactically invalid .nvtest file.

    Carries the line number and raw line text so the runner can print a
    precise, actionable error instead of a bare traceback.
    """

    def __init__(self, message: str, line_no: int = None, line_text: str = None):
        self.line_no = line_no
        self.line_text = line_text
        if line_no is not None:
            message = f"line {line_no}: {message}"
            if line_text:
                message += f"\n    > {line_text}"
        super().__init__(message)


# Validation kinds (internal only -- never shown to the test writer):
EXIT = "EXIT"
TEXT_CONTAINS = "TEXT_CONTAINS"
TEXT_NOT_CONTAINS = "TEXT_NOT_CONTAINS"
TEXT_NOT_EMPTY = "TEXT_NOT_EMPTY"
BYTE = "BYTE"
HEX = "HEX"

_TEXT_OPERATORS = {"CONTAINS", "NOT_CONTAINS", "NOT_EMPTY"}


@dataclass
class Validation:
    """One EXPECT_EXIT / EXPECT / EXPECT_BYTE / EXPECT_HEX statement.

    `run_index` binds this validation to the output of a specific RUN
    statement -- the nearest RUN that appeared above it in the file. This
    lets a .nvtest file issue more than one RUN and have each EXPECT-style
    line check the command it logically follows.
    """

    kind: str
    run_index: int
    line_no: int
    # EXIT
    expected_exit: Optional[int] = None
    # TEXT_*
    field: Optional[str] = None
    value: Optional[str] = None
    # BYTE / HEX
    offset: Optional[int] = None
    expected_byte: Optional[int] = None
    hex_string: Optional[str] = None


@dataclass
class TestCase:
    """Internal representation of a parsed .nvtest file.

    TestCase
     |-- name          : str, from TEST "..."
     |-- commands       : list[str], one per RUN "...", in order
     |-- validations    : list[Validation], in declared order
    """

    name: str
    commands: List[str] = field(default_factory=list)
    validations: List[Validation] = field(default_factory=list)
    source_path: Optional[str] = None


def _split_tokens(line: str, line_no: int) -> List[str]:
    try:
        return shlex.split(line, posix=True)
    except ValueError as exc:
        # shlex raises ValueError for things like an unterminated quote.
        raise ParseError(f"malformed line ({exc})", line_no, line)


def _parse_int_maybe_hex(token: str, line_no: int, line: str, what: str) -> int:
    try:
        return int(token, 0) if token.lower().startswith("0x") else int(token)
    except ValueError:
        raise ParseError(f"expected an integer for {what}, got {token!r}", line_no, line)


def parse_text(text: str, source_path: str = None) -> TestCase:
    """Parse .nvtest source text into a TestCase. Raises ParseError on any
    invalid syntax or structure."""

    raw_lines = text.splitlines()

    test_name = None
    end_seen = False
    commands: List[str] = []
    validations: List[Validation] = []
    current_run_index = -1  # -1 means "no RUN encountered yet"
    statements_seen = 0  # non-blank lines processed so far

    for i, raw_line in enumerate(raw_lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue

        if end_seen:
            raise ParseError("no statements are allowed after END", i, raw_line)

        tokens = _split_tokens(line, i)
        if not tokens:
            continue
        keyword = tokens[0]
        statements_seen += 1

        if keyword == "TEST":
            if test_name is not None:
                raise ParseError("only one TEST statement is allowed per file", i, raw_line)
            if statements_seen != 1:
                raise ParseError("TEST must be the first statement in the file", i, raw_line)
            if len(tokens) != 2:
                raise ParseError('expected: TEST "<name>"', i, raw_line)
            test_name = tokens[1]

        elif keyword == "RUN":
            if test_name is None:
                raise ParseError("RUN must come after TEST", i, raw_line)
            if len(tokens) != 2:
                raise ParseError('expected: RUN "<command>"', i, raw_line)
            commands.append(tokens[1])
            current_run_index = len(commands) - 1

        elif keyword == "EXPECT_EXIT":
            if current_run_index == -1:
                raise ParseError("EXPECT_EXIT must come after a RUN statement", i, raw_line)
            if len(tokens) != 2:
                raise ParseError("expected: EXPECT_EXIT <code>", i, raw_line)
            expected_exit = _parse_int_maybe_hex(tokens[1], i, raw_line, "EXPECT_EXIT")
            validations.append(Validation(
                kind=EXIT, run_index=current_run_index, line_no=i,
                expected_exit=expected_exit,
            ))

        elif keyword == "EXPECT":
            if current_run_index == -1:
                raise ParseError("EXPECT must come after a RUN statement", i, raw_line)
            if len(tokens) < 3:
                raise ParseError(
                    'expected: EXPECT "<field>" CONTAINS "<value>" | '
                    'EXPECT "<field>" NOT_CONTAINS | EXPECT "<field>" NOT_EMPTY',
                    i, raw_line,
                )
            field_name = tokens[1]
            operator = tokens[2]
            if operator not in _TEXT_OPERATORS:
                raise ParseError(
                    f"unknown EXPECT operator {operator!r} "
                    f"(expected CONTAINS, NOT_CONTAINS, or NOT_EMPTY)",
                    i, raw_line,
                )
            if operator == "CONTAINS":
                if len(tokens) != 4:
                    raise ParseError('expected: EXPECT "<field>" CONTAINS "<value>"', i, raw_line)
                validations.append(Validation(
                    kind=TEXT_CONTAINS, run_index=current_run_index, line_no=i,
                    field=field_name, value=tokens[3],
                ))
            elif operator == "NOT_CONTAINS":
                if len(tokens) != 3:
                    raise ParseError('expected: EXPECT "<field>" NOT_CONTAINS', i, raw_line)
                validations.append(Validation(
                    kind=TEXT_NOT_CONTAINS, run_index=current_run_index, line_no=i,
                    field=field_name,
                ))
            else:  # NOT_EMPTY
                if len(tokens) != 3:
                    raise ParseError('expected: EXPECT "<field>" NOT_EMPTY', i, raw_line)
                validations.append(Validation(
                    kind=TEXT_NOT_EMPTY, run_index=current_run_index, line_no=i,
                    field=field_name,
                ))

        elif keyword == "EXPECT_BYTE":
            if current_run_index == -1:
                raise ParseError("EXPECT_BYTE must come after a RUN statement", i, raw_line)
            if len(tokens) != 3:
                raise ParseError("expected: EXPECT_BYTE <offset> <value>", i, raw_line)
            offset = _parse_int_maybe_hex(tokens[1], i, raw_line, "EXPECT_BYTE offset")
            value = _parse_int_maybe_hex(tokens[2], i, raw_line, "EXPECT_BYTE value")
            if not (0 <= value <= 0xFF):
                raise ParseError(f"EXPECT_BYTE value must be a single byte (0x00-0xFF), got {tokens[2]}", i, raw_line)
            if offset < 0:
                raise ParseError("EXPECT_BYTE offset must be >= 0", i, raw_line)
            validations.append(Validation(
                kind=BYTE, run_index=current_run_index, line_no=i,
                offset=offset, expected_byte=value,
            ))

        elif keyword == "EXPECT_HEX":
            if current_run_index == -1:
                raise ParseError("EXPECT_HEX must come after a RUN statement", i, raw_line)
            if len(tokens) != 3:
                raise ParseError('expected: EXPECT_HEX <offset> "<hexstring>"', i, raw_line)
            offset = _parse_int_maybe_hex(tokens[1], i, raw_line, "EXPECT_HEX offset")
            if offset < 0:
                raise ParseError("EXPECT_HEX offset must be >= 0", i, raw_line)
            hex_string = tokens[2]
            try:
                bytes.fromhex(hex_string)
            except ValueError:
                raise ParseError(f"invalid hex string {hex_string!r} for EXPECT_HEX", i, raw_line)
            validations.append(Validation(
                kind=HEX, run_index=current_run_index, line_no=i,
                offset=offset, hex_string=hex_string,
            ))

        elif keyword == "END":
            if len(tokens) != 1:
                raise ParseError("END takes no arguments", i, raw_line)
            end_seen = True

        else:
            raise ParseError(f"unknown statement {keyword!r}", i, raw_line)

    if test_name is None:
        raise ParseError("missing TEST statement (every .nvtest file must start with TEST \"<name>\")")
    if not commands:
        raise ParseError("no RUN statement found (every .nvtest file needs at least one RUN)")
    if not end_seen:
        raise ParseError("missing END statement (every .nvtest file must end with END)")

    return TestCase(name=test_name, commands=commands, validations=validations, source_path=source_path)


def parse_file(path: str) -> TestCase:
    """Read and parse a .nvtest file from disk.

    Extension enforcement (rejecting non-.nvtest files) is the runner's
    job, not the parser's -- this function will happily parse .nvtest
    *syntax* regardless of what it's called; call it only after the runner
    has confirmed the extension.
    """
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    return parse_text(text, source_path=path)
