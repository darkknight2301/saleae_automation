"""
executor.py - Core command execution layer.

Design intent (Phase 1):
- There is exactly ONE executor for everything: nvme-cli commands, fio commands,
  and plain Linux commands. No per-tool subclasses. A "command" is just a string
  (or list) handed to the shell.
- stdout/stderr are always captured as raw bytes internally. Callers decide
  later whether to treat the output as text or binary (see CommandResult
  helper methods and logger.py's binary_output flag).
- No pass/fail judgement is made here. This layer only executes and reports
  what happened (exit code + output). Result interpretation belongs to
  higher layers (validation, added in a later phase).
"""

import subprocess
import time
from dataclasses import dataclass


@dataclass
class CommandResult:
    """Everything captured from a single command execution."""

    command: str
    exit_code: int
    stdout: bytes
    stderr: bytes
    start_time: float
    end_time: float

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    def stdout_text(self, errors: str = "replace") -> str:
        """Best-effort text decode of stdout. Use when output is known/expected
        to be text (nvme list, lsblk, lspci, etc)."""
        return self.stdout.decode("utf-8", errors=errors)

    def stderr_text(self, errors: str = "replace") -> str:
        return self.stderr.decode("utf-8", errors=errors)


class CommandExecutor:
    """Executes shell commands and captures their result.

    A single execute()/run() method handles every command type -- nvme-cli,
    fio, or any other Linux command. Nothing here is NVMe- or fio-aware.
    """

    def __init__(self, default_timeout: float = None):
        self.default_timeout = default_timeout

    def run(self, command, shell: bool = True, timeout: float = None) -> CommandResult:
        """Run a command and capture exit code, stdout, and stderr as raw bytes.

        Args:
            command: command string (or list of args if shell=False).
            shell:   run through the shell (default True; lets callers pass
                     full command lines like "nvme id-ctrl /dev/nvme0").
            timeout: optional timeout in seconds; falls back to
                     self.default_timeout (from ConfigManager) if not given.

        Returns:
            CommandResult with raw bytes always populated for stdout/stderr.
        """
        if timeout is None:
            timeout = self.default_timeout
        start = time.time()
        try:
            proc = subprocess.run(
                command,
                shell=shell,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
            exit_code = proc.returncode
            stdout = proc.stdout if proc.stdout is not None else b""
            stderr = proc.stderr if proc.stderr is not None else b""

        except subprocess.TimeoutExpired as exc:
            exit_code = -1
            stdout = exc.stdout or b""
            stderr = (exc.stderr or b"") + f"\n[TIMEOUT after {timeout}s]".encode()

        except FileNotFoundError as exc:
            # Command/binary doesn't exist at all.
            exit_code = 127
            stdout = b""
            stderr = str(exc).encode()

        except OSError as exc:
            exit_code = 1
            stdout = b""
            stderr = str(exc).encode()

        end = time.time()

        cmd_str = command if isinstance(command, str) else " ".join(command)

        return CommandResult(
            command=cmd_str,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            start_time=start,
            end_time=end,
        )


# Backward-compat alias (Executor was renamed to CommandExecutor).
Executor = CommandExecutor
