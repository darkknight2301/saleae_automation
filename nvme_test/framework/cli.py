"""
cli.py - Command-line entry point logic for running `.nvtest` files.

Design intent (Phase 3):
- Two invocation shapes only, per spec:
      python3 run.py tests/TC001.nvtest      (single file)
      python3 run.py tests/                  (directory)
- `.nvtest` is the only extension ever executed. A directory scan discovers
  only `*.nvtest` and silently ignores everything else (.py, .yaml, .txt,
  etc. are never even opened). A single file path that doesn't end in
  `.nvtest` is rejected the same way runner.py already rejects it
  (UnsupportedFileTypeError) -- reported, not crashed, and not executed.
- Output is a concise PASS/FAIL table plus a Total/Passed/Failed summary,
  per spec. No pytest, no other reporting framework.
- Discovery order is deterministic (sorted) so output is reproducible.
"""

import glob
import os
import sys

from .parser import ParseError
from .runner import run_nvtest_file, UnsupportedFileTypeError, SUPPORTED_EXTENSION


class CliTarget:
    """One file that will be run and reported on."""

    def __init__(self, path):
        self.path = path
        self.label = os.path.splitext(os.path.basename(path))[0]


def discover_targets(path):
    """Resolve a CLI path argument into a list of CliTarget to run.

    - A directory: only *.nvtest files inside it, sorted, non-recursive.
      Every other file extension is ignored -- never opened, never parsed.
    - A file: that single path, whatever its extension. (Non-.nvtest files
      are still returned here; run_targets() reports them as rejected via
      the same UnsupportedFileTypeError the runner already raises, so
      there's one rejection code path rather than two.)

    Raises:
        FileNotFoundError: if `path` does not exist at all.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"No such file or directory: {path!r}")

    if os.path.isdir(path):
        pattern = os.path.join(path, f"*{SUPPORTED_EXTENSION}")
        return [CliTarget(p) for p in sorted(glob.glob(pattern))]

    return [CliTarget(path)]


def run_targets(targets, executor=None, logger=None):
    """Run each target and collect (label, status, detail) rows.

    status is one of "PASS", "FAIL", or "ERROR":
      - PASS/FAIL come from the validator (a single failed validation makes
        the test FAIL, per Phase 2 rules).
      - ERROR means the file was never executed at all -- either it was
        rejected for having the wrong extension, or its .nvtest syntax was
        invalid. Either way, no .log file is produced for it (consistent
        with Phase 2 behavior).
    """
    rows = []
    for target in targets:
        try:
            result = run_nvtest_file(target.path, executor=executor, logger=logger)
            rows.append((target.label, result.status, result.log_path))
        except UnsupportedFileTypeError as exc:
            rows.append((target.label, "ERROR", str(exc)))
        except ParseError as exc:
            rows.append((target.label, "ERROR", str(exc).splitlines()[0]))
    return rows


def print_summary(rows, stream=sys.stdout):
    """Print the concise PASS/FAIL table + Total/Passed/Failed summary."""
    if not rows:
        print("No .nvtest files found.", file=stream)
        return

    width = max(len(label) for label, _, _ in rows) + 4
    passed = 0
    failed = 0

    for label, status, detail in rows:
        print(f"{label:<{width}}{status}", file=stream)
        if status == "PASS":
            passed += 1
        else:
            failed += 1
            if status == "ERROR":
                print(f"{'':<{width}}  -> {detail}", file=stream)

    print("", file=stream)
    print(f"Total: {len(rows)}", file=stream)
    print(f"Passed: {passed}", file=stream)
    print(f"Failed: {failed}", file=stream)


def main(argv):
    """CLI entry point. `argv` is sys.argv[1:] (the path argument(s)).

    Returns a process exit code: 0 if every discovered test PASSed
    (or none were discovered), 1 otherwise.
    """
    if len(argv) != 1:
        print("Usage: python3 run.py <path-to.nvtest | tests-directory>", file=sys.stderr)
        return 2

    path = argv[0]
    try:
        targets = discover_targets(path)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    rows = run_targets(targets)
    print_summary(rows)

    failed = any(status != "PASS" for _, status, _ in rows)
    return 1 if failed else 0
