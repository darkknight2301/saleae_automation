#!/usr/bin/env python3
"""nvme_util.py - generic helpers that turn nvme-cli / fio-style output into `field value` lines,
so the framework's native EXPECT (EQ/NEQ/GT/GE/LT/LE/CONTAINS) can validate it.

  fields                 JSON (stdin, any `nvme ... -o json`) -> every numeric field, lists indexed (name_0_x)
  id-ns                  id-ns JSON -> lbaf_in_use lba_bytes lbaf_count lbaf_other lbaf_invalid nsze
  ns-present NSID        list-ns text or JSON -> ns_present 1|0
  feature-value          get/set-feature text -> feature_value <decimal>  feature_hex <0x..>
  kv KEY                 `KEY : value` text (id-ctrl, smart-log text ...) -> KEY <value>
  afi                    fw-log text -> active_slot next_slot
  sanitize-state [--done]  sanitize-log JSON/text -> san_status san_progress san_progress_pct san_gde san_in_progress
                         (--done: print nothing, exit 0 only when no sanitize is in progress  -> for wait_until.sh)
  errlog-entries         error-log JSON -> errlog_entries (entries with a non-zero error_count)
  save FILE              JSON (stdin) -> baseline snapshot file
  delta-from FILE [--update]  JSON (stdin) -> current `name value` lines, then `d_<name> <now - baseline>` lines
  monotonic [--tol 0.05] `name value` lines (stdin) -> non_monotonic <count of decreases>
  pattern FILE BYTES SEED  write deterministic pseudo-random data -> pattern_bytes N
Common options: --metric NAME (repeatable; only those fields)  --bare (print just the number/value of the first line)  --list
Exit 2 + message on stderr when the input does not contain what the subcommand needs (so EXPECT_EXIT 0 catches a failed command)."""
import argparse, json, os, random, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fio_metrics import _clean, fmt, _substring_safe_order  # noqa: E402


class Bad(Exception):
    pass


def load_json(text):
    m = re.search(r"[\[{]", text)
    if not m:
        raise Bad("no JSON in input (did the nvme command fail?)")
    return json.JSONDecoder().raw_decode(text[m.start():])[0]


def flat(obj, prefix="", out=None):
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            flat(v, "%s_%s" % (prefix, _clean(k)) if prefix else _clean(k), out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            flat(v, "%s_%d" % (prefix, i), out)
    elif isinstance(obj, bool):
        out.append((prefix, int(obj)))
    elif isinstance(obj, (int, float)):
        out.append((prefix, obj))
    return out


def num(x):
    return fmt(x) if isinstance(x, (int, float)) else str(x)


def emit(pairs, a):
    d = dict(pairs)
    if a.list:
        print("\n".join(n for n, _ in pairs)); return
    if a.metric:
        miss = [m for m in a.metric if m not in d]
        if miss:
            raise Bad("unknown metric(s): %s (have: %s)" % (", ".join(miss), ", ".join(list(d)[:12])))
        pairs = [(m, d[m]) for m in a.metric]
    if a.bare:
        print(num(pairs[0][1])); return
    for n, v in pairs:
        print("%s %s" % (n, num(v)))


def hexint(s):
    return int(s, 16) if str(s).lower().startswith("0x") else int(s)


def main(argv=None):
    com = argparse.ArgumentParser(add_help=False)
    com.add_argument("--metric", action="append"); com.add_argument("--bare", action="store_true"); com.add_argument("--list", action="store_true")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for name in ("fields", "id-ns", "feature-value", "afi", "errlog-entries", "sanitize-state"):
        p = sp.add_parser(name, parents=[com])
        if name == "sanitize-state":
            p.add_argument("--done", action="store_true")
    for name in ("ns-present", "kv"):
        sp.add_parser(name, parents=[com]).add_argument("arg")
    sp.add_parser("save", parents=[com]).add_argument("file")
    p = sp.add_parser("delta-from", parents=[com]); p.add_argument("file"); p.add_argument("--update", action="store_true")
    p = sp.add_parser("monotonic", parents=[com]); p.add_argument("--tol", type=float, default=0.05)
    p = sp.add_parser("pattern", parents=[com]); p.add_argument("file"); p.add_argument("nbytes", type=int); p.add_argument("seed", type=int)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "pattern":
            data = random.Random(a.seed).getrandbits(8 * a.nbytes).to_bytes(a.nbytes, "little")
            open(a.file, "wb").write(data); print("pattern_bytes %d" % len(data)); return 0
        text = sys.stdin.read()
        if a.cmd == "fields":
            emit(_substring_safe_order(flat(load_json(text))), a)
        elif a.cmd == "id-ns":
            d = load_json(text); fl = d["flbas"]; idx = (fl & 0xF) | (((fl >> 5) & 3) << 4); L = d["lbafs"]
            other = next((i for i, x in enumerate(L) if i != idx and x["ms"] == 0 and x["ds"] != L[idx]["ds"]),
                         next((i for i, x in enumerate(L) if i != idx and x["ms"] == 0), -1))
            emit([("lbaf_in_use", idx), ("lba_bytes", 1 << L[idx]["ds"]), ("lbaf_count", len(L)), ("lbaf_other", other),
                  ("lbaf_invalid", len(L) if len(L) < 16 else -1), ("nsze", d["nsze"])], a)
        elif a.cmd == "ns-present":
            n = int(a.arg)
            hit = re.search(r"0x0*%x\b" % n, text) or re.search(r'"nsid"\s*:\s*%d\b' % n, text)
            emit([("ns_present", 1 if hit else 0)], a)
        elif a.cmd == "feature-value":
            m = re.search(r"value\s*:\s*(0x[0-9a-fA-F]+|\d+)", text, re.I)
            if not m:
                raise Bad("no 'value:' found in get/set-feature output")
            v = hexint(m.group(1)); emit([("feature_value", v), ("feature_hex", "0x%x" % v)], a)
        elif a.cmd == "kv":
            m = re.search(r"^\s*%s\s*:\s*(.*?)\s*$" % re.escape(a.arg), text, re.M)
            if not m:
                raise Bad("key %r not found" % a.arg)
            emit([(a.arg, m.group(1))], a)
        elif a.cmd == "afi":
            m = re.search(r"\bafi\s*:\s*(0x[0-9a-fA-F]+|\d+)", text)
            if not m:
                raise Bad("no 'afi' in fw-log output")
            v = hexint(m.group(1)); emit([("active_slot", v & 7), ("next_slot", (v >> 4) & 7)], a)
        elif a.cmd == "sanitize-state":
            try:
                d = dict(flat(load_json(text))); sp_, ss = d["sprog"], d["sstat"]
            except (Bad, KeyError):
                m1, m2 = re.search(r"SPROG\)?\s*:\s*(\d+)", text), re.search(r"SSTAT\)?\s*:\s*(0x[0-9a-fA-F]+|\d+)", text)
                if not (m1 and m2):
                    raise Bad("no sprog/sstat in sanitize-log output")
                sp_, ss = int(m1.group(1)), hexint(m2.group(1))
            st = ss & 7
            if a.done:
                return 1 if st == 2 else 0
            emit([("san_status", st), ("san_progress", sp_), ("san_progress_pct", round(sp_ * 100.0 / 65536, 2)),
                  ("san_gde", (ss >> 8) & 1), ("san_in_progress", 1 if st == 2 else 0)], a)
        elif a.cmd == "errlog-entries":
            def count(o):
                if isinstance(o, dict):
                    return (1 if o.get("error_count", 0) else 0) + sum(count(v) for v in o.values())
                return sum(count(v) for v in o) if isinstance(o, list) else 0
            emit([("errlog_entries", count(load_json(text)))], a)
        elif a.cmd == "save":
            f = flat(load_json(text)); json.dump(dict(f), open(a.file, "w")); print("saved_fields %d" % len(f))
        elif a.cmd == "delta-from":
            cur = _substring_safe_order(flat(load_json(text))); base = json.load(open(a.file))
            for n, v in cur:
                print("%s %s" % (n, num(v)))
            for n, v in cur:
                if n in base:
                    print("d_%s %s" % (n, num(v - base[n])))
            if a.update:
                json.dump(dict(cur), open(a.file, "w"))
        elif a.cmd == "monotonic":
            vals = [float(l.split()[-1]) for l in text.splitlines() if l.split()]
            print("non_monotonic %d" % sum(1 for x, y in zip(vals, vals[1:]) if y < x * (1 - a.tol)))
    except BrokenPipeError:
        return 0
    except (Bad, OSError, KeyError, ValueError) as e:
        print("nvme_util: %s" % e, file=sys.stderr); return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
