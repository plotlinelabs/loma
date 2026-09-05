#!/usr/bin/env bash
# Run only on a disposable host. No provider credentials or preview deployment.
set -euo pipefail
if [[ $# -ne 1 || $1 != /* ]]; then
  echo 'Usage: verify_worker_isolation.sh /absolute/path/to/exported-worker-rootfs' >&2
  exit 2
fi
if [[ $(id -u) -ne 0 ]]; then
  echo 'Real isolation verification needs root on a disposable namespace/cgroup-capable host.' >&2
  exit 2
fi
command -v runsc >/dev/null || { echo 'Install authenticated gVisor runsc first.' >&2; exit 2; }
[[ -f "$1/.loma-worker-image" ]] || { echo 'Supply the exported worker-base image, not the host root.' >&2; exit 2; }
unshare --mount --pid --net --fork true || { echo 'Host does not permit the required namespaces.' >&2; exit 2; }
cd "$(dirname "$0")/.."
python=$(command -v python3)
# Do not inherit host personal/provider credentials into even the test controller.
exec env -i PATH="$PATH" HOME=/root LANG=C.UTF-8 \
  LOMA_TEST_GVISOR=1 LOMA_WORKER_ROOTFS="$1" \
  "$python" -m pytest -q tests/test_gvisor_execution.py
