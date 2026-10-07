"""Check a GPU trace's alignment against the work itself, across two clocks.

The workload times each iteration with CUDA events and stamps it on the
monotonic clock. Nsight Systems records every kernel on its own session
clock, placed through the wall clock. If the timeline is right, every
iteration slice holds exactly the kernels it launched, and their time adds
up to the iteration's device time. Nothing here shares a clock, so agreement
is evidence the mapping is right, not an echo of it.

    uv run python -m evaluation.gpu_alignment evidence/traces/gpu-nsys.trace.json
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def check(trace: dict[str, Any], kernel: str = "gemm") -> dict[str, Any]:
    events = trace["traceEvents"]
    pid = {e["args"]["name"]: e["pid"] for e in events if e["ph"] == "M"}
    stream = next(p for name, p in pid.items() if ".stream" in name)
    iterations = [
        e
        for e in events
        if e["ph"] == "X" and e["pid"] == pid["workload"] and e["name"] == "iteration_latency"
    ]
    kernels = sorted(
        (e for e in events if e["ph"] == "X" and e["pid"] == stream and kernel in e["name"]),
        key=lambda e: e["ts"],
    )
    counts, ratios, lead, trail = [], [], [], []
    for it in iterations:
        start, end = it["ts"], it["ts"] + it["dur"]
        inside = [k for k in kernels if start <= k["ts"] and k["ts"] + k["dur"] <= end]
        counts.append(len(inside))
        if inside:
            ratios.append(sum(k["dur"] for k in inside) / it["dur"])
            lead.append(inside[0]["ts"] - start)
            trail.append(end - (inside[-1]["ts"] + inside[-1]["dur"]))
    return {
        "iterations": len(iterations),
        "kernels_inside_each_iteration": dict(sorted(Counter(counts).items())),
        "kernel_time_over_event_time": {
            "median": statistics.median(ratios),
            "min": min(ratios),
            "max": max(ratios),
        },
        "slack_us": {
            "before_first_kernel": statistics.median(lead),
            "after_last_kernel": statistics.median(trail),
        },
    }


def main(path: str) -> None:
    result = check(json.loads(Path(path).read_text()))
    out = Path(path).with_name("gpu-alignment.json")
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main(sys.argv[1])
