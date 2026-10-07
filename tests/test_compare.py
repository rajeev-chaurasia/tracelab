from dataclasses import replace
from typing import Any

import numpy as np

from tracelab.core.baseline import Selection
from tracelab.core.compare import compare
from tracelab.core.policy import BenchmarkPolicy, JitterPolicy, MetricClass, MetricPolicy, Verdict
from tracelab.core.run import Run, Series
from tracelab.core.stats import Mode

from .factories import CANDIDATE, rig, run

POLICY = BenchmarkPolicy(
    benchmark="matmul",
    n_boot=500,
    metrics=[
        MetricPolicy(metric="latency", statistic="median", threshold=0.03),
        MetricPolicy(
            metric="max_rss", statistic="median", threshold=0.05, role=MetricClass.GUARDRAIL
        ),
    ],
)


def runs(
    start: int, count: int, *, level: float, drift: float, seed: int, **overrides: Any
) -> list[Run]:
    rng = np.random.default_rng(seed)
    out = []
    for i in range(count):
        center = level * (1 + rng.normal(0, drift))
        out.append(
            run(
                start + i,
                metrics={
                    "latency": list(center * (1 + rng.normal(0, 0.01, 30))),
                    "max_rss": [100.0 + rng.normal(0, 0.2)],
                },
                **overrides,
            )
        )
    return out


def candidates(level: float, drift: float = 0.01, count: int = 3, **kw: Any) -> list[Run]:
    return runs(100, count, level=level, drift=drift, seed=7, revision=CANDIDATE, **kw)


BASELINE = Selection(runs(0, 20, level=10.0, drift=0.01, seed=1))


def test_same_code_passes() -> None:
    result = compare(candidates(10.0), BASELINE, POLICY)

    assert result.verdict is Verdict.PASS
    assert len(result.baseline_runs) == 20


def test_a_ten_percent_slowdown_is_a_regression_with_its_interval() -> None:
    result = compare(candidates(11.0), BASELINE, POLICY)

    assert result.verdict is Verdict.REGRESSION
    latency = result.metrics[0]
    assert latency.estimate is not None
    assert 0.07 < latency.estimate.change < 0.13
    assert result.reason.startswith("latency.median: +")


def test_a_speedup_is_an_improvement() -> None:
    assert compare(candidates(9.0), BASELINE, POLICY).verdict is Verdict.IMPROVEMENT


def test_direction_comes_from_the_declaration() -> None:
    def throughput(rs: list[Run]) -> list[Run]:
        return [
            r.with_metrics(
                {"latency": replace(r.metrics["latency"], unit="ops_per_s", higher_is_better=True)}
            )
            for r in rs
        ]

    lower = compare(throughput(candidates(9.0)), Selection(throughput(BASELINE.runs)), POLICY)

    assert lower.verdict is Verdict.REGRESSION


def test_a_noisy_baseline_is_inconclusive_even_with_a_real_slowdown() -> None:
    noisy = Selection(runs(0, 20, level=10.0, drift=0.10, seed=1))

    result = compare(candidates(11.0), noisy, POLICY)

    assert result.verdict is Verdict.INCONCLUSIVE
    assert "noise" in result.reason


def test_an_emulated_candidate_is_incomparable_not_a_regression() -> None:
    result = compare(candidates(11.0, rig=rig(emulated=True)), BASELINE, POLICY)

    assert result.verdict is Verdict.INCOMPARABLE
    assert "rig.emulated" in result.reason
    assert result.metrics == []


def test_a_failed_candidate_is_inconclusive_not_a_regression() -> None:
    failed = candidates(11.0)
    failed[1] = replace(failed[1], status="FAILED", status_reason="timeout")

    result = compare(failed, BASELINE, POLICY)

    assert result.verdict is Verdict.INCONCLUSIVE
    assert "FAILED: timeout" in result.reason


def test_candidates_that_disagree_with_each_other_are_incomparable() -> None:
    mixed = [*candidates(10.0, count=2), *candidates(10.0, count=1, comparison_key="0" * 64)]

    assert compare(mixed, BASELINE, POLICY).verdict is Verdict.INCOMPARABLE


def test_no_compatible_baseline_is_incomparable() -> None:
    empty = Selection(runs=[])
    empty.excluded["environment: rig.hardware_class differs"] = 20

    result = compare(candidates(10.0), empty, POLICY)

    assert result.verdict is Verdict.INCOMPARABLE
    assert "20 recent runs" in result.reason


def test_too_few_runs_on_either_side_is_inconclusive() -> None:
    thin = Selection(BASELINE.runs[:4])

    assert compare(candidates(11.0), thin, POLICY).verdict is Verdict.INCONCLUSIVE
    one = compare(candidates(11.0, count=1), BASELINE, POLICY)
    assert one.verdict is Verdict.INCONCLUSIVE
    assert "1 candidate runs" in one.reason


def test_a_missing_metric_is_inconclusive_for_that_metric() -> None:
    bare = [run(100 + i, revision=CANDIDATE, metrics={"latency": [10.0] * 30}) for i in range(3)]

    result = compare(bare, BASELINE, POLICY)

    rss = result.metrics[1]
    assert rss.verdict is Verdict.INCONCLUSIVE
    assert "missing from 3" in rss.reason


def test_guardrail_regression_warns_unless_guardrails_block() -> None:
    heavy = [
        r.with_metrics({"max_rss": replace(r.metrics["max_rss"], values=(120.0,))})
        for r in candidates(10.0)
    ]

    assert compare(heavy, BASELINE, POLICY).verdict is Verdict.WARNING
    strict = POLICY.model_copy(update={"guardrails_block": True})
    assert compare(heavy, BASELINE, strict).verdict is Verdict.REGRESSION


def test_jitter_regression_with_an_unchanged_mean() -> None:
    policy = BenchmarkPolicy(
        benchmark="matmul",
        n_boot=500,
        jitter=JitterPolicy(source="tick", period=20.0, tolerance=0.25),
        metrics=[
            MetricPolicy(metric="tick", statistic="mean", threshold=0.03),
            MetricPolicy(
                metric="tick.deadline_miss", statistic="mean", threshold=0.01, mode=Mode.ABSOLUTE
            ),
        ],
    )
    steady = [20.0, 20.1, 19.9, 20.0] * 25
    # Same mean, but four of every hundred ticks now land late.
    spiky = [20.0] * 96 + [30.0, 10.0, 30.0, 10.0]
    base = Selection([run(i, metrics={"tick": steady}) for i in range(10)])
    cand = [run(100 + i, revision=CANDIDATE, metrics={"tick": spiky}) for i in range(3)]

    result = compare(cand, base, policy)

    mean, misses = result.metrics
    assert mean.verdict is Verdict.PASS
    assert misses.verdict is Verdict.REGRESSION
    assert result.verdict is Verdict.REGRESSION


def test_the_same_seed_gives_the_same_interval() -> None:
    a = compare(candidates(10.5), BASELINE, POLICY, seed=3).metrics[0].estimate
    b = compare(candidates(10.5), BASELINE, POLICY, seed=3).metrics[0].estimate

    assert a == b


def test_driver_drift_is_reported_without_blocking() -> None:
    result = compare(candidates(10.0, rig=rig(driver_version="555")), BASELINE, POLICY)

    assert result.verdict is Verdict.PASS
    assert result.drift == ["rig.driver_version: candidate '555', baseline ''"]


def test_series_is_immutable_so_a_derived_metric_cannot_leak_back() -> None:
    original = run()
    derived = original.with_metrics({"extra": Series("ns", False, (1.0,))})

    assert "extra" not in original.metrics
    assert "extra" in derived.metrics


CONFIRMING = POLICY.model_copy(update={"confirm_regressions": True})


def later(level: float) -> list[Run]:
    return runs(200, 3, level=level, drift=0.01, seed=9, revision=CANDIDATE)


def test_a_first_batch_regression_waits_for_confirmation() -> None:
    result = compare(candidates(11.0), BASELINE, CONFIRMING)

    assert result.verdict is Verdict.INCONCLUSIVE
    assert result.needs_confirmation
    assert result.reason.startswith("awaiting a confirmation batch")


def test_a_regression_both_batches_show_is_confirmed() -> None:
    result = compare(candidates(11.0), BASELINE, CONFIRMING, confirmation=later(11.0))

    assert result.verdict is Verdict.REGRESSION
    assert result.confirmation is not None
    assert result.confirmation.verdict is Verdict.REGRESSION
    assert result.reason.startswith("confirmed by a second batch on latency.median")


def test_a_burst_that_hit_only_the_first_batch_is_not_a_regression() -> None:
    result = compare(candidates(11.0), BASELINE, CONFIRMING, confirmation=later(10.0))

    assert result.verdict is Verdict.INCONCLUSIVE
    assert "did not agree (PASS" in result.reason


def test_confirmation_must_regress_on_the_same_blocking_metric() -> None:
    policy = CONFIRMING.model_copy(update={"guardrails_block": True})
    heavy_second = [
        r.with_metrics({"max_rss": replace(r.metrics["max_rss"], values=(130.0,))})
        for r in later(10.0)
    ]

    result = compare(candidates(11.0), BASELINE, policy, confirmation=heavy_second)

    assert result.confirmation is not None
    assert result.confirmation.verdict is Verdict.REGRESSION
    assert result.verdict is Verdict.INCONCLUSIVE


def test_a_confirmation_batch_on_other_hardware_is_incomparable() -> None:
    elsewhere = runs(
        200, 3, level=11.0, drift=0.01, seed=9, revision=CANDIDATE, rig=rig(arch="x86_64")
    )

    result = compare(candidates(11.0), BASELINE, CONFIRMING, confirmation=elsewhere)

    assert result.verdict is Verdict.INCOMPARABLE
    assert "rig.arch" in result.reason


def test_without_the_policy_flag_confirmation_is_ignored() -> None:
    result = compare(candidates(11.0), BASELINE, POLICY, confirmation=later(10.0))

    assert result.verdict is Verdict.REGRESSION
    assert result.confirmation is None
