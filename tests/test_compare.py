from typing import Any

import numpy as np

from tracelab.core.baseline import Selection
from tracelab.core.compare import compare
from tracelab.core.policy import BenchmarkPolicy, JitterPolicy, MetricClass, MetricPolicy, Verdict
from tracelab.core.schema import RunResult
from tracelab.core.stats import Mode

from .factories import environment, run

POLICY = BenchmarkPolicy(
    benchmark="matmul",
    n_boot=500,
    metrics=[
        MetricPolicy(metric="latency_ms", statistic="median", threshold=0.03),
        MetricPolicy(
            metric="rss_mb", statistic="median", threshold=0.05, role=MetricClass.GUARDRAIL
        ),
    ],
)


def runs(
    start: int, count: int, *, level: float, drift: float, seed: int, **overrides: Any
) -> list[RunResult]:
    rng = np.random.default_rng(seed)
    out = []
    for i in range(count):
        center = level * (1 + rng.normal(0, drift))
        out.append(
            run(
                start + i,
                metrics={
                    "latency_ms": list(center * (1 + rng.normal(0, 0.01, 30))),
                    "rss_mb": [100.0 + rng.normal(0, 0.2)],
                },
                **overrides,
            )
        )
    return out


def candidates(level: float, drift: float = 0.01, count: int = 3, **kw: Any) -> list[RunResult]:
    return runs(100, count, level=level, drift=drift, seed=7, git_sha="b" * 40, **kw)


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
    assert result.reason.startswith("latency_ms.median: +")


def test_a_speedup_is_an_improvement() -> None:
    assert compare(candidates(9.0), BASELINE, POLICY).verdict is Verdict.IMPROVEMENT


def test_a_noisy_baseline_is_inconclusive_even_with_a_real_slowdown() -> None:
    noisy = Selection(runs(0, 20, level=10.0, drift=0.10, seed=1))

    result = compare(candidates(11.0), noisy, POLICY)

    assert result.verdict is Verdict.INCONCLUSIVE
    assert "noise" in result.reason


def test_a_driver_change_is_incomparable_not_a_regression() -> None:
    upgraded = candidates(11.0, env=environment(driver_version="555"))

    result = compare(upgraded, BASELINE, POLICY)

    assert result.verdict is Verdict.INCOMPARABLE
    assert "driver_version" in result.reason
    assert result.metrics == []


def test_candidates_that_disagree_with_each_other_are_incomparable() -> None:
    mixed = [*candidates(10.0, count=2), *candidates(10.0, count=1, scenario="n512")]

    assert compare(mixed, BASELINE, POLICY).verdict is Verdict.INCOMPARABLE


def test_no_compatible_baseline_is_incomparable() -> None:
    empty = Selection(runs=[], excluded={"environment: driver_version differs": 20})  # type: ignore[arg-type]

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
    bare = [run(100 + i, git_sha="b" * 40, metrics={"latency_ms": [10.0] * 30}) for i in range(3)]

    result = compare(bare, BASELINE, POLICY)

    rss = result.metrics[1]
    assert rss.verdict is Verdict.INCONCLUSIVE
    assert "missing from 3" in rss.reason


def test_guardrail_regression_warns_unless_guardrails_block() -> None:
    heavy = candidates(10.0)
    heavy = [
        r.with_metrics({"rss_mb": r.metrics["rss_mb"].model_copy(update={"values": [120.0]})})
        for r in heavy
    ]

    assert compare(heavy, BASELINE, POLICY).verdict is Verdict.WARNING
    strict = POLICY.model_copy(update={"guardrails_block": True})
    assert compare(heavy, BASELINE, strict).verdict is Verdict.REGRESSION


def test_jitter_regression_with_an_unchanged_mean() -> None:
    policy = BenchmarkPolicy(
        benchmark="matmul",
        n_boot=500,
        jitter=JitterPolicy(source="loop.interval_ms", period_ms=20, tolerance=0.25),
        metrics=[
            MetricPolicy(metric="loop.interval_ms", statistic="mean", threshold=0.03),
            MetricPolicy(
                metric="loop.interval_ms.deadline_miss",
                statistic="mean",
                threshold=0.01,
                mode=Mode.ABSOLUTE,
            ),
        ],
    )
    steady = [20.0, 20.1, 19.9, 20.0] * 25
    # Same mean, but four of every hundred ticks now land late.
    spiky = [20.0] * 96 + [30.0, 10.0, 30.0, 10.0]
    base = Selection([run(i, metrics={"loop.interval_ms": steady}) for i in range(10)])
    cand = [run(100 + i, git_sha="b" * 40, metrics={"loop.interval_ms": spiky}) for i in range(3)]

    result = compare(cand, base, policy)

    mean, misses = result.metrics
    assert mean.verdict is Verdict.PASS
    assert misses.verdict is Verdict.REGRESSION
    assert result.verdict is Verdict.REGRESSION


def test_the_same_seed_gives_the_same_interval() -> None:
    a = compare(candidates(10.5), BASELINE, POLICY, seed=3).metrics[0].estimate
    b = compare(candidates(10.5), BASELINE, POLICY, seed=3).metrics[0].estimate

    assert a == b


def test_kernel_drift_is_reported_without_blocking() -> None:
    drifted = candidates(10.0, env=environment(kernel="test-kernel-2"))

    result = compare(drifted, BASELINE, POLICY)

    assert result.verdict is Verdict.PASS
    assert result.drift == ["kernel: candidate 'test-kernel-2', baseline 'test-kernel-1'"]
