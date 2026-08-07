"""
@Author         : Vedang
@Description    : Lightweight Saleae Logic 8 Automation API wrapper - connection lifecycle, capture control, markers, exports, waveform analysis, plotting, and SPI/I2C/UART analyzer wrappers for headless Ubuntu 20.04 automation frameworks.
@Input          : None
@Output         : None
@Note           : NumPy/matplotlib are optional; if missing, only trace.waveform()/trace.plot() are disabled, with a clear CaptureError, not an import-time crash.
"""

from __future__ import annotations

import csv
import json
import logging
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, Dict, List, Optional, Sequence, Type

import yaml

from exceptions import (
    CaptureError,
    ConfigurationError,
    ConnectionError,
    ExportError,
    SaleaeException,
    TimeoutError,
)

try:
    # Official Saleae Logic 2 Automation API client library.
    # https://saleae.github.io/logic2-automation/
    from saleae import automation
except ImportError:  # pragma: no cover - exercised only when SDK missing
    automation = None  # type: ignore[assignment]

try:
    from waveform import Waveform, WaveformError
except ImportError as _waveform_import_error:  # pragma: no cover - exercised only when numpy missing
    Waveform = None  # type: ignore[assignment]
    WaveformError = None  # type: ignore[assignment]
    _WAVEFORM_IMPORT_ERROR: Optional[ImportError] = _waveform_import_error
else:
    _WAVEFORM_IMPORT_ERROR = None

try:
    from plotting import PlottingError, plot_waveform
except ImportError as _plotting_import_error:  # pragma: no cover - exercised only when matplotlib missing
    plot_waveform = None  # type: ignore[assignment]
    PlottingError = None  # type: ignore[assignment]
    _PLOTTING_IMPORT_ERROR: Optional[ImportError] = _plotting_import_error
else:
    _PLOTTING_IMPORT_ERROR = None


logger = logging.getLogger("saleae_wrapper")


def _configure_logging(level: int = logging.INFO) -> None:
    """
    @Author         : Vedang
    @Description    : Attaches a single structured stream handler to the module-level logger, idempotently.
    @Input          : level           - logging level to apply, defaults to logging.INFO
    @Output         : None            - configures the module logger in place
    @Note           : Safe to call repeatedly (e.g. across multiple Saleae() instances) without adding duplicate handlers.
    """
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(level)


@dataclass
class SaleaeConfig:
    """
    @Author         : Vedang
    @Description    : Typed representation of config.yml holding connection, capture, export, and reliability settings.
    @Input          : host, port, launch_logic2, logic2_binary, startup_timeout, sample_rate, digital_channels, analog_channels, capture_duration, export_directory, retry_count, timeout, plot_dpi, figure_size - see per-field defaults
    @Output         : None
    @Note           : Construct via from_yaml(); direct construction skips file-based validation unless _validate() is called manually.
    """

    host: str = "127.0.0.1"
    port: int = 10430
    launch_logic2: bool = False
    logic2_binary: str = "/opt/Logic/Logic"
    startup_timeout: int = 30
    sample_rate: int = 500_000_000
    digital_channels: List[int] = field(default_factory=lambda: [0, 1, 2, 3])
    analog_channels: List[int] = field(default_factory=list)
    capture_duration: float = 1.0
    export_directory: str = "./captures"
    retry_count: int = 3
    timeout: int = 10
    plot_dpi: int = 150
    figure_size: List[int] = field(default_factory=lambda: [10, 6])

    @classmethod
    def from_yaml(cls, path: str) -> "SaleaeConfig":
        """
        @Author         : Vedang
        @Description    : Loads and validates a SaleaeConfig instance from a YAML file.
        @Input          : path            - path to the config.yml file
        @Output         : config          - a populated, validated SaleaeConfig instance
        @Note           : Raises ConfigurationError on a missing file, invalid YAML, or invalid field values; unknown keys are logged and ignored.
        """
        config_path = Path(path)
        if not config_path.is_file():
            raise ConfigurationError(f"Configuration file not found: {path}")

        try:
            with config_path.open("r", encoding="utf-8") as handle:
                raw = yaml.safe_load(handle) or {}
        except yaml.YAMLError as exc:
            raise ConfigurationError(f"Failed to parse {path}: {exc}") from exc

        if not isinstance(raw, dict):
            raise ConfigurationError(f"{path} must contain a top-level mapping.")

        known_fields = set(cls.__dataclass_fields__)
        unknown = set(raw) - known_fields
        if unknown:
            logger.warning("Ignoring unknown config key(s): %s", sorted(unknown))
        filtered = {key: value for key, value in raw.items() if key in known_fields}

        try:
            instance = cls(**filtered)
        except TypeError as exc:
            raise ConfigurationError(
                f"Invalid configuration value(s) in {path}: {exc}"
            ) from exc

        instance._validate()
        return instance

    def _validate(self) -> None:
        """
        @Author         : Vedang
        @Description    : Sanity-checks every SaleaeConfig field after construction.
        @Input          : None            - validates self's own fields
        @Output         : None
        @Note           : Raises ConfigurationError on the first invalid field (port, sample_rate, retry_count, timeout, startup_timeout, capture_duration, channel selection, or figure_size shape).
        """
        if not (0 < self.port <= 65535):
            raise ConfigurationError(f"Invalid port: {self.port}")
        if self.sample_rate <= 0:
            raise ConfigurationError(f"Invalid sample_rate: {self.sample_rate}")
        if self.retry_count < 0:
            raise ConfigurationError(
                f"retry_count must be >= 0, got {self.retry_count}"
            )
        if self.timeout <= 0:
            raise ConfigurationError(f"timeout must be > 0, got {self.timeout}")
        if self.startup_timeout <= 0:
            raise ConfigurationError(
                f"startup_timeout must be > 0, got {self.startup_timeout}"
            )
        if self.capture_duration <= 0:
            raise ConfigurationError(
                f"capture_duration must be > 0, got {self.capture_duration}"
            )
        if not self.digital_channels and not self.analog_channels:
            raise ConfigurationError(
                "At least one digital or analog channel must be enabled."
            )
        if len(self.figure_size) != 2:
            raise ConfigurationError("figure_size must be a [width, height] pair.")


@dataclass
class Marker:
    """
    @Author         : Vedang
    @Description    : A single timestamped marker recorded during a session via trace.mark().
    @Input          : label           - user-supplied marker text
                       timestamp       - wall-clock time.time() the marker was recorded
                       session_elapsed - seconds since connect() began
                       segment_elapsed - seconds since the active capture segment began, or None if no capture was active
    @Output         : None
    @Note           : segment_elapsed is what plotting.py uses to position marker overlays on the waveform time axis.
    """

    label: str
    timestamp: float
    session_elapsed: float
    segment_elapsed: Optional[float]


class Saleae:
    """
    @Author         : Vedang
    @Description    : Context-managed wrapper around the Logic 2 Automation API - connection lifecycle, capture control, markers, exports, waveform analysis, plotting, and protocol analyzers.
    @Input          : config_path     - via constructor, path to the YAML configuration file
    @Output         : None
    @Note           : Use as `with Saleae() as trace:` so connect()/close() run automatically, including on exception.
    """

    def __init__(self, config_path: str = "config.yml") -> None:
        """
        @Author         : Vedang
        @Description    : Initializes wrapper state; does not connect to the Automation API or touch hardware.
        @Input          : config_path     - path to the YAML configuration file, defaults to "config.yml"
        @Output         : None            - sets up internal connection/capture/marker/waveform state
        @Note           : Call connect() (or use the `with` context manager) before any capture/export/plot method.
        """
        _configure_logging()
        self.config_path = config_path
        self.config: Optional[SaleaeConfig] = None
        self.manager = None  # type: ignore[assignment]  # automation.Manager
        self.device_id: Optional[str] = None
        self.device_configuration = None  # automation.LogicDeviceConfiguration
        self._connected: bool = False
        self._reserved: bool = False
        self._launched_process: Optional[subprocess.Popen] = None

        # --- Capture lifecycle state -----------------------------------
        self._state: str = "idle"  # idle | running | paused
        self._active_capture: Optional[Any] = None  # automation.Capture
        self._captures: List[Any] = []  # finalized automation.Capture segments
        self._last_capture: Optional[Any] = None
        self._segment_start_time: Optional[float] = None  # time.monotonic()

        # --- Session/marker bookkeeping ---------------------------------
        self._session_start_wall: Optional[float] = None
        self._session_start_monotonic: Optional[float] = None
        self._markers: List[Marker] = []

        # --- Waveform analysis (Phase 3) --------------------------------
        self._waveform: Optional["Waveform"] = None

    # ------------------------------------------------------------------
    # Context manager protocol
    # ------------------------------------------------------------------
    def __enter__(self) -> "Saleae":
        """
        @Author         : Vedang
        @Description    : Context manager entry point; calls connect() and returns self.
        @Input          : None
        @Output         : self            - the connected Saleae instance
        @Note           : Enables the `with Saleae() as trace:` usage pattern.
        """
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> bool:
        """
        @Author         : Vedang
        @Description    : Context manager exit point; calls close() unconditionally and never suppresses exceptions.
        @Input          : exc_type        - exception type raised in the with-block, if any
                           exc_val         - exception instance raised in the with-block, if any
                           exc_tb          - traceback of the exception, if any
        @Output         : suppress        - always False, so exceptions propagate normally
        @Note           : close() itself never raises; cleanup failures are logged instead.
        """
        self.close()
        return False  # never suppress exceptions raised in the `with` block

    # ------------------------------------------------------------------
    # Public lifecycle API (Phase 1, unchanged)
    # ------------------------------------------------------------------
    def connect(self) -> None:
        """
        @Author         : Vedang
        @Description    : Loads config, ensures the Automation Server is reachable (launching it if configured), connects with retry, and reserves a device.
        @Input          : None            - reads self.config_path and populates self.config
        @Output         : None            - populates self.manager, self.device_id, self.device_configuration
        @Note           : Raises ConfigurationError/ConnectionError/TimeoutError/CaptureError depending on which step fails; called automatically by __enter__.
        """
        if automation is None:
            raise ConnectionError(
                "The 'saleae' automation client library is not installed. "
                "Install it with: pip install logic2-automation"
            )

        self._session_start_wall = time.time()
        self._session_start_monotonic = time.monotonic()

        logger.info("Loading configuration from '%s'.", self.config_path)
        self.config = SaleaeConfig.from_yaml(self.config_path)
        logger.info("Configuration in use: %s", self.config)

        self._ensure_server_available()
        self.manager = self._connect_with_retry()
        self._verify_version()
        self._select_and_configure_device()

        elapsed = time.monotonic() - self._session_start_monotonic
        logger.info(
            "Saleae wrapper ready (device_id=%s) in %.3fs.", self.device_id, elapsed
        )

    def close(self) -> None:
        """
        @Author         : Vedang
        @Description    : Stops any active capture, releases the device, disconnects from the Automation API, and terminates any Logic 2 process this wrapper launched.
        @Input          : None
        @Output         : None            - resets all connection/capture state to idle
        @Note           : Safe to call multiple times or after a failed connect(); all cleanup failures are logged, never raised.
        """
        if self._active_capture is not None:
            logger.info("Active capture detected during close(); stopping it.")
            try:
                self._end_active_capture()
            except Exception as exc:  # noqa: BLE001 - cleanup must not raise
                logger.warning("Error while stopping active capture on close: %s", exc)

        for segment in self._captures:
            closer = getattr(segment, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Error while closing capture segment: %s", exc)

        if self._reserved:
            logger.info("Releasing device %s.", self.device_id)
            self._reserved = False

        if self.manager is not None:
            try:
                logger.info("Disconnecting from Automation API.")
                self.manager.close()
            except Exception as exc:  # noqa: BLE001 - cleanup must not raise
                logger.warning("Error while closing manager: %s", exc)
            finally:
                self.manager = None
                self._connected = False

        if self._launched_process is not None:
            logger.info("Terminating Logic 2 process launched by this wrapper.")
            timeout = self.config.timeout if self.config else 10
            try:
                self._launched_process.terminate()
                self._launched_process.wait(timeout=timeout)
            except Exception as exc:  # noqa: BLE001 - cleanup must not raise
                logger.warning("Error while terminating Logic 2 process: %s", exc)
            finally:
                self._launched_process = None

        if self._session_start_monotonic is not None:
            total = time.monotonic() - self._session_start_monotonic
            logger.info(
                "Session closed. Total duration=%.3fs, capture_segments=%d, markers=%d.",
                total,
                len(self._captures),
                len(self._markers),
            )
        self._state = "idle"

    # ------------------------------------------------------------------
    # Startup helpers (Phase 1, unchanged)
    # ------------------------------------------------------------------
    @staticmethod
    def _is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
        """
        @Author         : Vedang
        @Description    : Checks whether a TCP port is currently accepting connections.
        @Input          : host            - hostname or IP address
                           port            - TCP port number
                           timeout         - socket connection timeout in seconds
        @Output         : is_open         - True if a connection could be established, else False
        @Note           : Used both to detect an already-running Automation Server and to poll readiness after launching one.
        """
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def _ensure_server_available(self) -> None:
        """
        @Author         : Vedang
        @Description    : Ensures the Automation Server is reachable, launching Logic 2 headlessly if configured to do so.
        @Input          : None            - reads self.config.host/port/launch_logic2
        @Output         : None
        @Note           : Raises ConnectionError if unreachable with launch_logic2 disabled, or TimeoutError if a launched instance never becomes ready.
        """
        assert self.config is not None
        if self._is_port_open(self.config.host, self.config.port):
            logger.info(
                "Automation Server already running on %s:%d.",
                self.config.host,
                self.config.port,
            )
            return

        if not self.config.launch_logic2:
            raise ConnectionError(
                f"Automation Server not reachable at "
                f"{self.config.host}:{self.config.port} and "
                f"'launch_logic2' is disabled in {self.config_path}."
            )

        self._launch_logic2()
        self._wait_for_api_ready()

    def _launch_logic2(self) -> None:
        """
        @Author         : Vedang
        @Description    : Launches Logic 2 headlessly (--automation flag) using the configured binary path.
        @Input          : None            - reads self.config.logic2_binary
        @Output         : None            - stores the subprocess handle on self._launched_process
        @Note           : Raises ConnectionError if the binary path does not exist or fails to start.
        """
        assert self.config is not None
        binary = self.config.logic2_binary
        if not Path(binary).exists():
            raise ConnectionError(f"logic2_binary not found: {binary}")

        logger.info("Launching Logic 2 from '%s' ...", binary)
        try:
            # --automation starts Logic 2 with the Automation API enabled
            # and without requiring a desktop/X11 session.
            self._launched_process = subprocess.Popen(
                shlex.split(f"{binary} --automation"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise ConnectionError(f"Failed to launch Logic 2: {exc}") from exc

    def _wait_for_api_ready(self) -> None:
        """
        @Author         : Vedang
        @Description    : Polls the Automation Server port until it accepts connections or the startup timeout elapses.
        @Input          : None            - reads self.config.host/port/startup_timeout
        @Output         : None
        @Note           : Raises TimeoutError if the port never opens within startup_timeout seconds.
        """
        assert self.config is not None
        deadline = time.monotonic() + self.config.startup_timeout
        poll_interval = 0.5
        start = time.monotonic()

        while time.monotonic() < deadline:
            if self._is_port_open(self.config.host, self.config.port):
                logger.info(
                    "Automation API is ready after %.3fs.", time.monotonic() - start
                )
                return
            time.sleep(poll_interval)

        raise TimeoutError(
            f"Automation API did not become ready within "
            f"{self.config.startup_timeout}s of launching Logic 2."
        )

    def _connect_with_retry(self):
        """
        @Author         : Vedang
        @Description    : Connects to the Automation API, retrying with exponential backoff on failure.
        @Input          : None            - reads self.config.host/port/retry_count
        @Output         : manager         - a connected saleae.automation.Manager instance
        @Note           : Raises ConnectionError after every retry attempt is exhausted.
        """
        assert self.config is not None
        last_error: Optional[Exception] = None

        for attempt in range(1, self.config.retry_count + 1):
            attempt_start = time.monotonic()
            try:
                logger.info(
                    "Connecting to Automation API at %s:%d (attempt %d/%d).",
                    self.config.host,
                    self.config.port,
                    attempt,
                    self.config.retry_count,
                )
                manager = automation.Manager.connect(
                    host=self.config.host, port=self.config.port
                )
                self._connected = True
                logger.info(
                    "Connected on attempt %d in %.3fs.",
                    attempt,
                    time.monotonic() - attempt_start,
                )
                return manager
            except Exception as exc:  # noqa: BLE001 - SDK-raised exceptions vary
                last_error = exc
                logger.warning(
                    "Connection attempt %d/%d failed after %.3fs: %s",
                    attempt,
                    self.config.retry_count,
                    time.monotonic() - attempt_start,
                    exc,
                )
                if attempt < self.config.retry_count:
                    backoff = min(2 ** attempt, 10)
                    logger.info("Retrying in %.1fs.", backoff)
                    time.sleep(backoff)

        raise ConnectionError(
            f"Could not connect to Automation API after "
            f"{self.config.retry_count} attempt(s): {last_error}"
        )

    def _verify_version(self) -> None:
        """
        @Author         : Vedang
        @Description    : Logs the installed saleae-automation client library version for diagnostics.
        @Input          : None
        @Output         : None
        @Note           : Does not yet enforce a minimum version; raises ConnectionError only if the manager appears unhealthy while querying.
        """
        try:
            client_version = getattr(automation, "__version__", "unknown")
            logger.info("saleae-automation client library version: %s", client_version)
        except Exception as exc:  # noqa: BLE001
            raise ConnectionError(f"Unable to query Automation API version: {exc}") from exc

    def _select_and_configure_device(self) -> None:
        """
        @Author         : Vedang
        @Description    : Discovers an attached Logic device, builds its LogicDeviceConfiguration, and marks it reserved.
        @Input          : None            - reads self.config.digital_channels/analog_channels/sample_rate
        @Output         : None            - populates self.device_id and self.device_configuration
        @Note           : Raises CaptureError if no device is found or configuration fails.
        """
        assert self.config is not None
        try:
            devices = self.manager.get_devices()
        except Exception as exc:  # noqa: BLE001
            raise CaptureError(f"Failed to enumerate devices: {exc}") from exc

        if not devices:
            raise CaptureError("No Saleae devices found on the Automation Server.")

        target = devices[0]
        self.device_id = getattr(target, "device_id", None) or getattr(target, "id", None)
        logger.info("Selected device: %s", self.device_id)

        try:
            self.device_configuration = automation.LogicDeviceConfiguration(
                enabled_digital_channels=self.config.digital_channels,
                enabled_analog_channels=self.config.analog_channels,
                digital_sample_rate=self.config.sample_rate,
            )
        except Exception as exc:  # noqa: BLE001
            raise CaptureError(f"Failed to build device configuration: {exc}") from exc

        self._reserved = True
        logger.info(
            "Device configured: sample_rate=%d Hz, digital_channels=%s, "
            "analog_channels=%s",
            self.config.sample_rate,
            self.config.digital_channels,
            self.config.analog_channels,
        )

    # ------------------------------------------------------------------
    # Capture lifecycle
    # ------------------------------------------------------------------
    def start(
        self,
        duration_seconds: Optional[float] = None,
        trigger_channel: Optional[int] = None,
        trigger_type: str = "rising",
        before_trigger_seconds: float = 0.0,
        after_trigger_seconds: Optional[float] = None,
    ) -> None:
        """
        @Author         : Vedang
        @Description    : Begins a new capture segment - manual (runs until stop()), timed, or trigger-based.
        @Input          : duration_seconds       - fixed capture length in seconds, enables timed mode
                           trigger_channel        - digital channel to arm a hardware trigger on, enables trigger mode
                           trigger_type           - trigger edge/condition, e.g. "rising"/"falling"/"pulse_high"/"pulse_low"
                           before_trigger_seconds - seconds of pre-trigger data to capture
                           after_trigger_seconds  - seconds of post-trigger data to capture
        @Output         : None            - sets self._active_capture and self._state = "running"
        @Note           : Raises CaptureError if a capture is already active, the device isn't reserved, or the requested mode isn't supported by the installed SDK.
        """
        if self._active_capture is not None:
            raise CaptureError(
                "A capture is already running or paused; call stop() first."
            )
        if not self._reserved or self.device_id is None:
            raise CaptureError(
                "Device is not reserved; connect() must succeed before starting a capture."
            )

        capture_mode = self._build_capture_mode(
            duration_seconds=duration_seconds,
            trigger_channel=trigger_channel,
            trigger_type=trigger_type,
            before_trigger_seconds=before_trigger_seconds,
            after_trigger_seconds=after_trigger_seconds,
        )
        self._active_capture = self._start_capture_segment(capture_mode)
        self._state = "running"

    def stop(self) -> None:
        """
        @Author         : Vedang
        @Description    : Stops and finalizes the active capture segment.
        @Input          : None
        @Output         : None            - appends the finished segment to self._captures and resets state to idle
        @Note           : Logs a warning (does not raise) if called with no active capture.
        """
        if self._active_capture is None:
            logger.warning("stop() called but no capture is currently active.")
            return

        logger.info("Stopping active capture segment.")
        self._end_active_capture()
        self._state = "idle"
        logger.info(
            "Capture stopped. Total segments this session: %d.", len(self._captures)
        )

    def pause(self) -> None:
        """
        @Author         : Vedang
        @Description    : Logically pauses the active capture by ending and finalizing the current segment.
        @Input          : None
        @Output         : None            - sets self._state = "paused"
        @Note           : Raises CaptureError if no capture is running; hardware has no true in-place pause, so paused data is really separate stitched segments.
        """
        if self._state != "running" or self._active_capture is None:
            raise CaptureError("pause() requires an active running capture.")

        logger.info(
            "Pausing capture (ending current segment; hardware has no native pause)."
        )
        self._end_active_capture()
        self._state = "paused"
        logger.info("Capture paused after segment #%d.", len(self._captures))

    def resume(self) -> None:
        """
        @Author         : Vedang
        @Description    : Resumes a paused session by starting a new manual capture segment.
        @Input          : None
        @Output         : None            - sets self._state = "running"
        @Note           : Raises CaptureError if the session is not currently paused.
        """
        if self._state != "paused":
            raise CaptureError("resume() requires a paused capture; call pause() first.")

        logger.info("Resuming capture (starting new manual capture segment).")
        capture_mode = self._build_capture_mode(
            duration_seconds=None,
            trigger_channel=None,
            trigger_type="rising",
            before_trigger_seconds=0.0,
            after_trigger_seconds=None,
        )
        self._active_capture = self._start_capture_segment(capture_mode)
        self._state = "running"

    def capture(
        self,
        seconds: float = 2.0,
        trigger_channel: Optional[int] = None,
        trigger_type: str = "rising",
        before_trigger_seconds: float = 0.0,
        after_trigger_seconds: Optional[float] = None,
    ) -> None:
        """
        @Author         : Vedang
        @Description    : Runs a single blocking capture (timed or trigger-based) and waits for it to complete.
        @Input          : seconds                - capture duration in seconds, ignored when trigger_channel with explicit after_trigger_seconds is given
                           trigger_channel        - optional digital channel index to trigger on
                           trigger_type           - trigger edge/condition, see start()
                           before_trigger_seconds - seconds of pre-trigger data
                           after_trigger_seconds  - seconds of post-trigger data, defaults to seconds when triggering
        @Output         : None            - finalizes the segment and resets state to idle
        @Note           : Equivalent to start(...) immediately followed by an internal finalize/wait; raises CaptureError if a capture is already active.
        """
        if trigger_channel is not None and after_trigger_seconds is None:
            after_trigger_seconds = seconds

        duration_seconds = None if trigger_channel is not None else seconds
        logger.info(
            "Starting blocking capture (%s).",
            f"trigger on channel {trigger_channel}"
            if trigger_channel is not None
            else f"duration={seconds:.3f}s",
        )
        self.start(
            duration_seconds=duration_seconds,
            trigger_channel=trigger_channel,
            trigger_type=trigger_type,
            before_trigger_seconds=before_trigger_seconds,
            after_trigger_seconds=after_trigger_seconds,
        )
        self._end_active_capture()
        self._state = "idle"
        logger.info("Blocking capture complete.")

    # ------------------------------------------------------------------
    # Capture lifecycle internals
    # ------------------------------------------------------------------
    def _build_capture_mode(
        self,
        duration_seconds: Optional[float],
        trigger_channel: Optional[int],
        trigger_type: str,
        before_trigger_seconds: float,
        after_trigger_seconds: Optional[float],
    ):
        """
        @Author         : Vedang
        @Description    : Constructs the appropriate automation capture-mode object (manual, timed, or trigger-based) for a new segment.
        @Input          : duration_seconds       - fixed capture length, selects timed mode
                           trigger_channel        - digital channel to trigger on, selects trigger mode
                           trigger_type           - trigger edge/condition string
                           before_trigger_seconds - pre-trigger capture length
                           after_trigger_seconds  - post-trigger capture length
        @Output         : capture_mode    - an automation.*CaptureMode instance
        @Note           : Raises CaptureError if the requested mode is unsupported by the installed SDK or construction fails.
        """
        if trigger_channel is not None:
            trigger_cls = getattr(automation, "DigitalTriggerCaptureMode", None)
            if trigger_cls is None:
                raise CaptureError(
                    "Trigger-based captures are not supported by the "
                    "installed saleae automation client."
                )
            trigger_type_value = self._resolve_trigger_type(trigger_type)
            kwargs = {
                "trigger_channel_index": trigger_channel,
                "trigger_type": trigger_type_value,
                "before_trigger_seconds": before_trigger_seconds,
            }
            if after_trigger_seconds is not None:
                kwargs["after_trigger_seconds"] = after_trigger_seconds
            logger.info(
                "Configuring trigger-based capture: channel=%d type=%s "
                "before=%.3fs after=%s",
                trigger_channel,
                trigger_type,
                before_trigger_seconds,
                f"{after_trigger_seconds:.3f}s" if after_trigger_seconds else "n/a",
            )
            try:
                return trigger_cls(**kwargs)
            except Exception as exc:  # noqa: BLE001
                raise CaptureError(f"Failed to build trigger capture mode: {exc}") from exc

        if duration_seconds is not None:
            logger.info("Configuring timed capture: duration=%.3fs", duration_seconds)
            try:
                return automation.TimedCaptureMode(duration_seconds=duration_seconds)
            except Exception as exc:  # noqa: BLE001
                raise CaptureError(f"Failed to build timed capture mode: {exc}") from exc

        manual_cls = getattr(automation, "ManualCaptureMode", None)
        if manual_cls is None:
            raise CaptureError(
                "Manual (start/stop) captures are not supported by the "
                "installed saleae automation client."
            )
        logger.info("Configuring manual capture (runs until stop() is called).")
        try:
            return manual_cls()
        except Exception as exc:  # noqa: BLE001
            raise CaptureError(f"Failed to build manual capture mode: {exc}") from exc

    @staticmethod
    def _resolve_trigger_type(trigger_type: str):
        """
        @Author         : Vedang
        @Description    : Resolves a trigger-type string to the SDK's DigitalTriggerType enum member.
        @Input          : trigger_type    - e.g. "rising", "falling", "pulse_high", "pulse_low"
        @Output         : member          - the matching DigitalTriggerType enum value
        @Note           : Raises CaptureError if the SDK has no such enum, or the string doesn't match a valid member.
        """
        trigger_type_cls = getattr(automation, "DigitalTriggerType", None)
        if trigger_type_cls is None:
            raise CaptureError(
                "DigitalTriggerType is not available in the installed "
                "saleae automation client."
            )
        key = trigger_type.strip().upper()
        try:
            return getattr(trigger_type_cls, key)
        except AttributeError as exc:
            valid = [m for m in dir(trigger_type_cls) if not m.startswith("_")]
            raise CaptureError(
                f"Unknown trigger_type '{trigger_type}'. Valid options: {valid}"
            ) from exc

    def _start_capture_segment(self, capture_mode) -> Any:
        """
        @Author         : Vedang
        @Description    : Starts a capture segment on the reserved device via the SDK, using a given capture-mode object.
        @Input          : capture_mode    - an automation.*CaptureMode instance built by _build_capture_mode()
        @Output         : capture         - the SDK's automation.Capture handle for this segment
        @Note           : Raises CaptureError if the SDK call fails; logs the segment number and start time.
        """
        assert self.config is not None
        capture_configuration = automation.CaptureConfiguration(capture_mode=capture_mode)

        start_time = time.monotonic()
        try:
            capture = self.manager.start_capture(
                device_id=self.device_id,
                device_configuration=self.device_configuration,
                capture_configuration=capture_configuration,
            )
        except Exception as exc:  # noqa: BLE001
            raise CaptureError(f"Failed to start capture: {exc}") from exc

        self._segment_start_time = start_time
        logger.info("Capture segment #%d started.", len(self._captures) + 1)
        return capture

    def _end_active_capture(self) -> None:
        """
        @Author         : Vedang
        @Description    : Stops and finalizes self._active_capture, storing the result and invalidating any cached waveform.
        @Input          : None            - operates on self._active_capture
        @Output         : None            - appends to self._captures, updates self._last_capture, clears self._waveform
        @Note           : Raises CaptureError only if the finalize/wait call fails; an early stop() failure (already complete) is logged, not raised.
        """
        assert self._active_capture is not None
        segment_index = len(self._captures) + 1
        start_time = self._segment_start_time

        try:
            self._active_capture.stop()
        except Exception as exc:  # noqa: BLE001
            # Timed/trigger captures may already be complete; not fatal.
            logger.debug(
                "capture.stop() raised for segment #%d (may already be "
                "complete): %s",
                segment_index,
                exc,
            )

        try:
            self._active_capture.wait()
        except Exception as exc:  # noqa: BLE001
            raise CaptureError(
                f"Failed while finalizing capture segment #{segment_index}: {exc}"
            ) from exc

        elapsed = time.monotonic() - start_time if start_time is not None else float("nan")
        logger.info("Capture segment #%d finalized in %.3fs.", segment_index, elapsed)

        self._captures.append(self._active_capture)
        self._last_capture = self._active_capture
        self._active_capture = None
        self._segment_start_time = None
        # A new segment invalidates any waveform built from a prior capture.
        self._waveform = None

    # ------------------------------------------------------------------
    # Markers
    # ------------------------------------------------------------------
    def mark(self, label: str) -> None:
        """
        @Author         : Vedang
        @Description    : Records a timestamped marker for later logging, JSON export, and plot overlay.
        @Input          : label           - human-readable marker text, e.g. "After Reset"
        @Output         : None            - appends a Marker to self._markers and logs it
        @Note           : segment_elapsed is None if no capture is active when the marker is recorded, which excludes it from plot overlays.
        """
        now = time.time()
        session_elapsed = (
            now - self._session_start_wall if self._session_start_wall else 0.0
        )
        segment_elapsed: Optional[float] = None
        if self._active_capture is not None and self._segment_start_time is not None:
            segment_elapsed = time.monotonic() - self._segment_start_time

        marker = Marker(
            label=label,
            timestamp=now,
            session_elapsed=session_elapsed,
            segment_elapsed=segment_elapsed,
        )
        self._markers.append(marker)
        logger.info(
            "Marker recorded: '%s' (session_elapsed=%.3fs, segment_elapsed=%s)",
            label,
            session_elapsed,
            f"{segment_elapsed:.3f}s" if segment_elapsed is not None else "n/a",
        )

    def get_markers(self) -> List[Marker]:
        """
        @Author         : Vedang
        @Description    : Returns a copy of every marker recorded so far in this session.
        @Input          : None
        @Output         : markers         - list of Marker instances, in recorded order
        @Note           : Returns a copy, so mutating the result does not affect the wrapper's internal marker list.
        """
        return list(self._markers)

    # ------------------------------------------------------------------
    # Export APIs
    # ------------------------------------------------------------------
    def export_csv(
        self,
        directory: Optional[str] = None,
        capture: Optional[Any] = None,
    ) -> Path:
        """
        @Author         : Vedang
        @Description    : Exports the most recent (or specified) capture's raw data to CSV via the Automation API.
        @Input          : directory       - output directory, defaults to config.export_directory
                           capture         - specific SDK capture handle, defaults to the last finalized segment
        @Output         : export_dir      - the directory the CSV data was written to
        @Note           : Raises ExportError if no completed capture is available or the SDK export call fails.
        """
        assert self.config is not None
        target_capture = capture or self._last_capture
        if target_capture is None:
            raise ExportError(
                "No completed capture available to export; run capture() "
                "or start()/stop() first."
            )

        export_dir = Path(directory or self.config.export_directory)
        export_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Exporting capture to CSV: %s", export_dir)

        try:
            target_capture.export_raw_data_csv(
                directory=str(export_dir),
                digital_channels=self.config.digital_channels or None,
                analog_channels=self.config.analog_channels or None,
            )
        except Exception as exc:  # noqa: BLE001
            raise ExportError(f"CSV export failed: {exc}") from exc

        logger.info("CSV export complete: %s", export_dir)
        return export_dir

    def export_json(
        self,
        directory: Optional[str] = None,
        filename: str = "capture_metadata.json",
    ) -> Path:
        """
        @Author         : Vedang
        @Description    : Exports session metadata (device/config/segment count) and recorded markers as a JSON file.
        @Input          : directory       - output directory, defaults to config.export_directory
                           filename        - output JSON filename, defaults to "capture_metadata.json"
        @Output         : output_path     - path to the written JSON file
        @Note           : Does not export raw waveform samples as JSON - the Automation API has no such exporter, only CSV/.sal.
        """
        assert self.config is not None
        export_dir = Path(directory or self.config.export_directory)
        export_dir.mkdir(parents=True, exist_ok=True)
        output_path = export_dir / filename

        payload = {
            "device_id": self.device_id,
            "sample_rate": self.config.sample_rate,
            "digital_channels": self.config.digital_channels,
            "analog_channels": self.config.analog_channels,
            "capture_segments": len(self._captures),
            "markers": [asdict(marker) for marker in self._markers],
        }

        logger.info("Exporting session metadata and markers to JSON: %s", output_path)
        try:
            with output_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
        except OSError as exc:
            raise ExportError(f"JSON export failed: {exc}") from exc

        logger.info("JSON export complete: %s", output_path)
        return output_path

    def export_native(
        self,
        directory: Optional[str] = None,
        filename: str = "capture.sal",
        capture: Optional[Any] = None,
    ) -> Path:
        """
        @Author         : Vedang
        @Description    : Exports the native Saleae capture file (.sal) if the installed SDK supports it.
        @Input          : directory       - output directory, defaults to config.export_directory
                           filename        - output filename, defaults to "capture.sal"
                           capture         - specific SDK capture handle, defaults to the last finalized segment
        @Output         : output_path     - path to the written .sal file
        @Note           : Raises ExportError if no completed capture is available or the SDK has no save_capture() method.
        """
        assert self.config is not None
        target_capture = capture or self._last_capture
        if target_capture is None:
            raise ExportError(
                "No completed capture available to export; run capture() "
                "or start()/stop() first."
            )

        save_method = getattr(target_capture, "save_capture", None)
        if save_method is None:
            raise ExportError(
                "Native .sal export (save_capture) is not supported by the "
                "installed saleae automation client."
            )

        export_dir = Path(directory or self.config.export_directory)
        export_dir.mkdir(parents=True, exist_ok=True)
        output_path = export_dir / filename

        logger.info("Exporting native Saleae capture (.sal): %s", output_path)
        try:
            save_method(filepath=str(output_path))
        except Exception as exc:  # noqa: BLE001
            raise ExportError(f"Native capture export failed: {exc}") from exc

        logger.info("Native export complete: %s", output_path)
        return output_path

    # ------------------------------------------------------------------
    # Waveform analysis
    # ------------------------------------------------------------------
    def waveform(
        self,
        capture: Optional[Any] = None,
        digital_channels: Optional[List[int]] = None,
        analog_channels: Optional[List[int]] = None,
        keep_export: bool = False,
    ) -> "Waveform":
        """
        @Author         : Vedang
        @Description    : Exports the target capture's raw data and builds/caches a waveform.Waveform for analysis.
        @Input          : capture         - specific SDK capture handle, defaults to the last finalized segment
                           digital_channels - digital channels to analyze, defaults to config.digital_channels
                           analog_channels - analog channels to analyze, defaults to config.analog_channels
                           keep_export     - if True, keep the intermediate CSV export instead of deleting it
        @Output         : wave            - a waveform.Waveform instance, also cached on self._waveform
        @Note           : Raises CaptureError if waveform.py/NumPy is unavailable or no completed capture exists.
        """
        if Waveform is None:
            raise CaptureError(
                f"waveform.py is unavailable ({_WAVEFORM_IMPORT_ERROR}); "
                "install NumPy with: pip install numpy"
            )

        assert self.config is not None
        target_capture = capture or self._last_capture
        if target_capture is None:
            raise CaptureError(
                "No completed capture available to analyze; run capture() "
                "or start()/stop() first."
            )

        digital_channels = (
            digital_channels if digital_channels is not None else self.config.digital_channels
        )
        analog_channels = (
            analog_channels if analog_channels is not None else self.config.analog_channels
        )

        export_dir = Path(tempfile.mkdtemp(prefix="saleae_waveform_"))
        logger.info("Exporting capture data for waveform analysis to %s.", export_dir)
        try:
            target_capture.export_raw_data_csv(
                directory=str(export_dir),
                digital_channels=digital_channels or None,
                analog_channels=analog_channels or None,
            )
        except Exception as exc:  # noqa: BLE001
            shutil.rmtree(export_dir, ignore_errors=True)
            raise CaptureError(f"Failed to export capture data for waveform analysis: {exc}") from exc

        try:
            wave = Waveform.from_csv_directory(
                export_dir,
                digital_channels=digital_channels,
                analog_channels=analog_channels,
                sample_rate=self.config.sample_rate,
            )
        finally:
            if keep_export:
                logger.info("Keeping waveform export directory: %s", export_dir)
            else:
                shutil.rmtree(export_dir, ignore_errors=True)

        self._waveform = wave
        logger.info("Waveform built for channels: %s", wave.channels)
        return wave

    def _get_waveform(self) -> "Waveform":
        """
        @Author         : Vedang
        @Description    : Returns the cached Waveform, building it from the last capture if none is cached yet.
        @Input          : None
        @Output         : wave            - a waveform.Waveform instance
        @Note           : Backing helper for every trace.<metric>() shortcut method below.
        """
        if self._waveform is None:
            return self.waveform()
        return self._waveform

    # ------------------------------------------------------------------
    # Waveform analysis shortcuts
    # ------------------------------------------------------------------
    def bandwidth(self, channel: Optional[int] = None):
        """
        @Author         : Vedang
        @Description    : Shortcut for self.waveform().bandwidth(channel).
        @Input          : channel         - a channel id, list of ids, or None for all channels
        @Output         : bandwidth_hz    - see Waveform.bandwidth()
        @Note           : Lazily builds/reuses the cached waveform for the last capture.
        """
        return self._get_waveform().bandwidth(channel)

    def frequency(self, channel: Optional[int] = None):
        """
        @Author         : Vedang
        @Description    : Shortcut for self.waveform().frequency(channel).
        @Input          : channel         - a channel id, list of ids, or None for all channels
        @Output         : frequency_hz    - see Waveform.frequency()
        @Note           : Lazily builds/reuses the cached waveform for the last capture.
        """
        return self._get_waveform().frequency(channel)

    def period(self, channel: Optional[int] = None):
        """
        @Author         : Vedang
        @Description    : Shortcut for self.waveform().period(channel).
        @Input          : channel         - a channel id, list of ids, or None for all channels
        @Output         : period_s        - see Waveform.period()
        @Note           : Lazily builds/reuses the cached waveform for the last capture.
        """
        return self._get_waveform().period(channel)

    def edge_count(self, channel: Optional[int] = None):
        """
        @Author         : Vedang
        @Description    : Shortcut for self.waveform().edge_count(channel).
        @Input          : channel         - a channel id, list of ids, or None for all channels
        @Output         : edge_count      - see Waveform.edge_count()
        @Note           : Lazily builds/reuses the cached waveform for the last capture.
        """
        return self._get_waveform().edge_count(channel)

    def high_time(self, channel: Optional[int] = None):
        """
        @Author         : Vedang
        @Description    : Shortcut for self.waveform().high_time(channel).
        @Input          : channel         - a channel id, list of ids, or None for all channels
        @Output         : high_time_s     - see Waveform.high_time()
        @Note           : Lazily builds/reuses the cached waveform for the last capture.
        """
        return self._get_waveform().high_time(channel)

    def low_time(self, channel: Optional[int] = None):
        """
        @Author         : Vedang
        @Description    : Shortcut for self.waveform().low_time(channel).
        @Input          : channel         - a channel id, list of ids, or None for all channels
        @Output         : low_time_s      - see Waveform.low_time()
        @Note           : Lazily builds/reuses the cached waveform for the last capture.
        """
        return self._get_waveform().low_time(channel)

    def statistics(self, channel: Optional[int] = None):
        """
        @Author         : Vedang
        @Description    : Shortcut for self.waveform().statistics(channel).
        @Input          : channel         - a channel id, list of ids, or None for all channels
        @Output         : stats           - see Waveform.statistics()
        @Note           : Lazily builds/reuses the cached waveform for the last capture.
        """
        return self._get_waveform().statistics(channel)

    # ------------------------------------------------------------------
    # Plotting
    # ------------------------------------------------------------------
    def plot(
        self,
        channels: Optional[List[int]] = None,
        start: Optional[float] = None,
        end: Optional[float] = None,
        save: Optional[str] = None,
        dpi: Optional[int] = None,
        figsize: Optional[Sequence[float]] = None,
        title: Optional[str] = None,
        channel_labels: Optional[Dict[int, str]] = None,
        show_markers: bool = True,
    ) -> Path:
        """
        @Author         : Vedang
        @Description    : Renders a waveform plot for the current capture via plotting.py and saves it to disk.
        @Input          : channels        - channels to plot, defaults to all channels on the waveform
                           start           - plot window start in seconds
                           end             - plot window end in seconds
                           save            - output path (.png/.svg), auto-generated under export_directory when None
                           dpi             - figure resolution, defaults to config.plot_dpi
                           figsize         - (width, height) inches, defaults to config.figure_size
                           title           - plot title, auto-generated when None
                           channel_labels  - optional {channel: label} overrides
                           show_markers    - if True, overlay recorded markers within the plotted window
        @Output         : save_path       - Path to the saved image file
        @Note           : Always headless (matplotlib Agg backend, no window); raises CaptureError if plotting.py/matplotlib is unavailable.
        """
        if plot_waveform is None:
            raise CaptureError(
                f"plotting.py is unavailable ({_PLOTTING_IMPORT_ERROR}); "
                "install matplotlib with: pip install matplotlib"
            )
        assert self.config is not None
        wave = self._get_waveform()

        dpi = dpi if dpi is not None else self.config.plot_dpi
        figsize = tuple(figsize) if figsize is not None else tuple(self.config.figure_size)
        save_path = (
            Path(save)
            if save
            else Path(self.config.export_directory) / f"waveform_{len(self._captures)}.png"
        )
        save_path.parent.mkdir(parents=True, exist_ok=True)

        markers = self._markers if show_markers else None
        logger.info(
            "Rendering waveform plot: channels=%s start=%s end=%s -> %s",
            channels or wave.channels,
            start,
            end,
            save_path,
        )
        result = plot_waveform(
            wave,
            channels=channels,
            start=start,
            end=end,
            markers=markers,
            channel_labels=channel_labels,
            title=title,
            figsize=figsize,
            dpi=dpi,
            save=save_path,
        )
        logger.info("Waveform plot saved: %s", result)
        return result

    def save_image(
        self,
        path: str,
        channels: Optional[List[int]] = None,
        start: Optional[float] = None,
        end: Optional[float] = None,
        dpi: Optional[int] = None,
        show_markers: bool = True,
        capture: Optional[Any] = None,
    ) -> Path:
        """
        @Author         : Vedang
        @Description    : Saves a waveform image, preferring a native SDK image export before falling back to trace.plot().
        @Input          : path            - output image path (.png/.svg)
                           channels        - channels to include, fallback path only
                           start           - time range start in seconds, fallback path only
                           end             - time range end in seconds, fallback path only
                           dpi             - image resolution, defaults to config.plot_dpi
                           show_markers    - overlay markers, fallback path only
                           capture         - specific SDK capture handle, defaults to the last finalized segment
        @Output         : save_path       - Path to the saved image file
        @Note           : Native export method names are probed opportunistically (export_waveform_image/export_image/save_screenshot) since the SDK does not consistently document one.
        """
        assert self.config is not None
        target_capture = capture or self._last_capture
        if target_capture is None:
            raise CaptureError(
                "No completed capture available to save an image from; "
                "run capture() or start()/stop() first."
            )

        save_path = Path(path)
        dpi = dpi if dpi is not None else self.config.plot_dpi

        for native_method_name in ("export_waveform_image", "export_image", "save_screenshot"):
            native_method = getattr(target_capture, native_method_name, None)
            if native_method is None:
                continue
            logger.info("Using native SDK image export (%s).", native_method_name)
            try:
                native_method(filepath=str(save_path))
            except Exception as exc:  # noqa: BLE001
                raise ExportError(f"Native waveform image export failed: {exc}") from exc
            logger.info("Waveform image saved via native SDK export: %s", save_path)
            return save_path

        logger.info("No native SDK waveform image export found; rendering via plotting.py.")
        return self.plot(
            channels=channels,
            start=start,
            end=end,
            save=str(save_path),
            dpi=dpi,
            show_markers=show_markers,
        )

    # ------------------------------------------------------------------
    # Protocol analyzers (SPI / I2C / UART)
    # ------------------------------------------------------------------
    def spi(
        self,
        mosi: Optional[int] = None,
        miso: Optional[int] = None,
        clock: Optional[int] = None,
        enable: Optional[int] = None,
        settings: Optional[Dict[str, Any]] = None,
        label: Optional[str] = None,
    ) -> "SPIAnalyzer":
        """
        @Author         : Vedang
        @Description    : Attaches and returns a lightweight SPI analyzer for the last capture.
        @Input          : mosi            - MOSI digital channel index
                           miso            - MISO digital channel index
                           clock           - clock digital channel index
                           enable          - chip-select/enable digital channel index
                           settings        - raw analyzer settings overrides
                           label           - optional analyzer label
        @Output         : analyzer        - an SPIAnalyzer instance
        @Note           : Raises AnalyzerError if the installed SDK does not support add_analyzer().
        """
        return SPIAnalyzer(
            self, mosi=mosi, miso=miso, clock=clock, enable=enable, settings=settings, label=label
        )

    def i2c(
        self,
        sda: Optional[int] = None,
        scl: Optional[int] = None,
        settings: Optional[Dict[str, Any]] = None,
        label: Optional[str] = None,
    ) -> "I2CAnalyzer":
        """
        @Author         : Vedang
        @Description    : Attaches and returns a lightweight I2C analyzer for the last capture.
        @Input          : sda             - SDA digital channel index
                           scl             - SCL digital channel index
                           settings        - raw analyzer settings overrides
                           label           - optional analyzer label
        @Output         : analyzer        - an I2CAnalyzer instance
        @Note           : Raises AnalyzerError if the installed SDK does not support add_analyzer().
        """
        return I2CAnalyzer(self, sda=sda, scl=scl, settings=settings, label=label)

    def uart(
        self,
        channel: Optional[int] = None,
        bit_rate: int = 9600,
        settings: Optional[Dict[str, Any]] = None,
        label: Optional[str] = None,
    ) -> "UARTAnalyzer":
        """
        @Author         : Vedang
        @Description    : Attaches and returns a lightweight UART (async serial) analyzer for the last capture.
        @Input          : channel         - digital channel carrying the serial data
                           bit_rate        - serial bit rate in bits/second
                           settings        - raw analyzer settings overrides
                           label           - optional analyzer label
        @Output         : analyzer        - a UARTAnalyzer instance
        @Note           : Raises AnalyzerError if the installed SDK does not support add_analyzer().
        """
        return UARTAnalyzer(self, channel=channel, bit_rate=bit_rate, settings=settings, label=label)


class AnalyzerError(SaleaeException):
    """
    @Author         : Vedang
    @Description    : Raised for protocol analyzer setup, export, or parsing failures.
    @Input          : None
    @Output         : None
    @Note           : Subclasses SaleaeException; raised by _ProtocolAnalyzer and its SPI/I2C/UART subclasses.
    """


class _ProtocolAnalyzer:
    """
    @Author         : Vedang
    @Description    : Shared plumbing for lightweight SPI/I2C/UART analyzer wrappers built on the SDK's add_analyzer/export_data_table calls.
    @Input          : trace           - via constructor, the owning Saleae instance
                       settings        - via constructor, analyzer settings dict
                       label           - via constructor, optional analyzer label
    @Output         : None
    @Note           : Does not decode protocol data itself - only configures the SDK analyzer and reads back its exported table as plain dicts.
    """

    #: Analyzer name as registered in Logic 2 (e.g. ``"SPI"``). Subclasses override.
    analyzer_name: str = ""

    def __init__(self, trace: "Saleae", settings: Dict[str, Any], label: Optional[str] = None) -> None:
        """
        @Author         : Vedang
        @Description    : Stores analyzer configuration and immediately adds the analyzer to the last capture.
        @Input          : trace           - the owning Saleae instance
                           settings        - analyzer settings dict to pass to the SDK
                           label           - optional analyzer label, defaults to analyzer_name
        @Output         : None            - populates self._handle via _add_analyzer()
        @Note           : Raises AnalyzerError if no completed capture exists or the SDK add_analyzer() call fails.
        """
        self._trace = trace
        self._settings = settings
        self._label = label or self.analyzer_name
        self._handle: Optional[Any] = None
        self._transactions_cache: Optional[List[Dict[str, Any]]] = None
        self._add_analyzer()

    def _capture(self) -> Any:
        """
        @Author         : Vedang
        @Description    : Returns the owning trace's last finalized capture, or raises if none exists.
        @Input          : None
        @Output         : capture         - the SDK capture handle
        @Note           : Raises AnalyzerError if no capture has completed yet.
        """
        capture = self._trace._last_capture
        if capture is None:
            raise AnalyzerError(
                "No completed capture available; run capture() or "
                "start()/stop() before adding a protocol analyzer."
            )
        return capture

    def _add_analyzer(self) -> None:
        """
        @Author         : Vedang
        @Description    : Adds this analyzer to the last capture via the SDK's add_analyzer() call.
        @Input          : None            - reads self.analyzer_name and self._settings
        @Output         : None            - stores the SDK's analyzer handle on self._handle
        @Note           : Raises AnalyzerError if the installed SDK has no add_analyzer() method or the call fails.
        """
        capture = self._capture()
        add_method = getattr(capture, "add_analyzer", None)
        if add_method is None:
            raise AnalyzerError(
                "The installed saleae automation client does not support "
                "add_analyzer(); protocol analysis is unavailable."
            )
        logger.info("Adding %s analyzer (settings=%s).", self.analyzer_name, self._settings)
        try:
            self._handle = add_method(self.analyzer_name, label=self._label, settings=self._settings)
        except Exception as exc:  # noqa: BLE001
            raise AnalyzerError(f"Failed to add {self.analyzer_name} analyzer: {exc}") from exc

    def transactions(self, refresh: bool = False) -> List[Dict[str, Any]]:
        """
        @Author         : Vedang
        @Description    : Exports and parses the analyzer's decoded data table into a list of dicts.
        @Input          : refresh         - if True, re-export and re-parse even if a cached result exists
        @Output         : rows            - list of dicts, one per decoded row, in SDK export order
        @Note           : Raises AnalyzerError if the SDK has no export_data_table() method or the export/parse fails.
        """
        if self._transactions_cache is not None and not refresh:
            return self._transactions_cache

        capture = self._capture()
        export_method = getattr(capture, "export_data_table", None)
        if export_method is None:
            raise AnalyzerError(
                "The installed saleae automation client does not support "
                "export_data_table(); cannot read decoded transactions."
            )

        export_dir = Path(tempfile.mkdtemp(prefix="saleae_analyzer_"))
        export_path = export_dir / f"{self.analyzer_name.lower().replace(' ', '_')}_data.csv"
        try:
            try:
                export_method(filepath=str(export_path), analyzers=[self._handle])
            except TypeError:
                # SDK variants differ on kwarg names/positions; retry positionally.
                export_method(str(export_path))
            rows = self._parse_data_table(export_path)
        except AnalyzerError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AnalyzerError(
                f"Failed to export/parse {self.analyzer_name} transactions: {exc}"
            ) from exc
        finally:
            shutil.rmtree(export_dir, ignore_errors=True)

        self._transactions_cache = rows
        logger.info("%s: parsed %d transaction(s).", self.analyzer_name, len(rows))
        return rows

    @staticmethod
    def _parse_data_table(path: Path) -> List[Dict[str, Any]]:
        """
        @Author         : Vedang
        @Description    : Parses an exported analyzer CSV file into a list of dicts using column headers as keys.
        @Input          : path            - path to the exported CSV file
        @Output         : rows            - list of dicts, one per CSV row
        @Note           : Raises AnalyzerError if the expected export file was not found.
        """
        if not path.is_file():
            raise AnalyzerError(f"Expected analyzer export file not found: {path}")
        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            return [dict(row) for row in reader]

    def errors(self, refresh: bool = False) -> List[Dict[str, Any]]:
        """
        @Author         : Vedang
        @Description    : Returns the subset of transactions() that look like protocol errors.
        @Input          : refresh         - passed through to transactions()
        @Output         : flagged         - list of transaction dicts with a truthy value in any column containing "error"
        @Note           : Generic best-effort filter; returns an empty list (not an error) if no error column exists in the export.
        """
        rows = self.transactions(refresh=refresh)
        error_cols = {k for k in rows[0]} if rows else set()
        error_cols = {k for k in error_cols if "error" in k.lower()}
        if not error_cols:
            logger.info(
                "%s: no error column found in exported data; assuming no errors.",
                self.analyzer_name,
            )
            return []

        flagged = [
            row
            for row in rows
            if any(
                str(row.get(col, "")).strip().lower() not in ("", "0", "false", "none")
                for col in error_cols
            )
        ]
        logger.info("%s: %d of %d transaction(s) flagged as errors.", self.analyzer_name, len(flagged), len(rows))
        return flagged

    # TODO (future phase): add a remove()/close() to drop the analyzer
    # from the capture once the SDK exposes that; not required for the
    # current scope. TODO: protocol-aware decode helpers belong on the
    # subclasses below, not here.


class SPIAnalyzer(_ProtocolAnalyzer):
    """
    @Author         : Vedang
    @Description    : Lightweight SPI analyzer wrapper mapping MOSI/MISO/Clock/Enable channels to the SDK's SPI analyzer.
    @Input          : mosi, miso, clock, enable - via constructor, SPI signal channel indices
    @Output         : None
    @Note           : TODO (future phase) - protocol-aware helpers, e.g. grouping transactions() by chip-select assertion.
    """

    analyzer_name = "SPI"

    def __init__(
        self,
        trace: "Saleae",
        mosi: Optional[int] = None,
        miso: Optional[int] = None,
        clock: Optional[int] = None,
        enable: Optional[int] = None,
        settings: Optional[Dict[str, Any]] = None,
        label: Optional[str] = None,
    ) -> None:
        """
        @Author         : Vedang
        @Description    : Builds the SPI analyzer settings dict from channel assignments and adds the analyzer.
        @Input          : trace           - the owning Saleae instance
                           mosi            - MOSI digital channel index
                           miso            - MISO digital channel index
                           clock           - clock digital channel index
                           enable          - chip-select/enable digital channel index
                           settings        - raw settings overrides, merged over the channel mapping
                           label           - optional analyzer label
        @Output         : None
        @Note           : Only non-None channel arguments are included in the settings dict sent to the SDK.
        """
        merged = {"MOSI": mosi, "MISO": miso, "Clock": clock, "Enable": enable}
        merged = {k: v for k, v in merged.items() if v is not None}
        if settings:
            merged.update(settings)
        super().__init__(trace, settings=merged, label=label)


class I2CAnalyzer(_ProtocolAnalyzer):
    """
    @Author         : Vedang
    @Description    : Lightweight I2C analyzer wrapper mapping SDA/SCL channels to the SDK's I2C analyzer.
    @Input          : sda, scl        - via constructor, I2C signal channel indices
    @Output         : None
    @Note           : TODO (future phase) - protocol-aware helpers, e.g. grouping transactions() into start/address/data/stop sequences.
    """

    analyzer_name = "I2C"

    def __init__(
        self,
        trace: "Saleae",
        sda: Optional[int] = None,
        scl: Optional[int] = None,
        settings: Optional[Dict[str, Any]] = None,
        label: Optional[str] = None,
    ) -> None:
        """
        @Author         : Vedang
        @Description    : Builds the I2C analyzer settings dict from channel assignments and adds the analyzer.
        @Input          : trace           - the owning Saleae instance
                           sda             - SDA digital channel index
                           scl             - SCL digital channel index
                           settings        - raw settings overrides, merged over the channel mapping
                           label           - optional analyzer label
        @Output         : None
        @Note           : Only non-None channel arguments are included in the settings dict sent to the SDK.
        """
        merged = {"SDA": sda, "SCL": scl}
        merged = {k: v for k, v in merged.items() if v is not None}
        if settings:
            merged.update(settings)
        super().__init__(trace, settings=merged, label=label)


class UARTAnalyzer(_ProtocolAnalyzer):
    """
    @Author         : Vedang
    @Description    : Lightweight UART (async serial) analyzer wrapper mapping an input channel and bit rate to the SDK's Async Serial analyzer.
    @Input          : channel, bit_rate - via constructor, serial signal channel index and baud rate
    @Output         : None
    @Note           : TODO (future phase) - protocol-aware helpers, e.g. reassembling decoded frames into byte streams or ASCII text.
    """

    analyzer_name = "Async Serial"

    def __init__(
        self,
        trace: "Saleae",
        channel: Optional[int] = None,
        bit_rate: int = 9600,
        settings: Optional[Dict[str, Any]] = None,
        label: Optional[str] = None,
    ) -> None:
        """
        @Author         : Vedang
        @Description    : Builds the UART analyzer settings dict from the channel/bit-rate assignment and adds the analyzer.
        @Input          : trace           - the owning Saleae instance
                           channel         - digital channel carrying the serial data
                           bit_rate        - serial bit rate in bits/second, defaults to 9600
                           settings        - raw settings overrides, merged over the mapping
                           label           - optional analyzer label
        @Output         : None
        @Note           : Only non-None arguments are included in the settings dict sent to the SDK.
        """
        merged = {"Input Channel": channel, "Bit Rate (Bits/s)": bit_rate}
        merged = {k: v for k, v in merged.items() if v is not None}
        if settings:
            merged.update(settings)
        super().__init__(trace, settings=merged, label=label)


if __name__ == "__main__":
    # Minimal manual smoke test: verify the connection + capture lifecycle
    # end to end from the terminal, e.g. `python3 saleae_wrapper.py`.
    try:
        with Saleae() as trace:
            logger.info("Session established with device_id=%s", trace.device_id)
            trace.mark("Session start")
            trace.capture(seconds=2)
            trace.mark("Capture complete")
            trace.export_csv()
            trace.export_json()
            trace.plot()
    except SaleaeException as exc:
        logger.error("Saleae wrapper failed: %s", exc)
        sys.exit(1)
