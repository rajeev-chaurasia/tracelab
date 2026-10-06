"""Evaluation cases: real runs, cut into windows, with known changes injected.

Every run in the corpus is the same code, so an untouched window is a
same-code comparison whose correct answer is known without trusting anything.
Injected cases transform the candidate runs, or the baseline, by a stated
amount, so their correct answer is known too. Nothing about the noise is
generated; only the change is.
"""

from __future__ import annotations

import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace

import numpy as np

from tracelab.core.policy import Verdict
from tracelab.core.run import Run

BASELINE_RUNS = 20
CANDIDATE_RUNS = 3
STRIDE = 3
CANDIDATE_REVISION = "c" * 40

V = Verdict
Transform = Callable[[list[Run], list[Run], np.random.Generator], tuple[list[Run], list[Run]]]


@dataclass(frozen=True)
class Kind:
    name: str
    benchmark: str
    allowed: frozenset[Verdict]
    # Whether a real regression past threshold exists in what the candidate
    # measured, independent of whether the baseline can support calling it.
    regression: bool
    transform: Transform
    candidates: int = CANDIDATE_RUNS


@dataclass(frozen=True)
class Case:
    case_id: str
    kind: Kind
    start: int
    baseline: list[Run]
    candidates: list[Run]


def _map(runs: list[Run], metric: str, fn: Callable[[np.ndarray], np.ndarray]) -> list[Run]:
    out = []
    for run in runs:
        series = run.metrics[metric]
        values = fn(np.asarray(series.values, dtype=float))
        out.append(run.with_metrics({metric: replace(series, values=tuple(map(float, values)))}))
    return out


def _scale(metric: str, factor: float) -> Transform:
    def apply(
        base: list[Run], cand: list[Run], _: np.random.Generator
    ) -> tuple[list[Run], list[Run]]:
        return base, _map(cand, metric, lambda v: v * factor)

    return apply


def _tail(metric: str, factor: float) -> Transform:
    """Inflate only samples above each run's own p90, leaving the median alone."""

    def apply(
        base: list[Run], cand: list[Run], _: np.random.Generator
    ) -> tuple[list[Run], list[Run]]:
        return base, _map(cand, metric, lambda v: np.where(v > np.quantile(v, 0.9), v * factor, v))

    return apply


def _spread(metric: str, factor: float) -> Transform:
    """Widen each run around its own median: same center, worse tail."""

    def apply(
        base: list[Run], cand: list[Run], _: np.random.Generator
    ) -> tuple[list[Run], list[Run]]:
        return base, _map(cand, metric, lambda v: np.median(v) + (v - np.median(v)) * factor)

    return apply


def _late_ticks(metric: str, period: float, share: float) -> Transform:
    """Make a share of ticks late by half a period, the next one early by as much.

    An absolute-deadline loop catches up after a late tick, so the mean
    interval does not move. Only the miss rate and the tail do.
    """

    def late(values: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        out = values.copy()
        count = max(1, round(share * (len(out) - 1)))
        for i in rng.choice(len(out) - 1, size=count, replace=False):
            out[i] += period / 2
            out[i + 1] = max(out[i + 1] - period / 2, 0.0)
        return out

    def apply(
        base: list[Run], cand: list[Run], rng: np.random.Generator
    ) -> tuple[list[Run], list[Run]]:
        return base, _map(cand, metric, lambda v: late(v, rng))

    return apply


def _noisy_baseline(metric: str, sd: float, then: Transform | None = None) -> Transform:
    """Move each baseline run's level independently, as a machine under load would."""

    def apply(
        base: list[Run], cand: list[Run], rng: np.random.Generator
    ) -> tuple[list[Run], list[Run]]:
        levels = iter(1 + rng.normal(0, sd, len(base)))
        moved = _map(base, metric, lambda v: v * next(levels))
        return then(moved, cand, rng) if then else (moved, cand)

    return apply


def _emulated(then: Transform) -> Transform:
    def apply(
        base: list[Run], cand: list[Run], rng: np.random.Generator
    ) -> tuple[list[Run], list[Run]]:
        base, cand = then(base, cand, rng)
        return base, [replace(r, rig=replace(r.rig, emulated=True)) for r in cand]

    return apply


def _same(base: list[Run], cand: list[Run], _: np.random.Generator) -> tuple[list[Run], list[Run]]:
    return base, cand


LAT, RSS, TICK = "iteration_latency", "max_rss", "tick_interval"
PERIOD = 20_000_000.0
NO = frozenset({V.PASS})

KINDS: list[Kind] = [
    Kind("same_code", "matmul", NO, False, _same),
    # Below both latency thresholds. Calling it real but small is right too.
    Kind("latency_+2%", "matmul", frozenset({V.PASS, V.WARNING}), False, _scale(LAT, 1.02)),
    Kind("latency_+5%", "matmul", frozenset({V.REGRESSION}), True, _scale(LAT, 1.05)),
    Kind("latency_+10%", "matmul", frozenset({V.REGRESSION}), True, _scale(LAT, 1.10)),
    Kind("latency_+20%", "matmul", frozenset({V.REGRESSION}), True, _scale(LAT, 1.20)),
    Kind("latency_-10%", "matmul", frozenset({V.IMPROVEMENT}), False, _scale(LAT, 0.90)),
    Kind("latency_tail_x1.5", "matmul", frozenset({V.REGRESSION}), True, _tail(LAT, 1.5)),
    Kind("latency_spread_x2", "matmul", frozenset({V.REGRESSION}), True, _spread(LAT, 2.0)),
    # A guardrail, so the right run verdict is a warning, not a block.
    Kind("rss_+10%", "matmul", frozenset({V.WARNING}), False, _scale(RSS, 1.10)),
    Kind(
        "noisy_baseline_same_code",
        "matmul",
        frozenset({V.INCONCLUSIVE, V.PASS}),
        False,
        _noisy_baseline(LAT, 0.08),
    ),
    Kind(
        "noisy_baseline_+10%",
        "matmul",
        frozenset({V.INCONCLUSIVE, V.REGRESSION}),
        True,
        _noisy_baseline(LAT, 0.08, _scale(LAT, 1.10)),
    ),
    Kind(
        "emulated_rig_+10%",
        "matmul",
        frozenset({V.INCOMPARABLE}),
        True,
        _emulated(_scale(LAT, 1.10)),
    ),
    Kind(
        "one_candidate_+10%",
        "matmul",
        frozenset({V.INCONCLUSIVE}),
        True,
        _scale(LAT, 1.10),
        candidates=1,
    ),
    Kind("same_code", "periodic", NO, False, _same),
    Kind("rate_+10%", "periodic", frozenset({V.REGRESSION}), True, _scale(TICK, 1.10)),
    Kind(
        "late_ticks_5%",
        "periodic",
        frozenset({V.REGRESSION}),
        True,
        _late_ticks(TICK, PERIOD, 0.05),
    ),
    Kind(
        "noisy_baseline_same_code",
        "periodic",
        frozenset({V.INCONCLUSIVE, V.PASS}),
        False,
        _noisy_baseline(TICK, 0.08),
    ),
]


def cases(corpus: dict[str, list[Run]]) -> Iterator[Case]:
    for kind in KINDS:
        runs = corpus[kind.benchmark]
        last = len(runs) - BASELINE_RUNS - CANDIDATE_RUNS
        for start in range(0, last + 1, STRIDE):
            case_id = f"{kind.benchmark}/{kind.name}/w{start:02d}"
            rng = np.random.default_rng(zlib.crc32(case_id.encode()))
            baseline = runs[start : start + BASELINE_RUNS]
            following = runs[start + BASELINE_RUNS : start + BASELINE_RUNS + kind.candidates]
            candidates = [replace(r, revision=CANDIDATE_REVISION) for r in following]
            baseline, candidates = kind.transform(baseline, candidates, rng)
            yield Case(case_id, kind, start, baseline, candidates)
