#!/usr/bin/env python3
"""fio_metrics.py - common helper: fio `--output-format=json`  ->  `field value` lines.

Every numeric field fio reports is exposed (nothing is hard-coded), so any IO scenario can
validate any field with the framework's native numeric EXPECT:

    RUN "fio ... --output-format=json | python3 tools/fio_metrics.py"
    EXPECT_EXIT 0
    EXPECT "read_iops"        GE {{min_iops}}
    EXPECT "read_clat_ns_p99" LT {{max_p99_ns}}
    EXPECT "error"            EQ 0

Field names are  <direction>_<fio key path>  with '_' separators, e.g.
    read_iops  read_bw  read_bw_bytes  read_io_bytes  read_total_ios  read_runtime
    read_lat_ns_mean  read_clat_ns_max  read_slat_ns_min
    read_clat_ns_p99  read_clat_ns_p99_9  read_clat_ns_p99_99   (percentile 99, 99.9, 99.99)
    read_iops_min/max/mean/stddev   read_bw_min/max/mean/dev   (same for write_, trim_)
plus  total_iops  total_bw  total_bw_bytes  total_io_bytes  total_ios  (read+write+trim)
and job-level fields  error  usr_cpu  sys_cpu  ctx  majf  minf  job_runtime  latency_ns_<bucket> ...
Use `--list` to see every name for your fio version/job.

Usage:
    fio_metrics.py                         all fields, one `name value` per line (stdin)
    fio_metrics.py FILE                    same, from a saved fio JSON file
    fio_metrics.py --metric read_iops      ONLY the number  -> for `RUN ... CAPTURE var`
    fio_metrics.py --metric a --metric b   `name value` lines for just those fields
    fio_metrics.py --list                  field names only
    fio_metrics.py --job NAME|N|all        pick a job (default 0). `all` prefixes names with the job name.
    fio_metrics.py --skip-idle             hide directions that did no IO (e.g. write_* of a read-only job)
    fio_metrics.py --int                   print values truncated to integers

Notes
- With numjobs>1 add `--group_reporting` to fio so one aggregated job is reported.
- Latencies are in nanoseconds (fio >= 3.0). Older fio reports microseconds under `lat`/`clat`:
  those are converted to ns and named lat_ns/clat_ns/slat_ns so names stay the same.
- The framework matches a field as a SUBSTRING on the first line containing it, so this tool
  prints shorter names before longer ones that contain them (read_bw before read_bw_bytes).
  Always use the full field name in EXPECT.
- Values never use exponent notation (the framework's number parser cannot read `1e-05`).
- Exit code: 0 ok, 2 bad input / unknown metric (message on stderr).
"""
import argparse
import json
import re
import sys

DIRECTIONS = ("read", "write", "trim")
SUMMED = ("iops", "bw", "bw_bytes", "io_bytes", "total_ios")
_OLD_LAT = {"slat": "slat_ns", "clat": "clat_ns", "lat": "lat_ns"}


def _clean(key):
    key = (str(key).strip().replace(">=", "ge_").replace("<=", "le_")
           .replace(">", "gt_").replace("<", "lt_"))
    return re.sub(r"[^A-Za-z0-9]+", "_", key).strip("_")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def fmt(v, as_int=False):
    """Number -> text without exponent notation."""
    if as_int:
        return str(int(v))
    if isinstance(v, int):
        return str(v)
    s = ("%.6f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _flatten(obj, prefix, out, parent=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if parent == "percentile":
                try:
                    name = "p" + _clean(fmt(float(k)))          # "99.900000" -> p99_9
                except ValueError:
                    name = _clean(k)
            else:
                name = _clean(k)
            # the "percentile" dict adds no path segment: read_clat_ns_percentile_p99 -> read_clat_ns_p99
            child = prefix if k == "percentile" else ("%s_%s" % (prefix, name) if prefix else name)
            _flatten(v, child, out, parent=str(k))
    elif _num(obj) or isinstance(obj, bool):
        out.append((prefix, int(obj) if isinstance(obj, bool) else obj))
    # strings and lists are not numeric fields -> skipped


def _normalise_direction(d):
    """Old fio (2.x) used lat/clat/slat in usec -> rename to *_ns and convert."""
    d = dict(d)
    for old, new in _OLD_LAT.items():
        if old in d and new not in d and isinstance(d[old], dict):
            blk = {}
            for k, v in d.pop(old).items():
                if k == "percentile" and isinstance(v, dict):
                    blk[k] = {pk: pv * 1000 for pk, pv in v.items()}
                elif k in ("min", "max", "mean", "stddev") and _num(v):
                    blk[k] = v * 1000
                else:
                    blk[k] = v
            d[new] = blk
    return d


def job_fields(job, skip_idle=False):
    """Ordered [(name, value)] for one fio job."""
    fields, totals = [], {k: 0 for k in SUMMED}
    have_total = {k: False for k in SUMMED}
    for dn in DIRECTIONS:
        d = job.get(dn)
        if not isinstance(d, dict):
            continue
        d = _normalise_direction(d)
        idle = not d.get("total_ios") and not d.get("io_bytes")
        for k in SUMMED:
            if _num(d.get(k)):
                totals[k] += d[k]
                have_total[k] = True
        if skip_idle and idle:
            continue
        _flatten(d, dn, fields)
    head = [("total_" + k if k != "total_ios" else "total_ios", totals[k])
            for k in SUMMED if have_total[k]]
    for k, v in job.items():
        if k in DIRECTIONS or k in ("job options", "jobname"):
            continue
        _flatten(v, _clean(k), fields)
    return head + fields


def _substring_safe_order(fields):
    """If a name is a substring of an earlier line's name, move it before that line, so the
    framework's 'first line containing <field>' always finds the intended field first."""
    out = []
    for name, val in fields:
        pos = next((i for i, (n, _) in enumerate(out) if name in n), None)
        if pos is None:
            out.append((name, val))
        else:
            out.insert(pos, (name, val))
    return out


def load_fio_json(text):
    start = text.find("{")                      # fio may print warnings before the JSON
    if start < 0:
        raise ValueError("no JSON found in input (did fio fail? use --output-format=json)")
    data, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list) or not data["jobs"]:
        raise ValueError("JSON has no 'jobs' list - not fio --output-format=json output")
    return data


def collect(data, job_sel="0", skip_idle=False):
    jobs = data["jobs"]
    if job_sel == "all":
        fields, used = [], set()
        for i, job in enumerate(jobs):
            p = _clean(job.get("jobname", "job%d" % i)) or "job%d" % i
            if p in used:
                p = "%s_%d" % (p, i)
            used.add(p)
            fields += [("%s_%s" % (p, n), v) for n, v in job_fields(job, skip_idle)]
    else:
        if job_sel.isdigit():
            idx = int(job_sel)
            if idx >= len(jobs):
                raise ValueError("job index %d out of range (%d job(s))" % (idx, len(jobs)))
            job = jobs[idx]
        else:
            match = [j for j in jobs if j.get("jobname") == job_sel]
            if not match:
                raise ValueError("no job named %r (have: %s)" % (
                    job_sel, ", ".join(str(j.get("jobname")) for j in jobs)))
            job = match[0]
        fields = job_fields(job, skip_idle)
    # disk_util: per-device stats, only numeric fields
    for du in data.get("disk_util", []) or []:
        if isinstance(du, dict) and du.get("name"):
            _flatten({k: v for k, v in du.items() if k != "name"}, "disk_util_" + _clean(du["name"]), fields)
    return _substring_safe_order(fields)


def main(argv=None):
    ap = argparse.ArgumentParser(description="fio JSON -> `field value` lines (see module docstring)")
    ap.add_argument("file", nargs="?", help="fio JSON file (default: stdin)")
    ap.add_argument("--metric", action="append", help="field to print (repeatable)")
    ap.add_argument("--job", default="0", help="job index, job name, or 'all' (default 0)")
    ap.add_argument("--list", action="store_true", help="print field names only")
    ap.add_argument("--skip-idle", action="store_true", help="hide directions with no IO")
    ap.add_argument("--int", dest="as_int", action="store_true", help="truncate values to integers")
    args = ap.parse_args(argv)
    try:
        text = open(args.file).read() if args.file else sys.stdin.read()
        fields = collect(load_fio_json(text), args.job, args.skip_idle)
    except (OSError, ValueError) as exc:
        print("fio_metrics: %s" % exc, file=sys.stderr)
        return 2
    table = dict(fields)
    if args.list:
        print("\n".join(n for n, _ in fields))
    elif args.metric:
        missing = [m for m in args.metric if m not in table]
        if missing:
            near = sorted({n for n in table for m in missing if m.split("_")[-1] in n})[:8]
            print("fio_metrics: unknown metric(s): %s%s" % (
                ", ".join(missing), ("  (similar: %s)" % ", ".join(near)) if near else ""), file=sys.stderr)
            return 2
        if len(args.metric) == 1:
            print(fmt(table[args.metric[0]], args.as_int))      # bare number -> CAPTURE-friendly
        else:
            for m in args.metric:
                print("%s %s" % (m, fmt(table[m], args.as_int)))
    else:
        for n, v in fields:
            print("%s %s" % (n, fmt(v, args.as_int)))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:          # e.g. `... | head`; not an error
        sys.stderr.close()
        sys.exit(0)
