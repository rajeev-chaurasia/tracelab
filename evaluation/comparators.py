"""The comparators every case is scored with.

TraceLab is one of four. The other three are the gates people actually build,
run on the identical runs and the identical policy metrics, so any difference
in the published table comes from the method and not from the data.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from evaluation.cases import Case
from tracelab.core import jitter
from tracelab.core.baseline import Selection
from tracelab.core.compare import Comparison, MetricResult, compare
from tracelab.core.policy import BenchmarkPolicy, MetricPolicy, Verdict, rollup
from tracelab.core.run import Run
from tracelab.core.stats import Estimate, Mode, Statistic

# The fixed gate a team writes on its first day: five percent, either way.
NAIVE_THRESHOLD = 0.05


@dataclass(frozen=True)
class Comparator:
    name: str
    describe: str
    run: Callable[[Case, BenchmarkPolicy, int], Comparison]


def _tracelab(case: Case, policy: BenchmarkPolicy, seed: int) -> Comparison:
    return compare(
        case.candidates, Selection(case.baseline), policy, seed=seed, confirmation=case.confirmation
    )


def _one_batch(case: Case, policy: BenchmarkPolicy, seed: int) -> Comparison:
    single = policy.model_copy(update={"confirm_regressions": False})
    return compare(case.candidates, Selection(case.baseline), single, seed=seed)


def _pooled(case: Case, policy: BenchmarkPolicy, seed: int) -> Comparison:
    # Single batch, so it differs from tracelab_one_batch only in how it
    # resamples. Left on, confirmation would hold every first-batch
    # regression at INCONCLUSIVE, since no second batch is passed here.
    single = policy.model_copy(update={"confirm_regressions": False})
    return compare(case.candidates, Selection(case.baseline), single, seed=seed, pool=True)


def _point(policy: MetricPolicy, base: list[Run], cand: list[Run]) -> MetricResult:
    statistic = Statistic.parse(policy.statistic)
    b = float(statistic.of(np.concatenate([r.metrics[policy.metric].values for r in base])))
    c = float(statistic.of(np.concatenate([r.metrics[policy.metric].values for r in cand])))
    sign = -1.0 if cand[0].metrics[policy.metric].higher_is_better else 1.0
    if policy.mode is Mode.ABSOLUTE:
        change, threshold = sign * (c - b), policy.threshold
    else:
        change, threshold = sign * (c - b) / b, NAIVE_THRESHOLD
    if change >= threshold:
        verdict = Verdict.REGRESSION
    elif change <= -threshold:
        verdict = Verdict.IMPROVEMENT
    else:
        verdict = Verdict.PASS
    estimate = Estimate(b, c, change, change, change, policy.mode)
    return MetricResult(policy, verdict, f"{change:+.4f} against {threshold}", estimate)


def _naive(base: list[Run], cand: list[Run], policy: BenchmarkPolicy) -> Comparison:
    if policy.jitter is not None:
        base = [jitter.derive(r, policy.jitter) for r in base]
        cand = [jitter.derive(r, policy.jitter) for r in cand]
    results = [_point(m, base, cand) for m in policy.metrics]
    verdict = rollup([(m.policy.role, m.verdict) for m in results], policy.guardrails_block)
    return Comparison(
        verdict=verdict,
        reason="",
        candidate_runs=[r.run_id for r in cand],
        baseline_runs=[r.run_id for r in base],
        metrics=results,
    )


def _naive_window(case: Case, policy: BenchmarkPolicy, _: int) -> Comparison:
    return _naive(case.baseline, case.candidates, policy)


def _previous_run(case: Case, policy: BenchmarkPolicy, _: int) -> Comparison:
    return _naive(case.baseline[-1:], case.candidates[:1], policy)


TRACELAB = Comparator(
    "tracelab",
    "hierarchical bootstrap, decision table, noise and comparability gates, and "
    "confirmation when the policy asks for it",
    _tracelab,
)
ONE_BATCH = Comparator(
    "tracelab_one_batch",
    "TraceLab with confirmation turned off, to show what confirmation is worth",
    _one_batch,
)

BASELINES = [
    Comparator(
        "pooled_bootstrap",
        "the same gates and table, resampling samples as if independent",
        _pooled,
    ),
    Comparator(
        "fixed_5pct_window",
        "point statistic of the candidates against the twenty-run window, 5% either way",
        _naive_window,
    ),
    Comparator(
        "fixed_5pct_previous_run",
        "point statistic of one candidate run against the run before it, 5% either way",
        _previous_run,
    ),
]
