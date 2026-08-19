#!/usr/bin/env python3
"""
run.py - CLI entry point + internal self-verification.

Two ways to use this file:

  1. CLI mode (Phase 3) -- run real .nvtest file(s):
         python3 run.py tests/TC001_success.nvtest      (single file)
         python3 run.py tests/                           (directory;
                                                            only *.nvtest
                                                            files inside
                                                            are executed)
     Prints a concise PASS/FAIL/ERROR table and exits 0 if everything
     passed, 1 if anything failed/errored, 2 for a usage/path error.

  2. No-argument mode -- runs this framework's own internal
     self-verification (Phase 1 smoke checks + Phase 2/3 .nvtest checks),
     with no pytest and no external test framework. This is what
     "python3 run.py" (no arguments) has done since Phase 1; it still does
     that, unchanged, and now additionally verifies the CLI/discovery
     layer itself using the existing safe tests/ suite.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from framework.executor import Executor
from framework.logger import Logger
from framework.parser import ParseError, parse_file
from framework.runner import run_nvtest_file, UnsupportedFileTypeError
from framework import cli


def run_command(test_name, command, binary_output=False, logger=None, executor=None):
    """Convenience wrapper: execute a command and write its .log.

    This is the shape later phases (the .nvtest parser) will call into:
    given a test name and a command line, run it and produce exactly one
    log file, and hand back the CommandResult for optional further checks.
    """
    executor = executor or Executor()
    logger = logger or Logger()

    result = executor.run(command)
    log_path = logger.write_log(test_name, result, binary_output=binary_output)
    return result, log_path


# ---------------------------------------------------------------------------
# Smoke verification (no pytest, per spec). Each function asserts a basic
# property of the core engine and prints PASS/FAIL. Any AssertionError
# propagates and aborts the run with a non-zero exit code.
# ---------------------------------------------------------------------------

def smoke_success():
    """A command that succeeds: exit code 0, expected text in stdout."""
    result, log_path = run_command("smoke_success", "echo hello_nvme_test")
    assert result.exit_code == 0, f"expected exit 0, got {result.exit_code}"
    assert b"hello_nvme_test" in result.stdout, "expected stdout to contain echoed text"
    print(f"[PASS] smoke_success -> {log_path}")


def smoke_failure():
    """A command that fails: non-zero exit code, stderr captured."""
    result, log_path = run_command("smoke_failure", "ls /this_path_should_not_exist_xyz")
    assert result.exit_code != 0, "expected non-zero exit code for missing path"
    assert len(result.stderr) > 0, "expected stderr output for failed command"
    print(f"[PASS] smoke_failure -> {log_path}")


def smoke_binary():
    """A command that emits raw binary data on stdout (mocked, no NVMe
    hardware required). Verifies binary bytes survive the executor untouched
    and get hex-dumped into the log rather than written as a .bin file."""
    # Mock binary output: write the full 0-255 byte range to stdout.
    cmd = "python3 -c \"import sys; sys.stdout.buffer.write(bytes(range(256)))\""
    result, log_path = run_command("smoke_binary", cmd, binary_output=True)

    assert result.exit_code == 0, f"expected exit 0, got {result.exit_code}"
    assert len(result.stdout) == 256, f"expected 256 raw bytes, got {len(result.stdout)}"
    assert result.stdout == bytes(range(256)), "raw bytes were altered/corrupted"

    # Confirm no sidecar .bin file was created anywhere in logs/.
    log_dir = os.path.dirname(log_path)
    bin_files = [f for f in os.listdir(log_dir) if f.endswith(".bin")]
    assert not bin_files, f"unexpected .bin file(s) created: {bin_files}"

    print(f"[PASS] smoke_binary -> {log_path}")


TESTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests")


def _expect_result(filename, expected_status):
    """Run a .nvtest file and assert it produced the expected overall PASS/FAIL."""
    path = os.path.join(TESTS_DIR, filename)
    result = run_nvtest_file(path)
    assert result.status == expected_status, (
        f"{filename}: expected {expected_status}, got {result.status}"
    )
    for passed, msg in result.validation_lines:
        tag = "PASS" if passed else "FAIL"
        print(f"    [{tag}] {msg}")
    print(f"[{expected_status}] {filename} -> {result.status} (log: {result.log_path})")
    return result


def nvtest_success():
    """TC001: every validation should pass -> overall PASS."""
    _expect_result("TC001_success.nvtest", "PASS")


def nvtest_failed_validation():
    """TC002: a text validation deliberately fails -> overall FAIL, but the
    test still executes and all validations still run (no short-circuit)."""
    result = _expect_result("TC002_failed_validation.nvtest", "FAIL")
    statuses = [passed for passed, _ in result.validation_lines]
    assert len(statuses) == 4, "expected all 4 validations to have run despite the failure"
    assert statuses.count(False) == 1, "expected exactly one failing validation (Model Number)"


def nvtest_byte_validation():
    """TC003: EXPECT_BYTE checks against raw stdout bytes -> overall PASS."""
    _expect_result("TC003_byte_validation.nvtest", "PASS")


def nvtest_hex_validation():
    """TC004: EXPECT_HEX checks against raw stdout bytes -> overall PASS."""
    _expect_result("TC004_hex_validation.nvtest", "PASS")


def nvtest_invalid_syntax():
    """TC005: malformed .nvtest syntax must be rejected with ParseError,
    and must NOT produce a .log file (nothing should have executed)."""
    path = os.path.join(TESTS_DIR, "TC005_invalid_syntax.nvtest")
    log_path = os.path.join("logs", "Invalid_Syntax_Example.log")
    if os.path.exists(log_path):
        os.remove(log_path)

    try:
        run_nvtest_file(path)
        raise AssertionError("expected ParseError for invalid .nvtest syntax, but none was raised")
    except ParseError as exc:
        assert not os.path.exists(log_path), "invalid syntax must not produce a .log file"
        print(f"[PASS] nvtest_invalid_syntax -> correctly rejected: {exc}")


def nvtest_unsupported_extension():
    """TC006: a .yaml file must be rejected outright -- never parsed or
    silently interpreted as a test case."""
    path = os.path.join(TESTS_DIR, "TC006_unsupported_format.yaml")
    try:
        run_nvtest_file(path)
        raise AssertionError("expected UnsupportedFileTypeError for a .yaml file, but none was raised")
    except UnsupportedFileTypeError as exc:
        print(f"[PASS] nvtest_unsupported_extension -> correctly rejected: {exc}")


EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "examples")

# Files under tests/examples/ that require real NVMe hardware and/or would
# be destructive to a real block device. The framework's own verification
# never executes these -- it only confirms they PARSE as valid .nvtest
# syntax, which is safe and requires no hardware.
_HARDWARE_OR_DESTRUCTIVE_EXAMPLES = {
    "TC001_nvme_list.nvtest",               # needs nvme-cli + a device to be meaningful
    "TC002_identify_ctrl.nvtest",           # needs real /dev/nvme0
    "TC003_smartlog.nvtest",                # needs real /dev/nvme0
    "TC006_fio_write_DESTRUCTIVE.nvtest",   # writes to a raw block device
    "TC007_admin_passthru_HARDWARE_REQUIRED.nvtest",
    "TC008_io_passthru_HARDWARE_REQUIRED.nvtest",
}

# Examples that are genuinely safe to execute (no hardware, no destructive
# writes) and so are actually run -- not just parsed -- as part of
# verification. Their PASS/FAIL outcome depends on the real environment
# (e.g. TC004 legitimately fails on a machine with no NVMe device), so only
# "it ran and produced a result" is asserted, not a specific status.
_SAFE_RUNNABLE_EXAMPLES = {
    "TC004_check_nvme_device.nvtest",  # lsblk; read-only, always safe
    "TC005_fio_safe_smoke.nvtest",     # fio against a /tmp file, not a device
}


def cli_single_file():
    """CLI mode, single file: `python3 run.py tests/TC001_success.nvtest`."""
    rc = cli.main(["tests/TC001_success.nvtest"])
    assert rc == 0, f"expected exit code 0 for a passing single file, got {rc}"
    print("[PASS] cli_single_file")


def cli_directory():
    """CLI mode, directory: `python3 run.py tests/`.

    tests/ contains a mix of PASS, FAIL, invalid-syntax (.nvtest that fails
    to parse), and a non-.nvtest file (.yaml) that must be silently
    ignored by discovery. One directory invocation exercises all of it.
    """
    targets = cli.discover_targets(TESTS_DIR)
    discovered_names = {os.path.basename(t.path) for t in targets}
    assert "TC006_unsupported_format.yaml" not in discovered_names, (
        "directory discovery must ignore non-.nvtest files entirely"
    )
    assert all(name.endswith(".nvtest") for name in discovered_names), (
        "directory discovery must only pick up *.nvtest files"
    )

    rows = cli.run_targets(targets)
    status_by_label = {label: status for label, status, _ in rows}

    assert status_by_label["TC001_success"] == "PASS"
    assert status_by_label["TC002_failed_validation"] == "FAIL"
    assert status_by_label["TC003_byte_validation"] == "PASS"
    assert status_by_label["TC004_hex_validation"] == "PASS"
    assert status_by_label["TC005_invalid_syntax"] == "ERROR"

    # PASS/FAIL rows get a .log; ERROR (parse failure) rows must not.
    for label, status, detail in rows:
        log_path = os.path.join("logs", f"{label}.log")
        if status == "ERROR":
            continue  # detail is the error message here, not a log path
        assert os.path.exists(detail), f"expected a .log file for {label} at {detail}"

    cli.print_summary(rows)
    print("[PASS] cli_directory (single directory run covers PASS/FAIL/ERROR/ignored-extension)")


def cli_rejects_unsupported_extension():
    """CLI mode must refuse a single non-.nvtest file, not execute it."""
    rc = cli.main(["tests/TC006_unsupported_format.yaml"])
    assert rc == 1, f"expected exit code 1 for a rejected file, got {rc}"
    print("[PASS] cli_rejects_unsupported_extension")


def examples_parse_cleanly():
    """Every example under tests/examples/ (including the hardware-required
    and destructive ones) must be syntactically valid .nvtest -- proven by
    parsing only, never executing the hardware/destructive ones."""
    example_files = sorted(f for f in os.listdir(EXAMPLES_DIR) if f.endswith(".nvtest"))
    assert example_files, "expected example .nvtest files under tests/examples/"
    for filename in example_files:
        path = os.path.join(EXAMPLES_DIR, filename)
        test_case = parse_file(path)  # raises ParseError if malformed
        assert test_case.commands, f"{filename}: expected at least one RUN command"
    print(f"[PASS] examples_parse_cleanly ({len(example_files)} files under tests/examples/)")


def examples_safe_ones_execute():
    """The examples with no hardware/destructive requirement are actually
    executed (real lsblk, real file-based fio) to prove the framework
    genuinely drives real commands end-to-end, not just mock ones."""
    for filename in sorted(_SAFE_RUNNABLE_EXAMPLES):
        path = os.path.join(EXAMPLES_DIR, filename)
        result = run_nvtest_file(path)
        assert result.status in ("PASS", "FAIL"), (
            f"{filename}: expected the command to actually execute (PASS or FAIL), got {result.status}"
        )
        assert os.path.exists(result.log_path), f"{filename}: expected a .log file"
        print(f"    {filename}: {result.status} (log: {result.log_path})")
    print(f"[PASS] examples_safe_ones_execute ({len(_SAFE_RUNNABLE_EXAMPLES)} safe examples actually run)")


def examples_hardware_ones_not_auto_executed():
    """Sanity check that verification's own list of "don't auto-run" files
    matches every hardware/destructive example that actually exists on
    disk -- so a newly added risky example can't be silently auto-run by
    accident."""
    example_files = {f for f in os.listdir(EXAMPLES_DIR) if f.endswith(".nvtest")}
    accounted_for = _HARDWARE_OR_DESTRUCTIVE_EXAMPLES | _SAFE_RUNNABLE_EXAMPLES
    assert example_files == accounted_for, (
        f"unaccounted example file(s): {example_files - accounted_for}"
    )
    print("[PASS] examples_hardware_ones_not_auto_executed (destructive/hardware examples never executed)")


def main():
    print("Running Phase 1 smoke verification...\n")
    smoke_success()
    smoke_failure()
    smoke_binary()
    print("\nAll Phase 1 smoke tests passed.")

    print("\nRunning Phase 2 .nvtest verification...\n")
    nvtest_success()
    nvtest_failed_validation()
    nvtest_byte_validation()
    nvtest_hex_validation()
    nvtest_invalid_syntax()
    nvtest_unsupported_extension()
    print("\nAll Phase 2 verification checks passed.")

    print("\nRunning Phase 3 CLI + example verification...\n")
    cli_single_file()
    cli_directory()
    cli_rejects_unsupported_extension()
    examples_parse_cleanly()
    examples_safe_ones_execute()
    examples_hardware_ones_not_auto_executed()
    print("\nAll Phase 3 verification checks passed.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(cli.main(sys.argv[1:]))
    main()
