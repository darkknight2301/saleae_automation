# NVMe test-case suite — automation reference

TC01–TC10 from the "NVMe Testing – Test Case Document", plus TC12 and TC14 from its extended list, as `.nvtest` files.
Each file documents (in comments) what it does and which reusable **pattern** it demonstrates; use them as templates.

## Run
```bash
./tests/nvme_suite/run_mock.sh                                   # whole suite against the simulator in mock/ (no hardware)
MOCK_CORRUPT_READ=1 ./tests/nvme_suite/run_mock.sh               # inject a fault: the matching test must FAIL (switches: mock/bin/mocklib.py)

# real hardware: edit tests/nvme_suite/variables.json (ctrl, ns, fw_image, thresholds ...), then
python3 run.py tests/nvme_suite/TC01_set_get_feature_ctrl_reset.nvtest --config config/nvme_suite_config.yaml
```
`allow_destructive` defaults to **no**: every command that writes/erases/resets-with-data-loss is wrapped in `tools/guard.sh` and is
refused (exit 77, the test FAILs visibly) until you set it to `yes`. Use a dedicated drive. `mock/` is a simulator for validating the
*framework logic* only — it is not NVMe-compliant.

## Test cases and the patterns they demonstrate
| TC | File | Patterns |
|---|---|---|
| 01 Set/Get Feature + Reset | `TC01_set_get_feature_ctrl_reset` | CAPTURE "before", precondition as numeric EXPECT, event-based recovery wait, compare after-reset to captured value |
| 02 Write/Read + Feature + SMART | `TC02_write_read_feature_smart` | deterministic data files + `cmp`, buffer size from id-ns, SMART baseline `save` / `delta-from` |
| 03 FIO + SMART | `TC03_fio_workloads_smart` | fio JSON → numeric EXPECTs on any fio field, per-phase SMART deltas (`--update`), guarded destructive fio |
| 04 Namespace lifecycle | `TC04_namespace_lifecycle` | ID captured from command output (sed), `ns-present` verification, dynamic cntlid/nsid, wait for device node |
| 05 Multiple features | `TC05_multi_feature_validation` | one CAPTURE per item, "untouched items unchanged", **rejection check** (`; echo exit_status $?` + `EXPECT_STDERR`), restore |
| 06 Reset during I/O | `TC06_reset_during_active_io` | `PARALLEL` (fio + delayed reset), sentinel data outside the I/O region, restart I/O, geometry unchanged |
| 07 SMART/Error log stress | `TC07_smart_errorlog_stress` | counter deltas, error-log entry count before/after, temperature limit |
| 08 Format LBA formats | `TC08_format_lba_formats` | format index discovered from id-ns, invalid index rejected, I/O re-sized to the new LBA size |
| 09 FW download/commit/reset | `TC09_fw_download_commit_reset` | preconditions, **hook variable** for hardware-specific step (`fw_activate_cmd`), string compare with `CONTAINS "{{var}}"` |
| 10 Sanitize | `TC10_sanitize` | start long operation + poll with timeout (`wait_until.sh`), bit-field decode in shell arithmetic |
| 12 R/W across reset | `TC12_rw_across_reset` | smallest "data survives event" test |
| 14 QD variation | `TC14_queue_depth_scaling` | parameter sweep in one RUN (`for`), trend assertion (`monotonic`) |

## Pattern catalog (copy these)
| Need | Recipe |
|---|---|
| Value from a command into a variable | `RUN "<cmd> \| python3 tools/nvme_util.py <sub> --bare" CAPTURE name` (`--bare` = just the value) |
| Any JSON field as a numeric check | `nvme ... -o json \| python3 tools/nvme_util.py fields` then `EXPECT "field" GE {{limit}}` (`--list` shows names) |
| Hex / string values | normalise in the RUN: `$(printf '%d' 0x1f)`, `nvme_util.py feature-value`, `kv KEY`; compare strings with `CONTAINS "{{var}}"` |
| Counter changed by at least N | baseline `... \| nvme_util.py save FILE`, later `... \| nvme_util.py delta-from FILE` → `EXPECT "d_<field>" GE N` |
| Two values compared | `RUN "echo ok $(( {{a}} == {{b}} ))"` then `EXPECT "ok" EQ 1` |
| Command must FAIL (negative test) | `RUN "<cmd>; echo exit_status $?"`, `EXPECT_EXIT 0`, `EXPECT "exit_status" NEQ 0`, `EXPECT_STDERR "NVMe status" CONTAINS "..."` |
| Wait for something | `RUN "tools/wait_until.sh <timeout> <interval> '<cmd that succeeds when ready>'"` + `EXPECT_EXIT 0` |
| Concurrent activity | `PARALLEL` … `END_PARALLEL` (2+ RUNs) |
| Repeat / sweep | `RUN "…" LOOP n` (same command n×) or a shell `for` inside one RUN printing `name value` lines |
| Destructive command | `RUN "tools/guard.sh {{allow_destructive}} <cmd>"` |
| Hardware-specific action (power cycle, vendor tool) | put the command in a variable (`fw_activate_cmd`) and `RUN "{{fw_activate_cmd}}"` |
| Host reboot | two-phase test + wrapper (USER_GUIDE §15) |
| Precondition / capability gate | numeric EXPECT on a computed line (`oacs`, `frmw`, `sanicap` bits) — it FAILs the test early and visibly |

## Framework rules these tests rely on (found while building them)
* **Every `RUN` always executes**, even after an earlier failure — there is no skip/abort. Hence `guard.sh`, preconditions as EXPECTs,
  and commands that use captured IDs (an empty ID makes the nvme command fail instead of hitting another namespace).
* **`{{var}}` is substituted in commands, numeric operands and `CONTAINS` values — NOT in the field name of an `EXPECT`.**
  `EXPECT "{{x}}" NOT_CONTAINS` would pass vacuously. Do the check in the command and report it under a fixed field name (see TC04 `node_listed`).
* `EXPECT_EXIT` is an exact match; a pipeline's exit status is the **last** command's, so every `nvme_util.py` subcommand exits 2 on empty/invalid
  input (a failed `nvme` command then fails the step).
* `EXPECT "<field>" …` uses the **first line containing the field as a substring**; `fio_metrics.py`/`nvme_util.py` order names so plain names work.
  The numeric parser reads decimal only (`0x1f` → 0) — convert hex in the RUN.
* `EXPECT`s bind to the nearest preceding `RUN`; there is no iteration over a list except `LOOP n`.

## Differences from the manual document (spec / nvme-cli corrections applied)
* `nvme write/read`: `--block-count` is **zero-based** (7 = 8 blocks) and `--data-size` is required → size is computed from the real LBA size.
* TC04: **Format NVM requires an attached namespace**; order is create → attach → format → detach → delete (not detach → format). nsid/cntlid are dynamic.
* `nvme format` needs `--force` when non-interactive.
* TC01: a feature set **without** `-s` reverts to its saved/default value at a Controller Level Reset — that is the oracle; a saved feature
  (`-s`) must persist (check with `get-feature -s 2`). Capabilities: `get-feature -s 3`.
* TC09: commit action 1 = replace + activate at next reset; whether a controller reset suffices or a power cycle is needed is drive-specific
  (hook variable). Slot 1 may be read-only (FRMW bit 0). `fw-log` slot strings are hex-encoded; the revision is read from id-ctrl `fr`.
* SMART JSON `temperature` is Kelvin; data units are 512000-byte units and can lag; error-log may hold benign entries (`max_new_error_entries`).
* TC10: sanitize status/progress decoded from SSTAT[2:0] / SPROG; content after block erase is unspecified, so it is not asserted.

## Remaining test cases from the extended list — how they map (not implemented here)
TC11 power-cycle persistence = TC01 with the reset replaced by a hook variable (Quarch/PDU command) and `-s`; TC13 multi-namespace I/O = TC04 recipes + a
`PARALLEL` of fio per namespace + `cmp`; TC15 error injection / TC16 abort = vendor/passthru command in a RUN (+ `EXPECT_STDERR`), then error-log delta;
TC17 parallel namespace ops = `PARALLEL` (fio on one NS, TC04 flow on another); TC18 power management = TC05 on feature 0x02 + latency/power reads;
TC19 log pages = `nvme_util.py fields` over each log; TC20 long stress = TC07 with a long runtime variable (run via systemd/screen, not interactively).

## Validated vs not validated
* Validated: all 12 tests parse and PASS against the simulator (47 s), and each FAILs on its injected fault (corrupt read, lost data on reset,
  unpersisted/ignored settings, media errors, slow I/O, firmware not activating, failed sanitize, ignored format, QD collapse); destructive guard refuses with `allow_destructive=no`.
* **Not validated on real hardware.** On your first run check, with `--list`/the raw command: JSON field names (`cntlid`, `oacs`, `frmw`, `sanicap`, `sprog`, `sstat`,
  `error_count`, `lbafs`, `flbas`, `nsid_list`), the text of `get-feature` (`...value:0x...`), `fw-log` (`afi`), `create-ns` (`created nsid:N`), and the
  success text of `fw-download`/`fw-commit` — they vary between nvme-cli versions. All of them are isolated in `tools/nvme_util.py`, so a mismatch is a one-place fix.
