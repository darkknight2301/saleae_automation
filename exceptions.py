"""
@Author         : Vedang
@Description    : Custom exception hierarchy for the Saleae Logic 8 automation wrapper (saleae_wrapper.py, waveform.py, plotting.py).
@Input          : None            - definitions-only module, imported by the other files
@Output         : None
@Note           : All exceptions below inherit from SaleaeException so callers can catch that one type to handle any wrapper error.
"""


class SaleaeException(Exception):
    """
    @Author         : Vedang
    @Description    : Base class for every exception raised by the Saleae wrapper.
    @Input          : args            - standard Exception message args, passed through to Exception.__init__
    @Output         : None            - this is an exception class, not a callable
    @Note           : Catch this one type to handle any error from saleae_wrapper.py / waveform.py / plotting.py in a single except clause.
    """


class ConnectionError(SaleaeException):
    """
    @Author         : Vedang
    @Description    : Raised when the wrapper cannot connect to the Logic 2 Automation API.
    @Input          : args            - standard Exception message args
    @Output         : None            - this is an exception class, not a callable
    @Note           : Covers Automation Server not running, host/port unreachable, missing saleae client library, or a dropped connection.
    """


class CaptureError(SaleaeException):
    """
    @Author         : Vedang
    @Description    : Raised when device discovery, configuration, reservation, or the capture start/stop lifecycle fails.
    @Input          : args            - standard Exception message args
    @Output         : None            - this is an exception class, not a callable
    @Note           : Also raised by trace.waveform()/trace.plot() when their optional dependencies (NumPy/matplotlib) are missing.
    """


class ExportError(SaleaeException):
    """
    @Author         : Vedang
    @Description    : Raised when exporting capture data (raw CSV, JSON metadata, native .sal, or a waveform image) fails.
    @Input          : args            - standard Exception message args
    @Output         : None            - this is an exception class, not a callable
    @Note           : Distinguishes export-time failures from capture-time failures (CaptureError).
    """


class ConfigurationError(SaleaeException):
    """
    @Author         : Vedang
    @Description    : Raised when config.yml is missing, malformed, or contains invalid values.
    @Input          : args            - standard Exception message args
    @Output         : None            - this is an exception class, not a callable
    @Note           : Raised by SaleaeConfig.from_yaml()/_validate() before any hardware interaction occurs.
    """


class TimeoutError(SaleaeException):
    """
    @Author         : Vedang
    @Description    : Raised when an operation exceeds its configured timeout (e.g. startup_timeout).
    @Input          : args            - standard Exception message args
    @Output         : None            - this is an exception class, not a callable
    @Note           : Shadows the built-in TimeoutError only within this package's namespace, not globally.
    """
