#!/usr/bin/env bash
# Record a workload inside a Linux container with every collector, including
# eBPF run-queue latency, and write the aligned trace next to the repository.
#
#   script/ebpf_record.sh periodic evidence/traces/periodic-ebpf.json
#   CONTENTION=12 script/ebpf_record.sh periodic evidence/traces/periodic-ebpf-contended.json
#
# CONTENTION starts that many busy loops in the container for the whole
# recording, so the scheduler has to choose between them and the workload.
#
# The container runs privileged in the host's PID namespace: loading eBPF
# programs needs the privilege, and the tracepoints report PIDs from that
# namespace, so the workload's PID has to mean the same thing to both.
set -euo pipefail
cd "$(dirname "$0")/.."
workload="${1:?workload name}"
out="${2:?trace output path inside the repository}"

docker build -q -t tracelab-ebpf deploy/ebpf >/dev/null
docker run --rm --privileged --pid=host \
  -v /sys/kernel/tracing:/sys/kernel/tracing \
  -v /sys/kernel/debug:/sys/kernel/debug \
  -v "$PWD:/src" -v tracelab-ebpf-venv:/opt/venv -v tracelab-ebpf-cache:/opt/uv-cache \
  -e CONTENTION="${CONTENTION:-0}" \
  tracelab-ebpf bash -c '
    # Plain shell loops, so contention never depends on an interpreter that
    # may not exist yet in a fresh container.
    for _ in $(seq 1 "$CONTENTION"); do (while :; do :; done) & done
    if [ "$CONTENTION" -gt 0 ]; then
      sleep 1
      echo "contention: $(jobs -p | wc -l) busy loops running"
    fi
    uv run --frozen tracelab record --ebpf --out "/src/$0" -- \
      /opt/venv/bin/python -m evaluation.workload "$1"
    status=$?
    jobs -p | xargs -r kill
    exit $status
  ' "$out" "$workload"
