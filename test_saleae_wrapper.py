"""
@Author         : Vedang
@Description    : Runnable regression/unit test suite for saleae_wrapper.py, waveform.py, and plotting.py, using fake SDK stubs so it runs without real Saleae hardware or the logic2-automation package installed.
@Input          : None            - run via `python3 -m unittest test_saleae_wrapper.py -v` or `pytest test_saleae_wrapper.py`
@Output         : None            - prints pass/fail results to stdout via unittest's runner
@Note           : Stubs saleae.automation in sys.modules before importing saleae_wrapper, so this file must import saleae_wrapper only after the stub is installed (see top of file).
"""

import csv
import json
import shutil
import socket
import sys
import tempfile
import types
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Install a fake 'saleae.automation' module BEFORE importing saleae_wrapper,
# so these tests run without the real logic2-automation SDK or any hardware.
# ---------------------------------------------------------------------------
_saleae_pkg = types.ModuleType("saleae")
_automation_mod = types.ModuleType("saleae.automation")


class _FakeManualCaptureMode:
    """
    @Author         : Vedang
    @Description    : Stand-in for automation.ManualCaptureMode used only by the test suite's fake SDK.
    @Input          : None
    @Output         : None
    @Note           : Test infrastructure only - not part of the shipped library.
    """


class _FakeTimedCaptureMode:
    """
    @Author         : Vedang
    @Description    : Stand-in for automation.TimedCaptureMode used only by the test suite's fake SDK.
    @Input          : duration_seconds - capture length in seconds
    @Output         : None
    @Note           : Test infrastructure only - not part of the shipped library.
    """

    def __init__(self, duration_seconds):
        """
        @Author         : Vedang
        @Description    : Stores the fake timed capture-mode duration.
        @Input          : duration_seconds - capture length in seconds
        @Output         : None
        @Note           : Test infrastructure only.
        """
        self.duration_seconds = duration_seconds


class _FakeCaptureConfiguration:
    """
    @Author         : Vedang
    @Description    : Stand-in for automation.CaptureConfiguration used only by the test suite's fake SDK.
    @Input          : capture_mode    - the fake capture-mode instance to wrap
    @Output         : None
    @Note           : Test infrastructure only - not part of the shipped library.
    """

    def __init__(self, capture_mode):
        """
        @Author         : Vedang
        @Description    : Stores the fake capture-mode object being wrapped.
        @Input          : capture_mode    - the fake capture-mode instance to wrap
        @Output         : None
        @Note           : Test infrastructure only.
        """
        self.capture_mode = capture_mode


class _FakeLogicDeviceConfiguration:
    """
    @Author         : Vedang
    @Description    : Stand-in for automation.LogicDeviceConfiguration used only by the test suite's fake SDK.
    @Input          : kwargs          - arbitrary device configuration keyword arguments
    @Output         : None
    @Note           : Test infrastructure only - not part of the shipped library.
    """

    def __init__(self, **kwargs):
        """
        @Author         : Vedang
        @Description    : Stores arbitrary fake device configuration keyword arguments.
        @Input          : kwargs          - arbitrary device configuration keyword arguments
        @Output         : None
        @Note           : Test infrastructure only.
        """
        self.kwargs = kwargs


class _FakeDevice:
    """
    @Author         : Vedang
    @Description    : Stand-in for a discovered Logic device used only by the test suite's fake SDK.
    @Input          : None
    @Output         : None
    @Note           : Test infrastructure only - not part of the shipped library.
    """

    device_id = "FAKE-DEVICE-1"


class FakeCapture:
    """
    @Author         : Vedang
    @Description    : Fake automation.Capture handle recording stop()/wait() calls and serving synthetic CSV/analyzer exports.
    @Input          : None
    @Output         : None
    @Note           : Used by every capture-lifecycle and export-related test below.
    """

    def __init__(self):
        """
        @Author         : Vedang
        @Description    : Initializes stop()/wait() call-tracking flags for this fake capture.
        @Input          : None
        @Output         : None
        @Note           : Test infrastructure only.
        """
        self.stopped = False
        self.waited = False

    def stop(self):
        """
        @Author         : Vedang
        @Description    : Records that stop() was called, mirroring the real SDK's early-stop behavior.
        @Input          : None
        @Output         : None
        @Note           : Test infrastructure only.
        """
        self.stopped = True

    def wait(self):
        """
        @Author         : Vedang
        @Description    : Records that wait() was called, mirroring the real SDK's finalize/block behavior.
        @Input          : None
        @Output         : None
        @Note           : Test infrastructure only.
        """
        self.waited = True

    def export_raw_data_csv(self, directory, digital_channels=None, analog_channels=None):
        """
        @Author         : Vedang
        @Description    : Writes a small synthetic digital.csv into directory, standing in for the real SDK export.
        @Input          : directory       - target export directory
                           digital_channels - requested digital channel indices
                           analog_channels - requested analog channel indices (unused by this fake)
        @Output         : None            - writes digital.csv to disk
        @Note           : Test infrastructure only.
        """
        path = Path(directory) / "digital.csv"
        header = ["Time [s]"] + [f"Channel {ch}" for ch in (digital_channels or [0])]
        rows = [header, ["0.000000", "0"], ["0.010000", "1"], ["0.020000", "0"]]
        with path.open("w", newline="") as handle:
            csv.writer(handle).writerows(rows)

    def add_analyzer(self, name, label=None, settings=None):
        """
        @Author         : Vedang
        @Description    : Returns an opaque analyzer handle, standing in for the real SDK's add_analyzer().
        @Input          : name            - analyzer name, e.g. "SPI"
                           label           - optional analyzer label
                           settings        - analyzer settings dict
        @Output         : handle          - an opaque object representing the analyzer
        @Note           : Test infrastructure only.
        """
        return object()

    def export_data_table(self, filepath, analyzers=None):
        """
        @Author         : Vedang
        @Description    : Writes a small synthetic analyzer transaction CSV, standing in for the real SDK export.
        @Input          : filepath        - output CSV file path
                           analyzers       - analyzer handles requested (unused by this fake)
        @Output         : None            - writes filepath to disk
        @Note           : Test infrastructure only.
        """
        with open(filepath, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Time [s]", "MOSI", "MISO", "Error"])
            writer.writerow(["0.0001", "0xA5", "0x00", ""])
            writer.writerow(["0.0003", "0xFF", "0x01", "Framing Error"])


class FakeManager:
    """
    @Author         : Vedang
    @Description    : Fake automation.Manager standing in for a connected Automation API session.
    @Input          : None
    @Output         : None
    @Note           : Used by capture-lifecycle tests that need a working self.manager without real hardware.
    """

    def __init__(self):
        """
        @Author         : Vedang
        @Description    : Initializes the close() call-tracking flag for this fake manager.
        @Input          : None
        @Output         : None
        @Note           : Test infrastructure only.
        """
        self.closed = False

    def get_devices(self):
        """
        @Author         : Vedang
        @Description    : Returns a single fake discovered device.
        @Input          : None
        @Output         : devices         - list containing one _FakeDevice
        @Note           : Test infrastructure only.
        """
        return [_FakeDevice()]

    def start_capture(self, device_id, device_configuration, capture_configuration):
        """
        @Author         : Vedang
        @Description    : Returns a new FakeCapture, standing in for the real SDK's start_capture().
        @Input          : device_id       - target device id (unused by this fake)
                           device_configuration - device configuration object (unused by this fake)
                           capture_configuration - capture configuration object (unused by this fake)
        @Output         : capture         - a new FakeCapture instance
        @Note           : Test infrastructure only.
        """
        return FakeCapture()

    def close(self):
        """
        @Author         : Vedang
        @Description    : Records that close() was called.
        @Input          : None
        @Output         : None
        @Note           : Test infrastructure only.
        """
        self.closed = True


_automation_mod.Manager = types.SimpleNamespace(connect=lambda host, port: FakeManager())
_automation_mod.ManualCaptureMode = _FakeManualCaptureMode
_automation_mod.TimedCaptureMode = _FakeTimedCaptureMode
_automation_mod.CaptureConfiguration = _FakeCaptureConfiguration
_automation_mod.LogicDeviceConfiguration = _FakeLogicDeviceConfiguration
_automation_mod.__version__ = "test-stub-1.0"
_saleae_pkg.automation = _automation_mod
sys.modules["saleae"] = _saleae_pkg
sys.modules["saleae.automation"] = _automation_mod

sys.path.insert(0, str(Path(__file__).resolve().parent))

import saleae_wrapper as sw  # noqa: E402
from exceptions import CaptureError, ConfigurationError, ConnectionError  # noqa: E402
from waveform import Waveform, WaveformError  # noqa: E402
from plotting import PlottingError, plot_waveform  # noqa: E402


def make_bare_trace():
    """
    @Author         : Vedang
    @Description    : Builds a Saleae instance without running connect(), pre-wired with a fake config/manager/device for lifecycle tests.
    @Input          : None
    @Output         : trace           - a usable Saleae instance bypassing socket/SDK connection
    @Note           : Test helper only; not part of the shipped library.
    """
    trace = sw.Saleae.__new__(sw.Saleae)
    sw.Saleae.__init__(trace, config_path="config.yml")
    trace.config = sw.SaleaeConfig(digital_channels=[0, 1], analog_channels=[])
    trace.manager = FakeManager()
    trace.device_id = "FAKE-DEVICE-1"
    trace.device_configuration = object()
    trace._reserved = True
    return trace


class TestConfig(unittest.TestCase):
    """
    @Author         : Vedang
    @Description    : Tests for SaleaeConfig.from_yaml() loading and validation.
    @Input          : None
    @Output         : None
    @Note           : Uses temporary YAML files; no hardware or network required.
    """

    def test_01_valid_config_loads(self):
        """
        @Author         : Vedang
        @Description    : A well-formed config.yml loads into a SaleaeConfig with matching field values.
        @Input          : None
        @Output         : None            - asserts via unittest
        @Note           : Covers the happy path of SaleaeConfig.from_yaml().
        """
        tmpdir = tempfile.mkdtemp()
        try:
            path = Path(tmpdir) / "config.yml"
            path.write_text("host: 127.0.0.1\nport: 10430\nsample_rate: 1000000\n"
                             "digital_channels: [0, 1]\nanalog_channels: []\n")
            cfg = sw.SaleaeConfig.from_yaml(str(path))
            self.assertEqual(cfg.host, "127.0.0.1")
            self.assertEqual(cfg.port, 10430)
            self.assertEqual(cfg.digital_channels, [0, 1])
        finally:
            shutil.rmtree(tmpdir)

    def test_02_missing_file_raises_configuration_error(self):
        """
        @Author         : Vedang
        @Description    : Loading a nonexistent config.yml path raises ConfigurationError.
        @Input          : None
        @Output         : None            - asserts via unittest
        @Note           : Covers the missing-file branch of SaleaeConfig.from_yaml().
        """
        with self.assertRaises(ConfigurationError):
            sw.SaleaeConfig.from_yaml("/nonexistent/path/config.yml")

    def test_03_invalid_port_raises_configuration_error(self):
        """
        @Author         : Vedang
        @Description    : A config with an out-of-range port raises ConfigurationError.
        @Input          : None
        @Output         : None            - asserts via unittest
        @Note           : Covers SaleaeConfig._validate()'s port bounds check.
        """
        with self.assertRaises(ConfigurationError):
            sw.SaleaeConfig(port=99999, digital_channels=[0])._validate()

    def test_04_no_channels_raises_configuration_error(self):
        """
        @Author         : Vedang
        @Description    : A config with both digital_channels and analog_channels empty raises ConfigurationError.
        @Input          : None
        @Output         : None            - asserts via unittest
        @Note           : Covers SaleaeConfig._validate()'s channel-selection check.
        """
        with self.assertRaises(ConfigurationError):
            sw.SaleaeConfig(digital_channels=[], analog_channels=[])._validate()


class TestConnection(unittest.TestCase):
    """
    @Author         : Vedang
    @Description    : Tests for port-detection and connection-failure handling.
    @Input          : None
    @Output         : None
    @Note           : Uses a real local TCP socket for the open-port case; no Saleae hardware involved.
    """

    def test_05_is_port_open_true_and_false(self):
        """
        @Author         : Vedang
        @Description    : _is_port_open() correctly reports True for a listening local port and False for a closed one.
        @Input          : None
        @Output         : None            - asserts via unittest
        @Note           : Opens a throwaway TCP server on an OS-assigned free port for the True case.
        """
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            self.assertTrue(sw.Saleae._is_port_open("127.0.0.1", port, timeout=1.0))
        finally:
            server.close()
        self.assertFalse(sw.Saleae._is_port_open("127.0.0.1", port, timeout=0.5))

    def test_06_connect_raises_when_server_unreachable_and_launch_disabled(self):
        """
        @Author         : Vedang
        @Description    : connect() raises ConnectionError when the Automation Server is unreachable and launch_logic2 is False.
        @Input          : None
        @Output         : None            - asserts via unittest
        @Note           : Uses an unused local port so no real server is ever contacted.
        """
        tmpdir = tempfile.mkdtemp()
        try:
            path = Path(tmpdir) / "config.yml"
            path.write_text(
                "host: 127.0.0.1\nport: 1\nlaunch_logic2: false\n"
                "digital_channels: [0]\nanalog_channels: []\n"
            )
            trace = sw.Saleae(config_path=str(path))
            with self.assertRaises(ConnectionError):
                trace.connect()
        finally:
            shutil.rmtree(tmpdir)


class TestCaptureLifecycle(unittest.TestCase):
    """
    @Author         : Vedang
    @Description    : Tests for start()/stop()/pause()/resume()/mark() using a fake manager/capture, bypassing real hardware.
    @Input          : None
    @Output         : None
    @Note           : Every test uses make_bare_trace() to skip socket/SDK connection entirely.
    """

    def test_07_start_stop_records_one_segment(self):
        """
        @Author         : Vedang
        @Description    : A single start()/stop() cycle finalizes exactly one capture segment.
        @Input          : None
        @Output         : None            - asserts via unittest
        @Note           : Confirms _end_active_capture() calls stop()+wait() on the fake capture.
        """
        trace = make_bare_trace()
        trace.start()
        self.assertEqual(trace._state, "running")
        trace.stop()
        self.assertEqual(trace._state, "idle")
        self.assertEqual(len(trace._captures), 1)
        self.assertTrue(trace._captures[0].stopped)
        self.assertTrue(trace._captures[0].waited)

    def test_08_pause_resume_creates_two_segments(self):
        """
        @Author         : Vedang
        @Description    : pause() finalizes the current segment and resume() opens a new one, yielding two total segments after stop().
        @Input          : None
        @Output         : None            - asserts via unittest
        @Note           : Documents the "logical pause = stitched segments" behavior described in Saleae.pause()'s docstring.
        """
        trace = make_bare_trace()
        trace.start()
        trace.pause()
        self.assertEqual(trace._state, "paused")
        self.assertEqual(len(trace._captures), 1)
        trace.resume()
        self.assertEqual(trace._state, "running")
        trace.stop()
        self.assertEqual(len(trace._captures), 2)

    def test_09_stop_without_active_capture_does_not_raise(self):
        """
        @Author         : Vedang
        @Description    : Calling stop() with no active capture logs a warning but does not raise.
        @Input          : None
        @Output         : None            - asserts via unittest
        @Note           : Confirms the defensive "safe to call during cleanup" behavior.
        """
        trace = make_bare_trace()
        trace.stop()  # should not raise
        self.assertEqual(len(trace._captures), 0)

    def test_10_pause_without_running_capture_raises_capture_error(self):
        """
        @Author         : Vedang
        @Description    : Calling pause() with no active capture raises CaptureError.
        @Input          : None
        @Output         : None            - asserts via unittest
        @Note           : Confirms pause() enforces the "running" state precondition.
        """
        trace = make_bare_trace()
        with self.assertRaises(CaptureError):
            trace.pause()

    def test_11_mark_records_expected_fields(self):
        """
        @Author         : Vedang
        @Description    : mark() appends a Marker with the given label and a non-negative session_elapsed.
        @Input          : None
        @Output         : None            - asserts via unittest
        @Note           : session_start_wall is set manually since connect() was bypassed.
        """
        trace = make_bare_trace()
        trace._session_start_wall = __import__("time").time()
        trace.mark("Power Cycle")
        markers = trace.get_markers()
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].label, "Power Cycle")
        self.assertGreaterEqual(markers[0].session_elapsed, 0.0)


class TestWaveform(unittest.TestCase):
    """
    @Author         : Vedang
    @Description    : Tests for waveform.py metric correctness against synthetic, known-answer CSV data.
    @Input          : None
    @Output         : None
    @Note           : No SDK/hardware dependency; builds Waveform directly from hand-written CSV files.
    """

    def test_12_digital_frequency_period_duty_cycle(self):
        """
        @Author         : Vedang
        @Description    : A synthetic 10 Hz, 40%-duty digital square wave yields the expected frequency/period/duty_cycle.
        @Input          : None
        @Output         : None            - asserts via unittest
        @Note           : Cross-checked by hand: period 0.1s -> 10 Hz; high segment 0.04s of 0.1s period -> 40% duty.
        """
        tmpdir = tempfile.mkdtemp()
        try:
            path = Path(tmpdir) / "digital.csv"
            rows = [("Time [s]", "Channel 0"), ("0.000000", 0)]
            for cycle in range(1, 6):
                base = cycle * 0.1
                rows.append((f"{base:.6f}", 1))
                rows.append((f"{base + 0.04:.6f}", 0))
            with path.open("w", newline="") as handle:
                csv.writer(handle).writerows(rows)

            wave = Waveform.from_csv_directory(tmpdir, digital_channels=[0], sample_rate=500_000_000)
            self.assertAlmostEqual(wave.frequency(0), 10.0, places=6)
            self.assertAlmostEqual(wave.period(0), 0.1, places=6)
            self.assertGreater(wave.duty_cycle(0), 30.0)
            self.assertLess(wave.duty_cycle(0), 45.0)
            self.assertEqual(wave.edge_count(0), 10)
        finally:
            shutil.rmtree(tmpdir)

    def test_13_rise_time_raises_on_digital_channel(self):
        """
        @Author         : Vedang
        @Description    : rise_time() raises WaveformError on a purely digital channel, since it has no ramp data.
        @Input          : None
        @Output         : None            - asserts via unittest
        @Note           : Confirms the wrapper never fabricates a rise-time value for instantaneous digital transitions.
        """
        tmpdir = tempfile.mkdtemp()
        try:
            path = Path(tmpdir) / "digital.csv"
            rows = [("Time [s]", "Channel 0"), ("0.0", 0), ("0.1", 1), ("0.2", 0)]
            with path.open("w", newline="") as handle:
                csv.writer(handle).writerows(rows)
            wave = Waveform.from_csv_directory(tmpdir, digital_channels=[0])
            with self.assertRaises(WaveformError):
                wave.rise_time(0)
        finally:
            shutil.rmtree(tmpdir)

    def test_14_analog_rise_time_matches_known_ramp(self):
        """
        @Author         : Vedang
        @Description    : A synthetic 100 ns linear ramp yields a measured 10%-90% rise_time close to 80 ns.
        @Input          : None
        @Output         : None            - asserts via unittest
        @Note           : 10%-90% of a linear ramp spans 80% of its total duration, so 100ns * 0.8 = 80ns.
        """
        tmpdir = tempfile.mkdtemp()
        try:
            path = Path(tmpdir) / "analog.csv"
            fs = 100e6
            dt = 1.0 / fs
            rise_dur = 100e-9
            n = 50
            rows = [("Time [s]", "Channel 0")]
            for i in range(n):
                t = i * dt
                v = 3.3 * min(t / rise_dur, 1.0)
                rows.append((f"{t:.9f}", f"{v:.6f}"))
            with path.open("w", newline="") as handle:
                csv.writer(handle).writerows(rows)
            wave = Waveform.from_csv_directory(tmpdir, analog_channels=[0], sample_rate=fs)
            rise_time = wave.rise_time(0)
            self.assertAlmostEqual(rise_time * 1e9, 80.0, delta=15.0)
        finally:
            shutil.rmtree(tmpdir)

    def test_15_coordinate_export_formats_agree_on_point_count(self):
        """
        @Author         : Vedang
        @Description    : coordinates()/to_json()/to_csv()/to_numpy()/to_dataframe() all report the same number of points for one channel.
        @Input          : None
        @Output         : None            - asserts via unittest
        @Note           : Skips the DataFrame check gracefully if pandas is not installed.
        """
        tmpdir = tempfile.mkdtemp()
        try:
            path = Path(tmpdir) / "digital.csv"
            rows = [("Time [s]", "Channel 0"), ("0.0", 0), ("0.1", 1), ("0.2", 0)]
            with path.open("w", newline="") as handle:
                csv.writer(handle).writerows(rows)
            wave = Waveform.from_csv_directory(tmpdir, digital_channels=[0])

            points = wave.coordinates(0)
            n = len(points)
            self.assertEqual(n, 3)

            parsed_json = json.loads(wave.to_json(0))
            self.assertEqual(len(parsed_json), n)

            csv_text = wave.to_csv(0)
            self.assertEqual(len(csv_text.strip().splitlines()) - 1, n)  # minus header

            arr = wave.to_numpy(0)
            self.assertEqual(arr.shape[0], n)

            try:
                df = wave.to_dataframe(0)
            except WaveformError:
                pass
            else:
                self.assertEqual(len(df), n)
        finally:
            shutil.rmtree(tmpdir)


class TestPlotting(unittest.TestCase):
    """
    @Author         : Vedang
    @Description    : Tests for plotting.py's headless PNG/SVG rendering and format validation.
    @Input          : None
    @Output         : None
    @Note           : Runs entirely off-screen via matplotlib's Agg backend; no display required.
    """

    def test_16_plot_waveform_saves_png(self):
        """
        @Author         : Vedang
        @Description    : plot_waveform() with save=... writes a non-empty PNG file and returns its Path.
        @Input          : None
        @Output         : None            - asserts via unittest
        @Note           : Confirms the end-to-end Waveform -> plot -> file pipeline works headlessly.
        """
        tmpdir = tempfile.mkdtemp()
        try:
            path = Path(tmpdir) / "digital.csv"
            rows = [("Time [s]", "Channel 0"), ("0.0", 0), ("0.01", 1), ("0.02", 0)]
            with path.open("w", newline="") as handle:
                csv.writer(handle).writerows(rows)
            wave = Waveform.from_csv_directory(tmpdir, digital_channels=[0])

            out = Path(tmpdir) / "wave.png"
            result = plot_waveform(wave, save=out)
            self.assertTrue(result.is_file())
            self.assertGreater(result.stat().st_size, 0)
        finally:
            shutil.rmtree(tmpdir)

    def test_17_plot_waveform_rejects_unsupported_format(self):
        """
        @Author         : Vedang
        @Description    : plot_waveform() raises PlottingError when asked to save with an unsupported file extension.
        @Input          : None
        @Output         : None            - asserts via unittest
        @Note           : Only .png and .svg are supported; .txt should be rejected before any file is written.
        """
        tmpdir = tempfile.mkdtemp()
        try:
            path = Path(tmpdir) / "digital.csv"
            rows = [("Time [s]", "Channel 0"), ("0.0", 0), ("0.01", 1)]
            with path.open("w", newline="") as handle:
                csv.writer(handle).writerows(rows)
            wave = Waveform.from_csv_directory(tmpdir, digital_channels=[0])
            with self.assertRaises(PlottingError):
                plot_waveform(wave, save=Path(tmpdir) / "wave.txt")
        finally:
            shutil.rmtree(tmpdir)


class TestAnalyzers(unittest.TestCase):
    """
    @Author         : Vedang
    @Description    : Tests for the SPI/I2C/UART analyzer wrappers against a fake capture object.
    @Input          : None
    @Output         : None
    @Note           : No real protocol decoding happens here or in the library; only export/parse plumbing is tested.
    """

    def test_18_spi_transactions_and_errors(self):
        """
        @Author         : Vedang
        @Description    : SPIAnalyzer.transactions() parses the fake exported CSV, and errors() correctly isolates the flagged row.
        @Input          : None
        @Output         : None            - asserts via unittest
        @Note           : FakeCapture.export_data_table() writes a fixed 2-row table with one row flagged as a Framing Error.
        """
        trace = make_bare_trace()
        trace._last_capture = FakeCapture()
        spi = trace.spi(mosi=0, miso=1, clock=2, enable=3)
        txns = spi.transactions()
        self.assertEqual(len(txns), 2)
        errs = spi.errors()
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0]["Error"], "Framing Error")

    def test_19_analyzer_raises_without_last_capture(self):
        """
        @Author         : Vedang
        @Description    : Requesting an analyzer with no completed capture raises AnalyzerError.
        @Input          : None
        @Output         : None            - asserts via unittest
        @Note           : Confirms _ProtocolAnalyzer._capture()'s precondition check.
        """
        trace = make_bare_trace()
        trace._last_capture = None
        with self.assertRaises(sw.AnalyzerError):
            trace.i2c(sda=0, scl=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
