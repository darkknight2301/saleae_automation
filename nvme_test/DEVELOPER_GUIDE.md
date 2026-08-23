# NVMe/FIO Test Automation Framework — Developer Guide

This guide is for engineers maintaining, debugging, or extending the framework. All class
names, method names, file paths, and behavior described here were verified directly against
the source in `framework/` and `run.py`, and by actually executing the code shown.

```
CLI (framework/cli.py)
 |
 v
ConfigManager (framework/config_manager.py)
 |
 v
TestRunner construction (framework/runner.py) -- creates run dir, FrameworkLogger,
 |                                                VariableManager, TestParser,
 |                                                CommandExecutor, Validator, ResultLogger
 v
Test discovery (framework/cli.py: discover_targets)
 |
 v
TestParser.parse() -> TestCase (framework/parser.py)
 |
 v
TestRunner.run() orchestrates:
 |
 v
CommandExecutor.run() -> CommandResult (framework/executor.py)
 |
 v
Validator.validate() -> ValidationResult (framework/validator.py)
 |
 v
ResultLogger.write_nvtest_log() (framework/logger.py)
 |
 v
Log file in logs/{run_id}/
```

---

## 1. Architecture Overview

```
run.py
  |
  +-- (no args) --> internal self-verification (functions defined directly in run.py)
  |
  +-- (args)    --> framework.cli.main(argv)
                       |
                       +-- ConfigManager(args.config)
                       +-- TestRunner(config=...)      <-- one instance per CLI invocation
                             |
                             +-- FrameworkLogger        (console + run.log)
                             +-- VariableManager         (loads common_variables.json)
                             +-- TestParser
                             +-- CommandExecutor
                             +-- Validator
                             +-- ResultLogger
                       |
                       +-- discover_targets(path) --> [CliTarget, ...]
                       +-- run_targets(targets, test_runner) --> [(label, status, detail), ...]
                             (each target: test_runner.run(path))
                       +-- print_summary(rows, test_runner.log)
                       +-- test_runner.close()
```

**Class responsibility summary:**

| Class | Responsibility |
|---|---|
| `TestRunner` | Owns one execution: one run directory, one `ConfigManager`, one `VariableManager`, and the shared `CommandExecutor`/`Validator`/`ResultLogger`/`FrameworkLogger` instances used for every `.nvtest` file run through it |
| `TestParser` | Thin object wrapper around `parse_file()`/`parse_text()` |
| `TestCase` / `Validation` | Plain dataclasses -- the parsed, in-memory representation of one `.nvtest` file |
| `CommandExecutor` | Runs one shell command, returns a `CommandResult` |
| `CommandResult` | Plain dataclass -- exit code + raw stdout/stderr bytes + timing |
| `Validator` | Runs every `Validation` in a `TestCase` against its bound `CommandResult`, returns `ValidationResult`s |
| `ValidationResult` | Plain dataclass -- `passed: bool`, `message: str` |
| `ResultLogger` | Formats one test's commands/output/validations into its `.log` file |
| `FrameworkLogger` | Console + `run.log` diagnostic logging (INFO/DEBUG/WARNING/ERROR) -- not test-result `.log` files |
| `ConfigManager` | Loads/merges YAML config with built-in defaults |
| `VariableManager` | Loads `common_variables.json`, resolves `{{name}}` placeholders |
| `Utility` (module, not a class) | `hex_dump`, `safe_filename`, `format_timestamp`, `new_run_id`, `parse_int_maybe_hex` |
| `CliTarget` | One resolved file path + its CLI display label |

**Design decisions worth knowing:**
- `Executor` and `Logger` still exist as **backward-compatible aliases** (`Executor =
  CommandExecutor` in `executor.py`; `Logger = ResultLogger` in `logger.py`) from earlier
  naming. New code should use `CommandExecutor`/`ResultLogger` directly.
- There is a module-level `validate()` function in `validator.py` in addition to the
  `Validator` class; `Validator.validate()` is a thin wrapper delegating to it. This is
  acknowledged, low-priority duplication (documented technical debt, not a bug) rather than an
  oversight — see Section 24.

---

## 2. Repository Structure

```
nvme_test/
├── run.py                       # CLI entry point + internal self-verification harness
├── requirements.txt              # PyYAML>=6.0
├── common_variables.json         # shipped default variables file
├── config/
│   └── config.yaml                # shipped config template (NOT auto-loaded, see cli.py)
├── framework/
│   ├── __init__.py                # empty
│   ├── parser.py                   # ParseError, Validation, TestCase, TestParser, parse_text/parse_file
│   ├── validator.py                 # ValidationResult, validate(), Validator
│   ├── executor.py                  # CommandResult, CommandExecutor (alias: Executor)
│   ├── runner.py                    # UnsupportedFileTypeError, NvtestResult, TestRunner,
│   │                                  check_extension(), run_nvtest_file()
│   ├── logger.py                    # ResultLogger (alias: Logger), private render helpers
│   ├── framework_log.py             # FrameworkLogger
│   ├── config_manager.py            # ConfigManager, _DEFAULTS
│   ├── variable_manager.py          # VariableError, VariableManager
│   ├── utility.py                   # hex_dump, safe_filename, format_timestamp, new_run_id,
│   │                                  parse_int_maybe_hex
│   └── cli.py                       # CliTarget, build_arg_parser, discover_targets,
│                                      run_targets, dry_run_targets, print_summary, main
├── tests/                         # .nvtest files used by run.py's internal self-verification
│   └── examples/                    # realistic examples, never auto-executed if
│                                      hardware-required/destructive (see run.py's
│                                      _HARDWARE_OR_DESTRUCTIVE_EXAMPLES set)
└── logs/                          # created at runtime; one logs/{run_id}/ per execution
```

**Per-module responsibility, dependencies, and what belongs/doesn't belong there:**

- **`parser.py`** — Owns `.nvtest` syntax only. Depends on `utility.parse_int_maybe_hex`.
  Nothing here should know about `subprocess`, YAML, JSON, or logging.
- **`validator.py`** — Owns comparison logic against a `CommandResult`. Depends on `parser`
  (for the `Validation` dataclass and kind constants). Takes an optional `VariableManager` for
  substituting `CONTAINS` values — does not import `variable_manager` at module scope beyond
  what's passed in, keeping it decoupled.
- **`executor.py`** — Owns `subprocess` invocation only. No parsing, no validation, no
  knowledge of `.nvtest` at all — by design, a command is just a string.
- **`runner.py`** — The only module that imports from every other framework module
  (`config_manager`, `executor`, `framework_log`, `logger`, `parser`, `utility`, `validator`,
  `variable_manager`). This is intentional: it's the orchestration layer. Nothing else should
  need this many imports.
- **`logger.py`** — Owns `.log` file formatting/writing only. Depends on `utility` for
  `hex_dump`/`safe_filename`/`format_timestamp`. Does not know about `TestCase`,
  `Validation`, or `CommandExecutor` — it receives already-computed results and strings.
- **`framework_log.py`** — Owns console/`run.log` diagnostics only (stdlib `logging`). No
  dependency on any other framework module.
- **`config_manager.py`** — Owns YAML loading + defaults only. Depends only on `yaml`
  (soft-imported) and stdlib `os`.
- **`variable_manager.py`** — Owns JSON loading + `{{name}}` substitution only. No
  dependency on any other framework module.
- **`utility.py`** — The **one** common utility module. Only generic, reusable,
  non-test-specific helpers belong here (hex formatting, filename sanitization, timestamp
  formatting, run-id generation, int-or-hex parsing). Do not add anything here that only one
  module needs, and do not add `.nvtest`-specific or NVMe-specific logic here.
- **`cli.py`** — Owns `argparse` + directory/file discovery + result-row formatting only. Constructs
  exactly one `TestRunner` per invocation and reuses it for every discovered target.

---

## 3. Execution Lifecycle

Tracing `python3 run.py tests/TC001.nvtest`:

1. **`run.py`** inserts its own directory onto `sys.path`, imports `framework.cli`, and (since
   `len(sys.argv) > 1`) calls `sys.exit(cli.main(sys.argv[1:]))`.
2. **`cli.main(argv)`** (`framework/cli.py`) builds an `argparse.ArgumentParser`
   (`build_arg_parser()`) and parses `argv` into `args` (`path`, `config`, `log_level`,
   `dry_run`).
3. **Configuration loading:** `ConfigManager(args.config)` is constructed. If `args.config` is
   `None` (the common case), `ConfigManager.__init__` skips file I/O entirely and uses its
   built-in `_DEFAULTS` dict. If a path is given, it's read via `yaml.safe_load()` and merged
   over the defaults (`_merge()`). A missing file or missing PyYAML raises immediately, caught
   in `cli.main()` and reported via a bare `print()` (the one place in the framework that
   still uses `print()` directly, because no logger can exist yet — see Section 10).
4. **`TestRunner(config=config)`** is constructed (`framework/runner.py`):
   - `self.run_id = new_run_id()` (`utility.py`) — `YYYYMMDD_HHMMSS_<8-hex>`.
   - `self.run_dir = os.path.join(config.log_directory, self.run_id)`, created via
     `os.makedirs(..., exist_ok=True)`.
   - `self.log = FrameworkLogger(level=config.log_level, run_dir=self.run_dir)` — this is
     **logger initialization**, and it happens here, tied to the run directory.
   - `VariableManager` is loaded from `config.variables_file` if that path exists on disk (a
     missing variables file is not an error at this stage — it's simply not loaded, and any
     `{{...}}` in a later `.nvtest` file would then fail at substitution time instead).
   - `TestParser()`, `CommandExecutor(default_timeout=config.command_timeout)`,
     `Validator(variable_manager=...)`, `ResultLogger(log_dir=self.run_dir)` are constructed.
5. **`if args.log_level:`** — `cli.main()` calls `test_runner.log.set_level(args.log_level)`
   to override the console level for this invocation only.
6. **Test discovery:** `discover_targets(args.path)` — if `args.path` is a directory, globs
   `*.nvtest` inside it (non-recursive) and sorts the result; if it's a file, wraps it as a
   single `CliTarget` regardless of extension (rejection happens later, uniformly).
7. **`run_targets(targets, test_runner)`** iterates the targets; for each, calls
   `test_runner.run(target.path)`:
   - **Extension check:** `check_extension(path)` — raises `UnsupportedFileTypeError` if the
     path doesn't end in `.nvtest`.
   - **Parsing:** `self.parser.parse(path)` → `TestParser.parse()` → `parse_file()` →
     `parse_text()`, producing a `TestCase`.
   - **Variable substitution (commands):** if a `VariableManager` was loaded, every command
     string in `test_case.commands` is passed through `substitute()`.
   - **Command execution:** for each command index, `RUN`s sharing a `parallel_group_id`
     execute concurrently via `ThreadPoolExecutor`; every other index executes sequentially,
     one at a time. Each index's command runs `loop_counts[i]` times in sequence (1 unless
     `LOOP <n>` was used) — see Section 7a for the full mechanics.
   - **Binary-flag detection:** for each `Validation` of kind `BYTE`/`HEX`, the corresponding
     command's index in `binary_flags` is set `True` (drives hex-dump vs. text rendering).
   - **Validation:** each bound `Validation` is checked via `check_validation()` once per
     iteration of its command (once, for an ordinary `LOOP 1` command — identical to before),
     with pass/fail counts and first-failure detail accumulated per validation. See Section 7a.
   - **Logging:** `self.result_logger.write_nvtest_log(...)` writes the `.log` file, with
     `filename_stem` set to the source file's own basename (not the free-text `TEST` name —
     see Section 10), and `loop_infos` carrying per-command loop/parallel metadata (`None` for
     any command with `loop_count == 1` and no parallel group, rendering exactly as before).
   - Returns an `NvtestResult` (`name`, `status`, `log_path`, `validation_lines`,
     `source_path`).
   - Any exception (`UnsupportedFileTypeError`, `ParseError`, `VariableError`, or any other
     `Exception`) is caught **inside `run_targets()`**, not inside `TestRunner.run()` — a
     target's failure becomes an `"ERROR"` row and the loop continues to the next target.
8. **`print_summary(rows, test_runner.log)`** — formats the `label / STATUS` table and
   `Total`/`Passed`/`Failed` counts, all emitted via `test_runner.log.result_line()` (→
   `FrameworkLogger.info()`), never `print()`.
9. **Exit code:** `cli._run_cli()` returns `1` if any row's status isn't `"PASS"`, else `0`
   (`cli.main()` itself can also return `2` for a path/config error before any tests run).
10. **`cli.main()`**'s `finally` block calls `test_runner.close()` →
    `self.log.close()` (`FrameworkLogger.close()`), detaching and closing its handlers.
11. **`run.py`**'s `sys.exit(...)` propagates the returned code as the process exit code.

---

## 4. Class Responsibilities

| Class | Responsibility | Inputs | Outputs | Used By |
|---|---|---|---|---|
| `TestRunner` | Owns one execution's shared state and orchestrates parse→execute→validate→log per file | `ConfigManager`, `variables_path`; then a `.nvtest` path per `run()` call | `NvtestResult` per `run()` call | `cli.py`, `run.py` (both directly and via `run_nvtest_file()`) |
| `TestParser` | Wraps `.nvtest` parsing | file path or raw text | `TestCase` | `TestRunner`, `cli.dry_run_targets()` |
| `CommandExecutor` | Runs one shell command | command string, optional timeout | `CommandResult` | `TestRunner` |
| `Validator` | Runs all validations for a test | `TestCase`, `List[CommandResult]` | `(List[ValidationResult], all_passed: bool)` | `TestRunner` |
| `ResultLogger` | Writes a test's `.log` file | test name, `CommandResult`s, binary flags, validation lines, status | path to the written `.log` | `TestRunner` |
| `FrameworkLogger` | Console + `run.log` diagnostics | level, run dir | log calls (`info`/`debug`/`warning`/`error`/`result_line`) | `TestRunner`, `cli.py` |
| `ConfigManager` | Loads/exposes config | optional YAML path | `.log_directory`, `.log_level`, `.command_timeout`, `.variables_file` properties | `TestRunner`, `cli.main()` |
| `VariableManager` | Loads/substitutes variables | JSON path | substituted strings, or raises `VariableError` | `TestRunner`, `Validator` |

---

## 5. `.nvtest` Parser

```
.nvtest file (text)
      |
      v
parse_text()/parse_file()  -- line-by-line, single pass, no lookahead
      |
      v
  per non-blank, non-comment line:
      strip() -> shlex.split() -> dispatch on tokens[0] (the keyword)
      |
      v
TestCase(name, commands: List[str], validations: List[Validation], source_path,
          loop_counts: List[int], parallel_group_id: List[Optional[int]])
```

**Parsing flow (`framework/parser.py: parse_text()`):**
- Iterates `text.splitlines()` with 1-based line numbers.
- Blank lines and lines starting with `#` (after `.strip()`) are skipped entirely.
- Once `END` has been seen, any further non-blank/non-comment line raises `ParseError("no
  statements are allowed after END", ...)`.
- Each line is tokenized with `shlex.split(line, posix=True)` — this is what makes
  `"quoted values with spaces"` and embedded single quotes work correctly.
- The first token dispatches to one of: `TEST`, `RUN`, `PARALLEL`, `END_PARALLEL`,
  `EXPECT_EXIT`, `EXPECT`, `EXPECT_STDERR`, `EXPECT_BYTE`, `EXPECT_HEX`, `END`. Anything else
  raises `ParseError(f"unknown statement {keyword!r}", ...)`.

**Structural validation performed inline, not as a second pass:**
- `TEST` must be the very first statement (`statements_seen != 1` check) and must appear
  exactly once.
- `RUN` must come after `TEST`.
- `RUN "<command>" [LOOP <n>]` — `len(tokens)` must be `2` (no LOOP) or `4` (`RUN`, command,
  the literal token `"LOOP"`, and the count); `loop_counts.append(loop_count)` records `1` for
  every plain `RUN`, keeping `loop_counts` the same length/order as `commands` at all times.
- `PARALLEL` opens a block: `in_parallel` must be `False` (no nesting), and a fresh
  `current_parallel_group` id (`next_parallel_group`, a simple incrementing counter) is
  assigned. Every `RUN` parsed while `in_parallel` is `True` gets that id appended to
  `parallel_group_id` (`None` for any `RUN` outside a `PARALLEL` block) and increments
  `parallel_group_run_count`.
- `END_PARALLEL` closes the block: raises if `not in_parallel` (no matching `PARALLEL`), and
  raises if `parallel_group_run_count < 2` (a `PARALLEL` block must contain at least 2 `RUN`
  statements) — this check fires at `END_PARALLEL`, using `parallel_block_start_line` (the
  `PARALLEL` line's number) for the error, since that's the line the mistake conceptually
  belongs to.
- `END` (the file's real ending statement) raises `ParseError("missing END_PARALLEL before
  END", ...)` if `in_parallel` is still `True` — an unclosed `PARALLEL` block is caught the
  moment `END` is reached, not only at end-of-file.
- Every `EXPECT*`/`EXPECT_STDERR` keyword requires `current_run_index != -1` (i.e., at least
  one `RUN` must have already been seen) — this is how "validation binds to the nearest
  preceding RUN" is implemented, and it works identically whether that `RUN` is inside a
  `PARALLEL` block or not: `current_run_index` is simply updated to `len(commands) - 1` every
  time a `RUN` line is processed, and every subsequent `Validation` object is stamped with
  whatever `current_run_index` currently holds.
- After the loop: `test_name is None`, `not commands`, `in_parallel` (still open), and `not
  end_seen` are checked and raise `ParseError` for missing `TEST`, missing `RUN`, an unclosed
  `PARALLEL` block, and missing `END` respectively.

**`EXPECT` vs `EXPECT_STDERR`:** both keywords share one grammar helper,
`_parse_text_expect(tokens, i, raw_line, keyword, kind_prefix, current_run_index, stream,
validations)` — the only difference between the two call sites in `parse_text()` is the
`stream` argument (`"stdout"` for `EXPECT`, `"stderr"` for `EXPECT_STDERR`), which is stored on
the resulting `Validation.stream` field and read by `validator.check_validation()` to pick
`result.stdout_text()` vs `result.stderr_text()`.

**Internal representation:** `TestCase` (name, `commands: List[str]`, `validations:
List[Validation]`, `source_path`, `loop_counts: List[int]`, `parallel_group_id:
List[Optional[int]]`) and `Validation` (kind, `run_index`, `line_no`, `stream` (`"stdout"` or
`"stderr"`), plus kind-specific optional fields: `expected_exit`, `field`/`value`,
`offset`/`expected_byte`/`hex_string`). Both are plain `@dataclass`es — no methods beyond what
`@dataclass` auto-generates. `kind` is one of the module-level string constants `EXIT`,
`TEXT_CONTAINS`, `TEXT_NOT_CONTAINS`, `TEXT_NOT_EMPTY`, `BYTE`, `HEX` — these are internal
only, never surfaced to the test author (`EXPECT` and `EXPECT_STDERR` produce the exact same
`kind` values, distinguished only by `stream`).

**Error handling:** every rejection path raises `parser.ParseError`, a subclass of `Exception`
that prepends `"line N: "` and appends the offending raw line (`\n    > <line>`) to the
message when a line number is available. `runner.check_extension()` is a **separate**
function, called before parsing is ever attempted, so a `.yaml` file is never handed to the
parser at all.

---

## 6. TestCase Model

```
TestCase
 ├── name: str                       (from TEST "...")
 ├── commands: List[str]             (one per RUN "...", in order)
 ├── validations: List[Validation]   (in declared order)
 │    └── Validation
 │         ├── kind: str             (EXIT / TEXT_CONTAINS / TEXT_NOT_CONTAINS /
 │         │                          TEXT_NOT_EMPTY / BYTE / HEX)
 │         ├── run_index: int        (index into `commands` this validation is bound to)
 │         ├── line_no: int
 │         ├── stream: str           ("stdout" for EXPECT, "stderr" for EXPECT_STDERR)
 │         └── kind-specific fields  (expected_exit | field+value | offset+expected_byte |
 │                                    offset+hex_string)
 ├── source_path: Optional[str]
 ├── loop_counts: List[int]          (same length/order as `commands`; 1 unless LOOP <n>)
 └── parallel_group_id: List[Optional[int]]  (same length/order as `commands`; None for a
                                                normal RUN, or a shared int for every RUN
                                                inside the same PARALLEL block)
```

There is no separate "steps" or "result" substructure — commands and validations are two
parallel flat lists linked only by `Validation.run_index`. This is deliberately simpler than
the `steps: [{command, validations}]` shape one might expect; it was chosen because it lets
`TestRunner.run()` iterate command indices generically (sequential execution for `LOOP`-only
commands, `ThreadPoolExecutor`-submitted for commands sharing a `parallel_group_id`) rather
than needing a richer nested tree. `loop_counts`/`parallel_group_id` are two more flat lists in
that same shape, extended this way (rather than folding them into `Validation` or introducing
a new `Command` dataclass) specifically so the existing `run_index`-based binding logic needed
no changes at all when this feature was added.

---

## 7. Command Execution

`framework/executor.py: CommandExecutor`:

- **Subprocess usage:** `subprocess.run(command, shell=True, stdout=subprocess.PIPE,
  stderr=subprocess.PIPE, timeout=timeout)`. `shell=True` is used unconditionally by every
  caller in this codebase (the `shell: bool = True` parameter exists but nothing sets it to
  `False`).
- **stdout/stderr:** captured as raw `bytes` (`proc.stdout`/`proc.stderr`), never decoded at
  this layer.
- **Return code:** `proc.returncode`, stored verbatim in `CommandResult.exit_code`.
- **Timeout:** `CommandExecutor.__init__(default_timeout=...)` stores a default (normally
  wired from `ConfigManager.command_timeout`); `run(..., timeout=None)` falls back to that
  default if no explicit value is passed for that call. On `subprocess.TimeoutExpired`,
  `exit_code` is set to `-1` and `[TIMEOUT after {timeout}s]` is appended to stderr — the
  command isn't retried.
- **Errors:** `FileNotFoundError` → `exit_code = 127`; other `OSError` → `exit_code = 1`; both
  populate `stderr` with the exception text and return a normal `CommandResult` rather than
  raising — callers never need a `try/except` around `.run()`.
- **`CommandResult`** (`@dataclass`): `command`, `exit_code`, `stdout: bytes`, `stderr:
  bytes`, `start_time`, `end_time`, plus a `duration` property and `stdout_text()`/
  `stderr_text()` helper methods (UTF-8 decode with `errors="replace"`).

---

## 7a. Loop & Parallel Execution

`TestRunner.run()` (`framework/runner.py`) implements `LOOP <n>` and `PARALLEL` entirely at the
orchestration layer — `CommandExecutor` itself has no concept of looping or concurrency; it
only ever runs one command once per call, exactly as before this feature existed.

```
for each command index i (0..n-1), in file order:
    if parallel_group_id[i] is None:
        execute_slot(i)                          # sequential, in the main thread
    else:
        group = [j for j in range(n)
                 if parallel_group_id[j] == parallel_group_id[i] and not executed[j]]
        submit execute_slot(j) for every j in group to a ThreadPoolExecutor
        wait for all of them (future.result()) before continuing past the group

execute_slot(i):
    for iteration in 1..loop_counts[i]:
        result = self.executor.run(commands[i])          # one real CommandExecutor.run() call
        for v in validations bound to index i:
            passed, message = check_validation(v, result, self.variable_manager)
            update per-validation pass/fail counts, last_message, first_failure_by_vid
    results[i] = the LAST iteration's CommandResult
    loop_infos[i] = None if loop_count == 1 and not parallel, else a summary dict
```

**Why per-iteration validation, not "run everything, then validate":** `check_validation()` is
called immediately after each iteration's `CommandExecutor.run()`, inside the same loop —
`CommandResult`s are not accumulated in memory across iterations (only the pass/fail tallies
and the single most-recent `CommandResult` are kept). This bounds memory usage at O(1) per
command regardless of `LOOP` count, rather than O(n) for n iterations of potentially large
captured stdout.

**Why `ThreadPoolExecutor`, not `multiprocessing` or `asyncio`:** every command ultimately runs
via `subprocess.run()`, which releases the GIL while the child process executes — threads are
sufficient to get genuine wall-clock overlap between concurrently-running commands (confirmed:
two 10x-looped `sleep 0.05` commands in one `PARALLEL` block complete in ~0.5s, vs. ~1.0s run
sequentially). No new dependency was introduced — `concurrent.futures` is stdlib.

**Thread-safety of what's shared across threads:**
- `CommandExecutor.run()` — safe to call concurrently; each call is an independent
  `subprocess.run()`, and the only instance state (`self.default_timeout`) is a read-only
  `int` set once at construction.
- `check_validation()`/`describe_validation()` — pure functions with no shared mutable state.
- `FrameworkLogger` — backed by Python's stdlib `logging`, whose handlers are internally
  lock-protected; `self.log.info("Running PARALLEL group ...")` is called once from the main
  thread before submitting a group (not from inside `execute_slot()`), so per-iteration logging
  contention/interleaving was avoided by design rather than needing to rely on `logging`'s
  thread-safety at all.
- `results`/`loop_infos`/`per_index_validation_data` (plain `list`s in `TestRunner.run()`) —
  each thread in a group only ever writes to its own distinct index; concurrent writes to
  different indices of the same Python list need no additional locking (CPython's GIL makes
  each individual `list.__setitem__` atomic, and there is no overlap between which indices
  different threads write to).

**First-failure tracking, at two granularities:** `execute_slot()` tracks
`first_failure_by_vid` — the first iteration each *individual* bound validation failed on
(used for that validation's own aggregate message) — separately from the command block's
single `loop_infos[i]["first_failure"]`, which is the earliest failure across *any* bound
validation for that command (used for the `.log`'s one `FIRST FAILURE:` line per command
block). If a command has two bound validations that fail on different iterations, each gets
its own accurate "first failure at iteration N" in its own `[FAIL]` line, while the command
block shows whichever of the two failed earliest overall.

**Backward compatibility guarantee:** for any `.nvtest` file that uses no `LOOP`/`PARALLEL`
(every `loop_counts[i] == 1` and every `parallel_group_id[i] is None`), `execute_slot()`
degenerates to exactly one iteration with no aggregation needed — `last_message[id(v)]` *is*
the single check's message, `loop_infos[i]` stays `None`, and both the `.log` rendering and the
`NvtestResult`/CLI-visible behavior are byte-for-byte identical to before this feature existed.
This was verified directly: the full pre-existing regression suite (all checks predating
LOOP/PARALLEL) still passes unchanged.

---

## 8. Binary Data Handling

```
nvme admin-passthru / io-passthru (or any command)
        |
        v
  subprocess.run(..., stdout=subprocess.PIPE)  -- captured as raw bytes, no text assumption
        |
        v
  CommandResult.stdout: bytes                  -- untouched, exactly what the process wrote
        |
        v
  Validator (framework/validator.py): BYTE/HEX validations index directly into
  `result.stdout` (e.g. `data[v.offset]`, `data[v.offset:v.offset+len(expected)]`)
        |
        v
  utility.hex_dump(result.stdout) -- called only by logger.py, only for DISPLAY,
        |                            strictly after validation has already happened
        v
  .log file's "BINARY OUTPUT:" section
```

**Why validation operates on raw bytes, not the formatted dump:** the hex dump is produced by
`logger._render_command_block()` calling `utility.hex_dump()`, which is invoked from
`ResultLogger.write_nvtest_log()` — a completely separate code path from
`Validator.validate()`. The validator never imports or calls anything from `logger.py`. This
means the hex-dump text format (column widths, ASCII sidebar, etc.) could change freely without
ever affecting what a test actually checks — validation always operates on `CommandResult.stdout`
directly.

**Which `RUN` blocks get hex-dumped:** decided in `TestRunner.run()`, not in the logger —
`binary_flags[v.run_index] = True` for every `Validation` whose `kind` is `"BYTE"` or `"HEX"`.
A `RUN` with no bound binary validation is always rendered as text, even if its output happens
to be binary garbage (it will simply decode with `errors="replace"`).

**No `.bin` files:** confirmed by direct inspection — `logger.py` has exactly one file-write
call per test (`open(log_path, "w", ...)`), and nothing anywhere in the codebase writes a
second file per test. Re-verified live by running the full suite and searching for `.bin`
files afterward.

---

## 9. Validation Architecture

`framework/validator.py`:

- **`check_validation(v, result, variable_manager=None) -> (bool, str)`** — the actual
  per-kind comparison logic (all six kinds: `EXIT`, `TEXT_CONTAINS`, `TEXT_NOT_CONTAINS`,
  `TEXT_NOT_EMPTY`, `BYTE`, `HEX`, as one `if`/`elif` chain) against a **single** `Validation`
  and a **single** `CommandResult`. This is the one place stream selection happens for text
  validations: `text = result.stderr_text() if v.stream == "stderr" else
  result.stdout_text()`. Public (no leading underscore) specifically so `TestRunner.run()` can
  call it directly, once per loop iteration, for a `LOOP > 1` `RUN` — see Section 3/14.
- **`validate(test_case, results, variable_manager=None)`** — the `LOOP == 1` path: a thin
  loop over `test_case.validations` calling `check_validation()` exactly once per validation,
  collecting `(bool, str)` pairs and an overall `all_passed`. This function's behavior and
  output are **identical** to before the LOOP/PARALLEL feature existed — nothing at this layer
  changed for an ordinary, non-looped `.nvtest` file.
- **`Validator.validate()`** — thin class wrapper around `validate()`, converting its raw
  tuples into `ValidationResult` (`@dataclass`: `passed: bool`, `message: str`). Still
  constructed and available on every `TestRunner` as `self.validator`, but `TestRunner.run()`
  itself now calls `check_validation()` directly rather than `self.validator.validate()`,
  because a looped `RUN`'s aggregation (per-iteration counting, first-failure tracking) needs
  finer-grained control than `validate()`'s "one CommandResult per command" shape provides.
- **`describe_validation(v, variable_manager=None) -> str`** — a human-readable label for a
  `Validation` with no `CommandResult` involved at all (e.g. `'Exit code == 0'`, `'"Model
  Number" contains "Samsung"'`). Used only by `TestRunner.run()` to build the aggregate
  `"<label> across N iterations: X passed, Y failed"` message for a `LOOP > 1` `RUN` — there's
  no single `CommandResult` to hand `check_validation()` for "the whole loop," so the label and
  the pass/fail counts are assembled separately. Deliberately duplicates the small "base
  description" fragments already inside `check_validation()` rather than threading a
  "label-only" mode through that function's signature, keeping `check_validation()` simple and
  single-purpose.
- **Failure handling:** there is no early return or short-circuiting anywhere in `validate()`
  or in `TestRunner.run()`'s per-iteration loop — every validation is checked against every
  iteration, and `all_passed`/per-validation fail counts accumulate rather than breaking on the
  first `False`.

**Adding a new validator** (see also Section 17) means adding a new branch to
`check_validation()`'s `if`/`elif` chain, a matching branch to `describe_validation()` (so
`LOOP`ed usage of the new validator reports a sensible aggregate label), plus a new kind
constant in `parser.py` and new parsing logic there to recognize its syntax.

---

## 10. Logger Architecture

There are **two distinct loggers** in this codebase — do not confuse them:

| | `ResultLogger` (`logger.py`) | `FrameworkLogger` (`framework_log.py`) |
|---|---|---|
| Purpose | Formats one **test's** COMMAND/OUTPUT/VALIDATION into its `.log` | Framework diagnostics + CLI result output |
| Backing | Hand-built string formatting, plain file write | Python stdlib `logging` module |
| Levels | None — it's a fixed report format | `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| Output | One file per test (`<stem>.log`) | Console (`stdout`) + one `run.log` per run directory |
| Alias | `Logger = ResultLogger` (backward compat) | none |

**`FrameworkLogger`** (`framework/framework_log.py`):
- Wraps `logging.getLogger(unique_name)` where `unique_name = f"{name}.{uuid.uuid4().hex}"` —
  **every instance gets a unique underlying logger name**, specifically to avoid two live
  `TestRunner`s (and thus two live `FrameworkLogger`s) from sharing/clobbering each other's
  handlers via the stdlib logging registry. This was a fixed defect (see Section 24); do not
  revert to a fixed/shared name.
- One `StreamHandler` (console, level from `--log-level`/config) and, if `run_dir` is given,
  one `FileHandler` writing `run.log` inside it (always at `DEBUG`, regardless of the console
  level).
- `close()` removes and closes all handlers — call this when a `TestRunner`/`FrameworkLogger`
  is done being used, to avoid leaking file descriptors. `TestRunner.close()` delegates to it.
- `result_line()` is just `self.info()` under a different name, used specifically for
  CLI table/summary output so its purpose is clear at call sites in `cli.py`.

**Avoiding direct `print()`:** the only `print()` call left anywhere in `framework/` is in
`cli.main()`, for a `ConfigManager` construction failure — deliberately, because that happens
*before* a `TestRunner`/`FrameworkLogger` can exist at all. Every other status/result/error
message in the framework goes through a `FrameworkLogger` method.

**Adding a new log message correctly:** call `self.log.debug(...)` / `.info(...)` /
`.warning(...)` / `.error(...)` on the `FrameworkLogger` instance you already have access to
(`TestRunner.log`, or the `test_runner.log` passed into `cli.py`'s helper functions) — never
add a bare `print()`.

**Note on `ResultLogger` and loop/parallel rendering:** `_render_command_block()`
(`framework/logger.py`) takes an optional `loop_info` dict; when `None` (every `.nvtest` file
that doesn't use `LOOP`/`PARALLEL`), rendering is byte-for-byte identical to before that
feature existed. When given, it adds `LOOP COUNT:`/`PARALLEL GROUP:`/`ITERATIONS RUN:`/`FIRST
FAILURE:` lines and relabels `EXIT CODE:`/`OUTPUT:` to `EXIT CODE (last iteration):`/`OUTPUT
(last iteration):`, since `result` in that case is the *last* iteration's `CommandResult`, not
the only one. This is a `ResultLogger` (per-test `.log`) concern, entirely separate from
`FrameworkLogger` — `TestRunner.run()` never logs per-iteration progress via `FrameworkLogger`
(see Section 7a's thread-safety notes for why).

---

## 11. Configuration Architecture

```
YAML file (optional, via --config)
      |
      v
ConfigManager.__init__()  -- yaml.safe_load() -> _merge() over a deep copy of _DEFAULTS
      |
      v
ConfigManager.log_directory / .log_level / .command_timeout / .variables_file  (properties)
      |
      v
TestRunner (reads all four properties at construction time)
      |
      v
FrameworkLogger(level=...), VariableManager(path from .variables_file),
CommandExecutor(default_timeout=...), ResultLogger(log_dir=<run_dir built from
.log_directory>)
```

- **Loading:** `ConfigManager(config_path=None)` — if `config_path` is falsy, no file I/O
  happens at all; `self._data` is just a deep copy of the module-level `_DEFAULTS` dict. If a
  path is given, it must exist (`FileNotFoundError` otherwise) and `yaml` must be importable
  (`RuntimeError` otherwise); the loaded YAML is merged over the defaults with `_merge()`,
  which does a **shallow per-section merge** — e.g. providing only `framework: {log_level:
  DEBUG}` in your YAML still keeps the default `log_directory`, because `_merge()` calls
  `base[section].update(values)` per top-level section rather than replacing the whole
  section wholesale.
- **Defaults:** the module-level `_DEFAULTS` dict in `config_manager.py` — currently
  `log_directory="logs"`, `log_level="INFO"`, `command_timeout=300`,
  `variables.file="common_variables.json"`.
- **Validation:** none beyond what `yaml.safe_load()` itself does — there is no schema check;
  a YAML file with an unexpected key/type is accepted as-is and will surface as a runtime
  error wherever that value is actually used (e.g. a non-numeric `command_timeout` would fail
  inside `subprocess.run(timeout=...)`).
- **Precedence:** built-in defaults < YAML file values (when `--config` is passed) < CLI flags
  that explicitly override a config value at a narrower scope (currently only `--log-level`,
  which overrides `log_level` for console output only, after `TestRunner` construction, via
  `test_runner.log.set_level(...)`).
- **Not auto-loaded:** `config/config.yaml` shipping in the repository does **not** mean it's
  read by default — `ConfigManager()` with no argument (the default path throughout
  `TestRunner`/`run.py`) never touches the filesystem for config. This is intentional (see
  the file's own header comment) rather than a bug — see Section 24 for the reasoning behind
  not adding auto-discovery.

**Adding a new configuration option:**
1. Add the key (with its default) to `_DEFAULTS` in `config_manager.py`.
2. Add a `@property` on `ConfigManager` exposing it.
3. Read that property wherever the value is needed (typically in `TestRunner.__init__`).
4. Add the field to `config/config.yaml`'s example content and its header-comment table in the
   User Guide.

---

## 12. Variable Architecture

```
common_variables.json (or --config-specified variables.file)
        |
        v
VariableManager.load()  -- json.load(), must be a dict, else ValueError
        |
        v
VariableManager.get(name) / .substitute(text)
        |
        v
TestRunner.run(): commands = [vm.substitute(c) for c in test_case.commands]
Validator.validate(): value = vm.substitute(v.value)  (only for TEXT_CONTAINS)
        |
        v
Resolved command string handed to CommandExecutor.run(), or resolved value compared
during validation
```

- **Loading:** `VariableManager(variables_file)` calls `self.load()` in `__init__` if a path
  is given; `load()` requires the file to exist and to parse as a JSON *object* (a JSON array
  or scalar raises `ValueError`).
- **Lookup:** `get(name)` raises `VariableError` if `name` isn't a key in the loaded dict;
  otherwise returns the raw JSON value (str, int, float, bool, list, dict — whatever JSON
  allows) unmodified.
- **Substitution:** `substitute(text)` uses a single compiled regex,
  `_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")`, matching `{{name}}` (with
  optional internal whitespace around `name`) and replacing each match with `str(self.get(name))`.
  It short-circuits (`return text` unchanged) if `"{{"` isn't present at all, avoiding a
  regex pass on strings with no placeholders.
- **Supported types:** any valid JSON value can be a variable's value; it's coerced to `str()`
  only at substitution time, so a variable never *has* to be a string in the JSON file itself.
- **Missing variables:** `get()`/`substitute()` raise `VariableError` immediately on the first
  unknown name — there is no partial substitution or default-value fallback.
- **Adding future variable functionality:** any new capability (nested substitution, escaping,
  a different placeholder syntax) belongs entirely inside `variable_manager.py` — nothing
  outside it (parser, validator, runner) needs to change as long as `substitute()`'s
  signature stays the same, since every caller already treats it as an opaque
  string-in/string-out operation.

---

## 13. Utility Module

`framework/utility.py` — the single common utility module.

| Function | Purpose | Input | Output | Example |
|---|---|---|---|---|
| `hex_dump(data, bytes_per_line=16)` | Render bytes as a `hexdump -C`-style dump | `bytes` | `str` (multi-line) | `hex_dump(b"\x01\x00")` → `"00000000  01 00 ...  .."` |
| `safe_filename(name)` | Sanitize a string into a filesystem-safe stem | `str` | `str` (alnum + `-_.` only, everything else → `_`) | `safe_filename("A: B")` → `"A__B"` |
| `format_timestamp(epoch_seconds)` | Format epoch time for `.log` headers | `float` | `str`, `"%Y-%m-%d %H:%M:%S"` | `format_timestamp(0)` → `"1970-01-01 00:00:00"` (UTC-dependent) |
| `new_run_id()` | Generate a unique run-directory name | none | `str`, `"YYYYMMDD_HHMMSS_<8-hex>"` | `new_run_id()` → `"20260822_054710_3c2569c1"` |
| `parse_int_maybe_hex(token)` | Parse decimal or `0x`-prefixed hex | `str` | `int`, or raises `ValueError` | `parse_int_maybe_hex("0x10")` → `16` |

**What belongs here:** generic, string/hex/time helpers used (or reasonably reusable) by more
than one module, with no `.nvtest`/NVMe-specific meaning.

**What does not belong here:** anything that knows about `TestCase`, `Validation`,
`CommandResult`, `.nvtest` syntax, or NVMe/fio semantics — that logic stays in
`parser.py`/`validator.py`/`runner.py`.

**Avoiding duplication:** both `parser.py` (for `EXPECT_EXIT`/`EXPECT_BYTE`/`EXPECT_HEX`
integer arguments) and — potentially — any future variable-typing logic should call
`parse_int_maybe_hex()` rather than reimplementing `int(x, 0)` handling locally.
`safe_filename`/`format_timestamp`/`hex_dump` are each called from exactly one place today
(`logger.py`), but live here rather than in `logger.py` because they were duplicated between
two methods there before being extracted — keeping them in `utility.py` prevents that
duplication from reappearing if a third caller is added later.

---

## 14. Run/Log Lifecycle

```
python3 run.py tests/
        |
        v
TestRunner.__init__(): self.run_id = utility.new_run_id()
                        self.run_dir = os.path.join(config.log_directory, self.run_id)
                        os.makedirs(self.run_dir, exist_ok=True)
        |
        v
logs/YYYYMMDD_HHMMSS_<hex>/           <-- created ONCE, here
        |
        v
Every test_runner.run(path) call for every discovered target writes into
self.result_logger (constructed with log_dir=self.run_dir) -- same directory,
same TestRunner instance, for the entire CLI invocation
        |
        v
FrameworkLogger's run.log also lives in this same directory
```

The run id is generated **exactly once**, inside `TestRunner.__init__`, and is never
regenerated for the lifetime of that `TestRunner` instance. `cli.main()` constructs exactly
one `TestRunner` per invocation and passes it to `run_targets()`/`dry_run_targets()`, which
iterate every discovered target through that same instance — this is what guarantees "one
timestamp directory per execution" rather than "one per test." The random 8-hex suffix on
`new_run_id()` exists specifically so that two `TestRunner`s constructed within the same
wall-clock second (e.g. two rapid CLI invocations, or two instances in the same process during
the self-verification harness) never collide on `run_dir`.

---

## 15. CLI Architecture

`framework/cli.py`:

- **Argument parsing:** `build_arg_parser()` returns a plain `argparse.ArgumentParser` with
  one positional (`path`) and three optional flags (`--config`, `--log-level`, `--dry-run`).
  `main(argv)` calls `parser.parse_args(argv)` — `argparse` itself handles `-h`/`--help` and
  rejects extra positional arguments (`unrecognized arguments`) with its own exit code `2`
  before any of this framework's code runs.
- **Defaults:** `--config` defaults to `None` (→ `ConfigManager()` built-in defaults);
  `--log-level` defaults to `None` (→ no override, config's `log_level` stands);
  `--dry-run` defaults to `False`.
- **Validation:** `--log-level` is constrained via `choices=[...]` at the `argparse` level —
  an invalid value is rejected by `argparse` itself, not by framework code.
- **Error handling:** `ConfigManager` construction failures (bad `--config` path, missing
  PyYAML) are caught in `main()` and reported (the one `print()` exception, see Section 10)
  with exit code `2`. `discover_targets()` raising `FileNotFoundError` (bad `path`) is caught
  in `_run_cli()` and logged via `test_runner.log.error()`, also exit code `2`.
- **Exit codes:** `0` (all discovered targets `PASS`, or `--dry-run`, or zero targets found),
  `1` (at least one target `FAIL`/`ERROR`), `2` (usage/path/config error).

**Adding a new CLI option correctly:**
1. Add it to `build_arg_parser()`.
2. Read `args.<name>` in `main()`/`_run_cli()` and act on it — if it affects `TestRunner`
   construction (like a hypothetical `--variables` override), pass it through
   `TestRunner.__init__`'s existing parameters rather than reaching into `TestRunner`
   internals from `cli.py`.
3. If it changes reported behavior (like `--dry-run` did), make sure `print_summary()` still
   produces a sensible table for the new mode.

---

## 16. Adding a New `.nvtest` Feature

Worked example, using the hypothetical `EXPECT_REGEX` mentioned in the task brief as a
**worked, not-yet-implemented** illustration (this feature does **not** exist in the current
codebase — do not document it as real elsewhere):

1. **Parser** (`parser.py`): add a new kind constant (e.g. `TEXT_REGEX = "TEXT_REGEX"`), add
   an `elif keyword == "EXPECT_REGEX":` branch mirroring the existing `EXPECT_BYTE`/
   `EXPECT_HEX` branches — validate token count, validate/compile the pattern at parse time
   (so a malformed regex is a `ParseError`, not a runtime surprise), and append a
   `Validation(kind=TEXT_REGEX, run_index=current_run_index, line_no=i, field=...)`.
2. **`TestCase` representation:** no change needed — `Validation` already has enough generic
   optional fields (`field`, `value`) to hold a regex pattern string; only add a new field if
   the semantics genuinely don't fit the existing ones.
3. **Validator** (`validator.py`): add an `elif v.kind == TEXT_REGEX:` branch in `validate()`
   that runs `re.search(pattern, result.stdout_text())` and builds a `(passed, message)` pair
   consistent with the existing branches' style (state what was expected, append "(got ...)"
   on failure).
4. **Tests:** add a small `.nvtest` file exercising both a matching and non-matching case, and
   add a corresponding check to `run.py`'s self-verification harness (see Section 19) — do not
   introduce pytest.
5. **Documentation:** update the Validation Reference table in `USER_GUIDE.md` and this
   guide's Section 9.

This same five-step shape (Parser → TestCase → Validator → Tests → Documentation) applies to
any new `.nvtest` statement, not just validators.

---

## 17. Adding a New Validator

1. **Define syntax** — decide the keyword/operator and its argument shape, consistent with
   the existing style (`EXPECT_<NOUN>` for a new top-level statement, or a new operator word
   after `EXPECT "<field>"` for a text-style check).
2. **Update the parser** (`parser.py`) — add the keyword/operator branch, argument-count
   validation, and construct the appropriate `Validation`.
3. **Implement the validator** — add the corresponding branch inside
   `validator.py: validate()`.
4. **Connect it** — nothing else needs wiring; `Validator.validate()` and
   `TestRunner.run()` already iterate `test_case.validations` generically regardless of kind.
5. **Create a minimal test** — a `.nvtest` file under `tests/` exercising both pass and fail
   cases for the new validator, safe/mocked if it doesn't need real hardware.
6. **Verify logging** — run it and confirm the `.log`'s `VALIDATION:` section shows a
   sensible `[PASS]`/`[FAIL]` line (the message format is entirely up to your new branch —
   follow the existing `f"<description> == <expected>"` / `" (got <actual>)"` on-failure
   pattern for consistency).
7. **Update documentation** — the Validation Reference table in both guides.

---

## 18. Adding a New Command/Automation Tool

No code changes are needed to support a new tool (a new NVMe subcommand, a different storage
benchmarking tool, etc.) — `RUN "<any command>"` already executes anything through
`CommandExecutor`, which has no tool-specific branching at all:

```
NVMe commands  --\
FIO commands    --+--> RUN "<command string>" --> CommandExecutor.run() --> CommandResult
Linux commands  --/
Future tool     --/
```

The only reason to touch code for "supporting" a new tool is if you need a **new kind of
validation** specific to that tool's output shape (e.g. a JSON-output validator for a tool
that emits JSON) — that follows Section 17, not this section. Do not create a
`FioExecutor`/`NvmeExecutor` subclass or similar — the single generic `CommandExecutor` is a
deliberate design invariant from the framework's earliest phase, re-confirmed still true by
inspection of every current caller.

---

## 19. Testing Strategy

The framework does not use pytest anywhere — confirmed by inspection, no `pytest` import
exists in the codebase. Instead, `run.py` contains a large number of hand-written functions
(no test framework, just plain functions with `assert` statements and `print("[PASS] ...")`
on success), invoked in sequence from `main()` when `run.py` is executed with no CLI
arguments.

**What is mocked:** commands standing in for real NVMe output use `printf` (e.g.
`tests/TC001_success.nvtest` mocks `nvme id-ctrl`'s "Model Number"/"Firmware Revision" fields)
or a small inline `python3 -c '...'` script (for binary-output tests, e.g.
`tests/TC003_byte_validation.nvtest` writes literal bytes via
`sys.stdout.buffer.write(bytes([...]))`).

**What requires real NVMe hardware:** every file under `tests/examples/` whose name contains
`HARDWARE_REQUIRED` or targets a real device path, plus any file listed in `run.py`'s
`_HARDWARE_OR_DESTRUCTIVE_EXAMPLES` set — these are parsed (to confirm valid `.nvtest` syntax)
but never executed by the self-verification harness.

**Safe commands actually executed by the harness:** real `lsblk` (read-only, always safe) and
real `fio` targeting a file under `/tmp` (never a raw device) — these genuinely run as part of
`python3 run.py` with no arguments, proving real command integration, not just mocks.

**Binary-output testing:** `tests/TC003_byte_validation.nvtest` /
`tests/TC004_hex_validation.nvtest` exercise `EXPECT_BYTE`/`EXPECT_HEX` against mock binary
stdout; `examples_parse_cleanly()` (in `run.py`) confirms every file under `tests/examples/`
(including the real-hardware passthru examples) at least **parses** as valid `.nvtest` syntax.

**Parser testing:** covered both via the shipped `.nvtest` files (`TC005_invalid_syntax.nvtest`
exercises a parse failure) and via direct `parse_text()` calls with hand-constructed invalid
strings during earlier development (missing `END`, `EXPECT` before `RUN`, unknown keyword,
bad operator) — these ad hoc checks are not currently part of the permanent `run.py` suite,
but the shipped invalid-syntax `.nvtest` file is.

**Validator testing:** covered by `tests/TC001_success.nvtest` /
`TC002_failed_validation.nvtest` (text validations, including the "all validations still run
after one fails" property, explicitly asserted) and the byte/hex tests above.

**Integration testing:** `run.py`'s `cli_directory()` function runs an entire directory
(`tests/`) through the real CLI path in one call, asserting PASS/FAIL/ERROR status for
specific known files and confirming every test in that one invocation shares the same run
directory — this is the closest thing to an end-to-end integration test in the codebase.

**LOOP/PARALLEL/EXPECT_STDERR testing:** `loop_execution_aggregates_correctly()` and
`loop_execution_reports_first_failure()` (`run.py`) cover a plain `LOOP` and an intermittently
failing `LOOP` (the latter uses an external counter file rather than a `{{variable}}`, since
variables substitute once per `RUN`, not once per iteration, so a variable can't itself make a
command fail on a specific iteration). `parallel_block_runs_concurrently()` proves actual
wall-clock overlap using `sleep`-based commands with a generous timing margin (parallel must be
under 75% of the equivalent sequential duration — loose enough to avoid CI flakiness while
still catching a regression to fully-sequential execution).
`parallel_example_validates_each_loop_independently()` and
`parallel_parser_rejects_invalid_blocks()` cover the shipped `PARALLEL` example and the
parser's structural rules (min-2-`RUN`, no nesting, unclosed block). `expect_stderr_
validates_and_excludes_stdout()` covers `EXPECT_STDERR` matching plus confirming a plain
`EXPECT` on the same `RUN` still only sees stdout.

**Manually verifying a change:**
```bash
python3 -m pyflakes framework/*.py run.py   # lint (no unused imports/dead code)
python3 run.py                              # full internal self-verification suite
python3 run.py tests/TC001_success.nvtest   # spot-check a real CLI invocation
python3 run.py tests/TC011_parallel_stress.nvtest  # spot-check LOOP/PARALLEL
find . -name "*.bin"                        # must always be empty
```

---

## 20. Debugging Guide

| Problem area | Where to look | What to check |
|---|---|---|
| Parser errors | `framework/parser.py`, the `ParseError` message itself | The message always includes the exact line number and offending line text — start there before reading code |
| Command failures | The relevant `.log` file's `EXIT CODE:`/`OUTPUT:`/`STDERR:` blocks | `CommandExecutor.run()` never raises for a failed command — check the captured exit code/stderr in the log, not a stack trace |
| Validation failures | The `.log` file's `VALIDATION:` section | Each `[FAIL]` line includes a `(got ...)`/`(field not found in output)` suffix explaining exactly why |
| Variable problems | `framework/variable_manager.py`, the `VariableError` message | Names the exact missing `{{name}}` and which file was searched |
| YAML problems | `framework/config_manager.py` | `ConfigManager.__init__` raises `FileNotFoundError`/`RuntimeError` immediately at construction — check `cli.main()`'s try/except around it |
| Binary validation | `framework/validator.py`'s `BYTE`/`HEX` branches, and the `.log`'s `BINARY OUTPUT:` hex dump | Validation runs against raw bytes (`result.stdout`), the hex dump is only a rendering — if they seem to disagree, re-check the offset/byte-order in your `.nvtest`, not the dump |
| Logger issues | `framework/framework_log.py` | Each `FrameworkLogger` has a unique internal logger name (`uuid`-suffixed) — if you're debugging by inspecting `logging.getLogger()` state directly, remember the name isn't `"nvme_test"` verbatim |
| CLI issues | `framework/cli.py: main()`/`_run_cli()` | Check exit code first (`0`/`1`/`2` mean different failure classes), then the `ERROR` rows' `->` detail lines in the printed table |

---

## 21. Error Handling

| Error category | Raised as | Surfaces to the user as | Effect on result |
|---|---|---|---|
| Configuration Error | `FileNotFoundError` / `RuntimeError` from `ConfigManager.__init__` | `Error: <message>` via `print()`, before any test runs | Process exits `2`; no tests run at all |
| Parser Error | `parser.ParseError` | `ERROR` row in the CLI table, with the line-numbered message as detail | That one test is `ERROR`; no `.log` written for it; other targets in the batch still run |
| Variable Error | `variable_manager.VariableError` | `ERROR` row, "Unknown variable {{name}} (not found in ...)" | Same as Parser Error |
| Unsupported file type | `runner.UnsupportedFileTypeError` | `ERROR` row, "Unsupported test file type '...' ..." | Same as Parser Error |
| Any other unexpected exception during `test_runner.run()` | caught generically in `cli.run_targets()`'s final `except Exception` | `ERROR` row, "Unexpected error: <str(exc)>"; also logged via `FrameworkLogger.error()` | Same as Parser Error — critically, **does not abort the rest of the batch** |
| Command Error (nonzero exit, timeout, missing binary) | not an exception at all — `CommandExecutor.run()` always returns a `CommandResult` | Reflected only through whatever `EXPECT_EXIT`/`EXPECT`/etc. checks are bound to that command | That test is `FAIL` only if a bound validation actually fails because of it |
| Validation Failure | not an exception — a `ValidationResult(passed=False, ...)` | `[FAIL] ...` line in the `.log`, test status `FAIL` | Test is `FAIL`; every other validation in the file still runs |
| Framework/CLI usage error | `argparse` itself (bad flag, extra positional, missing `path`) | `argparse`'s own usage message to stderr | Process exits `2`, before any framework code runs |

**Key distinction:** everything in the top half of this table (Configuration/Parser/Variable/
Unsupported-file/Unexpected) means the test **never executed at all** and produces **no
`.log` file** — reported as `ERROR`. Everything in the bottom half (Command/Validation)
means the test **did execute** and its outcome is recorded normally as `PASS`/`FAIL` with a
full `.log`.

---

## 22. Extension Guidelines

- `.nvtest` remains the only test-case format — never add a code path that executes `.py`,
  `.yaml`, `.json`, or `.xml` as a test definition.
- Python is for framework implementation only — test authors should never need to write
  Python.
- Use classes only where they hold real state or a real dependency graph (see the "why each
  class exists" reasoning in Section 4) — do not add a class for a single free function with
  no state.
- Route all framework status/diagnostic/result output through `FrameworkLogger` — never add a
  bare `print()` outside the one documented bootstrap exception in `cli.main()`.
- Keep all configurable values in `ConfigManager`/`config.yaml` — never hardcode a new
  timeout, path, or level directly in framework code.
- Keep shared test values in `common_variables.json`/`VariableManager` — never hardcode a
  device path or expected value directly in framework code.
- Put genuinely reusable, non-test-specific helpers in `utility.py` — nowhere else, and don't
  create a second utility module.
- Preserve raw binary output through to validation — never validate against the formatted hex
  dump text.
- Avoid new dependencies beyond PyYAML unless a documented requirement genuinely needs one.
- Avoid duplicating logic that already exists in `utility.py`/`parser.py`'s hex-or-decimal
  parsing, timestamp formatting, or filename sanitization.
- Keep the framework lightweight — no database, no web UI/API, no plugin system, no pytest.

---

## 23. Developer Best Practices

- **Naming:** classes are `PascalCase` nouns describing their one responsibility
  (`CommandExecutor`, `ValidationResult`); functions/methods are `snake_case` verbs
  (`parse_file`, `write_nvtest_log`); module-level "private" helpers are prefixed `_`
  (`_render_command_block`, `_find_field_line`).
- **Error handling:** raise a specific exception type at the point of failure (`ParseError`,
  `VariableError`, `UnsupportedFileTypeError`) rather than a bare `Exception`/`ValueError`, so
  callers can catch precisely — see Section 21's table for the existing taxonomy.
- **Type hints:** used on public function/method signatures throughout (e.g.
  `def run(self, path: str) -> NvtestResult:`), but not exhaustively on every local variable —
  follow the existing density rather than adding hints everywhere.
- **Python 3.8 compatibility:** no walrus operator, no `X | Y` union-type syntax, no built-in
  generic subscripting (`list[str]` used only in comments/docstrings, never as a runtime
  annotation — `typing.List`/`typing.Optional` are used instead). Confirmed via direct
  inspection across every module.
- **Backward compatibility:** when renaming a public class, keep an alias (see `Executor =
  CommandExecutor`, `Logger = ResultLogger`) rather than breaking existing imports, unless the
  rename is accompanied by a deliberate, documented breaking-change decision.
- **Small methods:** most methods in this codebase are under ~30 lines; `TestRunner.run()` and
  `parser.parse_text()` are the largest (each doing one clearly-named multi-step job) — if a
  new addition grows a method much larger than these, consider extracting a helper.
- **Clear interfaces:** prefer passing already-constructed dependencies into `__init__`
  (`Validator(variable_manager=...)`, `ResultLogger(log_dir=...)`) over having a class reach
  out to global state to find its own dependencies.
- **Avoiding global state:** there is no module-level mutable state anywhere in `framework/`
  except the intentionally-shared stdlib `logging` registry (which `FrameworkLogger`
  deliberately works around with per-instance unique names, per Section 10/24) — keep it that
  way.
- **Avoiding unnecessary abstractions:** `CliTarget` is a two-field plain class rather than a
  dataclass or a namedtuple purely because that's what already existed when it was introduced
  — either would be fine; don't feel obligated to convert it without a real reason.

---

## 24. Known Limitations / Technical Debt

Confirmed by direct inspection of the current implementation — nothing here is invented:

- **`validate()` free function + `Validator` class near-duplication** (`validator.py`): the
  module-level `validate()` function has exactly one caller today
  (`Validator.validate()`). This is acknowledged technical debt from an earlier hardening
  pass, deliberately left as-is rather than inlined, since it carries no functional risk.
- **`config/config.yaml` is not auto-loaded by default** — a deliberate decision (documented
  in the file's own header comment and in `USER_GUIDE.md` Section 8), not an oversight: adding
  auto-discovery was considered and rejected because it would introduce new, implicit,
  environment-dependent path-resolution behavior (relative to script location vs. current
  working directory) with no documented requirement demanding it.
- **No numeric-comparison validator** — `common_variables.json`'s shipped `min_iops` field has
  no consumer anywhere in the DSL; adding one (e.g. `EXPECT_MIN_IOPS`) is a scope decision, not
  yet made.
- **`{{variable}}` substitution does not cover `EXPECT_EXIT`/`EXPECT_BYTE`/`EXPECT_HEX`** —
  only `RUN` commands and `EXPECT ... CONTAINS` values are substituted; `EXPECT_EXIT`'s
  argument is converted to an integer at parse time, before any substitution step could apply.
- **Variable values are not shell-escaped** before being substituted into `RUN` strings
  (`shell=True`) — documented as an explicit trust-boundary assumption in
  `variable_manager.py`'s module docstring rather than fixed, since automatic escaping
  (`shlex.quote()`) would break the common, legitimate case of a variable expanding to an
  unquoted path.
- **`CommandExecutor`'s `except FileNotFoundError` branch is effectively unreachable** under
  the `shell=True` mode every current caller uses (a missing binary surfaces as a nonzero
  shell exit code, not a Python-level `FileNotFoundError`) — left in place as defensive code
  for a hypothetical future `shell=False` caller, not removed, since it's harmless.
- **No parallel/concurrent test execution across files** — every *target file* in a directory
  batch still runs strictly sequentially through one `TestRunner` (`cli.run_targets()`); only
  concurrency *within* a single `.nvtest` file's `PARALLEL` block exists. Nothing in the
  current architecture prevents adding cross-file concurrency later, but it does not exist
  today.
- **`PARALLEL` uses one OS thread per `RUN` in the block, not a bounded worker pool** —
  `ThreadPoolExecutor(max_workers=len(group_indices))` sizes the pool exactly to the group, so
  a `PARALLEL` block with many `RUN` statements spawns that many threads at once. Fine for the
  framework's realistic use case (a handful of concurrent command streams), but not designed
  for dozens+ of concurrent members.
- **`describe_validation()` duplicates `check_validation()`'s base-description strings** — a
  small, deliberate duplication (see Section 9) rather than adding a "label-only" mode to
  `check_validation()`'s signature; if the two ever drift out of sync (e.g. someone updates one
  branch's wording but not the other's), a `LOOP`ed validation's aggregate message and a
  `LOOP 1` validation's message for the same kind could read slightly differently. Low risk,
  since both are short, colocated in the same file, and covered by the LOOP/PARALLEL
  regression tests.
- **`run.py`'s no-argument mode doubles as both "help text" and "run the whole internal test
  suite"** — there is no separate, lightweight "show usage" path for zero arguments; this is
  long-standing, intentional behavior (documented in `run.py`'s own module docstring) rather
  than an oversight, but is worth knowing before scripting around `run.py` with no arguments.
