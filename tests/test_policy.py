from pathlib import Path

import pytest
from pydantic import ValidationError

from tracelab.core.policy import (
    BenchmarkPolicy,
    MetricClass,
    MetricPolicy,
    Verdict,
    classify,
    rollup,
)
from tracelab.core.stats import Estimate, Mode

T = 0.03


def interval(low: float, change: float, high: float) -> Estimate:
    return Estimate(
        baseline=10, candidate=10, change=change, low=low, high=high, mode=Mode.RELATIVE
    )


@pytest.mark.parametrize(
    ("low", "change", "high", "verdict"),
    [
        # Confidently inside the band nobody cares about, even if significant.
        (0.001, 0.002, 0.004, Verdict.PASS),
        (-0.02, 0.0, 0.02, Verdict.PASS),
        # Clearly worse and at least as large as the threshold.
        (0.07, 0.10, 0.13, Verdict.REGRESSION),
        (0.01, 0.05, 0.09, Verdict.REGRESSION),
        # Real but small: statistically worse, point estimate below threshold.
        (0.005, 0.02, 0.035, Verdict.WARNING),
        (-0.13, -0.10, -0.07, Verdict.IMPROVEMENT),
        (-0.04, -0.02, -0.001, Verdict.PASS),
        # Spans zero and reaches past the threshold: cannot tell.
        (-0.01, 0.055, 0.12, Verdict.INCONCLUSIVE),
        (-0.12, -0.05, 0.01, Verdict.INCONCLUSIVE),
    ],
)
def test_decision_table(low: float, change: float, high: float, verdict: Verdict) -> None:
    assert classify(interval(low, change, high), T) is verdict


C, G, INFO = MetricClass.CRITICAL, MetricClass.GUARDRAIL, MetricClass.INFORMATIONAL
V = Verdict


@pytest.mark.parametrize(
    ("verdicts", "block", "expected"),
    [
        ([(C, V.PASS), (G, V.PASS)], False, V.PASS),
        ([(C, V.REGRESSION), (G, V.PASS)], False, V.REGRESSION),
        ([(C, V.PASS), (G, V.REGRESSION)], False, V.WARNING),
        ([(C, V.PASS), (G, V.REGRESSION)], True, V.REGRESSION),
        ([(C, V.PASS), (INFO, V.REGRESSION)], True, V.PASS),
        ([(C, V.INCONCLUSIVE), (G, V.REGRESSION)], False, V.INCONCLUSIVE),
        ([(C, V.INCONCLUSIVE), (C, V.REGRESSION)], False, V.REGRESSION),
        ([(C, V.PASS), (G, V.INCONCLUSIVE)], False, V.PASS),
        ([(C, V.IMPROVEMENT), (G, V.WARNING)], False, V.WARNING),
        ([(C, V.IMPROVEMENT), (G, V.PASS)], False, V.IMPROVEMENT),
        ([(G, V.IMPROVEMENT)], False, V.PASS),
        ([(C, V.REGRESSION), (G, V.INCOMPARABLE)], False, V.INCOMPARABLE),
    ],
)
def test_rollup(
    verdicts: list[tuple[MetricClass, Verdict]], block: bool, expected: Verdict
) -> None:
    assert rollup(verdicts, block) is expected


def test_loads_a_policy_file(tmp_path: Path) -> None:
    path = tmp_path / "bench.toml"
    path.write_text(
        'benchmark = "matmul"\n'
        "[[metrics]]\n"
        'metric = "latency_ms"\nstatistic = "p95"\nthreshold = 0.03\n'
        "[[metrics]]\n"
        'metric = "rss_mb"\nstatistic = "median"\nthreshold = 0.05\nrole = "guardrail"\n'
    )

    policy = BenchmarkPolicy.load(path)

    assert [m.label for m in policy.metrics] == ["latency_ms.p95", "rss_mb.median"]
    assert policy.metrics[1].role is MetricClass.GUARDRAIL
    assert policy.min_candidate_runs == 3


def test_rejects_an_unknown_statistic_at_load_time() -> None:
    with pytest.raises(ValidationError):
        MetricPolicy(metric="latency_ms", statistic="p999x", threshold=0.03)
