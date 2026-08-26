# NVMe/FIO Test Automation Framework — User Guide

This guide is for a **test engineer/test author** using the framework to write and run
`.nvtest` test cases. It documents the framework exactly as implemented — every syntax
element, CLI flag, config field, and log format shown here was verified directly against the
source code and by actually running the commands shown.

---

## 1. Introduction

The framework runs shell commands (`nvme-cli`, `fio`, and plain Linux commands like `lsblk`)
against a device, checks their output/exit code/raw bytes against expectations written in a
small text format called `.nvtest`, and writes one human-readable `.log` file per test.

There is exactly one execution engine for every command — `nvme id-ctrl`, `fio ...`, and
`lsblk` are all just "a command" to the framework. Nothing is NVMe- or fio-specific at the
execution layer.

```
Manual Test Case
      |
      v
.nvtest file (TEST / RUN / EXPECT... / END)
      |
      v
Parser            (framework/parser.py)
      |
      v
Command Execution (framework/executor.py -- subprocess, shell=True)
      |
      v
Validation        (framework/validator.py -- text + binary checks)
      |
      v
.log file          (framework/logger.py -- one per test, hex dump for binary)
      |
      v
PASS / FAIL        (a single failed validation makes the whole test FAIL)
```

**Supported tools:** `nvme-cli`, `fio`, and any Linux command (`lsblk`, `lspci`, etc.) — the
framework runs whatever string you put after `RUN`, via the shell.

**Environment:** built and verified against Ubuntu 20.04 / Python 3.8, though this
documentation was also verified running under a newer Python 3 (3.12) in the environment used
to write it — the code contains no version-specific syntax beyond 3.8.

**Python's role:** Python only implements the framework itself. You never write `.py` test
files — `.nvtest` is the only test-case format the framework will execute.

---

## 2. Prerequisites

- **Python 3.8+** (standard library only, plus one third-party dependency: PyYAML)
- **PyYAML** — required only if you pass `--config` to load a YAML file; without `--config`
  the framework runs entirely on built-in defaults with no PyYAML import failure.
- **`nvme-cli`** — required for any `.nvtest` file that runs `nvme ...` commands.
- **`fio`** — required for any `.nvtest` file that runs `fio ...` commands.
- **A real NVMe device** — only required for `.nvtest` files that target one (e.g.
  `/dev/nvme0`). Tests that use safe mock commands or Linux-only commands (`lsblk`) need no
  NVMe hardware at all.
- **Permissions** — commands like `nvme id-ctrl`, `nvme smart-log`, `nvme admin-passthru`,
  and `nvme io-passthru` typically require root or block-device read/write permissions on the
  target device. The framework does not elevate privileges itself; run it with whatever
  privileges the underlying `nvme`/`fio` commands need.

Verify your environment:

```bash
python3 --version
nvme version
fio --version
pip show pyyaml     # only needed if you plan to use --config
```

---

## 3. Installation / Setup

The framework is a plain directory of Python files — there is no packaging/installer step.

1. **Obtain the framework** — unzip/copy the project directory (containing `run.py`,
   `framework/`, `tests/`, `config/`, `common_variables.json`, `requirements.txt`) to your
   machine.
2. **Install the one dependency** (only needed if you plan to use `--config`):
   ```bash
   pip install -r requirements.txt
   ```
3. **Directory preparation** — none required. `logs/` is created automatically on first run.
4. **Configuration setup** — optional. `config/config.yaml` ships as a template; see
   Section 8 for whether/how to activate it.
5. **Initial verification** — run the framework's own built-in self-check with no arguments:
   ```bash
   python3 run.py
   ```
   This does **not** print a usage message — with zero arguments, `run.py` runs its internal
   self-verification suite (checks against the shipped `tests/` files), ending with `All
   hardening regression checks passed.` and exit code 0. This is a real, intentional behavior
   of the shipped `run.py`, not a bug — see the warning box in Section 14 before relying on it
   in scripts.
6. **Run your first real test:**
   ```bash
   python3 run.py tests/TC001_success.nvtest
   ```
   Expected output:
   ```
   TC001_success    PASS

   Total: 1
   Passed: 1
   Failed: 0
   ```

---

## 4. Project Structure

```
nvme_test/
├── run.py                      # CLI entry point (and internal self-verification, see Sec 14)
├── requirements.txt             # PyYAML>=6.0 (only needed for --config)
├── common_variables.json        # shared {{variable}} values -- edit this
├── config/
│   └── config.yaml               # optional config template -- edit this, see Sec 8
├── framework/                    # framework internals -- you normally do not edit these
│   ├── parser.py, validator.py, executor.py, runner.py, logger.py,
│   │   framework_log.py, config_manager.py, variable_manager.py,
│   │   utility.py, cli.py
├── tests/                        # your .nvtest files go here (any subdirectory layout)
│   └── examples/                 # shipped realistic NVMe/FIO/passthru examples
└── logs/                         # created automatically; one logs/{timestamp}/ per run
```

**What you will normally edit:** `.nvtest` files under `tests/`, `common_variables.json`, and
optionally `config/config.yaml`.

**What you normally should not edit:** anything under `framework/` -- that's covered in the
Developer Guide.

---

## 5. `.nvtest` Test Format

> **`.nvtest` is the only supported test-case format.** Any other extension is rejected
> outright -- a directory scan silently skips everything that isn't `*.nvtest`, and passing a
> non-`.nvtest` file path directly is refused with an explicit error before anything runs.

The format is a small, line-oriented DSL. One statement per line. Blank lines are ignored.
Lines whose first non-whitespace character is `#` are comments and are ignored too (useful for
documenting prerequisites inside a test file).

### Statement reference

| Statement | Syntax | Required/Optional | Notes |
|---|---|---|---|
| `TEST` | `TEST "<name>"` | Required, exactly once, must be the first statement | Free-text name shown in the `.log`'s `TEST:` header |
| `RUN` | `RUN "<command>" [LOOP <n>] [CAPTURE <name>]` | Required, one or more | The command string is passed to the shell (`subprocess.run(..., shell=True)`). `LOOP <n>` (optional) runs it `n` times in sequence instead of once -- see Section 10a. `CAPTURE <name>` (optional) stores its stdout as a runtime variable -- see Section 10c. Both modifiers can be combined, in either order |
| `PARALLEL` / `END_PARALLEL` | `PARALLEL` ... `END_PARALLEL` | Optional block, wraps 2+ `RUN` statements | Every `RUN` inside runs concurrently, one thread each -- see Section 10a |
| `EXPECT_EXIT` | `EXPECT_EXIT <code>` | Optional, any number | `<code>` is a decimal or `0x`-prefixed hex integer (e.g. `0` or `0x00`) |
| `EXPECT` | `EXPECT "<field>" CONTAINS "<value>"` | Optional, any number | Checks **stdout**. See the Validation Reference for exact matching semantics |
| `EXPECT` | `EXPECT "<field>" NOT_CONTAINS` | Optional, any number | Checks **stdout**. No value argument |
| `EXPECT` | `EXPECT "<field>" NOT_EMPTY` | Optional, any number | Checks **stdout**. No value argument |
| `EXPECT_STDERR` | `EXPECT_STDERR "<field>" CONTAINS "<value>"` / `NOT_CONTAINS` / `NOT_EMPTY` | Optional, any number | Identical grammar to `EXPECT`, but checks **stderr** instead of stdout -- see Section 10b |
| `EXPECT_BYTE` | `EXPECT_BYTE <offset> <byte>` | Optional, any number | Both `<offset>` and `<byte>` are decimal or `0x`-prefixed hex |
| `EXPECT_HEX` | `EXPECT_HEX <offset> "<hexstring>"` | Optional, any number | `<offset>` decimal/hex; `<hexstring>` is plain hex digits, no `0x` prefix, even length |
| `EXPECT_REGEX` | `EXPECT_REGEX "<pattern>"` | Optional, any number | Checks **stdout** matches a Python `re` pattern (compiled/validated at parse time) -- see Section 10d |
| `EXPECT_REGEX_STDERR` | `EXPECT_REGEX_STDERR "<pattern>"` | Optional, any number | Identical to `EXPECT_REGEX`, but checks **stderr** |
| `END` | `END` | Required, exactly once, must be the last statement | Nothing may follow it |

**Command association (very important):** every `EXPECT*`/`EXPECT_STDERR*`/`EXPECT_REGEX*`
statement applies to the **nearest preceding `RUN`**. A file can contain several `RUN` blocks;
each such line binds to whichever `RUN` most recently appeared above it. There is no way to
reference an earlier `RUN` by name or number -- only "the last one seen so far." This rule
applies the same way inside a `PARALLEL` block.

**Values must be quoted** with double quotes; the parser uses shell-style tokenizing
(`shlex.split`), so `"a value with spaces"` works, and a single quote inside a double-quoted
string (e.g. for embedding a `python3 -c '...'` command) is preserved literally.

**Failure behavior:** any syntax problem -- missing `TEST`/`END`, an `EXPECT*` before any
`RUN`, an unknown keyword, a wrong argument count, an invalid integer, an invalid hex string,
or any statement after `END` -- raises a parse error that names the exact line number and
shows the offending line. **No commands are executed and no `.log` file is written** for a
file that fails to parse.

Example of the exact error format (this is real output, not illustrative):
```
line 5: expected an integer for EXPECT_EXIT, got 'zero'
    > EXPECT_EXIT zero
```

---

## 6. First Test

Minimal test:

```text
TEST "NVMe Identify Controller"

RUN "nvme id-ctrl {{device}}"

EXPECT_EXIT 0

END
```

Line by line:
- `TEST "NVMe Identify Controller"` -- names this test case; shown in the log's header.
- `RUN "nvme id-ctrl {{device}}"` -- runs `nvme id-ctrl /dev/nvme0` (after `{{device}}` is
  substituted from `common_variables.json` -- see Section 7).
- `EXPECT_EXIT 0` -- the command's exit code must be `0`.
- `END` -- marks the end of the file; nothing may follow.

> **Note on syntax:** the framework's actual variable placeholder syntax is `{{device}}`
> (double curly braces), **not** `${device}`. This is a deliberate implementation choice:
> `RUN` commands are executed via the shell, and `${...}` is live POSIX shell
> parameter-expansion syntax -- using it as a placeholder would collide with any legitimate
> shell variable a test author might write. See `framework/variable_manager.py`'s module
> docstring for the full rationale. Every example in this guide uses the actual `{{...}}`
> syntax.

A more realistic test with multiple validations (this is the actual shipped
`tests/TC001_success.nvtest`, using a `printf` mock in place of real hardware so it runs
anywhere):

```text
TEST "Mock Identify Controller - Successful Validation"

RUN "printf 'Model Number   : Samsung SSD 970 EVO\nFirmware Revision : 2B2QEXM7\n'"

EXPECT_EXIT 0
EXPECT "Model Number" CONTAINS "Samsung"
EXPECT "Firmware Revision" NOT_EMPTY
EXPECT "ERROR" NOT_CONTAINS

END
```

Running it:

```bash
$ python3 run.py tests/TC001_success.nvtest
TC001_success    PASS

Total: 1
Passed: 1
Failed: 0
```

---

## 7. Variables

`common_variables.json` (project root) holds values shared across `.nvtest` files. Variables
can also be created at runtime, mid-test, by capturing a command's output -- see
Section 10c -- and are then usable exactly the same way as ones loaded from this file.

```json
{
    "device": "/dev/nvme0",
    "expected_model": "Samsung",
    "expected_fw": "ABC123",
    "min_iops": 100000
}
```

- **Defining variables:** add any `"name": value` pair to this JSON object, or capture one at
  runtime with `RUN "..." CAPTURE <name>` (Section 10c). The file must parse as a JSON object
  (not a list or scalar) -- `VariableManager.load()` raises `ValueError` otherwise.
- **Referencing variables:** write `{{name}}` anywhere inside a `RUN` command string or inside
  an `EXPECT "<field>" CONTAINS "<value>"` value. Example: `RUN "nvme id-ctrl {{device}}"`,
  `EXPECT "Model Number" CONTAINS "{{expected_model}}"`.
- **Command substitution:** every `RUN` command has `{{...}}` placeholders resolved before the
  command is executed.
- **Validation substitution:** only the `CONTAINS` value operand is substituted. `EXPECT_EXIT`,
  `EXPECT_BYTE`, and `EXPECT_HEX` do **not** support `{{...}}` placeholders -- `EXPECT_EXIT
  {{code}}` will fail to parse (the parser converts the argument to an integer immediately,
  before any substitution step exists). `NOT_CONTAINS`/`NOT_EMPTY` have no value operand to
  substitute in the first place.
- **Missing variables:** referencing a name not present in the loaded JSON raises a clear
  error and the test is reported as `ERROR` (no commands run, no `.log` written). Real output:
  ```
  TC009_missing_variable    ERROR
                              -> Unknown variable {{not_a_real_variable}} (not found in common_variables.json)
  ```
- **Supported value types:** any JSON value works as a variable (`VariableManager.get()`
  returns it as-is), but substitution always converts the value to its string form
  (`str(value)`) when inserting it into text -- so `"min_iops": 100000` would substitute as
  the text `100000`. In practice, **`min_iops` is not consumed anywhere in the shipped DSL**:
  there is no numeric-comparison validation statement (no `EXPECT_MIN_IOPS`, no `>=` operator)
  -- it is present in `common_variables.json` as shipped, but nothing currently checks it. If
  you need to validate a numeric threshold from `fio` output today, you must do it with a text
  `EXPECT ... CONTAINS` check against the specific line/number `fio` prints, which is fragile
  for numeric comparisons -- this is a known gap, not a hidden feature.
- **Nested variables:** not supported. Substitution is a single pass; a variable's value is
  never itself scanned for further `{{...}}` placeholders.
- **Escaping/special characters:** none of the JSON values are shell-escaped before being
  inserted into a `RUN` command. Because `RUN` strings execute via the shell, a variable value
  containing shell metacharacters would be interpreted by the shell. Treat
  `common_variables.json` as trusted input, exactly like your `.nvtest` files -- do not
  populate it from an untrusted or external source without validating its contents first.

---

## 8. YAML Configuration

`config/config.yaml` (shipped as a template) contains every configurable framework value.

| Name | Purpose | Type | Default | Example |
|---|---|---|---|---|
| `framework.log_directory` | Base directory under which each run's `logs/{timestamp}/` is created | string (path) | `"logs"` | `logs` |
| `framework.log_level` | Console log level for `FrameworkLogger` | string, one of `DEBUG`/`INFO`/`WARNING`/`ERROR` | `"INFO"` | `INFO` |
| `execution.command_timeout` | Default timeout (seconds) applied to every `RUN` command | number | `300` | `300` |
| `variables.file` | Path to the JSON variables file, loaded automatically at framework startup | string (path) | `"common_variables.json"` | `common_variables.json` |

Example (the actual shipped file):
```yaml
framework:
  log_directory: logs
  log_level: INFO

execution:
  command_timeout: 300

variables:
  file: common_variables.json
```

> **Important, verified behavior:** this file is **not loaded automatically**. Without
> `--config`, the framework uses its own built-in defaults (which happen to match every value
> shown above) -- editing `config/config.yaml` has **no effect at all** unless you pass it
> explicitly:
> ```bash
> python3 run.py tests/ --config config/config.yaml
> ```
> This is documented directly in the file's own header comment. There is no environment
> variable or auto-discovery mechanism that picks it up implicitly.

If a key is omitted from your YAML file, its built-in default is used (a partial YAML file is
valid -- only the sections/keys you provide override the defaults).

`--config path/to/nonexistent.yaml` fails immediately with `Error: Config file not found:
path/to/nonexistent.yaml` and exit code `2`, before any test runs.

---

## 9. Commands

`RUN "<command>"` executes `<command>` through the shell (`subprocess.run(command,
shell=True, ...)`) and captures its exit code, stdout, and stderr as raw bytes. There is one
execution path for every command -- the framework does not distinguish between `nvme`, `fio`,
or any other Linux command.

### NVMe
```text
RUN "nvme list"
RUN "nvme id-ctrl {{device}}"
RUN "nvme smart-log {{device}}"
RUN "nvme admin-passthru {{device}} --opcode=0x06 --cdw10=1 --data-len=4096 --read --raw-binary"
RUN "nvme io-passthru {{device}} --opcode=0x02 --namespace-id=1 --data-len=4096 --read --raw-binary"
```

### Linux
```text
RUN "lsblk"
RUN "lspci"
```

### FIO
```text
RUN "fio --name=safe_smoke --filename=/tmp/test.bin --size=4M --rw=write --bs=4k --numjobs=1 --minimal"
```

**Execution details:**
- **Return code:** captured exactly as the shell reports it.
- **stdout/stderr:** always captured as raw `bytes` internally. Text commands are decoded with
  UTF-8 (`errors="replace"` for any invalid bytes) when rendered into the `.log`; binary
  commands are hex-dumped instead (see Section 12).
- **Timeout:** every command is subject to `execution.command_timeout` from config (default
  300 seconds) unless overridden. A command that exceeds the timeout is reported with exit
  code `-1` and a `[TIMEOUT after Ns]` marker appended to captured stderr.
- **Command failure (nonexistent binary):** if the shell itself can't find the command, the
  framework still returns a result (exit code from the shell, typically `127`) rather than
  crashing -- this is then just an ordinary `EXPECT_EXIT` failure like any other.

---

## 10. Combined Automation

A single `.nvtest` file can chain any number of `RUN` steps across different tools -- NVMe,
Linux, and FIO -- with each step's own validations. This is a real, verified capability, not
aspirational. Actual shipped example (`tests/TC008_combined_safe.nvtest`, using safe/mock
commands so it runs without hardware):

```text
TEST "Combined Mock NVMe Health Check"

RUN "printf 'Model Number   : {{expected_model}} SSD 970 EVO\nFirmware Revision : {{expected_fw}}\n'"

EXPECT_EXIT 0
EXPECT "Model Number" CONTAINS "{{expected_model}}"
EXPECT "Firmware Revision" NOT_EMPTY

RUN "printf 'critical_warning : 0\ntemperature : 35C\n'"

EXPECT_EXIT 0
EXPECT "critical_warning" CONTAINS "0"

RUN "lsblk"

EXPECT_EXIT 0

RUN "fio --name=mock_combined --filename=/tmp/nvme_test_combined_fio.bin --size=4M --rw=write --bs=4k --numjobs=1 --minimal"

EXPECT_EXIT 0

END
```

**Precise rules:**
- **Command ordering:** `RUN` steps execute strictly in the order they appear in the file.
- **Validation association:** each `EXPECT*` binds to the nearest `RUN` above it -- in the
  example, the first two `EXPECT` lines check the `printf` "identify" output, `EXPECT
  "critical_warning" CONTAINS "0"` checks the `printf` "smart-log" output, the bare
  `EXPECT_EXIT 0` after `lsblk` checks `lsblk`'s exit code, and the final `EXPECT_EXIT 0`
  checks `fio`'s exit code.
- **After a command failure:** execution continues -- a nonzero exit code from an earlier
  `RUN` does not stop later `RUN` steps or skip their validations. Every `RUN` in the file
  always executes.
- **Final result:** if any validation across the entire file fails, the whole test is reported
  `FAIL`. All validations from all steps still run and are all shown in the `.log`, even after
  the first failure.

A realistic identify -> SMART -> FIO example targeting real hardware (shipped as
`tests/examples/TC011_combined_identify_smart_fio.nvtest`, marked destructive because its
`fio` step writes to a raw device) shows the same pattern with real `nvme`/`fio` commands
instead of mocks.

---

## 10a. Parallel & Loop Execution

Two additions on top of ordinary `RUN`/`EXPECT*` sequencing let one `.nvtest` file drive
repeated and concurrent command execution -- the scenario of "run `nvme id-ctrl` in a loop
1000 times, and `nvme reset` in a loop 100 times, in separate terminals at the same time" is
expressible directly, without any external shell scripting.

**`LOOP <n>` -- run a command repeatedly:**
```text
RUN "nvme id-ctrl {{device}}" LOOP 1000

EXPECT_EXIT 0
```
Every `EXPECT*`/`EXPECT_STDERR*` bound to that `RUN` is checked **after every single
iteration**, not just the last one. The test (and that validation) FAILs if **any** iteration
fails, not just if the last one does. Omitting `LOOP <n>` is exactly `LOOP 1` -- ordinary,
single-execution behavior, completely unchanged.

**`PARALLEL` / `END_PARALLEL` -- run two or more commands concurrently:**
```text
TEST "Concurrent Stress Example (mock)"

PARALLEL
RUN "echo mock-id-ctrl-iteration" LOOP 1000
EXPECT_EXIT 0

RUN "echo mock-reset-iteration" LOOP 100
EXPECT_EXIT 0
END_PARALLEL

END
```
(this is the actual shipped `tests/TC011_parallel_stress.nvtest`; the real-hardware version is
`tests/examples/TC013_parallel_identify_reset_HARDWARE_REQUIRED.nvtest`, using real `nvme
id-ctrl`/`nvme reset` in place of `echo`)

- Every `RUN` directly inside a `PARALLEL` block executes in its **own thread**, concurrently
  with every other `RUN` in that same block. If a `RUN` also has `LOOP <n>`, that command loops
  `n` times **inside its own thread**, independently of how many times any other command in the
  block loops.
- The framework waits for **every** thread in the block to finish before continuing past
  `END_PARALLEL`.
- A `PARALLEL` block must contain **at least 2** `RUN` statements (a block of one thing isn't
  meaningfully parallel) and cannot be nested inside another `PARALLEL` block. Both rules are
  enforced at parse time with a clear error.
- `EXPECT*`/`EXPECT_STDERR*` binding works exactly the same inside a `PARALLEL` block as
  outside it: each one binds to the nearest preceding `RUN` (which may now be one of several
  concurrently-running commands).

**How the `.log` reports a looped/parallel `RUN`** -- since a `RUN` looped hundreds or
thousands of times can't reasonably print one `OUTPUT:` block per iteration, the framework
records only a compact summary per command, regardless of how many times it looped:
```
COMMAND:
echo mock-id-ctrl-iteration

LOOP COUNT:
1000

PARALLEL GROUP:
0

ITERATIONS RUN:
1000

EXIT CODE (last iteration):
0

OUTPUT (last iteration):
mock-id-ctrl-iteration

FIRST FAILURE:
(none)
```
`PARALLEL GROUP:` only appears for a `RUN` that was inside a `PARALLEL` block. `FIRST FAILURE:`
shows `(none)` if every bound validation passed on every iteration, or the earliest failing
iteration/message across any bound validation otherwise. The `VALIDATION:` section then shows
one aggregate line per validation, e.g.:
```
[PASS] Exit code == 0 across 1000 iterations: 1000 passed, 0 failed
[FAIL] Exit code == 0 across 20 iterations: 14 passed, 6 failed (first failure at iteration 1: Exit code == 0 (got 1))
```
For an ordinary, non-looped `RUN` (`LOOP` omitted, i.e. `LOOP 1`), the `.log` is rendered
**exactly as before** this feature existed -- no `LOOP COUNT:`/`ITERATIONS RUN:`/`FIRST
FAILURE:` lines, and the `VALIDATION:` line is the plain `[PASS]/[FAIL] <message>` text you've
always seen. Nothing changes for any `.nvtest` file that doesn't use `LOOP`/`PARALLEL`.

---

## 10b. Stderr Validation (Negative / Expected-Failure Tests)

Plain `EXPECT` only ever looks at a command's **stdout**. To validate what a command prints to
**stderr** -- the natural thing to check for a test where a command is *expected* to fail,
e.g. because of an invalid field/argument -- use `EXPECT_STDERR`, which has identical grammar
to `EXPECT` (`CONTAINS "<value>"` / `NOT_CONTAINS` / `NOT_EMPTY`):

```text
TEST "Negative Test - Invalid Field on Stderr"

RUN "nvme id-ctrl {{device}} --this-field-does-not-exist=1"

EXPECT_EXIT 1
EXPECT_STDERR "invalid" NOT_EMPTY

END
```
(shipped as `tests/examples/TC014_invalid_field_stderr.nvtest`; a fully mocked, no-hardware
version demonstrating both a positive `EXPECT_STDERR` match and confirming plain `EXPECT`
still only sees stdout is `tests/TC012_stderr_validation.nvtest`)

- `EXPECT_STDERR "<field>" CONTAINS "<value>"` finds the first line of **stderr** containing
  `<field>`, then checks that line also contains `<value>` -- same "first matching line"
  semantics as `EXPECT ... CONTAINS`, just applied to the other stream.
- `EXPECT_STDERR "<field>" NOT_CONTAINS` / `NOT_EMPTY` work the same way as their `EXPECT`
  counterparts, against stderr instead of stdout.
- A plain `EXPECT` on the same `RUN` still only ever sees stdout -- if a command's error text
  goes to stderr (as is conventional, and as `nvme-cli` does), a plain `EXPECT` checking for
  that text will report "(field not found in output)" even though the text is genuinely
  present, just on the other stream. Use `EXPECT_STDERR` for it instead.
- `.log` messages for `EXPECT_STDERR` are tagged `(stderr)` so it's unambiguous which stream a
  given `[PASS]`/`[FAIL]` line checked, e.g. `"invalid field" contains "invalid field"
  (stderr)`.

---

## 10c. Capturing Command Output Into a Variable

`RUN "<command>" CAPTURE <name>` stores that command's stdout (stripped of leading/trailing
whitespace) as a runtime variable, usable via `{{name}}` in any **later** `RUN` or `EXPECT ...
CONTAINS` in the same file -- exactly like a variable loaded from `common_variables.json`,
except captured live from a command's own output instead of coming from a static file.

```text
TEST "Capture Output Into Variable"

RUN "echo FW1234ABCD" CAPTURE fw_version

EXPECT_EXIT 0

RUN "echo Using captured firmware {{fw_version}}"

EXPECT_EXIT 0
EXPECT "Using captured firmware" CONTAINS "{{fw_version}}"

END
```
(this is the actual shipped `tests/TC013_capture_variable.nvtest`; a real-hardware version
capturing `nvme id-ctrl` output is `tests/examples/TC015_capture_regex_HARDWARE_REQUIRED.nvtest`)

- `CAPTURE <name>` can be combined with `LOOP <n>` (in either order --
  `RUN "..." LOOP 5 CAPTURE x` and `RUN "..." CAPTURE x LOOP 5` are equivalent); when combined,
  the **last** iteration's stdout is what gets captured.
- `<name>` must contain only letters, digits, and underscores -- the same rule as `{{name}}`
  itself.
- A captured variable takes precedence over any same-named value loaded from
  `common_variables.json` for the rest of that run, and works even if no variables file is
  loaded at all (an empty/no `common_variables.json` doesn't prevent `CAPTURE` from working).
- **Important ordering detail:** because a later `RUN` might reference a variable captured by
  an earlier one, `{{...}}` placeholders are resolved for each `RUN` right before *that*
  command executes -- not all at once before any command runs (which is how plain,
  non-`CAPTURE` `.nvtest` files still effectively behave, since there's nothing to capture).
  This means if a later `RUN` references an undefined `{{name}}`, any earlier `RUN`s in the
  file will already have executed (with whatever real-world effects that implies) before the
  test is reported as `ERROR` for the bad reference.
- A `RUN`'s own bound `EXPECT*` lines cannot see that same `RUN`'s own capture -- the capture
  happens only after that `RUN`'s loop (if any) fully completes. Use the captured variable in
  a *later* `RUN`/`EXPECT`, not the one that captured it.
- Inside a `PARALLEL` block, each `RUN` should `CAPTURE` into its **own**, distinct variable
  name; two concurrently-running `RUN`s that both `CAPTURE` into the *same* name will race
  (last write wins, non-deterministic), since they execute at the same time.

---

## 10d. Regex Validation

`EXPECT_REGEX "<pattern>"` / `EXPECT_REGEX_STDERR "<pattern>"` check whether a Python `re`
pattern matches anywhere in the bound command's stdout/stderr respectively. The pattern is
compiled and validated at **parse time**, so a malformed regex is a clear `ParseError`, not a
runtime crash.

```text
TEST "Regex Validation"

RUN "printf 'Firmware Revision : 2B2QEXM7\n'"

EXPECT_EXIT 0
EXPECT_REGEX "Firmware Revision\s*:\s*[A-Z0-9]+"

RUN "python3 -c 'import sys; print(\"Error: field 42 invalid\", file=sys.stderr); sys.exit(1)'"

EXPECT_EXIT 1
EXPECT_REGEX_STDERR "field \d+ invalid"

END
```
(this is the actual shipped `tests/TC014_regex_validation.nvtest`)

- The pattern uses standard Python `re` syntax (`\d`, `\s`, character classes, quantifiers,
  etc.) and is checked with `re.search()` -- it does not need to match the entire
  output, only somewhere within it.
- `{{variable}}` placeholders inside a regex pattern **are** substituted (unlike
  `EXPECT_EXIT`/`EXPECT_BYTE`/`EXPECT_HEX`, which don't support substitution at all). If
  substitution produces an invalid pattern at runtime (something that couldn't be checked at
  parse time, since the raw, unsubstituted text was valid), the validation fails with a clear
  "invalid regex after substitution" message rather than crashing the test.
- `.log` messages look like `matches regex "Firmware Revision\s*:\s*[A-Z0-9]+"`, tagged
  `(stderr)` for `EXPECT_REGEX_STDERR`, consistent with `EXPECT_STDERR`'s tagging.

---

## 11. Validation Reference

| Validator | Syntax | Purpose | Matching semantics | Example |
|---|---|---|---|---|
| Exit code | `EXPECT_EXIT <code>` | Check the bound command's exit code | Exact integer equality | `EXPECT_EXIT 0` |
| Contains | `EXPECT "<field>" CONTAINS "<value>"` | Check a field's value (stdout) | Finds the first line of stdout containing `<field>` as a substring, then checks that same line also contains `<value>` as a substring. Fails if no line contains `<field>` at all. | `EXPECT "Model Number" CONTAINS "Samsung"` |
| Not contains | `EXPECT "<field>" NOT_CONTAINS` | Assert absence (stdout) | `<field>` must not appear anywhere in the entire stdout (not line-scoped) | `EXPECT "ERROR" NOT_CONTAINS` |
| Not empty | `EXPECT "<field>" NOT_EMPTY` | Assert a field has a value (stdout) | Finds the first line containing `<field>`, strips the field text and any leading `:`/`=` and whitespace from what follows, and checks the remainder is non-empty | `EXPECT "Firmware Revision" NOT_EMPTY` |
| Stderr contains | `EXPECT_STDERR "<field>" CONTAINS "<value>"` | Check a field's value (stderr) | Identical semantics to Contains, applied to stderr instead of stdout | `EXPECT_STDERR "invalid" CONTAINS "invalid field"` |
| Stderr not contains | `EXPECT_STDERR "<field>" NOT_CONTAINS` | Assert absence (stderr) | Identical semantics to Not contains, applied to stderr | `EXPECT_STDERR "panic" NOT_CONTAINS` |
| Stderr not empty | `EXPECT_STDERR "<field>" NOT_EMPTY` | Assert a field has a value (stderr) | Identical semantics to Not empty, applied to stderr | `EXPECT_STDERR "Error" NOT_EMPTY` |
| Byte | `EXPECT_BYTE <offset> <byte>` | Check one raw byte | The byte at `<offset>` in the command's raw stdout equals `<byte>` exactly | `EXPECT_BYTE 0x00 0x01` |
| Hex | `EXPECT_HEX <offset> "<hexstring>"` | Check a byte range | The bytes at `<offset>` through `<offset>+len(hexstring)/2` equal the given hex string | `EXPECT_HEX 0x10 "12345678"` |
| Regex | `EXPECT_REGEX "<pattern>"` | Check stdout against a pattern | `re.search(pattern, stdout)` -- matches anywhere in stdout, not anchored to the whole string | `EXPECT_REGEX "Firmware.*[0-9]+"` |
| Regex (stderr) | `EXPECT_REGEX_STDERR "<pattern>"` | Check stderr against a pattern | Identical semantics to Regex, applied to stderr | `EXPECT_REGEX_STDERR "field \d+ invalid"` |

**Not implemented:** `EQUALS` (exact whole-output match) and any numeric-comparison operator
(`>=`, `<=`, etc.) -- regex matching (`EXPECT_REGEX`) is implemented and can approximate some
of these (e.g. `EXPECT_REGEX "iops:\s*[1-9][0-9]{5,}"` for a crude "6+ digit IOPS" check), but
there is no dedicated numeric-threshold statement.

**All validations always run**, in declared order, regardless of whether an earlier one
failed. A single failed validation anywhere in the file makes the whole test `FAIL`. For a
`RUN` that used `LOOP <n>` (n > 1), "run" here means "run against every iteration" -- see
Section 10a; a single failing iteration is enough to fail that validation, and thus the test.

---

## 12. Binary Output

`admin-passthru` and `io-passthru` (and any other command that emits raw binary data) are
handled by the exact same execution path as text commands -- the framework has no special
"binary mode" flag on `RUN`. Whether a command's output is rendered as text or as a hex dump
in the `.log` is decided automatically: **if any `EXPECT_BYTE` or `EXPECT_HEX` validation is
bound to that `RUN`, its output is hex-dumped; otherwise it's shown as text.**

```
nvme admin-passthru / nvme io-passthru
        |
        v
  raw bytes (captured exactly as written by the command)
        |
        v
  Binary validation (EXPECT_BYTE / EXPECT_HEX -- checked against the RAW bytes,
        |            never against the formatted hex-dump text)
        v
  Hex dump rendering (only for display, produced after validation)
        |
        v
  Same .log file as every other test (no separate .bin file, ever)
```

Realistic example (shipped as `tests/examples/TC007_admin_passthru_HARDWARE_REQUIRED.nvtest`):

```text
RUN "nvme admin-passthru {{device}} --opcode=0x06 --cdw10=1 --data-len=4096 --read --raw-binary"

EXPECT_EXIT 0
EXPECT_BYTE 0x00 0x00
EXPECT_BYTE 0x01 0x00
```

Actual hex dump format written into the `.log` (real output, from
`tests/TC003_byte_validation.nvtest`, using a Python-generated mock byte string in place of
real hardware):

```
BINARY OUTPUT:
Size: 8 bytes

00000000  01 00 00 00 4e 56 4d 65                          ....NVMe
```

- **Offset:** decimal or `0x`-prefixed hex, 0-based, counted from the start of the command's
  raw stdout.
- **Byte (`EXPECT_BYTE`):** a single byte value, `0x00`-`0xFF`; values outside that range are
  rejected at parse time.
- **Hex string (`EXPECT_HEX`):** plain hex digits (no `0x` prefix), must have even length and
  be valid hex, representing however many bytes you're checking starting at the offset.
- **Byte ordering:** the framework performs no endianness interpretation -- you are matching
  raw bytes exactly as captured; if a field is little-endian in the NVMe spec, write the bytes
  in that same little-endian order.
- **Output size:** if `<offset>` falls beyond the number of bytes actually captured, the
  validation fails with an explicit message (e.g. `"offset beyond 8 captured bytes"`) rather
  than raising an exception.
- **Binary validation failure:** reported exactly like a text validation failure -- a `[FAIL]`
  line in the `.log`'s `VALIDATION:` section, with the actual byte(s)/hex found appended for
  diagnosis, e.g. `Byte at offset 0x00 == 0x01 (got 0x02)`.

> **No separate `.bin` file is ever created.** All binary output lives exclusively inside the
> `.log` file's hex dump. This was directly re-verified: after running the framework's full
> test suite (including all binary-validation tests), searching for `.bin` files anywhere in
> the project returns nothing.

---

## 13. FIO Automation

FIO commands are just `RUN` statements like anything else -- there is no FIO-specific syntax.

Safe example (targets a regular file, not a device -- actually runnable, shipped as
`tests/examples/TC005_fio_safe_smoke.nvtest`):

```text
TEST "FIO Smoke Test (file-based, safe)"

RUN "fio --name=safe_smoke --filename=/tmp/nvme_test_fio_smoke.bin --size=4M --rw=write --bs=4k --numjobs=1 --minimal"

EXPECT_EXIT 0

END
```

Combining FIO with NVMe/common variables (shipped as
`tests/examples/TC011_combined_identify_smart_fio.nvtest`):

```text
RUN "fio --name=combined_write --filename={{device}} --rw=randwrite --bs=4k --size=256M --numjobs=1 --direct=1"

EXPECT_EXIT 0
```

> ### Destructive operations -- read before running against real hardware
> Any `fio` job whose `--filename` points at a raw block device (e.g. `{{device}}` resolving
> to `/dev/nvme0n1`) with a `--rw=write`/`--rw=randwrite`/similar mode **will destroy data and
> any filesystem on that device**. The shipped example
> `tests/examples/TC006_fio_write_DESTRUCTIVE.nvtest` is deliberately named and commented to
> make this unmistakable, and -- like every hardware-required/destructive example under
> `tests/examples/` -- is never executed automatically by the framework's own
> self-verification (`python3 run.py` with no arguments). You must run it explicitly and
> deliberately:
> ```bash
> python3 run.py tests/examples/TC006_fio_write_DESTRUCTIVE.nvtest
> ```
> Similarly, `nvme admin-passthru`/`nvme io-passthru` commands can modify device state
> depending on the opcode used -- the shipped passthru examples use read-only opcodes, but the
> framework itself does not restrict which opcodes you can pass. Treat any
> `admin-passthru`/`io-passthru` command as potentially state-modifying unless you have
> verified the specific opcode is read-only.

---

## 14. Running Tests

```bash
python3 run.py <path> [--config CONFIG] [--log-level {DEBUG,INFO,WARNING,ERROR}] [--dry-run]
```

Actual `--help` output:
```
usage: run.py [-h] [--config CONFIG] [--log-level {DEBUG,INFO,WARNING,ERROR}]
              [--dry-run]
              path

Run one .nvtest file or every .nvtest file in a directory.

positional arguments:
  path                  Path to a .nvtest file or a directory of .nvtest files

options:
  -h, --help            show this help message and exit
  --config CONFIG       Path to a YAML config file
  --log-level {DEBUG,INFO,WARNING,ERROR}
                        Console log level (overrides config.yaml
                        framework.log_level)
  --dry-run             Discover and parse target(s) only -- no commands
                        executed, no .log files written
```

**`<path>` accepts exactly one argument** -- either:
- **A single `.nvtest` file:** `python3 run.py tests/TC001_success.nvtest`
- **A directory:** `python3 run.py tests/` -- discovers and runs only `*.nvtest` files
  directly inside it (non-recursive), sorted alphabetically. Every other file in that
  directory (`.yaml`, `.txt`, `.py`, etc.) is silently ignored -- never opened, never parsed.

Passing more than one path (e.g. `python3 run.py tests/a.nvtest tests/b.nvtest`) is a usage
error: `argparse` rejects it with `unrecognized arguments`.

**`--config <path>`** -- load framework settings from a YAML file (see Section 8). Fails
immediately with exit code `2` if the path doesn't exist, or if PyYAML isn't installed.

**`--log-level <LEVEL>`** -- overrides the console log level for this run only (does not
affect what's written to that run's `run.log` file, which always captures everything at
`DEBUG` and above).

**`--dry-run`** -- parses every discovered target and reports `DRY-RUN` for each with its
command count, but executes no commands and writes no `.log` files. Useful to preview what a
directory invocation would do before actually running it (especially before pointing at a
directory that might contain destructive examples).

**Exit codes:** `0` = every discovered test passed (or none were discovered/`--dry-run` was
used); `1` = at least one test failed or errored; `2` = a usage error (bad path, missing
config file).

> ### Important: `python3 run.py` with no arguments does not print usage help
> With zero arguments, `run.py` runs the framework's own internal self-verification suite
> (hand-written checks covering every stage of the framework's own development), not a
> "please provide a path" message. This suite actually executes real commands, including real
> `lsblk` and real `fio` writes to files under `/tmp` (some shipped test/example files are
> genuinely run, not mocked). It does not touch any real NVMe hardware or run any
> destructive/hardware-required example. This is documented behavior of the shipped `run.py`,
> not a bug, but it means running `python3 run.py` bare is a self-test of the framework, not
> "show me how to use this" -- always pass a path for normal test execution.

---

## 15. Logs

```
logs/
└── 20260822_054710_3c2569c1/
    ├── TC001_success.log
    ├── TC002_failed_validation.log
    └── run.log
```

**One timestamp directory represents one complete framework execution** (one `python3 run.py
...` invocation). Every `.nvtest` file run during that invocation writes its `.log` into the
same directory, alongside one `run.log` (the framework's own diagnostic log for that
invocation -- not a test result).

The directory name format is `YYYYMMDD_HHMMSS_<8-hex-characters>` -- the trailing random
suffix guarantees each invocation gets its own directory even if two runs start within the
same second.

**The `.log` filename is derived from the source `.nvtest` file's name**, not the free-text
`TEST "..."` name inside it -- so `tests/TC001_success.nvtest` always produces
`TC001_success.log`, regardless of what its `TEST` line says.

Complete real example log (`TC008_combined_safe.log`, generated by actually running
`tests/TC008_combined_safe.nvtest`):

```
========================================
TEST: Combined Mock NVMe Health Check
START: 2026-08-22 05:47:10
========================================

COMMAND:
printf 'Model Number   : Samsung SSD 970 EVO\nFirmware Revision : ABC123\n'

EXIT CODE:
0

OUTPUT:
Model Number   : Samsung SSD 970 EVO
Firmware Revision : ABC123


COMMAND:
printf 'critical_warning : 0\ntemperature : 35C\n'

EXIT CODE:
0

OUTPUT:
critical_warning : 0
temperature : 35C


COMMAND:
lsblk

EXIT CODE:
0

OUTPUT:
NAME  MAJ:MIN RM  SIZE RO TYPE MOUNTPOINTS
...


COMMAND:
fio --name=mock_combined --filename=/tmp/nvme_test_combined_fio.bin --size=4M --rw=write --bs=4k --numjobs=1 --minimal

EXIT CODE:
0

OUTPUT:
3;fio-3.36;mock_combined;...


VALIDATION:
[PASS] Exit code == 0
[PASS] "Model Number" contains "Samsung"
[PASS] "Firmware Revision" is not empty
[PASS] Exit code == 0
[PASS] "critical_warning" contains "0"
[PASS] Exit code == 0
[PASS] Exit code == 0

RESULT:
PASS

END: 2026-08-22 05:47:12
========================================
```

**Sections explained:**
- `TEST:` / `START:` -- the test's declared name and when execution began.
- One `COMMAND:` / `EXIT CODE:` / `OUTPUT:` (or `BINARY OUTPUT:`) block per `RUN` statement in
  the file, in order. A `STDERR:` block is added automatically if the command produced any.
- `VALIDATION:` -- every validation's `[PASS]`/`[FAIL]` line, in declared order, across all
  `RUN` blocks.
- `RESULT:` -- `PASS` only if every validation passed; `FAIL` otherwise.
- `END:` -- when execution finished.

---

## 16. PASS / FAIL Behavior

Real `FAIL` example (`tests/TC002_failed_validation.nvtest` actually run):
```
COMMAND:
printf 'Model Number   : Kioxia KXG60ZNV\nFirmware Revision : 1A1AEXM2\n'

EXIT CODE:
0

VALIDATION:
[PASS] Exit code == 0
[FAIL] "Model Number" contains "Samsung"
[PASS] "Firmware Revision" is not empty
[PASS] "ERROR" not present in output

RESULT:
FAIL
```

- **Command failure** (nonzero exit code): does not stop the test -- it's simply reflected in
  any `EXPECT_EXIT` check bound to that command, and every other `RUN`/validation in the file
  still executes.
- **Validation failure:** recorded as `[FAIL] ...` with a reason (e.g. "field not found in
  output", "(got 0x02)", "(got <exit code>)") -- every other validation still runs.
- **Multiple validation failures:** all are shown; the test is `FAIL` if any failed.
- **Multiple commands:** each has its own block in the log; a failure in an earlier command's
  validation does not prevent later commands from running.
- **Whether execution continues:** always -- nothing in this framework short-circuits on
  failure, at either the command level or the validation level.
- **Final result:** `PASS` if and only if every validation in the file passed; otherwise
  `FAIL`. There is no partial-pass state.

---

## 17. Common Test Patterns

All fourteen patterns below are shipped, real files you can inspect and run (marked SAFE /
NEEDS HARDWARE / DESTRUCTIVE):

| # | Pattern | Shipped file |
|---|---|---|
| 1 | Identify Controller | `tests/examples/TC001_nvme_list.nvtest` (SAFE), `tests/examples/TC002_identify_ctrl.nvtest` (NEEDS HARDWARE) |
| 2 | SMART Log | `tests/examples/TC003_smartlog.nvtest` (NEEDS HARDWARE) |
| 3 | Namespace/device check | `tests/examples/TC004_check_nvme_device.nvtest` (SAFE, uses `lsblk`) |
| 4 | Linux environment check | `tests/examples/TC004_check_nvme_device.nvtest` (SAFE) |
| 5 | FIO | `tests/examples/TC005_fio_safe_smoke.nvtest` (SAFE), `TC006_fio_write_DESTRUCTIVE.nvtest` (DESTRUCTIVE) |
| 6 | Identify + SMART | `tests/examples/TC010_combined_identify_smart.nvtest` (NEEDS HARDWARE) |
| 7 | Identify + SMART + FIO | `tests/examples/TC011_combined_identify_smart_fio.nvtest` (DESTRUCTIVE), `tests/TC008_combined_safe.nvtest` (SAFE mock equivalent) |
| 8 | Admin passthru | `tests/examples/TC007_admin_passthru_HARDWARE_REQUIRED.nvtest` (NEEDS HARDWARE) |
| 9 | IO passthru | `tests/examples/TC008_io_passthru_HARDWARE_REQUIRED.nvtest` (NEEDS HARDWARE) |
| 10 | Variable-driven validation | `tests/TC007_variable_substitution.nvtest` (SAFE), `tests/examples/TC009_combined_identify.nvtest` (NEEDS HARDWARE) |
| 11 | Concurrent loop/stress (LOOP + PARALLEL) | `tests/TC010_loop_sequential.nvtest`, `tests/TC011_parallel_stress.nvtest` (both SAFE), `tests/examples/TC013_parallel_identify_reset_HARDWARE_REQUIRED.nvtest` (NEEDS HARDWARE) |
| 12 | Negative test / expected failure (EXPECT_STDERR) | `tests/TC012_stderr_validation.nvtest` (SAFE), `tests/examples/TC014_invalid_field_stderr.nvtest` (NEEDS nvme-cli) |
| 13 | Capture output into a variable | `tests/TC013_capture_variable.nvtest` (SAFE), `tests/examples/TC015_capture_regex_HARDWARE_REQUIRED.nvtest` (NEEDS HARDWARE) |
| 14 | Regex validation | `tests/TC014_regex_validation.nvtest` (SAFE) |

Every hardware-required/destructive file has an in-file comment stating its exact requirement
or destructive nature, and none of them are executed by the framework's own
self-verification.

---

## 18. Troubleshooting

| Symptom | Possible cause | How to verify | How to resolve |
|---|---|---|---|
| `line N: <message>` error, test reported `ERROR`, no `.log` written | Invalid `.nvtest` syntax | Read the quoted line/message -- it names the exact problem (missing `END`, `EXPECT` before `RUN`, unknown keyword, etc.) | Fix the `.nvtest` file per Section 5 |
| `Unknown variable {{name}} (not found in ...)` | `.nvtest` references a `{{name}}` not present in the loaded variables JSON | Check `common_variables.json` for the exact key spelling | Add the missing key, or fix the typo in the `.nvtest` file |
| `Unsupported test file type '.xyz' for '...'` | You pointed the CLI directly at a non-`.nvtest` file | Check the file extension | Only `.nvtest` files are runnable directly; rename or point at the correct file |
| A file is silently missing from a directory run | It doesn't end in `.nvtest` | Directory discovery only globs `*.nvtest` in that directory, non-recursively | Rename with a `.nvtest` extension, or run it directly by path |
| `Error: Config file not found: ...` | `--config` path doesn't exist | Check the path you passed | Fix the path, or omit `--config` to use built-in defaults |
| Editing `config/config.yaml` has no effect | The file is a template only applied via `--config` (see Section 8) | Check whether `--config config/config.yaml` was actually passed | Add `--config config/config.yaml` to your invocation |
| `[TIMEOUT after Ns]` in a command's stderr, exit code `-1` | The command exceeded `execution.command_timeout` (default 300s) | Check the command's expected runtime vs. the configured timeout | Raise `execution.command_timeout` via `--config`, or investigate why the command hangs |
| `nvme: command not found` / similar in stderr, high exit code | `nvme-cli`/`fio` not installed, or not on `PATH` | `nvme version` / `fio --version` | Install the missing tool |
| Permission denied errors in stderr | Insufficient privileges to access the NVMe device | Check who owns/can access `/dev/nvme0` etc. | Run with sufficient privileges for the target device |
| `EXPECT "field" ...` always fails even though the text is visible in `OUTPUT:` | `CONTAINS`/`NOT_EMPTY` only match the first line containing `<field>` | Check the exact `OUTPUT:` text in the `.log` -- is the field on a later line, or split across lines? | Rephrase the command/field so the value appears on the same line as the field name |
| `Byte at offset 0x.. == 0x.. (offset beyond N captured bytes)` | The command produced fewer bytes than the offset you're checking | Check `BINARY OUTPUT: Size: N bytes` in the `.log` | Confirm the command actually returned the expected data length; fix the offset |
| No `.log` file at all for a test that seemed to run | The file failed to parse, or was rejected for its extension, before any command executed | Check the CLI's `ERROR` row and its `->` detail line | Fix the underlying parse/extension issue -- by design, no `.log` is written until execution actually begins |
| `EXPECT "field" ...` always fails on a command's error text even though it's clearly printed | The text is on stderr, and plain `EXPECT` only ever checks stdout | Check whether the text appears under `STDERR:`/`STDERR (last iteration):` in the `.log`, not `OUTPUT:` | Use `EXPECT_STDERR` instead of `EXPECT` for that check (Section 10b) |
| `a PARALLEL block must contain at least 2 RUN statements` | A `PARALLEL` block was written with only one `RUN` inside it | Count the `RUN` lines between `PARALLEL` and `END_PARALLEL` | Add a second `RUN`, or remove the `PARALLEL`/`END_PARALLEL` wrapper if only one command is needed |
| `nested PARALLEL blocks are not supported` | A `PARALLEL` block was opened while already inside another one | Check for a `PARALLEL` line before the previous block's `END_PARALLEL` | Close the outer block first; PARALLEL blocks cannot be nested |
| `missing END_PARALLEL (PARALLEL block never closed)` | A `PARALLEL` block has no matching `END_PARALLEL` before `END` | Check the file for a matching `END_PARALLEL` | Add `END_PARALLEL` right after the block's `RUN` statements |
| A `LOOP`ed test's `.log` shows `across N iterations: X passed, Y failed` even though you expected a pass/fail per single check | This is the intended aggregate format for `LOOP > 1` (Section 10a) -- every iteration is checked, and the result summarizes all of them, not just one | Check `ITERATIONS RUN:`/`FIRST FAILURE:` in the same command block for the specific failing iteration | If you need to inspect one specific iteration's full output, re-run with a smaller `LOOP` count temporarily, or check the `FIRST FAILURE:`/aggregate message for the failing iteration number |
| `Unknown variable {{name}} (not found in ...)` for a name you thought you `CAPTURE`d | The `RUN` that was supposed to `CAPTURE` it either hasn't executed yet (it's *later* in the file, or a typo in `CAPTURE <name>` itself) | Check the `CAPTURE` spelling and that it's on a `RUN` **before** the one referencing `{{name}}` | Move the capturing `RUN` earlier, or fix the `CAPTURE <name>`/`{{name}}` spelling to match exactly |
| `invalid regex ... for EXPECT_REGEX: ...` | The pattern passed to `EXPECT_REGEX`/`EXPECT_REGEX_STDERR` isn't valid Python `re` syntax | Read the specific `re` error appended to the message | Fix the pattern (remember this is Python regex syntax, not shell globbing) |
| `EXPECT_REGEX` never matches even though the text looks present | `re.search()` is case-sensitive and does not anchor to the whole line/output by default | Check the exact casing/spacing in the `.log`'s `OUTPUT:`/`STDERR:` section against your pattern | Adjust the pattern (e.g. add `(?i)` for case-insensitive, or loosen anchoring) |

---

## 19. Best Practices

- Give every `TEST` a meaningful, specific name -- it's the only thing shown in the `.log`'s
  header, independent of the filename.
- Keep each `EXPECT*` checking one thing; prefer several small, specific validations over one
  broad one -- every validation always runs and is reported individually, so granularity costs
  nothing and helps diagnosis.
- Put shared values (`device`, `expected_model`, etc.) in `common_variables.json` rather than
  hardcoding them in every `.nvtest` file, so a device/firmware change is a one-line edit.
- Avoid duplicating the same literal value across many `.nvtest` files -- use a variable
  instead.
- Prefer safe/mock commands (like `printf`, or `fio` against a `/tmp` file) when developing or
  debugging a test's structure, and switch to the real `nvme`/device-targeting command only
  once the test logic is confirmed.
- There is no built-in "setup"/"action"/"validation" phase separation beyond ordinary
  `RUN`/`EXPECT*` sequencing -- structure multi-step tests so each `RUN` block's validations
  clearly belong to that step, and use `#` comments to mark logical sections if a file gets
  long.
- Avoid unnecessary shell complexity in `RUN` strings (pipes, subshells, etc.) -- the simpler
  the command, the easier the resulting `.log` is to read and diagnose.
- Mark any destructive or hardware-dependent `.nvtest` file clearly in its own name and with a
  `#` comment at the top, and never rely on directory-mode auto-discovery to run it -- invoke
  it by its exact file path deliberately.

---

## 20. Limitations

- **No numeric-comparison validator.** There is no way to assert "IOPS >= N" or any other
  numeric threshold directly -- only exact exit-code equality, text substring/emptiness
  checks, and exact byte/hex matches. `min_iops` in `common_variables.json` is present but
  currently unconsumed by anything in the DSL.
- **`EXPECT_EXIT`/`EXPECT_BYTE`/`EXPECT_HEX` do not support `{{variable}}` substitution** --
  only `RUN` commands and `EXPECT ... CONTAINS` values do.
- **No regex matching** -- only literal substring (`CONTAINS`/`NOT_CONTAINS`) and exact
  byte/hex-range matching are supported.
- **`CONTAINS`/`NOT_EMPTY` only look at the first matching line** -- if a field name appears
  on more than one line of output, only the first is considered.
- **A validation always applies to the nearest preceding `RUN`** -- there is no way to
  reference an earlier step by name/number from later in the file.
- **`config/config.yaml` is not auto-loaded** -- it must be passed explicitly via `--config`
  to take effect.
- **The CLI accepts exactly one path argument** -- you cannot list multiple files/directories
  in one invocation; run the CLI once per path, or point it at a directory containing
  everything you want to run.
- **`python3 run.py` with no arguments runs the framework's internal self-test suite**, not a
  help message -- always pass a path for normal use (see the box in Section 14).
- **Variable values are not shell-escaped** before substitution into `RUN` commands -- treat
  `common_variables.json` as trusted input, identical in trust level to your `.nvtest` files.
- **Parallel execution is per-`.nvtest`-file, not per-invocation.** `PARALLEL` lets commands
  *within one test* run concurrently (see Section 10a); running multiple different `.nvtest`
  files from a directory concurrently is still sequential, one file at a time.
- **`LOOP`/`PARALLEL` reporting is a compact aggregate, not a per-iteration record.** The
  `.log` shows total iterations, pass/fail counts, and only the *first* failing iteration's
  detail -- it does not keep a full `COMMAND`/`OUTPUT` block for every single iteration (which
  would make a 1000-iteration loop's `.log` impractically large). If you need to see every
  iteration's raw output, reduce the `LOOP` count temporarily.
- **`PARALLEL` blocks cannot be nested**, and must contain at least 2 `RUN` statements.
- **`{{variable}}` substitution in a `RUN` happens once per `RUN` line, not once per loop
  iteration** -- a looped `RUN`'s command string is fixed after substitution; there is no way
  to vary the command on each iteration (e.g. an incrementing counter) without external
  scripting outside the `.nvtest` file.
- **No timeout differentiation between loop iterations** -- `execution.command_timeout`
  (Section 8) applies to each individual iteration of a `LOOP`ed `RUN`, not to the loop as a
  whole; a `LOOP 1000` of a command with a 300-second timeout could in the worst case take up
  to 1000 x 300 seconds if every iteration hangs.
- **`CAPTURE` only stores stdout** -- there is no `CAPTURE_STDERR`; if you need a captured
  command's stderr later, you'd need to work around it (e.g. redirecting stderr into stdout in
  the `RUN` command itself, at the cost of losing the stream separation for that command's own
  `EXPECT`/`EXPECT_STDERR` checks).
- **A captured variable is not visible to its own `RUN`'s bound validations** -- only to a
  *later* `RUN`/`EXPECT`. Capture happens after that `RUN`'s loop (if any) fully completes.
- **Undefined-variable errors from a `CAPTURE`-dependent `RUN` are detected only when execution
  reaches that `RUN`**, not upfront before any command runs -- unlike a plain
  `common_variables.json`-only `.nvtest` file, whose variable references were traditionally
  fully resolvable before execution began. Any earlier `RUN`s will already have executed by
  the time such an error is reported.
- **Two `RUN`s in the same `PARALLEL` block should not `CAPTURE` into the same variable name**
  -- doing so races (last write wins, non-deterministic), since they execute concurrently.
- **`EXPECT_REGEX`/`EXPECT_REGEX_STDERR` use `re.search()`, not `re.fullmatch()`** -- a pattern
  matching anywhere in the output passes; there is no way to require the pattern match the
  *entire* output without writing `^...$` anchors yourself.
