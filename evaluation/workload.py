"""The two benchmarks the evaluation corpus is measured from.

Each invocation is one run: a fresh process, its own warmups, then measured
repetitions, printed as one JSON sample per line in the run artifact's sample
shape. Running each in its own process matters. Run-to-run noise comes from
process-level state, and a corpus collected inside one long-lived process
would not contain it.

    python -m evaluation.workload matmul
    python -m evaluation.workload periodic
"""

from __future__ import annotations

import json
import resource
import sys
import time

import numpy as np

MATMUL = {"warmups": 5, "repetitions": 30, "size": 256}
PERIODIC = {"warmups": 10, "repetitions": 150, "period_ns": 20_000_000, "size": 128}


def _max_rss_bytes() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Darwin reports bytes and Linux reports kilobytes.
    return float(rss if sys.platform == "darwin" else rss * 1024)


def _emit(metric: str, iteration: int, warmup: bool, value: float, unit: str, t0: int) -> None:
    sample = {
        "metric": metric,
        "iteration": iteration,
        "warmup": warmup,
        "value": value,
        "unit": unit,
        "t_offset_ns": time.monotonic_ns() - t0,
    }
    print(json.dumps(sample))


def matmul() -> None:
    rng = np.random.default_rng(0)
    a = rng.random((MATMUL["size"], MATMUL["size"]))
    b = rng.random((MATMUL["size"], MATMUL["size"]))
    t0 = time.monotonic_ns()
    for i in range(MATMUL["warmups"] + MATMUL["repetitions"]):
        start = time.perf_counter_ns()
        for _ in range(8):
            a @ b
        elapsed = time.perf_counter_ns() - start
        warmup = i < MATMUL["warmups"]
        _emit("iteration_latency", i, warmup, float(elapsed), "ns", t0)
        _emit("max_rss", i, warmup, _max_rss_bytes(), "bytes", t0)


def periodic() -> None:
    """A fixed-rate loop with real work in each tick, measured by its intervals.

    Deadlines are absolute, so one late tick does not shift every later one,
    and the interval recorded is between consecutive tick starts, which is
    what a downstream consumer of the loop would observe.
    """
    rng = np.random.default_rng(0)
    a = rng.random((PERIODIC["size"], PERIODIC["size"]))
    period = PERIODIC["period_ns"]
    t0 = time.monotonic_ns()
    previous = time.perf_counter_ns()
    deadline = previous
    for i in range(PERIODIC["warmups"] + PERIODIC["repetitions"]):
        deadline += period
        a @ a
        remaining = deadline - time.perf_counter_ns()
        if remaining > 0:
            time.sleep(remaining / 1e9)
        now = time.perf_counter_ns()
        _emit("tick_interval", i, i < PERIODIC["warmups"], float(now - previous), "ns", t0)
        previous = now


if __name__ == "__main__":
    {"matmul": matmul, "periodic": periodic}[sys.argv[1]]()
