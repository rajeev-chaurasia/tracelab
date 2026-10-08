"""Compare candidate runs against a baseline under a benchmark policy.

Every early return below is a case where computing an interval would produce a
number nobody should act on. They come before the statistics on purpose, so
that no path can reach REGRESSION without first passing them.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import numpy as np

from . import jitter, stats
from .baseline import Selection
from .compat import blocking, mismatches
from .policy import (
    BenchmarkPolicy,
    EnvironmentCheck,
    MetricClass,
    MetricPolicy,
    Verdict,
    blocking_roles,
    classify,
    rollup,
)
from .run import Run
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
    # Set when the first batch regressed and the policy wants a second batch,
    # run later, to agree before the verdict may block anything.
    needs_confirmation: bool = False
    confirmation: Comparison | None = None


def _stop(verdict: Verdict, reason: str, candidates: list[Run], selection: Selection) -> Comparison:
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
    baseline: list[Run],
    candidates: list[Run],
    rng: np.random.Generator,
    pool: bool,
) -> MetricResult:
    missing = sum(
        policy.metric not in r.metrics or not r.metrics[policy.metric].values
        for r in [*baseline, *candidates]
    )
    if missing:
        return MetricResult(
            policy, Verdict.INCONCLUSIVE, f"missing from {missing} of the compared runs"
        )

    base: Sequence[Sequence[float]] = [r.metrics[policy.metric].values for r in baseline]
    cand: Sequence[Sequence[float]] = [r.metrics[policy.metric].values for r in candidates]
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
            higher_is_better=candidates[0].metrics[policy.metric].higher_is_better,
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


def _decisive(
    results: list[MetricResult], verdict: Verdict, policy: BenchmarkPolicy
) -> MetricResult | None:
    """The metric the run verdict came from, so the headline never names a bystander.

    Blocking metrics are preferred, since a REGRESSION or INCONCLUSIVE run
    verdict can only come from one. A WARNING can come from a non-blocking
    guardrail's REGRESSION, so that is accepted as its source.
    """
    roles = blocking_roles(policy.guardrails_block)
    matching = [m for m in results if m.verdict is verdict]
    blocking = [m for m in matching if m.policy.role in roles]
    if blocking:
        return blocking[0]
    if verdict is Verdict.WARNING:
        demoted = [
            m for m in results if m.verdict is Verdict.REGRESSION and m.policy.role not in roles
        ]
        if demoted:
            return demoted[0]
    counted = [m for m in matching if m.policy.role is not MetricClass.INFORMATIONAL]
    return counted[0] if counted else None


def _regressed(result: Comparison, policy: BenchmarkPolicy) -> set[str]:
    roles = blocking_roles(policy.guardrails_block)
    return {
        m.policy.label
        for m in result.metrics
        if m.verdict is Verdict.REGRESSION and m.policy.role in roles
    }


@dataclass(frozen=True)
class Canary:
    """The canary benchmark's runs: its own baseline, and one list per candidate
    batch of the runs taken alongside that batch."""

    baseline: list[Run]
    during: list[list[Run]]


def compare(
    candidates: list[Run],
    selection: Selection,
    policy: BenchmarkPolicy,
    *,
    seed: int = 0,
    pool: bool = False,
    confirmation: list[Run] | None = None,
    canary: Canary | None = None,
) -> Comparison:
    """Compare, confirm, then check that the environment held.

    Confirmation answers a moment of noise. It cannot answer a change in the
    machine that lasts longer than the gap between batches, which is how v3,
    v4 and v5 failed. So a regression that survives confirmation is checked
    twice more when the policy asks: against environment metrics the runs
    carry, such as a GPU's clock, and against a canary benchmark run alongside
    it. Either one moving turns the regression into INCONCLUSIVE.
    """
    result = _confirmed(
        candidates, selection, policy, seed=seed, pool=pool, confirmation=confirmation
    )
    if result.verdict is not Verdict.REGRESSION:
        return result
    batches = [candidates, *([confirmation] if confirmation else [])]
    for check in policy.environment:
        if (drift := _environment_drift(check, selection.runs, batches)) is not None:
            return replace(result, verdict=Verdict.INCONCLUSIVE, reason=f"{drift}; {result.reason}")
    moved = _canary_moved(policy, canary, seed) if canary is not None else None
    if moved is not None:
        return replace(result, verdict=Verdict.INCONCLUSIVE, reason=f"{moved}; {result.reason}")
    return result


def _environment_drift(
    check: EnvironmentCheck, baseline: list[Run], batches: list[list[Run]]
) -> str | None:
    def median(runs: list[Run]) -> float | None:
        values = [
            v for r in runs if check.metric in r.metrics for v in r.metrics[check.metric].values
        ]
        return float(np.median(values)) if values else None

    base = median(baseline)
    if base is None or base == 0:
        return None
    for i, batch in enumerate(batches):
        cand = median(batch)
        if cand is not None and abs(cand - base) / abs(base) > check.limit:
            return (
                f"environment differed in batch {i + 1}: {check.metric} median {cand:.4g} "
                f"against {base:.4g} in the baseline, past the {check.limit:.1%} limit"
            )
    return None


def _canary_moved(policy: BenchmarkPolicy, canary: Canary, seed: int) -> str | None:
    spec = policy.canary
    if spec is None:
        return None
    statistic = Statistic.parse(spec.statistic)
    base = [r.metrics[spec.metric].values for r in canary.baseline if spec.metric in r.metrics]
    for i, during in enumerate(canary.during):
        cand = [r.metrics[spec.metric].values for r in during if spec.metric in r.metrics]
        if not base or not cand:
            continue
        estimate = stats.compare(
            base,
            cand,
            statistic,
            higher_is_better=during[0].metrics[spec.metric].higher_is_better,
            mode=Mode.RELATIVE,
            confidence=policy.confidence,
            n_boot=policy.n_boot,
            rng=np.random.default_rng([seed, 1000 + i]),
        )
        # Only a canary that regressed past its own threshold counts. Mild
        # drift shows up as a WARNING, and letting that excuse the candidate
        # would hide a real ten percent slowdown behind a one percent wobble.
        if classify(estimate, spec.threshold) is Verdict.REGRESSION:
            return (
                f"canary {spec.benchmark} also got worse during batch {i + 1}: "
                f"{spec.metric}.{spec.statistic} {estimate.change:+.1%}, interval "
                f"[{estimate.low:+.1%}, {estimate.high:+.1%}]"
            )
    return None


def _confirmed(
    candidates: list[Run],
    selection: Selection,
    policy: BenchmarkPolicy,
    *,
    seed: int = 0,
    pool: bool = False,
    confirmation: list[Run] | None = None,
) -> Comparison:
    """Compare, and when the policy says so, require a second batch to agree.

    A regression in one batch can be the machine: a burst of load that hit
    only the candidate's runs looks, from the samples alone, exactly like
    slower code. A second batch run later is unlikely to share the burst and
    certain to share the code. So with `confirm_regressions` on, a first-batch
    regression blocks only if the confirmation batch independently regresses
    on at least one of the same blocking metrics against the same baseline.

    `pool` exists only so the evaluation can run the naive bootstrap.
    """
    first = _compare_batch(candidates, selection, policy, seed=seed, pool=pool)
    if first.verdict is not Verdict.REGRESSION or not policy.confirm_regressions:
        return first
    if not confirmation:
        return replace(
            first,
            verdict=Verdict.INCONCLUSIVE,
            reason=f"awaiting a confirmation batch; first batch {first.reason}",
            needs_confirmation=True,
        )
    if (mismatch := blocking(candidates[0], confirmation[0])) is not None:
        return replace(
            first,
            verdict=Verdict.INCOMPARABLE,
            reason=f"confirmation batch differs from the first on {mismatch}",
        )

    # A different stream, so the two batches' intervals are not built from
    # the same random draws.
    second = _compare_batch(confirmation, selection, policy, seed=seed + 1, pool=pool)
    agreed = sorted(_regressed(first, policy) & _regressed(second, policy))
    if second.verdict is Verdict.REGRESSION and agreed:
        return replace(
            first,
            reason=f"confirmed by a second batch on {', '.join(agreed)}; {first.reason}",
            confirmation=second,
        )
    return replace(
        first,
        verdict=Verdict.INCONCLUSIVE,
        reason=(
            f"first batch regressed but the confirmation batch did not agree "
            f"({second.verdict.value}: {second.reason})"
        ),
        confirmation=second,
    )


def _compare_batch(
    candidates: list[Run],
    selection: Selection,
    policy: BenchmarkPolicy,
    *,
    seed: int,
    pool: bool,
) -> Comparison:
    if not candidates:
        raise ValueError("nothing to compare: no candidate runs")
    reference = candidates[0]
    unfit = [r for r in candidates if r.status != "SUCCEEDED"]
    if unfit:
        # A FAILED or INVALID candidate measured nothing trustworthy. Calling
        # that a regression would blame the code for the rig.
        return _stop(
            Verdict.INCONCLUSIVE,
            f"candidate run {unfit[0].run_id} is {unfit[0].status}: {unfit[0].status_reason}",
            candidates,
            selection,
        )

    for other in candidates[1:]:
        if (mismatch := blocking(reference, other)) is not None:
            return _stop(
                Verdict.INCOMPARABLE,
                f"candidate runs disagree with each other on {mismatch}",
                candidates,
                selection,
            )
    for run in selection.runs:
        if (mismatch := blocking(reference, run)) is not None:
            return _stop(
                Verdict.INCOMPARABLE,
                f"baseline run {run.run_id} differs on {mismatch}",
                candidates,
                selection,
            )

    unfit_base = [r for r in selection.runs if r.status != "SUCCEEDED"]
    if unfit_base:
        # baseline.select already leaves these out. A caller that builds a
        # Selection by hand gets the same rule rather than a quiet bypass.
        return _stop(
            Verdict.INCONCLUSIVE,
            f"baseline run {unfit_base[0].run_id} is {unfit_base[0].status}, never an input",
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
    decisive = _decisive(results, verdict, policy)
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
