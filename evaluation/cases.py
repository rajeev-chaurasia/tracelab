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
    # A second batch of the same revision, taken from later in the corpus, or
    # None when the version being scored has no confirmation step.
    confirmation: list[Run] | None = None


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


def _slower(rate: str, factor: float) -> Transform:
    """The same work taking `factor` times as long: latency up, its rate down.

    Both metrics come from one measurement, so a real slowdown moves them
    together; injecting it into only one would be a change no code makes.
    """

    def apply(
        base: list[Run], cand: list[Run], _: np.random.Generator
    ) -> tuple[list[Run], list[Run]]:
        cand = _map(cand, LAT, lambda v: v * factor)
        return base, _map(cand, rate, lambda v: v / factor)

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


def _throughput_kinds(benchmark: str, rate: str, guardrail: str = "cpu_time") -> list[Kind]:
    """The cases for a benchmark judged on a rate, where higher is better.

    Written before v4 was collected, with the same thresholds as the policies
    in policies/v4: 3% on the rate's median, 5% on latency p95.
    """
    return [
        Kind("same_code", benchmark, NO, False, _same),
        Kind("slower_2%", benchmark, frozenset({V.PASS, V.WARNING}), False, _slower(rate, 1.02)),
        Kind("slower_5%", benchmark, frozenset({V.REGRESSION}), True, _slower(rate, 1.05)),
        Kind("slower_10%", benchmark, frozenset({V.REGRESSION}), True, _slower(rate, 1.10)),
        Kind("slower_20%", benchmark, frozenset({V.REGRESSION}), True, _slower(rate, 1.20)),
        Kind("faster_10%", benchmark, frozenset({V.IMPROVEMENT}), False, _slower(rate, 1 / 1.10)),
        Kind("latency_tail_x1.5", benchmark, frozenset({V.REGRESSION}), True, _tail(LAT, 1.5)),
        Kind(
            f"{guardrail}_+10%", benchmark, frozenset({V.WARNING}), False, _scale(guardrail, 1.10)
        ),
        Kind(
            "noisy_baseline_same_code",
            benchmark,
            frozenset({V.INCONCLUSIVE, V.PASS}),
            False,
            _noisy_baseline(rate, 0.08),
        ),
    ]


CPU = "cpu_time"
KINDS += [
    *_throughput_kinds("membw", "memory_bandwidth"),
    *_throughput_kinds("network", "network_throughput"),
    # Written before v5 was collected on the L4. A GPU run has no CPU time
    # metric, so its guardrail case inflates GPU memory instead.
    *_throughput_kinds("gpu", "matmul_throughput", guardrail="gpu_memory"),
]


def cases(corpus: dict[str, list[Run]], confirmation_gap: int | None = None) -> Iterator[Case]:
    """Every window of every kind.

    With a confirmation gap, the confirmation batch starts that many runs after
    the first batch ends. The transform is applied to both batches in one call,
    because an injected regression is a property of the code and has to be in
    both, and a transform that draws random numbers must not draw them twice
    for the baseline.
    """
    for kind in KINDS:
        # Each corpus measures some benchmarks; kinds for the others do not
        # apply, and skipping them leaves the earlier versions' cases as they
        # were, in the same order.
        if kind.benchmark not in corpus:
            continue
        runs = corpus[kind.benchmark]
        tail = 0 if confirmation_gap is None else confirmation_gap + kind.candidates
        last = len(runs) - BASELINE_RUNS - CANDIDATE_RUNS - tail
        for start in range(0, last + 1, STRIDE):
            case_id = f"{kind.benchmark}/{kind.name}/w{start:02d}"
            rng = np.random.default_rng(zlib.crc32(case_id.encode()))
            baseline = runs[start : start + BASELINE_RUNS]
            first_at = start + BASELINE_RUNS
            batch = runs[first_at : first_at + kind.candidates]
            if confirmation_gap is not None:
                second_at = first_at + kind.candidates + confirmation_gap
                batch = batch + runs[second_at : second_at + kind.candidates]
            batch = [replace(r, revision=CANDIDATE_REVISION) for r in batch]
            baseline, batch = kind.transform(baseline, batch, rng)
            first, second = batch[: kind.candidates], batch[kind.candidates :]
            yield Case(case_id, kind, start, baseline, first, second or None)
