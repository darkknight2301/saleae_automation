#!/bin/bash
# Scenario (b) orchestrator:  phase 1 -> reboot -> phase 2 (started by systemd after boot).
#   sudo ./tests/user_scenarios/run_reboot_test.sh            run the test (default)
#   sudo ./tests/user_scenarios/run_reboot_test.sh cleanup    remove the systemd unit and the state file
# DRY_RUN=1 : write the unit to /tmp and print it; no systemctl, no reboot.
# UNIT_DIR=<dir> overrides where the unit file goes (default /etc/systemd/system; for testing).
#
# What this touches, and how it is cleaned:
#   unit file + enable symlink + daemon-reload -> removed on next run, on wrapper failure, or by `cleanup`
#                                                  (the service also disables itself when phase 2 starts)
#   failed state of the unit                    -> `systemctl reset-failed` (same places)
#   state file (state_file)                     -> removed by `cleanup`
#   logs/ and the journal                       -> never touched (journalctl is only read)
# Changes a test makes to the drive (e.g. set-feature) belong to that test; nothing here reverts them.
set -eE   # -E: the ERR trap below must also fire for failures inside functions (sc)
cd "$(dirname "$0")/../.."
ROOT=$(pwd)
PY=$(command -v python3)
CFG="--config config/scenarios_config.yaml"
VARS=tests/user_scenarios/variables.json
MODE=${1:-run}
UNIT_NAME=nvme-post-reboot.service
UNIT_DIR=${UNIT_DIR:-/etc/systemd/system}
[ "$DRY_RUN" = 1 ] && UNIT_DIR=/tmp
UNIT=$UNIT_DIR/$UNIT_NAME

# device and state-file path come from the same variables file the tests use
eval "$($PY - "$VARS" <<'PYEOF'
import json, shlex, sys
v = json.load(open(sys.argv[1]))
for var, key, default in (("DEV", "device0", "/dev/nvme0"), ("STATE", "state_file", "")):
    print("%s=%s" % (var, shlex.quote(str(v.get(key, default)))))
PYEOF
)"

sc() { if [ "$DRY_RUN" = 1 ]; then echo "[dry-run] systemctl $*"; else systemctl "$@"; fi; }

# systemd artifacts only (never touches the drive or logs). Safe to call when nothing is installed.
remove_unit() {
  sc disable "$UNIT_NAME" 2>/dev/null || true          # disable BEFORE deleting the file
  sc reset-failed "$UNIT_NAME" 2>/dev/null || true
  rm -f "$UNIT_DIR/multi-user.target.wants/$UNIT_NAME"  # dangling symlink, if any
  if [ -e "$UNIT" ]; then
    rm -f "$UNIT"; echo "removed $UNIT"
    sc daemon-reload
  fi
}

if [ "$MODE" = cleanup ]; then
  remove_unit
  rm -f "$STATE"
  echo "cleanup done (logs/ and the journal are untouched)"
  exit 0
fi
[ "$MODE" = run ] || { echo "usage: $0 [run|cleanup]"; exit 2; }

remove_unit                                              # leftovers from an earlier aborted run

# Phase 1: set + verify + save value to disk. Non-zero exit aborts here (set -e): nothing installed, no reboot.
$PY run.py tests/user_scenarios/b1_pre_reboot.nvtest $CFG

# From here on, any failure/interrupt must not leave a service behind that fires on some later boot.
on_fail() {
  trap - ERR INT TERM                                    # no re-entry while cleaning up
  echo "wrapper failed/interrupted after installing the unit - removing it"
  remove_unit || true
  exit 1
}
trap on_fail ERR INT TERM

# Quoted heredoc: no shell expansion; $$ is systemd's escape for a literal $.
# ExecStartPre disable => one-shot even when the test FAILS (ExecStartPost would be skipped on failure).
# The wait loop gives the controller time to enumerate after boot.
cat > "$UNIT" <<'UNIT'
[Unit]
Description=NVMe post-reboot verification (one-shot)
After=multi-user.target

[Service]
Type=oneshot
WorkingDirectory=@ROOT@
ExecStartPre=/bin/systemctl disable nvme-post-reboot.service
ExecStartPre=/bin/sh -c 'for i in $$(seq 1 60); do [ -e @DEV@ ] && exit 0; sleep 2; done; echo "@DEV@ did not appear"; exit 1'
ExecStart=@PY@ run.py tests/user_scenarios/b2_post_reboot.nvtest @CFG@

[Install]
WantedBy=multi-user.target
UNIT
sed -i -e "s#@ROOT@#$ROOT#g" -e "s#@DEV@#$DEV#g" -e "s#@PY@#$PY#g" -e "s#@CFG@#$CFG#g" "$UNIT"

if [ "$DRY_RUN" = 1 ]; then
  echo "--- DRY_RUN: unit written to $UNIT, not enabled, no reboot (remove with: DRY_RUN=1 $0 cleanup) ---"
  cat "$UNIT"; trap - ERR INT TERM; exit 0
fi
sc daemon-reload
sc enable "$UNIT_NAME"
sync
# Clear the traps BEFORE rebooting: systemd's shutdown sends SIGTERM to this script, and the TERM trap
# would otherwise delete the unit that has to run after boot.
trap - ERR INT TERM
reboot || { echo "reboot failed - removing the unit"; remove_unit; exit 1; }
# After boot:  logs/<run_id>/b2_post_reboot.log   and   journalctl -u nvme-post-reboot
# Afterwards:  sudo ./tests/user_scenarios/run_reboot_test.sh cleanup
