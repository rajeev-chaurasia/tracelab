"""The descriptive summary from the run artifact contract, recomputed.

A run's summary is a claim made by the writer. TraceLab never reads a number
from it; it recomputes each field from the raw samples and rejects the run if
the two disagree, so the definitions here have to match the contract exactly,
including where a value is null rather than zero.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

FIELDS = ("mean", "median", "p90", "p95", "p99", "stddev", "mad", "cv")


class Summary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    unit: str
    n: int
    mean: float | None
    median: float | None
    p90: float | None
    p95: float | None
    p99: float | None
    stddev: float | None
    mad: float | None
    cv: float | None


def percentile(ascending: Sequence[float], p: float) -> float:
    """Hyndman and Fan type 7, numpy's default."""
    n = len(ascending)
    h = (n - 1) * p / 100
    lo = math.floor(h)
    if lo >= n - 1:
        return ascending[n - 1]
    return ascending[lo] + (h - lo) * (ascending[lo + 1] - ascending[lo])


def summarize(unit: str, values: Sequence[float]) -> Summary:
    n = len(values)
    if n == 0:
        return Summary(unit=unit, n=0, **dict.fromkeys(FIELDS))
    x = sorted(float(v) for v in values)
    mean = math.fsum(x) / n
    median = percentile(x, 50)
    mad = percentile(sorted(abs(v - median) for v in x), 50)
    stddev: float | None = None
    cv: float | None = None
    if n >= 2:
        stddev = math.sqrt(math.fsum((v - mean) ** 2 for v in x) / (n - 1))
        if mean != 0:
            cv = stddev / mean
    return Summary(
        unit=unit,
        n=n,
        mean=mean,
        median=median,
        p90=percentile(x, 90),
        p95=percentile(x, 95),
        p99=percentile(x, 99),
        stddev=stddev,
        mad=mad,
        cv=cv,
    )


def agree(a: float, b: float) -> bool:
    # The absolute term exists because a relative tolerance alone can never be
    # met when the true value is zero, as mad is on constant data.
    return abs(a - b) <= 1e-9 * max(abs(a), abs(b)) + 1e-12


def disagreements(claimed: Summary, recomputed: Summary) -> list[str]:
    found: list[str] = []
    if claimed.unit != recomputed.unit:
        found.append(f"unit {claimed.unit!r} != {recomputed.unit!r}")
    if claimed.n != recomputed.n:
        found.append(f"n {claimed.n} != {recomputed.n}")
    for field in FIELDS:
        a, b = getattr(claimed, field), getattr(recomputed, field)
        if a is None and b is None:
            continue
        if a is None or b is None or not agree(a, b):
            found.append(f"{field} {a} != {b}")
    return found
