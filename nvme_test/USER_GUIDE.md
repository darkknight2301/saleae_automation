# USER_GUIDE.md — NVMe/FIO Test Automation Framework

For test authors. `.nvtest` is the **only** test-case format; Python implements the framework
only — you never write Python to author a test.

## 1. Requirements & Setup

- Python 3.8+
- `PyYAML>=6.0` (only needed if you use `--config`; `pip install -r requirements.txt`)
- `nvme-cli`, `fio` for real hardware tests; not required for mock/`lsblk`-based tests
- No install step otherwise — it's a plain script tree

```bash
python3 run.py tests/TC001_success.nvtest   # run one test
python3 run.py                              # runs the framework's own self-verification suite
```

## 2. Project Structure

```
run.py                       # CLI entry point (+ built-in self-verification, no args)
requirements.txt              # PyYAML>=6.0
common_variables.json         # variables you edit
config/config.yaml             # config template (must be passed via --config to take effect)
framework/                     # implementation — not normally edited
tests/                          # your .nvtest files
tests/examples/                 # realistic NVMe/FIO/passthru examples
tests/user_scenarios/            # IO speed, reboot persistence, hex/string validation, FW commit examples
tools/                          # helper scripts used inside RUN lines (fio_metrics.py)
logs/                            # created at runtime, one logs/{run_id}/ per invocation
```

## 3. `.nvtest` Syntax

One statement per line. Blank lines and `#` comments ignored.

| Statement | Syntax | Notes |
|---|---|---|
| `TEST` | `TEST "<name>"` | Required, first statement, once only |
| `RUN` | `RUN "<cmd>" [LOOP <n>] [CAPTURE <name>]` | Shell command (`shell=True`). `LOOP <n>` repeats it n times (default 1). `CAPTURE <name>` stores its stdout (stripped) as a variable for later `RUN`/`EXPECT ... CONTAINS` |
| `PARALLEL` / `END_PARALLEL` | wraps 2+ `RUN` | Every `RUN` inside runs concurrently (one thread each); framework waits for all before continuing. No nesting |
| `EXPECT_EXIT` | `EXPECT_EXIT <code>` | decimal or `0x..` |
| `EXPECT` | `EXPECT "<field>" CONTAINS/NOT_CONTAINS/NOT_EMPTY ["<value>"]` | checks **stdout** |
| `EXPECT` (numeric) | `EXPECT "<field>" EQ/NEQ/GT/GE/LT/LE <number>` | extracts first number after `<field>` on its line, compares numerically. `<number>` may be `{{var}}` |
| `EXPECT_STDERR` | same grammar as `EXPECT` (text + numeric) | checks **stderr** |
| `EXPECT_BYTE` | `EXPECT_BYTE <offset> <byte>` | raw byte at offset |
| `EXPECT_HEX` | `EXPECT_HEX <offset> "<hexstring>"` | raw byte range |
| `EXPECT_REGEX` / `EXPECT_REGEX_STDERR` | `EXPECT_REGEX "<pattern>"` | Python `re.search()`, compiled at parse time |
| `END` | `END` | Required, last statement |

**Binding rule:** every `EXPECT*` applies to the nearest preceding `RUN` — no by-name/number reference to an earlier `RUN`.

Minimal example:
```text
TEST "NVMe Identify"
RUN "nvme id-ctrl {{device}}"
EXPECT_EXIT 0
END
```

Any parse error (missing `TEST`/`END`, `EXPECT` before any `RUN`, bad keyword, unclosed
`PARALLEL`, bad regex, bad `CAPTURE` name, etc.) raises with an exact line number and no `.log`
is written:
```
line 5: expected an integer for EXPECT_EXIT, got 'zero'
    > EXPECT_EXIT zero
```

## 4. YAML Configuration

`config/config.yaml` is a **template only** — it has no effect unless passed via `--config`.
Without it, built-in defaults (identical to the file's values) are used.

| Key | Default | Meaning |
|---|---|---|
| `framework.log_directory` | `logs` | base dir for `logs/{run_id}/` |
| `framework.log_level` | `INFO` | console log level |
| `execution.command_timeout` | `300` | seconds, per iteration of every `RUN` |
| `variables.file` | `common_variables.json` | path to the JSON variables file |

```bash
python3 run.py tests/ --config config/config.yaml
```

## 5. Variables (`common_variables.json`)

```json
{"device": "/dev/nvme0", "expected_model": "Samsung", "expected_fw": "ABC123", "min_iops": 100000}
```

- Reference with `{{name}}` in a `RUN` command, `EXPECT ... CONTAINS "<value>"`,
  `EXPECT ... EQ/NEQ/GT/GE/LT/LE <number>`, or `EXPECT_REGEX "<pattern>"`.
- `EXPECT_EXIT`, `EXPECT_BYTE`, `EXPECT_HEX` do **not** support `{{...}}`.
- Missing variable → `VariableError`, reported as `ERROR`, no `.log` written.
- `RUN "..." CAPTURE <name>` creates/overwrites a variable at runtime from that command's
  stdout — usable by any **later** `RUN`/`EXPECT` in the same file (not by its own `RUN`'s
  own validations). Works even with no JSON file loaded.
- **Substitution is just-in-time**, per `RUN`, right before it executes — not all upfront. A
  bad reference in a later `RUN` is only caught once execution reaches it; earlier `RUN`s will
  already have run.
- `min_iops`: `EXPECT "read_iops" GE {{min_iops}}` — see §7/§8.

## 6. Command Examples

```text
RUN "nvme list"
RUN "nvme id-ctrl {{device}}"
RUN "nvme smart-log {{device}}"
RUN "lsblk"
RUN "fio --name=safe_smoke --filename=/tmp/test.bin --size=4M --rw=write --bs=4k --numjobs=1 --minimal"
```
Every command runs through the same `subprocess.run(cmd, shell=True, timeout=...)` path —
NVMe/FIO/Linux are not special-cased. Exit code, stdout, stderr are always captured as raw
bytes. Exceeding `command_timeout` → exit code `-1`, `[TIMEOUT after Ns]` appended to stderr.

## 7. Combined Automation

```text
TEST "Combined Health Check"

RUN "nvme id-ctrl {{device}}"
EXPECT_EXIT 0
EXPECT "Firmware Revision" NOT_EMPTY

RUN "nvme smart-log {{device}}"
EXPECT_EXIT 0
EXPECT "critical_warning" CONTAINS "0"

RUN "lsblk"
EXPECT_EXIT 0

RUN "fio --name=x --filename={{device}} --rw=randwrite --bs=4k --size=256M"
EXPECT_EXIT 0

END
```
All `RUN`s always execute regardless of earlier failures; a single failed validation anywhere
fails the whole test. See `tests/TC008_combined_safe.nvtest` (mock, actually runnable) and
`tests/examples/TC011_combined_identify_smart_fio.nvtest` (real hardware, destructive).

Parallel/loop stress pattern:
```text
PARALLEL
RUN "nvme id-ctrl {{device}}" LOOP 1000
EXPECT_EXIT 0
RUN "nvme reset {{device}}" LOOP 100
EXPECT_EXIT 0
END_PARALLEL
```
See `tests/TC011_parallel_stress.nvtest` (mock) /
`tests/examples/TC013_parallel_identify_reset_HARDWARE_REQUIRED.nvtest` (real).

FIO → command → FIO with numeric output validation (real fio, safe — targets a `/tmp` file).
fio's output isn't `field: number`-shaped by default, so `--output-format=json` is piped
through the common helper `tools/fio_metrics.py` (§7.1), which prints simple `field value` lines —
no fio-specific parsing exists in the framework; any command reducible to `field value` lines works the same:
```text
RUN "fio --name=w --filename=/tmp/f.bin --size=4M --rw=write --bs=4k --output-format=json | python3 tools/fio_metrics.py --metric write_iops --metric write_bw"
EXPECT_EXIT 0
EXPECT "write_iops" GE 1
EXPECT "write_bw" GT 0

RUN "lsblk"
EXPECT_EXIT 0

RUN "fio ... --rw=read ..."   # same pipe pattern
EXPECT_EXIT 0
EXPECT "read_iops" GE 1
```
See `tests/TC017_fio_numeric_output.nvtest`.

### 7.1 `tools/fio_metrics.py` — common fio helper

Turns fio's JSON into `field value` lines. **Every numeric field fio reports is exposed** (nothing is
hard-coded), so any IO scenario can validate any field. Paths are relative: run `run.py` from the `nvme_test/` directory.

```text
RUN "fio --name=m --filename={{fio_target}} --rw=randrw --rwmixread=70 --bs=4k --iodepth=32 --direct=1 --runtime=10 --time_based --output-format=json | python3 tools/fio_metrics.py"
EXPECT_EXIT 0
EXPECT "read_iops"        GE {{min_read_iops}}
EXPECT "read_clat_ns_p99" LT {{max_read_p99_ns}}
EXPECT "error"            EQ 0
```

| Usage | Result |
|---|---|
| `fio_metrics.py` | all fields, one `name value` per line |
| `fio_metrics.py --list` | field names only (run once to see the names for your fio version) |
| `--metric read_iops` | **only the number** — for `RUN ... CAPTURE var` |
| `--metric a --metric b` | `name value` lines for just those fields |
| `--job NAME\|N\|all` | choose a job (default `0`); `all` prefixes each name with its job name |
| `--skip-idle` | hide directions that did no IO (e.g. `write_*` of a read-only job) |
| `--int` | truncate values to integers |

Field names are `<direction>_<fio key path>`: `read_iops`, `read_bw` (KiB/s), `read_bw_bytes`, `read_io_bytes`,
`read_total_ios`, `read_lat_ns_mean`, `read_clat_ns_max`, `read_clat_ns_p99`, `read_clat_ns_p99_9`,
`read_iops_min/max/mean/stddev`, `read_bw_min/max/mean/dev` (same for `write_`, `trim_`), plus `total_iops`,
`total_bw`, `total_bw_bytes`, `total_io_bytes`, `total_ios` (read+write+trim) and job-level `error`, `usr_cpu`, `sys_cpu`, `ctx`.

Notes:
- Use the **full** field name in `EXPECT` (the framework matches a field as a substring on the first line containing it;
  the helper prints shorter names first, so `read_bw` never returns `read_bw_bytes`).
- Latencies are nanoseconds. Values never use exponent notation (the framework's number parser cannot read `1e-05`).
- With `numjobs` above 1 add `--group_reporting` to fio so one aggregated job is reported.
- Compare two runs: `CAPTURE` one value with `--metric`, then `RUN "echo iops_after {{iops_after}}"` and
  `EXPECT "iops_after" LT {{iops_before}}` (see `tests/user_scenarios/a_io_speed_drop.nvtest`).
- If fio fails there is no JSON: the helper exits `2` with a message on stderr, so `EXPECT_EXIT 0` catches it.
- `tools/fio_iops.py` is kept for older tests (prints total IOPS as an integer); prefer `fio_metrics.py --metric total_iops`.

## 8. Validation Reference

| Validator | Semantics |
|---|---|
| `EXPECT_EXIT` | exact int equality |
| `EXPECT .. CONTAINS` | first line of stdout containing `<field>` must also contain `<value>` |
| `EXPECT .. NOT_CONTAINS` | `<field>` absent anywhere in stdout |
| `EXPECT .. NOT_EMPTY` | text after `<field>` (past `:`/`=`) on its line is non-blank |
| `EXPECT .. EQ/NEQ/GT/GE/LT/LE <number>` | first number found after `<field>` on its line, compared numerically |
| `EXPECT_STDERR ..` | identical semantics (text + numeric), against stderr |
| `EXPECT_BYTE <off> <byte>` | `stdout[off] == byte` |
| `EXPECT_HEX <off> "<hex>"` | `stdout[off:off+len]` equals the hex bytes |
| `EXPECT_REGEX[_STDERR] "<pattern>"` | `re.search(pattern, text)` — not anchored/fullmatch |

Numeric example: `EXPECT "read_iops" GE 100000` matches `read_iops : 125000`, `IOPS=125000`,
etc. — any number after the field text. `<number>` may be `{{var}}` (e.g. `EXPECT "read_iops"
GE {{min_iops}}`); non-numeric/unresolved or missing-field fails cleanly, not a crash. Not
implemented: `EQUALS` (exact whole-output match). All validations always run; one failure
anywhere → test `FAIL`. `LOOP`ed `RUN`s check every iteration and aggregate in the `.log` (§11).

## 9. Binary Output / Passthru

```text
RUN "nvme admin-passthru {{device}} --opcode=0x06 --data-len=4096 --read --raw-binary"
EXPECT_EXIT 0
EXPECT_BYTE 0x00 0x01
EXPECT_HEX 0x10 "12345678"
```
- A command's output is hex-dumped in the `.log` automatically **iff** any `EXPECT_BYTE`/`EXPECT_HEX` is bound to it; otherwise shown as text.
- `EXPECT_BYTE`/`EXPECT_HEX` validate the **raw bytes**, never the rendered hex-dump text.
- No `.bin` file is ever created — hex dump lives only inside the `.log` (format: `00000000  01 00 00 00 4e 56 4d 65  ....NVMe`, see §11).

## 10. Running Tests (`run.py`)

```
usage: run.py [-h] [--config CONFIG] [--log-level {DEBUG,INFO,WARNING,ERROR}] [--dry-run] path
```
- `path`: one `.nvtest` file, or a directory (non-recursive `*.nvtest` glob; other files ignored silently).
- `--config PATH`: load YAML (§4); missing file/PyYAML → exit `2`.
- `--log-level`: overrides console level only for this run.
- `--dry-run`: parses targets, reports `N command(s)`, executes nothing, writes no logs.
- Exit codes: `0` all passed/none found, `1` any FAIL/ERROR, `2` usage/path/config error.
- **No arguments at all** runs the framework's own internal self-verification suite (real
  `lsblk`/`fio` calls against `/tmp`, no hardware) — not a help message.

## 11. Logs

One `logs/{run_id}/` directory per invocation (`YYYYMMDD_HHMMSS_<8-hex>`), shared by every test
run in that invocation, plus one `run.log` (framework diagnostics, not a test result).
`.log` filename = source `.nvtest` file's own name (`TC001_success.nvtest` → `TC001_success.log`), independent of the `TEST "..."` label.

```
========================================
TEST: Mock Identify Controller - Successful Validation
START: 2026-08-26 02:06:04
========================================

COMMAND:
printf 'Model Number   : Samsung SSD 970 EVO\nFirmware Revision : 2B2QEXM7\n'

EXIT CODE:
0

OUTPUT:
Model Number   : Samsung SSD 970 EVO
Firmware Revision : 2B2QEXM7

VALIDATION:
[PASS] Exit code == 0
[PASS] "Model Number" contains "Samsung"
[PASS] "Firmware Revision" is not empty
[PASS] "ERROR" not present in output

RESULT:
PASS

END: 2026-08-26 02:06:04
========================================
```
A `LOOP`ed/`PARALLEL` command's block instead shows `LOOP COUNT:`, `PARALLEL GROUP:` (if
applicable), `ITERATIONS RUN:`, `EXIT CODE (last iteration):`, `OUTPUT (last iteration):`, and
`FIRST FAILURE:` (`(none)` or `Iteration N: <message>`).

## 12. PASS/FAIL Behavior

- All `RUN`s execute regardless of earlier failures; all validations always run.
- A `LOOP`ed validation aggregates: `[FAIL] Exit code == 0 across 20 iterations: 14 passed, 6 failed (first failure at iteration 1: Exit code == 0 (got 1))`.
- Test `PASS` iff every validation (every iteration, for loops) passed.

## 13. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `line N: ...`, no `.log` | invalid `.nvtest` syntax | fix per the quoted line |
| `Unknown variable {{x}}` | not in JSON and never `CAPTURE`d earlier | fix name or add/capture it |
| `Unsupported test file type '.x'` | non-`.nvtest` path given directly | rename or point elsewhere |
| Editing `config/config.yaml` has no effect | not auto-loaded | pass `--config config/config.yaml` |
| `[TIMEOUT after Ns]` | exceeded `command_timeout` | raise it via `--config`, or fix the hang |
| `EXPECT "field"` fails but text is visible | it's on stderr | use `EXPECT_STDERR` |
| `a PARALLEL block must contain at least 2 RUN statements` | only 1 `RUN` inside | add one or remove `PARALLEL` |
| `invalid regex ...` | bad `re` pattern | fix syntax (Python regex, not shell glob) |
| `EXPECT_BYTE ... offset beyond N captured bytes` | output shorter than expected | check `BINARY OUTPUT: Size:` |
| `expected a number for EXPECT ... GE, got '...'` | non-numeric literal after a numeric operator | use a plain number or `{{var}}` |
| Test with `RUN "reboot"` leaves no `.log` and no result | the host reboot killed the framework process; the `.log` is only written when a test finishes | split into pre/post-reboot files (§15) |

## 14. Best Practices

- Small, specific validations — every one is reported individually regardless of count.
- Put shared values in `common_variables.json`; use `CAPTURE` for values only known at runtime.
- Use mock commands (`printf`, `python3 -c`, file-based `fio`) while developing a test, then
  switch to real `nvme`/device paths.
- Never rely on directory auto-discovery to run a destructive/hardware-required test — invoke
  it by exact path.
- Two `RUN`s in the same `PARALLEL` block should not `CAPTURE` into the same variable name (race).

## 15. Reboot / Power-Cycle Tests

**A host reboot cannot be a `RUN` inside a test.** `RUN "reboot"` takes the framework process down
with the machine, and:

- the `.log` is written once, when the test *finishes* — a test that reboots mid-way produces **no log and no result**;
- `CAPTURE` variables live in RAM — they are **lost** at reboot;
- validations that ran before the reboot are lost with the log.

**Pattern: two `.nvtest` files, reboot between them, state carried on disk.**

| Part | What it does |
|---|---|
| Phase 1 file (`b1_pre_reboot.nvtest`) | set the feature (`-s` = save), verify with get-feature, write the value to a state file |
| Wrapper (`run_reboot_test.sh`) | runs phase 1; if it passed, installs a one-shot service that runs phase 2 after boot; reboots |
| Phase 2 file (`b2_post_reboot.nvtest`) | reads the state file, runs get-feature, compares with the pre-reboot value |

Phase 1 (ends *before* the reboot — the wrapper does the reboot, not the test):
```text
TEST "Reboot persistence - PRE"
RUN "nvme set-feature {{device0}} -f {{feature_id}} -v {{set_value}} -s"
EXPECT_EXIT 0
RUN "nvme get-feature {{device0}} -f {{feature_id}}"
EXPECT "Current value" CONTAINS "{{set_value_hex}}"
RUN "nvme get-feature {{device0}} -f {{feature_id}} | awk -F: '/Current value/{print $NF}'" CAPTURE pre_value
RUN "echo '{{pre_value}}' > {{state_file}}"
EXPECT_EXIT 0
END
```
Phase 2 (after boot):
```text
TEST "Reboot persistence - POST"
RUN "cat {{state_file}}" CAPTURE pre_value
EXPECT_EXIT 0
RUN "nvme get-feature {{device0}} -f {{feature_id}}"
EXPECT "Current value" CONTAINS "{{pre_value}}"
RUN "nvme get-feature {{device0}} -f {{feature_id}} -s 2"      # the SAVED copy (select=2)
EXPECT "Current value" CONTAINS "{{pre_value}}"
END
```

Rules:
1. Never put the reboot in a `.nvtest`. End phase 1 before it; the wrapper reboots.
2. Anything phase 2 needs must be on disk. Define the path as a variable (`state_file` in the variables JSON), write with
   `echo '{{var}}' > {{state_file}}`, read back with `RUN "cat {{state_file}}" CAPTURE var`. Pick a path that survives
   reboot (`/var/tmp`, not `/tmp`).
3. Phase 1 and phase 2 are separate invocations: separate `logs/{run_id}/` directories and separate PASS/FAIL results.
   After boot the phase 2 result is in `logs/<run_id>/b2_post_reboot.log` and `journalctl -u nvme-post-reboot`.
4. If phase 1 fails, the wrapper stops before rebooting and installs nothing. The post-reboot service disables itself before it runs
   (`ExecStartPre`), so it runs once even if phase 2 fails.
5. After a reboot the controller may take a while to enumerate. The service waits up to ~120 s for `{{device0}}` to appear.
6. Compare with `CONTAINS "{{pre_value}}"` rather than parsing numbers: the exact get-feature text depends on your
   nvme-cli version (check `nvme get-feature -h`; `-s` is *save* for set-feature but *select* for get-feature).

Run it (root + systemd needed; run from any directory):
```bash
sudo ./tests/user_scenarios/run_reboot_test.sh              # phase 1 -> reboot -> phase 2 after boot
DRY_RUN=1 ./tests/user_scenarios/run_reboot_test.sh         # runs phase 1, writes the unit to /tmp, no systemctl, no reboot
sudo ./tests/user_scenarios/run_reboot_test.sh cleanup      # remove the systemd unit and the state file (see below)
```

### Cleanup

The wrapper changes systemd and leaves a state file. Both have a cleanup path:

| What changed | Created by | Cleaned by |
|---|---|---|
| unit file `/etc/systemd/system/nvme-post-reboot.service` + `daemon-reload` | wrapper, after phase 1 passes | next wrapper run (stale leftovers), automatically if the wrapper fails / is interrupted / `reboot` fails, or `cleanup` |
| enable symlink (`multi-user.target.wants`) | `systemctl enable` | the service disables itself when phase 2 starts; also `cleanup` |
| `failed` state of the unit | a failed phase 2 | `systemctl reset-failed` (same places as the unit file) |
| state file (`state_file`, e.g. in `/var/tmp`) | phase 1 | `cleanup` |

Notes:
- Run `cleanup` after you have looked at the phase 2 result: it deletes the state file that phase 2 compared against.
- `logs/` and the **journal** are never touched: `journalctl -u nvme-post-reboot` only *reads* the journal; do not vacuum it.
- **Changes a test makes to the drive stay with the test.** A command such as `set-feature ... -s` changes the drive
  (`-s` = saved across reboot) and neither the wrapper nor `cleanup` reverts it. If a test must leave the drive as it
  found it, put the restore in that `.nvtest`.
- The wrapper clears its failure trap just before `reboot`, because systemd's shutdown sends SIGTERM to it and that
  would delete the unit needed after boot.

**When no split is needed:** if only the *device* is power-cycled or reset (Quarch, `nvme reset`, subsystem reset) and the
host stays up, the framework process survives — keep it in one file: `RUN` the power-cycle/reset command, `RUN "sleep N"`,
then continue with the verification `RUN`s. Only a **host** reboot needs the two-phase pattern.

## 16. NVMe Test-Case Suite and Helper Tools

`tests/nvme_suite/` automates TC01–TC10 (+TC12, TC14) of the NVMe test-case document and doubles as a template library — see
`tests/nvme_suite/README.md` for the pattern catalog and the framework rules it relies on. Helpers in `tools/`:

| Tool | Purpose |
|---|---|
| `fio_metrics.py` | fio JSON → `field value` lines (§7.1) |
| `nvme_util.py` | nvme-cli JSON/text → `field value` lines; SMART snapshots + deltas; id-ns/list-ns/feature/fw-log/sanitize decoders; deterministic data patterns |
| `wait_until.sh` | `wait_until.sh TIMEOUT INTERVAL 'cmd'` — poll until `cmd` succeeds (controller back after reset, sanitize done, ...) |
| `guard.sh` | `guard.sh <yes\|no> cmd...` — runs destructive commands only when `allow_destructive=yes` (the framework has no skip statement) |

Two framework facts worth remembering: every `RUN` executes even after a failure, and `{{var}}` is **not** substituted in the field name of an `EXPECT`.
