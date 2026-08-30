# DEVELOPER_GUIDE.md — NVMe/FIO Test Automation Framework

For engineers maintaining/extending the framework.

## 1. Architecture & Execution Flow

```
run.py (argv) -> framework.cli.main()
  -> ConfigManager(args.config)
  -> TestRunner(config)             # one per invocation: run dir, FrameworkLogger,
  |                                 # VariableManager, TestParser, CommandExecutor,
  |                                 # Validator, ResultLogger
  -> cli.discover_targets(path)     # file, or *.nvtest glob of a dir (non-recursive)
  -> cli.run_targets(targets, test_runner)
       -> test_runner.run(path) per target:
            TestParser.parse -> TestCase
            -> per command index: substitute {{vars}} JIT -> CommandExecutor.run() (looped,
               and/or concurrently via ThreadPoolExecutor for a PARALLEL group)
            -> check_validation() per iteration -> aggregate pass/fail
            -> ResultLogger.write_nvtest_log() -> logs/{run_id}/<file>.log
  -> cli.print_summary() -> FrameworkLogger.result_line()
  -> test_runner.close()
```
`run.py` with **no argv** instead runs its own self-verification functions (defined directly
in `run.py`), not `cli.main()`.

## 2. Modules & Classes

| Module | Owns | Key names |
|---|---|---|
| `parser.py` | `.nvtest` grammar only | `ParseError`, `Validation`, `TestCase`, `TestParser`, `parse_text`/`parse_file` |
| `executor.py` | one `subprocess.run()` call | `CommandResult`, `CommandExecutor` (alias `Executor`) |
| `validator.py` | per-validation comparison | `check_validation`, `describe_validation`, `validate`, `Validator`, `ValidationResult` |
| `runner.py` | orchestration; only module importing all others | `TestRunner`, `NvtestResult`, `check_extension`, `UnsupportedFileTypeError`, `run_nvtest_file` |
| `logger.py` | `.log` file formatting | `ResultLogger` (alias `Logger`) |
| `framework_log.py` | console + `run.log` diagnostics | `FrameworkLogger` |
| `config_manager.py` | YAML load + defaults | `ConfigManager` |
| `variable_manager.py` | JSON load + `{{name}}` substitution + runtime capture | `VariableManager`, `VariableError` |
| `utility.py` | generic helpers, no test/NVMe semantics | `hex_dump`, `safe_filename`, `format_timestamp`, `new_run_id`, `parse_int_maybe_hex` |
| `cli.py` | argparse + discovery + reporting | `CliTarget`, `build_arg_parser`, `discover_targets`, `run_targets`, `dry_run_targets`, `print_summary`, `main` |

`TestCase`: `name`, `commands: List[str]`, `validations: List[Validation]`, `source_path`,
`loop_counts: List[int]` (1 unless `LOOP <n>`), `parallel_group_id: List[Optional[int]]`
(shared int per `PARALLEL` block, else `None`), `capture_names: List[Optional[str]]`.
`Validation`: `kind`, `run_index`, `line_no`, `stream` (`stdout`/`stderr`), plus kind-specific
fields (`expected_exit` | `field`/`value` | `offset`+`expected_byte` | `offset`+`hex_string` |
`pattern`). All flat lists indexed by `run_index`/command index — no nested step tree.

## 3. `.nvtest` Parser

Single pass, line-by-line, 1-based line numbers; `shlex.split(line, posix=True)` per line;
dispatch on `tokens[0]`. Keywords: `TEST`, `RUN`, `PARALLEL`, `END_PARALLEL`, `EXPECT_EXIT`,
`EXPECT`, `EXPECT_STDERR`, `EXPECT_BYTE`, `EXPECT_HEX`, `EXPECT_REGEX`,
`EXPECT_REGEX_STDERR`, `END`.

- `RUN "<cmd>" [LOOP <n>] [CAPTURE <name>]`: modifiers parsed as `(keyword, value)` pairs after
  token 2; either order, each at most once. `LOOP` value via `parse_int_maybe_hex` (`>=1`);
  `CAPTURE` value must match `[A-Za-z0-9_]+`.
- `PARALLEL`: rejects nesting (`in_parallel` flag); assigns an incrementing group id to every
  `RUN` until `END_PARALLEL`, which requires `>= 2` RUNs seen. `END` raises if a `PARALLEL`
  block is still open.
- `current_run_index` = index of the most recent `RUN`; every `EXPECT*` requires it `!= -1` —
  this is the entire "bind to nearest preceding RUN" mechanism, including inside `PARALLEL`.
- `EXPECT`/`EXPECT_STDERR` share `_parse_text_expect(...)`, handling text operators
  (`CONTAINS`/`NOT_CONTAINS`/`NOT_EMPTY`) and numeric operators (`EQ`/`NEQ`/`GT`/`GE`/`LT`/`LE`)
  in one function, differing only in `stream`. A numeric value is validated as `float()` at
  parse time unless it contains `"{{"` (deferred to `check_validation()` at runtime).
- `EXPECT_REGEX[_STDERR]`: pattern is `re.compile()`d immediately; `re.error` → `ParseError`.
- Missing `TEST`/`RUN`/`END`/unclosed `PARALLEL` are checked after the line loop.
- `runner.check_extension()` rejects non-`.nvtest` paths **before** parsing is ever attempted.

## 4. Command Execution & Binary Handling

`CommandExecutor.run(cmd, timeout=None)` → `subprocess.run(cmd, shell=True, stdout=PIPE,
stderr=PIPE, timeout=timeout or self.default_timeout)`. Always returns a `CommandResult`
(`command`, `exit_code`, `stdout: bytes`, `stderr: bytes`, `start_time`, `end_time`) — never
raises for a failed/missing/timed-out command (`TimeoutExpired`→exit `-1` + `[TIMEOUT
after Ns]`; `FileNotFoundError`→127; other `OSError`→1). No NVMe/FIO-specific branching
anywhere — a command is just a string.

Binary flow: raw `stdout`/`stderr` bytes are never decoded until `stdout_text()`/
`stderr_text()` is called. `check_validation()`'s `BYTE`/`HEX` branches index `result.stdout`
directly. `logger._render_command_block()` calls `utility.hex_dump()` **only for display**,
strictly after validation. Whether a command's block is hex-dumped is decided in
`TestRunner.run()`: `binary_flags[v.run_index] = True` for any bound `BYTE`/`HEX` validation.
No `.bin` file is ever written (one `open(log_path, "w")` call per test, full stop).

## 5. Validator Architecture

- `check_validation(v, result, variable_manager=None) -> (bool, str)`: the only per-kind
  comparison logic, one `if`/`elif` chain over `EXIT`/`TEXT_CONTAINS`/`TEXT_NOT_CONTAINS`/
  `TEXT_NOT_EMPTY`/`BYTE`/`HEX`/`REGEX`/`NUM_EQ`/`NUM_NEQ`/`NUM_GT`/`NUM_GE`/`NUM_LT`/`NUM_LE`.
  Substitutes `v.value`/`v.pattern` first. `REGEX` wraps `re.search()` in `try/except re.error`
  (a substituted pattern can be invalid at runtime even if valid at parse time). `NUM_*`
  substitutes `v.value`, `float()`s it (fails cleanly if non-numeric), finds the field's line
  via `_find_field_line()`, extracts the first number after it via
  `_extract_first_number_after_field()` (regex `[-+]?\d+(?:\.\d+)?`), compares via
  `_compare_numbers()`; `_NUMERIC_OP_SYMBOL` renders `==`/`!=`/`>`/`>=`/`<`/`<=`.
- `validate(test_case, results, variable_manager=None)`: thin loop over
  `check_validation()`, one call per validation — the `LOOP == 1` path, unchanged output shape.
- `Validator.validate()`: wraps `validate()` into `ValidationResult` objects. Still
  constructed on every `TestRunner` (`self.validator`) but **not called** by `TestRunner.run()`
  — looped aggregation needs per-iteration control `validate()`'s shape doesn't provide.
- `describe_validation(v, variable_manager=None) -> str`: label with no `CommandResult`
  involved (`"Exit code == 0"`, etc.), used only to build a `LOOP>1` aggregate message.
  Deliberately duplicates `check_validation()`'s base-description strings rather than adding a
  label-only mode to that function. Has a matching `NUMERIC_KINDS` branch.
- No short-circuiting anywhere: every validation checked, every iteration, always.

**Add a validator:** new kind constant in `parser.py` + parsing branch → branch in
`check_validation()` → matching branch in `describe_validation()` → a test file exercising
pass+fail → update the tables above.

## 6. Logger Architecture

Two distinct loggers:
| | `ResultLogger` | `FrameworkLogger` |
|---|---|---|
| Purpose | one test's `.log` | console + `run.log` diagnostics |
| Backing | hand-built strings, one file write | stdlib `logging` |
| Levels | none (fixed report) | DEBUG/INFO/WARNING/ERROR |

`FrameworkLogger` gives each instance a **unique** `logging.getLogger()` name
(`f"{name}.{uuid4().hex}"`) — a fixed shared name previously let a second `TestRunner` hijack
an earlier one's handlers. `close()` removes/closes handlers (fd leak); `TestRunner.close()`
delegates to it; `cli.main()` calls it in a `finally`.

`_render_command_block(..., loop_info=None)`: `None` renders byte-identical to the original
format; otherwise adds `LOOP COUNT:`/`PARALLEL GROUP:`/`ITERATIONS RUN:`/`FIRST FAILURE:`.
`write_nvtest_log(..., filename_stem=...)` keys the `.log` filename off the **source path's
basename**, not the free-text `TEST` name (two files with the same `TEST` label must not
overwrite each other's log).

## 7. Configuration Architecture

`ConfigManager(config_path=None)`: no path → deep-copies module-level `_DEFAULTS`, no file
I/O. A path → must exist (`FileNotFoundError`) and `yaml` must import (`RuntimeError`);
`yaml.safe_load()` result is merged over defaults per-section (`base[section].update(values)`
— missing keys keep their default). No schema validation. `config/config.yaml` is never
auto-discovered — deliberate, to avoid implicit CWD/script-relative path behavior.

## 8. Variable Manager

`VariableManager(variables_file=None)`: `None`/missing path → empty `_values`, no error —
always constructed by `TestRunner` so `CAPTURE` has somewhere to write even with no JSON file.
`get()`/`set()`/`load()` each hold `self._lock` (`threading.Lock`) — needed because `PARALLEL`
makes concurrent `substitute()` (read) and `CAPTURE` (`set()`, write) genuinely possible.
`substitute(text)`: regex `\{\{\s*([A-Za-z0-9_]+)\s*\}\}`, short-circuits if `"{{"` absent.

## 9. Utility Module

`hex_dump`, `safe_filename`, `format_timestamp`, `new_run_id` (`YYYYMMDD_HHMMSS_<8-hex-uuid>` —
a PID suffix was tried first and rejected: it doesn't disambiguate multiple `TestRunner`s in
one process/second), `parse_int_maybe_hex`. Only generic, reusable, non-test-specific helpers
belong here.

## 10. Run/Log Lifecycle

`TestRunner.__init__`: `run_id = new_run_id()`; `run_dir = log_directory/run_id`,
`os.makedirs(exist_ok=True)`; `FrameworkLogger`/`ResultLogger` both built with this one
`run_dir`. Generated **once** per `TestRunner`; `cli.main()` builds exactly one `TestRunner`
per invocation and reuses it for every discovered target — this is the entire mechanism behind
"one execution = one `logs/{run_id}/`".

## 11. CLI Architecture

`argparse` (`build_arg_parser`): positional `path`; `--config`, `--log-level`
(`choices=[DEBUG,INFO,WARNING,ERROR]`), `--dry-run` (`store_true`). `main()` builds
`ConfigManager` (bare `print()` on failure — the one unavoidable pre-logger case) → one
`TestRunner` → `discover_targets` → `run_targets`/`dry_run_targets` → `print_summary` →
`close()`. `run_targets()` catches `UnsupportedFileTypeError`/`ParseError`/`VariableError`
plus a final `except Exception` (not `BaseException`) so one bad target can't drop the rest of
a directory batch. Exit `0`/`1`/`2`.

**Add a CLI option:** extend `build_arg_parser()`; read `args.<name>` in `main()`/`_run_cli()`;
pass through `TestRunner.__init__` rather than reaching into its internals from `cli.py`.

## 12. Adding a New Automation Tool

No code change needed — `RUN "<any command>"` already runs through the one generic
`CommandExecutor`. Only touch code if the new tool's output needs a **new validator** (§5). Do
not add a per-tool executor subclass.

## 13. Testing & Debugging

No pytest anywhere. `run.py` holds ~55 hand-written `assert`-based check functions, run via
`python3 run.py` (no args). Mocks: `printf`/`python3 -c` for `nvme`-shaped output; real
`lsblk`/file-based `fio` actually execute (safe); hardware/destructive `tests/examples/` files
are only **parsed**, never run (`_HARDWARE_OR_DESTRUCTIVE_EXAMPLES` in `run.py`).

```bash
python3 -m pyflakes framework/*.py run.py   # lint
python3 run.py                              # full self-verification
find . -name "*.bin"                        # must always be empty
```
Debugging: parser errors carry line+text; command failures show in `.log`'s `EXIT
CODE:`/`STDERR:`; validation failures append `(got ...)`/`(field not found...)`.

## 14. Extension Guidelines

`.nvtest` stays the only test format. Python stays framework-only. Config stays in YAML via
`ConfigManager`; shared values stay in JSON via `VariableManager`. Route all status output
through `FrameworkLogger`, never `print()`. Keep `utility.py` the single reusable-helper
module. Validate against raw bytes, never the rendered hex dump. No new dependency beyond
PyYAML without a real need.

## 15. Actual Limitations

- `EXPECT_EXIT`/`EXPECT_BYTE`/`EXPECT_HEX` don't support `{{variable}}` (numeric `EQ`.. does).
- Numeric extraction takes the **first** number after `<field>` on its line — no Nth-number/column targeting; no `EQUALS`; `EXPECT_REGEX` uses `search()`, not `fullmatch()`.
- Variable values are not shell-escaped before substitution into `RUN` (`shell=True`) —
  `common_variables.json` must be trusted input, same as `.nvtest` files.
- Substitution is JIT per-`RUN`, not all upfront — a bad `{{name}}` in a later `RUN` is only
  caught once execution reaches it; earlier `RUN`s' side effects will already have happened.
- Two `PARALLEL`-block `RUN`s `CAPTURE`ing the same name race (last write wins); not
  statically prevented. `PARALLEL` spawns one OS thread per member with no pool cap.
- Concurrency is only intra-file (`PARALLEL`); directory-batch targets still run sequentially.
- `describe_validation()` duplicates `check_validation()`'s description strings (accepted, low-risk debt).
- `run.py` with no args runs the full internal test suite, not a help/usage message.
