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
import time

from .utility import hex_dump as _hex_dump, safe_filename, format_timestamp


def _resolve_log_path(log_dir: str, test_name: str, log_path: str = None) -> str:
    """Default log path: <log_dir>/<safe test name>.log, unless overridden."""
    if log_path is not None:
        return log_path
    return os.path.join(log_dir, f"{safe_filename(test_name)}.log")


def _header(test_name: str, start_str: str):
    return ["=" * 40, f"TEST: {test_name}", f"START: {start_str}", "=" * 40, ""]


def _footer(result_status: str, end_str: str):
    return ["", "RESULT:", result_status, "", f"END: {end_str}", "=" * 40]


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


class ResultLogger:
    """Formats a CommandResult into the standard test log and writes it to
    disk, inside one run directory shared by every test in the same
    framework execution (logs/{run_id}/<test>.log)."""

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

        start_str = format_timestamp(result.start_time)
        end_str = format_timestamp(result.end_time)

        lines = _header(test_name, start_str)
        # stderr section (if any) is included automatically by the shared
        # block renderer, keeping clean-run logs matching the spec template.
        lines.extend(_render_command_block(result, binary_output))
        lines.extend(_footer(result_status, end_str))

        content = "\n".join(lines) + "\n"
        log_path = _resolve_log_path(self.log_dir, test_name, log_path)

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
        filename_stem: str = None,
    ) -> str:
        """Write a single .log file for a `.nvtest` test case (Phase 2).

        Unlike write_log() (one command, no validations), a `.nvtest` test
        case may RUN more than one command and always has a VALIDATION
        section. Still exactly one .log file, still no .bin files, still a
        hex dump for binary output -- reuses the same block renderer and
        hex_dump as Phase 1 for consistency.

        Args:
            test_name:        name shown in the TEST: header (free text,
                                from the .nvtest file's TEST "..." line).
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
            log_path:          explicit output path; overrides everything
                                below if given.
            filename_stem:     filename (no extension) to use for the
                                default log_path, e.g. the source .nvtest
                                file's basename. Two different .nvtest files
                                can declare the same free-text TEST name, so
                                the filename is keyed off the source file,
                                not test_name (review F-2: using test_name
                                for the filename let two unrelated files
                                silently overwrite each other's .log).
                                Falls back to test_name if not given, for
                                callers with no source file (e.g. ad hoc use).

        Returns:
            The path of the log file written.
        """
        assert len(results) == len(binary_flags), "results/binary_flags length mismatch"

        start_str = format_timestamp(results[0].start_time) if results else format_timestamp(time.time())
        end_str = format_timestamp(results[-1].end_time) if results else start_str

        lines = _header(test_name, start_str)

        for i, result in enumerate(results):
            lines.extend(_render_command_block(result, binary_flags[i]))
            lines.append("")

        lines.append("VALIDATION:")
        if validation_lines:
            lines.extend(validation_lines)
        else:
            lines.append("(no validations declared)")

        lines.extend(_footer(result_status, end_str))

        content = "\n".join(lines) + "\n"
        log_path = _resolve_log_path(self.log_dir, filename_stem or test_name, log_path)

        with open(log_path, "w", encoding="utf-8", errors="replace") as f:
            f.write(content)

        return log_path


# Backward-compat alias (Logger was renamed to ResultLogger).
Logger = ResultLogger
