# Saleae Logic 8 Automation Wrapper

A lightweight Python 3.8 wrapper around the Saleae Logic 2 Automation API, built for
**headless Ubuntu 20.04 CLI automation** (no GUI, no X11, no desktop interaction). It is
designed to drop into an existing test/automation framework (e.g. an NVMe SSD test rig)
as a small set of importable modules.

## Files

| File                    | Purpose                                                                 |
|-------------------------|--------------------------------------------------------------------------|
| `saleae_wrapper.py`     | Core wrapper: connection lifecycle, capture control, markers, exports, waveform/plot shortcuts, SPI/I2C/UART analyzer wrappers |
| `waveform.py`           | Waveform analysis: edges, transitions, timing metrics, multi-format coordinate export |
| `plotting.py`           | Headless (matplotlib `Agg`) waveform plotting — PNG/SVG only, never opens a window |
| `exceptions.py`         | Custom exception hierarchy shared by all of the above                   |
| `config.yml`            | Default configuration (connection, capture, export, reliability settings) |
| `test_saleae_wrapper.py`| Runnable unit/regression test suite (stdlib `unittest`, no hardware needed) |

Everything is intentionally kept to a handful of files with no required GUI dependency —
`matplotlib.use("Agg")` is set before `pyplot` is ever imported, so `plotting.py` never
touches a display, even over SSH with no desktop environment installed.

## Requirements

```bash
pip install logic2-automation pyyaml numpy matplotlib
pip install pandas   # optional, only needed for Waveform.to_dataframe()
```

Assumes Logic 2 is already installed on the host (`config.yml` → `logic2_binary` points at
its executable) and Python 3.8+ is available.

## Quick start

```python
from saleae_wrapper import Saleae

with Saleae() as trace:                      # loads config.yml, connects, reserves device
    trace.mark("Before reset")
    trace.capture(seconds=2)                  # blocking timed capture
    trace.mark("After reset")

    trace.export_csv()                        # raw CSV via the SDK
    trace.export_json()                       # session metadata + markers as JSON

    print(trace.frequency(channel=0))         # waveform shortcut methods
    print(trace.statistics(channel=0))

    trace.plot(channels=[0, 1], start=0, end=0.05, save="wave.png")

    spi = trace.spi(mosi=0, miso=1, clock=2, enable=3)
    print(spi.transactions())
    print(spi.errors())
# connection, device, and any open capture are released automatically on exit
```

## Configuration (`config.yml`)

| Key                | Meaning                                                              |
|---------------------|-----------------------------------------------------------------------|
| `host` / `port`     | Where the Logic 2 Automation Server listens                          |
| `launch_logic2`     | If `true`, launch Logic 2 headlessly when the server isn't reachable |
| `logic2_binary`     | Path to the Logic 2 executable                                       |
| `startup_timeout`   | Seconds to wait for the Automation API to become ready               |
| `sample_rate`       | Digital sample rate, Hz                                              |
| `digital_channels` / `analog_channels` | Channel indices to enable                        |
| `capture_duration`  | Default capture length, seconds                                      |
| `export_directory`  | Where CSV/JSON/`.sal`/image exports land by default                  |
| `retry_count` / `timeout` | Connection retry attempts / per-operation timeout               |
| `plot_dpi` / `figure_size` | Default plot resolution / figure size                          |

## Capture lifecycle

```python
trace.start()                          # manual capture, runs until stop()
trace.stop()
trace.pause()                          # ends the current segment (no true HW pause exists)
trace.resume()                         # opens a new segment
trace.capture(seconds=2)               # one-shot blocking timed capture
trace.capture(trigger_channel=0, trigger_type="rising")   # trigger-based, if SDK supports it
```

> **Note on `pause()`/`resume()`:** the Logic 8 hardware has no true in-place pause. `pause()`
> finalizes the current capture segment; `resume()` starts a new one. Data across a
> pause/resume is therefore two (or more) stitched segments, not one continuous trace.

## Markers

```python
trace.mark("After Reset")
trace.mark("Power Cycle")
trace.get_markers()      # list of Marker(label, timestamp, session_elapsed, segment_elapsed)
```

Markers are timestamped, kept in memory, logged immediately, included in `export_json()`,
and overlaid automatically on `trace.plot()` output.

## Waveform analysis

```python
wave = trace.waveform()                 # exports + parses the last capture
wave.frequency(channel=0)
wave.duty_cycle(channel=0)
wave.rise_time(channel=0)               # analog channels only — raises WaveformError on digital
wave.statistics(channel=0)              # every metric in one dict
wave.coordinates(channel=0)             # list[{"time","channel","value"}]
wave.to_json(); wave.to_csv(); wave.to_numpy(); wave.to_dataframe()
```

Shortcut methods on `trace` (`bandwidth`, `frequency`, `period`, `edge_count`, `high_time`,
`low_time`, `statistics`) build/reuse this same cached `Waveform` automatically.

## Plotting

```python
trace.plot()                                            # all channels, auto-named PNG
trace.plot(channels=[0, 1], start=0, end=0.05, save="wave.png")
trace.save_image("wave.png")                             # native SDK export if available, else trace.plot()
```

Supports PNG/SVG (by file extension), configurable DPI/figure size, time-range cropping,
marker annotations, per-channel labels, and an automatic title — always rendered headlessly.

## Protocol analyzers (SPI / I2C / UART)

```python
spi = trace.spi(mosi=0, miso=1, clock=2, enable=3)
spi.transactions()      # SDK's own decoded rows, as a list of dicts
spi.errors()            # generic filter over any exported column containing "error"

i2c = trace.i2c(sda=0, scl=1)
uart = trace.uart(channel=0, bit_rate=115200)
```

These wrap the SDK's own `add_analyzer()` / `export_data_table()` calls — they do **not**
decode protocol data themselves. Each class carries a `# TODO` marker for future
protocol-aware helpers (e.g. grouping SPI transactions by chip-select), so the analyzer
layer stays modular and easy to extend without touching the public API.

---

## Troubleshooting: connecting to Saleae

| Symptom | Likely cause | What to check / do |
|---|---|---|
| `ConnectionError: ... 'saleae' automation client library is not installed` | `logic2-automation` isn't installed in this Python environment | `pip install logic2-automation`; confirm you're in the right venv/interpreter |
| `ConnectionError: Automation Server not reachable at host:port and 'launch_logic2' is disabled` | Logic 2 isn't running with the Automation API enabled | Start Logic 2 manually with the Automation API on, or set `launch_logic2: true` in `config.yml` |
| `ConnectionError: logic2_binary not found: ...` | `logic2_binary` in `config.yml` points to a path that doesn't exist | Fix the path (`which Logic` / check your install location, e.g. `/opt/Logic/Logic`) |
| `TimeoutError: Automation API did not become ready within Ns` | Logic 2 was launched but its Automation API server didn't come up in time | Increase `startup_timeout`; check Logic 2 actually starts standalone (`<binary> --automation`) without hanging/prompting; check `dmesg`/USB permissions if the app itself is stalling on device enumeration |
| `ConnectionError: Could not connect to Automation API after N attempt(s): ...` | Server is up but something else is blocking the connection (firewall, wrong port, another process already bound) | Verify `host`/`port` in `config.yml` match Logic 2's actual Automation API port; check `netstat -tlnp \| grep <port>`; check no firewall/iptables rule blocks localhost traffic; increase `retry_count` |
| `CaptureError: No Saleae devices found on the Automation Server` | Logic 2 is running but no Logic 8 is detected | Check the device is physically connected via USB; check USB permissions (`udev` rules — Saleae devices often need a rule granting non-root USB access on Linux); try unplug/replug; confirm the device shows up in Logic 2 itself |
| Connects fine but hangs/fails only when run via cron/systemd/CI | Headless session lacks environment Logic 2 expects, even though the Automation API itself shouldn't need a display | Confirm you're launching Logic 2 in its documented headless/automation mode, not accidentally invoking a GUI-mode binary; check the service's environment matches an interactive shell where it's known to work |
| Works locally but not over SSH from another machine | `host` in `config.yml` is `127.0.0.1` but you're connecting remotely, or vice versa | Match `host` to where Logic 2's Automation Server is actually bound; the Automation API is not designed as a general remote-network service — the wrapper and Logic 2 normally need to run on the same host |
| Intermittent connection failures under load | Automation Server briefly busy/still initializing when the first attempt lands | Increase `retry_count`; the wrapper already backs off exponentially (up to 10s) between attempts — check the logs for how many attempts it actually took |
| Everything above looks fine but it *still* won't connect | Version mismatch between the installed `logic2-automation` client and the running Logic 2 application | Check `saleae-automation client library version` in the logs, and compare against your Logic 2 app version; update one or the other to matching supported versions |

General debugging tips:
- The wrapper logs every step of `connect()` (config loaded, port check, launch, retry
  attempts with timing, device discovery) at `INFO` level — run with default logging and
  read the log line immediately before the exception; it almost always names the exact
  step that failed.
- Try the same sequence manually outside the wrapper: start Logic 2, confirm its Automation
  API is listening (`nc -zv 127.0.0.1 10430` or your configured port) before ever running
  the wrapper — this isolates "Logic 2 problem" from "wrapper problem".
- If a call raises `CaptureError`/`ExportError` referencing an SDK method (`add_analyzer`,
  `export_data_table`, `save_capture`, etc.) as unsupported, that method wasn't found on the
  installed SDK's `Capture` object — verify your `logic2-automation` version actually
  exposes it; the wrapper is written to probe for capability rather than assume one fixed
  API shape.

---

## Running the tests

```bash
python3 -m unittest test_saleae_wrapper -v
# or, if pytest is installed:
pytest test_saleae_wrapper.py -v
```

The suite stubs `saleae.automation` in `sys.modules` before importing `saleae_wrapper`, so
it runs with **no real Saleae hardware and no `logic2-automation` package installed** —
it validates config handling, capture-lifecycle state transitions, marker recording,
waveform metric correctness (against synthetic, hand-checked CSV data), headless plotting,
and the analyzer wrapper plumbing.

| # | Test | What it proves |
|---|---|---|
| 1 | `test_01_valid_config_loads` | `config.yml` parses into the correct `SaleaeConfig` fields |
| 2 | `test_02_missing_file_raises_configuration_error` | Missing config file fails clearly, not silently |
| 3 | `test_03_invalid_port_raises_configuration_error` | Out-of-range port is rejected at config-validation time |
| 4 | `test_04_no_channels_raises_configuration_error` | Config with no enabled channels is rejected |
| 5 | `test_05_is_port_open_true_and_false` | Port-detection logic correctly distinguishes open vs. closed |
| 6 | `test_06_connect_raises_when_server_unreachable_and_launch_disabled` | `connect()` fails fast and clearly when Logic 2 isn't reachable |
| 7 | `test_07_start_stop_records_one_segment` | Basic capture lifecycle finalizes exactly one segment |
| 8 | `test_08_pause_resume_creates_two_segments` | `pause()`/`resume()` correctly stitch segments as documented |
| 9 | `test_09_stop_without_active_capture_does_not_raise` | Defensive `stop()` is safe to call during cleanup |
| 10 | `test_10_pause_without_running_capture_raises_capture_error` | `pause()` enforces its precondition |
| 11 | `test_11_mark_records_expected_fields` | Markers capture label + timestamps correctly |
| 12 | `test_12_digital_frequency_period_duty_cycle` | Frequency/period/duty-cycle math is correct against known synthetic data |
| 13 | `test_13_rise_time_raises_on_digital_channel` | Rise time is never fabricated for digital-only channels |
| 14 | `test_14_analog_rise_time_matches_known_ramp` | 10%-90% rise-time measurement is numerically correct |
| 15 | `test_15_coordinate_export_formats_agree_on_point_count` | list/JSON/CSV/NumPy/DataFrame exports are all consistent |
| 16 | `test_16_plot_waveform_saves_png` | Headless plotting produces a real, non-empty PNG |
| 17 | `test_17_plot_waveform_rejects_unsupported_format` | Bad output extensions are rejected before writing anything |
| 18 | `test_18_spi_transactions_and_errors` | Analyzer export/parse and generic error-filtering work correctly |
| 19 | `test_19_analyzer_raises_without_last_capture` | Analyzer setup enforces its "capture exists" precondition |

All 19 tests currently pass against this codebase.

## Known limitations / things to verify against your real SDK install

- Exact SDK method/argument names (`ManualCaptureMode`, `DigitalTriggerCaptureMode`,
  `add_analyzer`, `export_data_table`, `save_capture`, native image export) were written
  against the documented shape of the Logic 2 Automation API, not executed against real
  hardware. The wrapper fails loudly (`CaptureError`/`ExportError`/`AnalyzerError`) rather
  than silently if any of these differ in your installed SDK version — a quick smoke test
  against real hardware is recommended before production use.
- `rise_time()`/`fall_time()`/true bandwidth measurement require **analog** channel data;
  purely digital channels raise `WaveformError` by design rather than returning a
  meaningless number.
- No protocol decoding, offline waveform processing, HTML report generation, or AI/ML
  functionality is implemented — by design, per project scope.
