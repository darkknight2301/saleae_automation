"""
parser.py - Parses the `.nvtest` DSL into a small internal representation.

Design intent (Phase 2):
- `.nvtest` is the ONLY supported test-case format. This module never reads
  or interprets .py/.yaml/.yml/.json/.xml files; extension enforcement lives
  in runner.py, which refuses to even call this parser on the wrong file type.
- The DSL is intentionally tiny and line-oriented: one statement per line,
  each line starts with a keyword (TEST, RUN, EXPECT_EXIT, EXPECT,
  EXPECT_STDERR, EXPECT_BYTE, EXPECT_HEX, EXPECT_REGEX, EXPECT_REGEX_STDERR,
  PARALLEL, END_PARALLEL, END).
  There is no branching or expression language -- this is a manual-test-case
  format, not a programming language.
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

Loop/Parallel addition:
- `RUN "<command>" LOOP <n>` runs that command sequentially `n` times
  in place of once. Every EXPECT* bound to it is then checked against
  EVERY iteration, not just one -- the test fails if any iteration fails
  any bound validation. Omitting `LOOP <n>` is exactly `LOOP 1`, which is
  identical to the pre-existing single-execution behavior (no change for
  any existing .nvtest file).
- `PARALLEL` / `END_PARALLEL` wraps two or more RUN statements (each with
  its own optional LOOP) that execute concurrently, one OS thread per RUN,
  with the framework waiting for all of them to finish before the test
  continues past END_PARALLEL. This is what makes "id-ctrl looped 1000x
  concurrently with reset looped 100x" expressible in one .nvtest file.
  Nesting PARALLEL blocks is not supported.

Stderr validation addition:
- `EXPECT_STDERR "<field>" CONTAINS|NOT_CONTAINS|NOT_EMPTY ...` is
  identical in grammar to EXPECT, but checks the bound command's stderr
  instead of its stdout (plain EXPECT never looked at stderr at all).

Variable capture addition:
- `RUN "<command>" [LOOP <n>] [CAPTURE <name>]` -- CAPTURE stores that
  RUN's stdout (last iteration, if LOOP is also used) into a runtime
  variable `<name>`, which later RUN/EXPECT ... CONTAINS statements can
  then reference as `{{name}}`, exactly like a common_variables.json
  variable. LOOP and CAPTURE are independent, order-insensitive modifiers.

Regex validation addition:
- `EXPECT_REGEX "<pattern>"` / `EXPECT_REGEX_STDERR "<pattern>"` check
  whether `<pattern>` (a Python `re` pattern) matches anywhere in the
  bound command's stdout/stderr. The pattern is validated (compiled) at
  parse time so a malformed regex is a ParseError, not a runtime surprise.
"""

import re
import shlex
from dataclasses import dataclass, field
from typing import List, Optional

from .utility import parse_int_maybe_hex


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
REGEX = "REGEX"
NUM_EQ = "NUM_EQ"
NUM_NEQ = "NUM_NEQ"
NUM_GT = "NUM_GT"
NUM_GE = "NUM_GE"
NUM_LT = "NUM_LT"
NUM_LE = "NUM_LE"

NUMERIC_KINDS = {NUM_EQ, NUM_NEQ, NUM_GT, NUM_GE, NUM_LT, NUM_LE}

_CAPTURE_NAME_RE = re.compile(r"[A-Za-z0-9_]+")

_TEXT_OPERATORS = {"CONTAINS", "NOT_CONTAINS", "NOT_EMPTY"}
_TEXT_KIND_BY_OPERATOR = {
    "CONTAINS": TEXT_CONTAINS,
    "NOT_CONTAINS": TEXT_NOT_CONTAINS,
    "NOT_EMPTY": TEXT_NOT_EMPTY,
}
_NUMERIC_OPERATORS = {"EQ", "NEQ", "GT", "GE", "LT", "LE"}
_NUMERIC_KIND_BY_OPERATOR = {
    "EQ": NUM_EQ, "NEQ": NUM_NEQ, "GT": NUM_GT, "GE": NUM_GE, "LT": NUM_LT, "LE": NUM_LE,
}


@dataclass
class Validation:
    """One EXPECT_EXIT / EXPECT / EXPECT_STDERR / EXPECT_BYTE / EXPECT_HEX /
    EXPECT_REGEX / EXPECT_REGEX_STDERR statement.

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
    # TEXT_* (EXPECT / EXPECT_STDERR)
    field: Optional[str] = None
    value: Optional[str] = None
    stream: str = "stdout"  # "stdout" (EXPECT/EXPECT_REGEX) or "stderr" (*_STDERR variants)
    # BYTE / HEX
    offset: Optional[int] = None
    expected_byte: Optional[int] = None
    hex_string: Optional[str] = None
    # REGEX
    pattern: Optional[str] = None


@dataclass
class TestCase:
    """Internal representation of a parsed .nvtest file.

    TestCase
     |-- name              : str, from TEST "..."
     |-- commands           : list[str], one per RUN "...", in order
     |-- validations        : list[Validation], in declared order
     |-- loop_counts        : list[int], same length/order as `commands`;
     |                         1 unless that RUN used "LOOP <n>"
     |-- parallel_group_id  : list[Optional[int]], same length/order as
     |                        `commands`; None for a normal sequential RUN,
     |                        or a shared int for every RUN inside the same
     |                        PARALLEL block (so the runner knows which
     |                        commands to execute concurrently together)
     |-- capture_names      : list[Optional[str]], same length/order as
                              `commands`; None unless that RUN used
                              "CAPTURE <name>", in which case its stdout is
                              stored as a runtime variable of that name
    """

    name: str
    commands: List[str] = field(default_factory=list)
    validations: List[Validation] = field(default_factory=list)
    source_path: Optional[str] = None
    loop_counts: List[int] = field(default_factory=list)
    parallel_group_id: List[Optional[int]] = field(default_factory=list)
    capture_names: List[Optional[str]] = field(default_factory=list)


def _split_tokens(line: str, line_no: int) -> List[str]:
    try:
        return shlex.split(line, posix=True)
    except ValueError as exc:
        # shlex raises ValueError for things like an unterminated quote.
        raise ParseError(f"malformed line ({exc})", line_no, line)


def _parse_int_maybe_hex(token: str, line_no: int, line: str, what: str) -> int:
    try:
        return parse_int_maybe_hex(token)
    except ValueError:
        raise ParseError(f"expected an integer for {what}, got {token!r}", line_no, line)


def _parse_text_expect(tokens, i, raw_line, keyword, kind_prefix, current_run_index, stream, validations):
    """Shared grammar for EXPECT and EXPECT_STDERR: "<field>"
    CONTAINS/NOT_CONTAINS/NOT_EMPTY/EQ/NEQ/GT/GE/LT/LE ["<value>"], differing
    only in which stream (`stdout`/`stderr`) the resulting Validation checks."""
    if current_run_index == -1:
        raise ParseError(f"{keyword} must come after a RUN statement", i, raw_line)
    if len(tokens) < 3:
        raise ParseError(
            f'expected: {keyword} "<field>" CONTAINS "<value>" | '
            f'{keyword} "<field>" NOT_CONTAINS | {keyword} "<field>" NOT_EMPTY | '
            f'{keyword} "<field>" EQ/NEQ/GT/GE/LT/LE <number>',
            i, raw_line,
        )
    field_name = tokens[1]
    operator = tokens[2]
    if operator in _NUMERIC_OPERATORS:
        if len(tokens) != 4:
            raise ParseError(f'expected: {keyword} "<field>" {operator} <number>', i, raw_line)
        number_token = tokens[3]
        if "{{" not in number_token:
            try:
                float(number_token)
            except ValueError:
                raise ParseError(
                    f"expected a number for {keyword} ... {operator}, got {number_token!r}",
                    i, raw_line,
                )
        validations.append(Validation(
            kind=_NUMERIC_KIND_BY_OPERATOR[operator], run_index=current_run_index, line_no=i,
            field=field_name, value=number_token, stream=stream,
        ))
        return
    if operator not in _TEXT_OPERATORS:
        raise ParseError(
            f"unknown {keyword} operator {operator!r} "
            f"(expected CONTAINS, NOT_CONTAINS, NOT_EMPTY, EQ, NEQ, GT, GE, LT, or LE)",
            i, raw_line,
        )
    kind = _TEXT_KIND_BY_OPERATOR[operator]
    if operator == "CONTAINS":
        if len(tokens) != 4:
            raise ParseError(f'expected: {keyword} "<field>" CONTAINS "<value>"', i, raw_line)
        validations.append(Validation(
            kind=kind, run_index=current_run_index, line_no=i,
            field=field_name, value=tokens[3], stream=stream,
        ))
    else:
        if len(tokens) != 3:
            raise ParseError(f'expected: {keyword} "<field>" {operator}', i, raw_line)
        validations.append(Validation(
            kind=kind, run_index=current_run_index, line_no=i,
            field=field_name, stream=stream,
        ))


def parse_text(text: str, source_path: str = None) -> TestCase:
    """Parse .nvtest source text into a TestCase. Raises ParseError on any
    invalid syntax or structure."""

    raw_lines = text.splitlines()

    test_name = None
    end_seen = False
    commands: List[str] = []
    validations: List[Validation] = []
    loop_counts: List[int] = []
    parallel_group_id: List[Optional[int]] = []
    capture_names: List[Optional[str]] = []
    current_run_index = -1  # -1 means "no RUN encountered yet"
    statements_seen = 0  # non-blank lines processed so far

    in_parallel = False
    parallel_block_start_line = None
    current_parallel_group = None
    next_parallel_group = 0
    parallel_group_run_count = 0  # RUN statements seen since the current PARALLEL opened

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

        elif keyword == "PARALLEL":
            if test_name is None:
                raise ParseError("PARALLEL must come after TEST", i, raw_line)
            if in_parallel:
                raise ParseError("nested PARALLEL blocks are not supported", i, raw_line)
            if len(tokens) != 1:
                raise ParseError("PARALLEL takes no arguments", i, raw_line)
            in_parallel = True
            parallel_block_start_line = i
            current_parallel_group = next_parallel_group
            next_parallel_group += 1
            parallel_group_run_count = 0

        elif keyword == "END_PARALLEL":
            if not in_parallel:
                raise ParseError("END_PARALLEL without a matching PARALLEL", i, raw_line)
            if len(tokens) != 1:
                raise ParseError("END_PARALLEL takes no arguments", i, raw_line)
            if parallel_group_run_count < 2:
                raise ParseError(
                    "a PARALLEL block must contain at least 2 RUN statements "
                    f"(found {parallel_group_run_count})",
                    parallel_block_start_line, None,
                )
            in_parallel = False
            current_parallel_group = None

        elif keyword == "RUN":
            if test_name is None:
                raise ParseError("RUN must come after TEST", i, raw_line)
            if len(tokens) < 2 or (len(tokens) - 2) % 2 != 0:
                raise ParseError('expected: RUN "<command>" [LOOP <n>] [CAPTURE <name>]', i, raw_line)
            loop_count = 1
            capture_name = None
            seen_modifiers = set()
            idx = 2
            while idx < len(tokens):
                modifier = tokens[idx]
                modifier_value = tokens[idx + 1]
                if modifier not in ("LOOP", "CAPTURE"):
                    raise ParseError(
                        f"unknown RUN modifier {modifier!r} (expected LOOP or CAPTURE)", i, raw_line,
                    )
                if modifier in seen_modifiers:
                    raise ParseError(f"RUN modifier {modifier} specified more than once", i, raw_line)
                seen_modifiers.add(modifier)
                if modifier == "LOOP":
                    loop_count = _parse_int_maybe_hex(modifier_value, i, raw_line, "RUN ... LOOP")
                    if loop_count < 1:
                        raise ParseError("LOOP count must be >= 1", i, raw_line)
                else:  # CAPTURE
                    if not _CAPTURE_NAME_RE.fullmatch(modifier_value):
                        raise ParseError(
                            f"invalid CAPTURE variable name {modifier_value!r} "
                            "(must contain only letters, digits, underscore)",
                            i, raw_line,
                        )
                    capture_name = modifier_value
                idx += 2
            commands.append(tokens[1])
            loop_counts.append(loop_count)
            parallel_group_id.append(current_parallel_group)
            capture_names.append(capture_name)
            if in_parallel:
                parallel_group_run_count += 1
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
            _parse_text_expect(tokens, i, raw_line, "EXPECT", TEXT_CONTAINS,
                                current_run_index, "stdout", validations)

        elif keyword == "EXPECT_STDERR":
            _parse_text_expect(tokens, i, raw_line, "EXPECT_STDERR", TEXT_CONTAINS,
                                current_run_index, "stderr", validations)

        elif keyword in ("EXPECT_REGEX", "EXPECT_REGEX_STDERR"):
            if current_run_index == -1:
                raise ParseError(f"{keyword} must come after a RUN statement", i, raw_line)
            if len(tokens) != 2:
                raise ParseError(f'expected: {keyword} "<pattern>"', i, raw_line)
            pattern = tokens[1]
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ParseError(f"invalid regex {pattern!r} for {keyword}: {exc}", i, raw_line)
            validations.append(Validation(
                kind=REGEX, run_index=current_run_index, line_no=i,
                pattern=pattern, stream="stderr" if keyword == "EXPECT_REGEX_STDERR" else "stdout",
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
            if in_parallel:
                raise ParseError("missing END_PARALLEL before END", i, raw_line)
            end_seen = True

        else:
            raise ParseError(f"unknown statement {keyword!r}", i, raw_line)

    if test_name is None:
        raise ParseError("missing TEST statement (every .nvtest file must start with TEST \"<name>\")")
    if not commands:
        raise ParseError("no RUN statement found (every .nvtest file needs at least one RUN)")
    if in_parallel:
        raise ParseError("missing END_PARALLEL (PARALLEL block never closed)", parallel_block_start_line, None)
    if not end_seen:
        raise ParseError("missing END statement (every .nvtest file must end with END)")

    return TestCase(
        name=test_name, commands=commands, validations=validations, source_path=source_path,
        loop_counts=loop_counts, parallel_group_id=parallel_group_id, capture_names=capture_names,
    )


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


class TestParser:
    """Thin class wrapper around parse_file()/parse_text(). Parsing logic
    itself is unchanged; this exists so TestRunner depends on an object,
    not a bare module function."""

    def parse(self, path: str) -> TestCase:
        return parse_file(path)

    def parse_text(self, text: str, source_path: str = None) -> TestCase:
        return parse_text(text, source_path=source_path)
