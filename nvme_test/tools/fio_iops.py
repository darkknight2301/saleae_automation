#!/usr/bin/env python3
"""Kept for existing tests. Prints total IOPS (read+write+trim) as an integer.
New tests should use the common tool:  python3 tools/fio_metrics.py --metric total_iops"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fio_metrics  # noqa: E402

sys.exit(fio_metrics.main(["--metric", "total_iops", "--int"] + sys.argv[1:]))
