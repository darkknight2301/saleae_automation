"""
framework_log.py - FrameworkLogger: thin wrapper around stdlib `logging`.

Replaces framework-level print() for status/progress/results output.
Distinct from logger.ResultLogger, which formats one test's COMMAND/OUTPUT/
VALIDATION into its .log report file. FrameworkLogger is for INFO/DEBUG/
WARNING/ERROR framework diagnostics and CLI result output -- console and,
when a run directory is known, a run.log file inside it.
"""

import logging
import os
import sys
import uuid

_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


class FrameworkLogger:
    """Console + optional file logger with INFO/DEBUG/WARNING/ERROR levels.

    Each instance owns its own stdlib `logging.Logger` under a unique name
    (review F-1: `logging.getLogger(name)` returns a process-wide cached
    object for a given name, so two instances sharing one name would tear
    down each other's handlers via handlers.clear() and silently redirect
    each other's output). `name` may still be passed for readability in
    tests/logs, but a unique suffix is always appended internally so two
    live FrameworkLogger instances never collide.
    """

    def __init__(self, level: str = "INFO", run_dir: str = None, name: str = "nvme_test"):
        unique_name = f"{name}.{uuid.uuid4().hex}"
        self._logger = logging.getLogger(unique_name)
        self._logger.setLevel(logging.DEBUG)  # handlers filter; logger itself stays permissive
        self._logger.propagate = False
        self._handlers = []

        formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        console.setLevel(self._level_value(level))
        self._logger.addHandler(console)
        self._handlers.append(console)

        self.file_path = None
        if run_dir:
            self.file_path = os.path.join(run_dir, "run.log")
            file_handler = logging.FileHandler(self.file_path, encoding="utf-8")
            file_handler.setFormatter(formatter)
            file_handler.setLevel(logging.DEBUG)  # file keeps everything regardless of console level
            self._logger.addHandler(file_handler)
            self._handlers.append(file_handler)

    @staticmethod
    def _level_value(level: str) -> int:
        return getattr(logging, level.upper(), logging.INFO)

    def set_level(self, level: str):
        for handler in self._handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                handler.setLevel(self._level_value(level))

    def close(self):
        """Detach and close this instance's handlers (review F-1 secondary
        defect: handlers were never closed, leaking file descriptors).
        Safe to call multiple times."""
        for handler in self._handlers:
            self._logger.removeHandler(handler)
            handler.close()
        self._handlers = []

    def debug(self, message: str):
        self._logger.debug(message)

    def info(self, message: str):
        self._logger.info(message)

    def warning(self, message: str):
        self._logger.warning(message)

    def error(self, message: str):
        self._logger.error(message)

    def result_line(self, message: str):
        """Plain result/table output (CLI PASS/FAIL rows, summaries) --
        still routed through the logger (INFO level) rather than print(),
        so it is captured by run.log and respects --log-level."""
        self.info(message)
