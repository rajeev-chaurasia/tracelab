"""Deadline metrics for periodic loops, where the average hides what matters.

A 20 ms loop that runs at 20.1 ms on average and occasionally takes 32 ms has
an unremarkable mean and a real problem. The derived series make the miss
itself a metric, so a change can regress the tail without moving the mean and
still be caught.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

from .policy import JitterPolicy
from .schema import RunResult, Series


def intervals_ms(timestamps_ns: Sequence[int]) -> list[float]:
    return [(b - a) / 1_000_000 for a, b in pairwise(timestamps_ns)]


def derive(run: RunResult, policy: JitterPolicy) -> RunResult:
    source = run.metrics.get(policy.source)
    if source is None:
        return run
    limit = policy.period_ms * (1 + policy.tolerance)
    return run.with_metrics(
        {
            f"{policy.source}.deadline_miss": Series(
                unit="ratio", values=[1.0 if v > limit else 0.0 for v in source.values]
            ),
            f"{policy.source}.deviation": Series(
                unit=source.unit, values=[abs(v - policy.period_ms) for v in source.values]
            ),
        }
    )
