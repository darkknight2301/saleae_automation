"""
utility.py - Small, genuinely reusable helpers shared across the framework.

Per the Phase 1 architecture review: this is the ONE common utility module.
No test-specific or business logic here -- only generic string/hex/time
helpers that more than one framework module needs.
"""

import uuid
from datetime import datetime

BYTES_PER_LINE = 16
TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S"
RUN_DIR_FMT = "%Y%m%d_%H%M%S"


def hex_dump(data: bytes, bytes_per_line: int = BYTES_PER_LINE) -> str:
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


def safe_filename(name: str) -> str:
    """Sanitize a test name into a safe filename stem (no extension)."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


def format_timestamp(epoch_seconds: float) -> str:
    """Format an epoch time as the standard log timestamp string."""
    return datetime.fromtimestamp(epoch_seconds).strftime(TIMESTAMP_FMT)


def new_run_id() -> str:
    """Generate a unique id for one framework execution/run directory
    (logs/{run_id}/): YYYYMMDD_HHMMSS_<8-hex>. The random suffix (review
    F-5) guarantees uniqueness per TestRunner instance -- a PID or
    timestamp alone is not enough, since multiple TestRunner instances
    are commonly constructed within the same second in the same process
    (e.g. this framework's own self-verification harness)."""
    return f"{datetime.now().strftime(RUN_DIR_FMT)}_{uuid.uuid4().hex[:8]}"


def parse_int_maybe_hex(token: str) -> int:
    """Parse a string as an int, accepting both decimal ("16") and hex
    ("0x10") forms. Raises ValueError on anything else -- callers attach
    their own context (line number, field name, etc.) to the error.
    """
    return int(token, 0) if token.lower().startswith("0x") else int(token)
