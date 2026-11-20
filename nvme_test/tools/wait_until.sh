#!/bin/sh
# wait_until.sh TIMEOUT_S INTERVAL_S 'COMMAND'   - re-run COMMAND (via sh -c) until it exits 0.
# prints `waited_s N`; exit 0 when COMMAND succeeded, exit 1 on timeout. Use for "wait for controller/namespace/sanitize".
t=$1; i=$2; shift 2
start=$(date +%s)
while :; do
  if sh -c "$*" >/dev/null 2>&1; then echo "waited_s $(( $(date +%s) - start ))"; exit 0; fi
  now=$(date +%s)
  if [ $(( now - start )) -ge "$t" ]; then echo "waited_s $(( now - start ))"; echo "TIMEOUT after ${t}s waiting for: $*" >&2; exit 1; fi
  sleep "$i"
done
