from collections import Counter

import pytest

from tracelab.core.compare import Comparison, MetricResult
from tracelab.core.policy import MetricPolicy, Verdict
from tracelab.report import CONCLUSIONS, render, value

from .factories import run


@pytest.mark.parametrize(
    ("unit", "x", "text"),
    [
        ("ns", 2.5e9, "2.500 s"),
        ("ns", 41_800_000, "41.800 ms"),
        ("ns", 999, "999 ns"),
        ("bytes", 3 * 2**30, "3.00 GiB"),
        ("bytes", 512, "512 B"),
        ("ratio", 0.0125, "1.25%"),
        ("ops_per_s", 1234.5, "1234 ops_per_s"),
    ],
)
def test_values_print_in_the_unit_a_person_reads(unit: str, x: float, text: str) -> None:
    assert value(unit, x) == text


def test_only_a_regression_fails_the_check() -> None:
    assert [v for v, c in CONCLUSIONS.items() if c == "failure"] == [Verdict.REGRESSION]
    assert CONCLUSIONS[Verdict.INCONCLUSIVE] == "neutral"


def test_an_undecided_metric_shows_why_and_exclusions_are_listed() -> None:
    policy = MetricPolicy(metric="latency", statistic="p95", threshold=0.05)
    result = Comparison(
        verdict=Verdict.INCONCLUSIVE,
        reason="latency.p95: baseline run-to-run noise 12.0% exceeds the 10.0% limit",
        candidate_runs=["a", "b", "c"],
        baseline_runs=["x"] * 20,
        metrics=[MetricResult(policy, Verdict.INCONCLUSIVE, "baseline run-to-run noise 12.0%")],
        excluded=Counter({"status FAILED": 2}),
        drift=["rig.kernel: candidate '1', baseline '2'"],
    )

    text = render("matmul", result, [run()])

    assert text.startswith("## Inconclusive: no decision either way: matmul")
    assert "| | | | | | | baseline run-to-run noise 12.0% |" in text
    assert "Left out of the baseline: 2 status FAILED." in text
    assert "- rig.kernel: candidate '1', baseline '2'" in text
