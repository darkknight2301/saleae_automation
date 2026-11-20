"""Shared state + helpers for the mock nvme / fio simulators.
NOT NVMe-compliant: it only exists so the .nvtest suite can be exercised without hardware.
State lives in $MOCK_NVME_DIR (default /tmp/mock_suite). Failure-injection env switches:
  MOCK_CORRUPT_READ=1    nvme read returns a flipped byte        MOCK_RESET_LOSES_DATA=1  reset wipes the namespace
  MOCK_NO_SAVE=1         set-feature -s is ignored               MOCK_FW_NOACT=1          firmware never activates
  MOCK_MEDIA_ERR=1       every fio run adds a media error        MOCK_SAN_FAIL=1          sanitize ends in 'failed'
  MOCK_SLOW_IO=1         fio IOPS x0.001                         MOCK_FIO_SLEEP=<s>       fio wall time (default 3)
  MOCK_RESET_KEEPS_FEATURES=1  reset does not revert unsaved features   MOCK_FORMAT_IGNORED=1  format does not change the LBA format
  MOCK_QD_COLLAPSE=1     IOPS drops x0.25 at QD>=64"""
import contextlib, fcntl, json, os, re, sys, time

D = os.environ.get("MOCK_NVME_DIR", "/tmp/mock_suite")
LBAF = [(0, 9), (0, 12), (8, 9), (8, 12)]          # (metadata bytes, lbads)
TNVMCAP = 4 << 30
CNTLID = 65
FEATURES = {1: "Arbitration", 2: "Power Management", 4: "Temperature Threshold", 7: "Number of Queues", 8: "Interrupt Coalescing"}


def _init():
    return {"fr": "FW_1.0", "slots": {"1": "FW_1.0", "2": "FW_1.0"}, "active": 1, "pending": None, "fwbuf": None,
            "feat": {str(f): {"d": {4: 0x14d, 7: 0x70007}.get(f, 0), "c": {4: 0x14d, 7: 0x70007}.get(f, 0), "s": None} for f in FEATURES},
            "ns": {"1": {"nsze": 1048576, "flbas": 0, "att": True}},
            "sm": {"rd_bytes": 0, "wr_bytes": 0, "hrc": 0, "hwc": 0, "media": 0, "errs": 0, "cw": 0, "temp": 310},
            "reset_until": 0.0, "reset_epoch": 0, "san": {"t0": 0.0, "dur": 3.0, "state": 0}}


@contextlib.contextmanager
def state():
    os.makedirs(D, exist_ok=True)
    with open(D + "/lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        p = D + "/state.json"
        st = json.load(open(p)) if os.path.exists(p) else _init()
        yield st
        json.dump(st, open(p, "w"))


def fail(msg, code):
    sys.stderr.write("NVMe status: %s(0x%x)\n" % (msg, code))
    sys.exit((code & 0xff) or 1)


def check_up(st):
    if time.time() < st["reset_until"]:
        sys.stderr.write("Failed to open device: Input/output error (controller resetting)\n")
        sys.exit(1)


def nsid_of(dev):
    m = re.search(r"nvme\d+n(\d+)$", dev or "")
    return m.group(1) if m else None


def lba_bytes(ns):
    return 1 << LBAF[ns["flbas"] & 0xF][1]


def img(nsid):
    return "%s/ns%s.img" % (D, nsid)


def unalloc(st):
    used = sum(n["nsze"] * lba_bytes(n) for n in st["ns"].values())
    return max(TNVMCAP - used, 0)


def smart(st):
    s = st["sm"]
    return {"critical_warning": s["cw"], "temperature": s["temp"], "avail_spare": 100, "spare_thresh": 10, "percent_used": 1,
            "data_units_read": s["rd_bytes"] // 512000, "data_units_written": s["wr_bytes"] // 512000,
            "host_read_commands": s["hrc"], "host_write_commands": s["hwc"], "controller_busy_time": 3, "power_cycles": 5,
            "power_on_hours": 100, "unsafe_shutdowns": 1, "media_errors": s["media"], "num_err_log_entries": s["errs"],
            "warning_temp_time": 0, "critical_comp_time": 0}


def parse(argv, flags=()):
    pos, o, i = [], {}, 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            k, eq, v = a[2:].partition("=")
            if not eq:
                if k in flags or i + 1 >= len(argv) or argv[i + 1].startswith("-"):
                    v = True
                else:
                    i += 1; v = argv[i]
            o[k] = v
        elif len(a) == 2 and a[0] == "-" and a[1].isalpha():
            k = a[1]
            if k in flags or i + 1 >= len(argv):
                o[k] = True
            else:
                i += 1; o[k] = argv[i]
        else:
            pos.append(a)
        i += 1
    return pos, o


def g(o, *names, default=None):
    for n in names:
        if n in o:
            return o[n]
    return default
