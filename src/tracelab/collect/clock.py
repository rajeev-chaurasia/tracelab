"""Put samples from different clocks onto one timeline, with a measured error.

Sources disagree about time. The workload stamps iterations on the monotonic
clock, an external sampler may stamp on the wall clock, a Linux profiler on
CLOCK_MONOTONIC of another kernel, a GPU on its own counter. Placing them on
one timeline means estimating the mapping between each pair of clocks, and a
mapping is only as good as the readings it was fitted from.

Each reading here is a sandwich: reference clock, other clock, reference
clock, as close together as the interpreter allows. The other clock's reading
happened somewhere inside the sandwich, so half its width bounds the error of
that single reading. Readings taken across a run are fitted with a line, so
drift between the clocks is modelled rather than ignored, and the worst
distance of any reading from the line is reported alongside the bound.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

Clock = Callable[[], int]


@dataclass(frozen=True)
class Reading:
    reference_ns: int
    other_ns: int
    # Half the width of the sandwich: the other clock was read no further
    # than this from reference_ns.
    uncertainty_ns: int


@dataclass(frozen=True)
class Mapping:
    """reference = offset + slope * other, fitted over a set of readings."""

    slope: float
    offset: float
    # The worst sandwich half-width among the readings: how far any single
    # reading could be from the truth.
    max_uncertainty_ns: int
    # The worst distance of any reading from the fitted line, which includes
    # drift the line failed to model.
    max_residual_ns: float
    readings: int

    def to_reference(self, other_ns: int) -> int:
        return round(self.offset + self.slope * other_ns)

    @property
    def error_bound_ns(self) -> float:
        return self.max_uncertainty_ns + self.max_residual_ns


def read(reference: Clock, other: Clock) -> Reading:
    before = reference()
    middle = other()
    after = reference()
    return Reading((before + after) // 2, middle, (after - before + 1) // 2)


def sample(reference: Clock, other: Clock, count: int = 64) -> Reading:
    """The tightest of several sandwiches, since scheduling can widen any one."""
    return min((read(reference, other) for _ in range(count)), key=lambda r: r.uncertainty_ns)


def fit(readings: list[Reading]) -> Mapping:
    if not readings:
        raise ValueError("a clock mapping needs at least one reading")
    if len(readings) == 1:
        only = readings[0]
        return Mapping(1.0, float(only.reference_ns - only.other_ns), only.uncertainty_ns, 0.0, 1)
    xs = [float(r.other_ns) for r in readings]
    ys = [float(r.reference_ns) for r in readings]
    # Centred before fitting, because nanosecond epoch values squared lose
    # precision in a double long before the slope does.
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = 1.0 if sxx == 0 else sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx
    offset = my - slope * mx
    residual = max(abs(y - (offset + slope * x)) for x, y in zip(xs, ys, strict=True))
    return Mapping(
        slope=slope,
        offset=offset,
        max_uncertainty_ns=max(r.uncertainty_ns for r in readings),
        max_residual_ns=residual,
        readings=len(readings),
    )


MONOTONIC: Clock = time.monotonic_ns
WALL: Clock = time.time_ns
