"""Deadline metrics for periodic loops, where the average hides what matters.

A 20 ms loop that runs at 20.1 ms on average and occasionally takes 32 ms has
an unremarkable mean and a real problem. The derived series make the miss
itself a metric, so a change can regress the tail without moving the mean and
still be caught.
"""

from __future__ import annotations

from .policy import JitterPolicy
from .run import Run, Series


def derive(run: Run, policy: JitterPolicy) -> Run:
    source = run.metrics.get(policy.source)
    if source is None:
        return run
    limit = policy.period * (1 + policy.tolerance)
    return run.with_metrics(
        {
            f"{policy.source}.deadline_miss": Series(
                unit="ratio",
                higher_is_better=False,
                values=tuple(1.0 if v > limit else 0.0 for v in source.values),
            ),
            f"{policy.source}.deviation": Series(
                unit=source.unit,
                higher_is_better=False,
                values=tuple(abs(v - policy.period) for v in source.values),
            ),
        }
    )
