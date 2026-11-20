#!/bin/bash
# Runs the whole suite against the SIMULATOR in mock/ (no hardware, nothing destructive). Extra args go to run.py.
# Failure injection example:  MOCK_CORRUPT_READ=1 ./tests/nvme_suite/run_mock.sh     (see mock/bin/mocklib.py for all switches)
cd "$(dirname "$0")/../.."
export PATH="$PWD/tests/nvme_suite/mock/bin:$PATH" MOCK_NVME_DIR="${MOCK_NVME_DIR:-/tmp/mock_suite}"
rm -rf "$MOCK_NVME_DIR"; mkdir -p "$MOCK_NVME_DIR/work"; printf 'FW_2.0\n\n' > "$MOCK_NVME_DIR/work/firmware.bin"
exec python3 run.py tests/nvme_suite --config config/nvme_suite_mock_config.yaml "$@"
