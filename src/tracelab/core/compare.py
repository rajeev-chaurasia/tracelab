"""Compare candidate runs against a baseline under a benchmark policy.

Every early return below is a case where computing an interval would produce a
number nobody should act on. They come before the statistics on purpose, so
that no path can reach REGRESSION without first passing them.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from . import jitter, stats
from .baseline import Selection
from .compat import mismatches
from .policy import BenchmarkPolicy, MetricPolicy, Verdict, classify, rollup
from .schema import RunResult
from .stats import Estimate, Mode, Statistic


@dataclass(frozen=True)
class MetricResult:
    policy: MetricPolicy
    verdict: Verdict
    reason: str
    estimate: Estimate | None = None
    baseline_noise: float | None = None


@dataclass(frozen=True)
class Comparison:
    verdict: Verdict
    reason: str
    candidate_runs: list[str]
    baseline_runs: list[str]
    metrics: list[MetricResult] = field(default_factory=list)
    excluded: Counter[str] = field(default_factory=Counter)
    drift: list[str] = field(default_factory=list)


def _stop(
    verdict: Verdict, reason: str, candidates: list[RunResult], selection: Selection
) -> Comparison:
    return Comparison(
        verdict=verdict,
        reason=reason,
        candidate_runs=[r.run_id for r in candidates],
        baseline_runs=[r.run_id for r in selection.runs],
        excluded=selection.excluded,
    )


def _metric(
    policy: MetricPolicy,
    bench: BenchmarkPolicy,
    baseline: list[RunResult],
    candidates: list[RunResult],
    rng: np.random.Generator,
    pool: bool,
) -> MetricResult:
    missing = sum(policy.metric not in r.metrics for r in [*baseline, *candidates])
    if missing:
        return MetricResult(
            policy, Verdict.INCONCLUSIVE, f"missing from {missing} of the compared runs"
        )

    base = [r.metrics[policy.metric].values for r in baseline]
    cand = [r.metrics[policy.metric].values for r in candidates]
    statistic = Statistic.parse(policy.statistic)

    noise: float | None = None
    # A coefficient of variation is a relative measure and means nothing for a
    # rate that sits at zero, so absolute-mode metrics skip the noise gate and
    # rely on the interval alone.
    if policy.mode is Mode.RELATIVE:
        noise = stats.run_noise(base, statistic)
        limit = policy.max_baseline_noise or bench.max_baseline_noise
        if noise > limit:
            return MetricResult(
                policy,
                Verdict.INCONCLUSIVE,
                f"baseline run-to-run noise {noise:.1%} exceeds the {limit:.1%} limit",
                baseline_noise=noise,
            )

    if pool:
        base, cand = stats.pooled(base), stats.pooled(cand)
    try:
        estimate = stats.compare(
            base,
            cand,
            statistic,
            higher_is_better=policy.higher_is_better,
            mode=policy.mode,
            confidence=bench.confidence,
            n_boot=bench.n_boot,
            rng=rng,
        )
    except ValueError as error:
        return MetricResult(policy, Verdict.INCONCLUSIVE, str(error), baseline_noise=noise)

    verdict = classify(estimate, policy.threshold)
    unit = "%" if policy.mode is Mode.RELATIVE else ""
    scale = 100 if policy.mode is Mode.RELATIVE else 1
    reason = (
        f"{estimate.change * scale:+.2f}{unit} worse, interval "
        f"[{estimate.low * scale:+.2f}{unit}, {estimate.high * scale:+.2f}{unit}], "
        f"threshold {policy.threshold * scale:.2f}{unit}"
    )
    return MetricResult(policy, verdict, reason, estimate, noise)


def compare(
    candidates: list[RunResult],
    selection: Selection,
    policy: BenchmarkPolicy,
    *,
    seed: int = 0,
    pool: bool = False,
) -> Comparison:
    """Compare. `pool` exists only so the evaluation can run the naive bootstrap."""
    if not candidates:
        raise ValueError("nothing to compare: no candidate runs")
    reference = candidates[0]

    for other in candidates[1:]:
        blocking = [m for m in mismatches(reference, other) if m.disqualifying]
        if blocking:
            return _stop(
                Verdict.INCOMPARABLE,
                f"candidate runs disagree with each other on {blocking[0]}",
                candidates,
                selection,
            )
    for run in selection.runs:
        blocking = [m for m in mismatches(reference, run) if m.disqualifying]
        if blocking:
            return _stop(
                Verdict.INCOMPARABLE,
                f"baseline run {run.run_id} differs on {blocking[0]}",
                candidates,
                selection,
            )

    if not selection.runs and selection.disqualified_by_environment:
        return _stop(
            Verdict.INCOMPARABLE,
            f"no compatible baseline: {selection.disqualified_by_environment} recent runs "
            "differ in environment",
            candidates,
            selection,
        )
    if len(selection.runs) < policy.min_baseline_runs:
        return _stop(
            Verdict.INCONCLUSIVE,
            f"{len(selection.runs)} baseline runs, policy needs {policy.min_baseline_runs}",
            candidates,
            selection,
        )
    if len(candidates) < policy.min_candidate_runs:
        return _stop(
            Verdict.INCONCLUSIVE,
            f"{len(candidates)} candidate runs, policy needs {policy.min_candidate_runs}",
            candidates,
            selection,
        )

    baseline = selection.runs
    if policy.jitter is not None:
        baseline = [jitter.derive(r, policy.jitter) for r in baseline]
        candidates = [jitter.derive(r, policy.jitter) for r in candidates]

    results = [
        _metric(metric, policy, baseline, candidates, np.random.default_rng([seed, i]), pool)
        for i, metric in enumerate(policy.metrics)
    ]
    verdict = rollup([(m.policy.role, m.verdict) for m in results], policy.guardrails_block)
    decisive = next((m for m in results if m.verdict is verdict), None)
    reason = f"{decisive.policy.label}: {decisive.reason}" if decisive else verdict.value
    drift = sorted({str(m) for r in baseline for m in mismatches(reference, r)})

    return Comparison(
        verdict=verdict,
        reason=reason,
        candidate_runs=[r.run_id for r in candidates],
        baseline_runs=[r.run_id for r in baseline],
        metrics=results,
        excluded=selection.excluded,
        drift=drift,
    )
