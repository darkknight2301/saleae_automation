"""
logger.py - Writes one human-readable .log file per test execution.

Rules enforced here (per Phase 1 spec):
- Exactly one .log file per test execution. No sidecar files.
- Binary output is NEVER written to a separate .bin file. It is rendered as
  a hex dump directly inside the .log file.
- The raw bytes captured by the executor are never discarded by this module;
  logger.write_log() only *formats* result.stdout for the log text. Callers
  that need the raw bytes for later validation should keep hold of the
  CommandResult object itself (result.stdout stays untouched here).
"""

import os
from datetime import datetime

BYTES_PER_LINE = 16


def _hex_dump(data: bytes, bytes_per_line: int = BYTES_PER_LINE) -> str:
    """Render bytes as a classic hexdump -C style dump:
    OFFSET  HEX BYTES...  |ascii|
    """
    if not data:
        return "(0 bytes)"

    lines = []
    for offset in range(0, len(data), bytes_per_line):
        chunk = data[offset:offset + bytes_per_line]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        hex_part = hex_part.ljust(bytes_per_line * 3 - 1)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{offset:08x}  {hex_part}  {ascii_part}")
    return "\n".join(lines)


def _render_command_block(result, binary_output: bool):
    """Render the COMMAND / EXIT CODE / OUTPUT (or BINARY OUTPUT) / STDERR
    section for a single CommandResult. Shared by write_log() (Phase 1,
    single-command logs) and write_nvtest_log() (Phase 2, multi-RUN .nvtest
    logs) so both stay byte-for-byte consistent.
    """
    lines = []
    lines.append("COMMAND:")
    lines.append(result.command)
    lines.append("")
    lines.append("EXIT CODE:")
    lines.append(str(result.exit_code))
    lines.append("")

    if binary_output:
        lines.append("BINARY OUTPUT:")
        lines.append(f"Size: {len(result.stdout)} bytes")
        lines.append("")
        lines.append(_hex_dump(result.stdout))
    else:
        lines.append("OUTPUT:")
        lines.append(result.stdout_text())

    if result.stderr:
        lines.append("")
        lines.append("STDERR:")
        lines.append(result.stderr_text())

    return lines


class Logger:
    """Formats a CommandResult into the standard test log and writes it to disk."""

    def __init__(self, log_dir: str = "logs"):
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)

    def write_log(
        self,
        test_name: str,
        result,
        binary_output: bool = False,
        result_status: str = None,
        log_path: str = None,
    ) -> str:
        """Write a single .log file for one test execution.

        Args:
            test_name:     name shown in the TEST: header and used for the
                            default filename.
            result:        CommandResult from Executor.run().
            binary_output: if True, stdout is rendered as a hex dump under a
                            "BINARY OUTPUT:" section instead of "OUTPUT:".
            result_status: "PASS"/"FAIL"/etc. Defaults to PASS if exit_code==0
                            else FAIL (Phase 1 has no real validation layer
                            yet, so this is just exit-code based).
            log_path:      explicit output path. Defaults to
                            <log_dir>/<test_name>.log

        Returns:
            The path of the log file written.
        """
        if result_status is None:
            result_status = "PASS" if result.exit_code == 0 else "FAIL"

        start_str = datetime.fromtimestamp(result.start_time).strftime("%Y-%m-%d %H:%M:%S")
        end_str = datetime.fromtimestamp(result.end_time).strftime("%Y-%m-%d %H:%M:%S")

        lines = []
        lines.append("=" * 40)
        lines.append(f"TEST: {test_name}")
        lines.append(f"START: {start_str}")
        lines.append("=" * 40)
        lines.append("")
        # stderr section (if any) is included automatically by the shared
        # block renderer, keeping clean-run logs matching the spec template.
        lines.extend(_render_command_block(result, binary_output))

        lines.append("")
        lines.append("RESULT:")
        lines.append(result_status)
        lines.append("")
        lines.append(f"END: {end_str}")
        lines.append("=" * 40)

        content = "\n".join(lines) + "\n"

        if log_path is None:
            safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in test_name)
            log_path = os.path.join(self.log_dir, f"{safe_name}.log")

        with open(log_path, "w", encoding="utf-8", errors="replace") as f:
            f.write(content)

        return log_path

    def write_nvtest_log(
        self,
        test_name: str,
        results,
        binary_flags,
        validation_lines,
        result_status: str,
        log_path: str = None,
    ) -> str:
        """Write a single .log file for a `.nvtest` test case (Phase 2).

        Unlike write_log() (one command, no validations), a `.nvtest` test
        case may RUN more than one command and always has a VALIDATION
        section. Still exactly one .log file, still no .bin files, still a
        hex dump for binary output -- reuses the same block renderer and
        hex_dump as Phase 1 for consistency.

        Args:
            test_name:        name shown in the TEST: header / filename.
            results:           list of CommandResult, one per RUN, in order.
            binary_flags:      list of bool, same length as results; True
                                means that command's output is rendered as a
                                hex dump instead of text.
            validation_lines:  list of pre-formatted "[PASS] ..."/"[FAIL] ..."
                                strings, in the order validations were
                                declared in the .nvtest file.
            result_status:     overall "PASS"/"FAIL" for the test case (a
                                single failed validation must make this FAIL
                                -- decided by the validator, not this class).
            log_path:          explicit output path. Defaults to
                                <log_dir>/<test_name>.log

        Returns:
            The path of the log file written.
        """
        assert len(results) == len(binary_flags), "results/binary_flags length mismatch"

        start_str = datetime.fromtimestamp(results[0].start_time).strftime("%Y-%m-%d %H:%M:%S") \
            if results else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        end_str = datetime.fromtimestamp(results[-1].end_time).strftime("%Y-%m-%d %H:%M:%S") \
            if results else start_str

        lines = []
        lines.append("=" * 40)
        lines.append(f"TEST: {test_name}")
        lines.append(f"START: {start_str}")
        lines.append("=" * 40)
        lines.append("")

        for i, result in enumerate(results):
            lines.extend(_render_command_block(result, binary_flags[i]))
            lines.append("")

        lines.append("VALIDATION:")
        if validation_lines:
            lines.extend(validation_lines)
        else:
            lines.append("(no validations declared)")
        lines.append("")

        lines.append("RESULT:")
        lines.append(result_status)
        lines.append("")
        lines.append(f"END: {end_str}")
        lines.append("=" * 40)

        content = "\n".join(lines) + "\n"

        if log_path is None:
            safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in test_name)
            log_path = os.path.join(self.log_dir, f"{safe_name}.log")

        with open(log_path, "w", encoding="utf-8", errors="replace") as f:
            f.write(content)

        return log_path
