"""How much of a periodic loop's lateness the scheduler explains, from eBPF traces.

For every tick in a recorded trace, the longest run-queue wait the eBPF
collector saw inside that tick: the time the loop's thread spent runnable and
not running. Comparing the late ticks with the on-time ones, and a quiet
recording with a contended one, says whether lateness came from waiting for a
CPU or from somewhere the scheduler cannot see, such as timer slack.

    uv run python -m evaluation.attribution evidence/traces/periodic-ebpf.json \
        evidence/traces/periodic-ebpf-contended.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

PERIOD_MS = 20.0
LATE_MS = PERIOD_MS * 1.25


def attribute(trace: dict[str, Any]) -> dict[str, Any]:
    events = trace["traceEvents"]
    pid = {e["args"]["name"]: e["pid"] for e in events if e["ph"] == "M"}
    ticks = [
        e
        for e in events
        if e["ph"] == "X" and e["pid"] == pid["workload"] and e["name"] == "tick_interval"
    ]
    waits = [e for e in events if e["ph"] == "X" and e["pid"] == pid["ebpf"]]
    preempted = [
        e
        for e in events
        if e["ph"] == "C" and e["pid"] == pid["ebpf"] and e["args"].get("preempted") == 1.0
    ]
    interval_ms, worst_wait_us, total_wait_us = [], [], []
    for tick in ticks:
        start, end = tick["ts"], tick["ts"] + tick["dur"]
        inside = [w["dur"] for w in waits if start <= w["ts"] < end]
        interval_ms.append(tick["dur"] / 1000)
        worst_wait_us.append(max(inside, default=0.0))
        total_wait_us.append(sum(inside))
    interval = np.asarray(interval_ms)
    worst = np.asarray(worst_wait_us)
    total = np.asarray(total_wait_us)
    late = interval > LATE_MS
    all_waits = np.asarray([w["dur"] for w in waits])
    # Run-queue time inside the late ticks, against how far those ticks ran
    # past their period. Waiting can overlap the loop's own sleep, so this is
    # not a share of the lateness: a ratio of 1 or more says the waits alone
    # are long enough to account for it, and a ratio well below 1 says the
    # lateness came from somewhere the scheduler cannot see.
    overrun_us = np.clip(interval - PERIOD_MS, 0, None) * 1000
    ratio = float(total[late].sum() / overrun_us[late].sum()) if late.any() else None
    return {
        "ticks": len(ticks),
        "late_ticks": int(late.sum()),
        "runq_waits": len(waits),
        "preemptions": len(preempted),
        "runq_wait_us": {
            "p50": float(np.percentile(all_waits, 50)),
            "p99": float(np.percentile(all_waits, 99)),
            "max": float(all_waits.max()),
        },
        "worst_wait_in_tick_us": {
            "late_median": float(np.median(worst[late])) if late.any() else None,
            "on_time_median": float(np.median(worst[~late])),
        },
        # Undefined when either side is constant, which a run with no waits is.
        "correlation_interval_vs_worst_wait": (
            float(np.corrcoef(interval, worst)[0, 1]) if worst.std() and interval.std() else None
        ),
        "late_wait_to_overrun_ratio": ratio,
    }


def main(paths: list[str]) -> None:
    out = {Path(p).name: attribute(json.loads(Path(p).read_text())) for p in paths}
    target = Path(paths[0]).parent / "attribution.json"
    target.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main(sys.argv[1:])
