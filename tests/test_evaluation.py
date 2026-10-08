"""The harness has to be trustworthy before its table is.

An injected change that does not inject what it says, or a validator that
cannot see an edit, would make every published number meaningless, so both
are tested against inputs whose answers are known.
"""

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from evaluation import cases as c
from evaluation.score import summarize
from script.validate_evidence import check_claim
from tracelab.core.run import Run

from .factories import run

RNG = np.random.default_rng(0)


def runs(metric: str, values: list[float], count: int = 3) -> list[Run]:
    return [run(i, metrics={metric: values}) for i in range(count)]


def test_scale_moves_only_the_candidates_by_the_stated_factor() -> None:
    base, cand = runs(c.LAT, [10.0, 20.0]), runs(c.LAT, [10.0, 20.0])

    new_base, new_cand = c._scale(c.LAT, 1.10)(base, cand, RNG)

    assert new_base == base
    assert new_cand[0].metrics[c.LAT].values == pytest.approx((11.0, 22.0))


def test_tail_inflates_only_samples_above_each_runs_p90() -> None:
    values = [float(v) for v in range(1, 21)]

    _, cand = c._tail(c.LAT, 1.5)([], runs(c.LAT, values), RNG)

    # p90 of 1..20 is 18.1 by numpy's default rule, so 19 and 20 are inflated.
    out = cand[0].metrics[c.LAT].values
    assert out[:18] == tuple(values[:18])
    assert out[18:] == (28.5, 30.0)
    assert np.median(out) == np.median(values)


def test_spread_keeps_the_median_and_doubles_distance_from_it() -> None:
    _, cand = c._spread(c.LAT, 2.0)([], runs(c.LAT, [8.0, 10.0, 13.0]), RNG)

    assert cand[0].metrics[c.LAT].values == (6.0, 10.0, 16.0)


def test_late_ticks_move_the_miss_rate_and_not_the_mean() -> None:
    steady = [c.PERIOD] * 101

    _, cand = c._late_ticks(c.TICK, c.PERIOD, 0.05)([], runs(c.TICK, steady), RNG)

    out = np.asarray(cand[0].metrics[c.TICK].values)
    assert (out > c.PERIOD * 1.25).sum() == 5
    assert out.mean() == pytest.approx(c.PERIOD)


def test_noisy_baseline_moves_each_run_by_its_own_factor() -> None:
    base = runs(c.LAT, [10.0, 10.0], count=20)

    moved, _ = c._noisy_baseline(c.LAT, 0.08)(base, [], np.random.default_rng(1))

    levels = [r.metrics[c.LAT].values[0] for r in moved]
    assert len(set(levels)) == 20
    assert all(r.metrics[c.LAT].values[0] == r.metrics[c.LAT].values[1] for r in moved)


def test_windows_never_overlap_baseline_and_candidates_and_are_reproducible() -> None:
    corpus = {
        "matmul": [run(i, metrics={c.LAT: [10.0] * 5, c.RSS: [1.0] * 5}) for i in range(30)],
        "periodic": [run(i, metrics={c.TICK: [c.PERIOD] * 5}) for i in range(30)],
    }

    first = list(c.cases(corpus))
    second = list(c.cases(corpus))

    assert [x.case_id for x in first] == [x.case_id for x in second]
    for case in first:
        base_ids = {r.run_id for r in case.baseline}
        assert base_ids.isdisjoint(r.run_id for r in case.candidates)
        assert all(r.revision == c.CANDIDATE_REVISION for r in case.candidates)
        assert len(case.baseline) == c.BASELINE_RUNS
        assert len(case.candidates) == case.kind.candidates


def decision(comparator: str, kind: str, verdict: str, allowed: list[str]) -> dict[str, Any]:
    return {
        "comparator": comparator,
        "benchmark": "matmul",
        "kind": kind,
        "verdict": verdict,
        "allowed": allowed,
        "correct": verdict in allowed,
    }


def test_claim_check_fails_on_a_tracelab_false_regression() -> None:
    decisions = [
        decision("tracelab", "same_code", "REGRESSION", ["PASS"]),
        decision("fixed_5pct_window", "same_code", "REGRESSION", ["PASS"]),
    ]

    assert check_claim(decisions) == ["claim fails: tracelab raised 1 false regressions"]


def test_negative_control_fails_when_nothing_is_fooled() -> None:
    decisions = [
        decision("tracelab", "same_code", "PASS", ["PASS"]),
        decision("fixed_5pct_window", "same_code", "PASS", ["PASS"]),
    ]

    [error] = check_claim(decisions)
    assert error.startswith("negative control fails")


def test_summary_counts_come_only_from_decisions() -> None:
    decisions = [
        decision("tracelab", "same_code", "PASS", ["PASS"]),
        decision("tracelab", "latency_+10%", "REGRESSION", ["REGRESSION"]),
        decision("tracelab", "latency_+5%", "INCONCLUSIVE", ["REGRESSION"]),
        decision("tracelab", "noisy_baseline_same_code", "REGRESSION", ["INCONCLUSIVE", "PASS"]),
    ]

    s = summarize(decisions)["comparators"]["tracelab"]

    assert s["correct"] == 2
    assert s["false_regressions"] == 1
    assert s["same_code_false_regressions"] == 0
    assert (s["regressions_caught"], s["regressions_to_catch"]) == (1, 2)
    assert s["regressions_inconclusive"] == 1


def test_a_confirmation_batch_comes_later_and_carries_the_injected_change() -> None:
    corpus = {
        "matmul": [run(i, metrics={c.LAT: [10.0] * 5, c.RSS: [1.0] * 5}) for i in range(60)],
        "periodic": [run(i, metrics={c.TICK: [c.PERIOD] * 5}) for i in range(60)],
    }

    built = [x for x in c.cases(corpus, confirmation_gap=18) if x.kind.name == "latency_+10%"]

    assert built
    for case in built:
        assert case.confirmation is not None
        first_end = int(case.candidates[-1].run_id.split("-")[1])
        second_start = int(case.confirmation[0].run_id.split("-")[1])
        assert second_start - first_end - 1 == 18
        assert all(r.metrics[c.LAT].values[0] == pytest.approx(11.0) for r in case.confirmation)
        assert all(r.revision == c.CANDIDATE_REVISION for r in case.confirmation)


def test_without_a_gap_there_is_no_confirmation_batch() -> None:
    corpus = {
        "matmul": [run(i, metrics={c.LAT: [10.0] * 5, c.RSS: [1.0] * 5}) for i in range(30)],
        "periodic": [run(i, metrics={c.TICK: [c.PERIOD] * 5}) for i in range(30)],
    }

    assert all(case.confirmation is None for case in c.cases(corpus))


def test_a_published_failure_is_pinned_to_its_count() -> None:
    decisions = [decision("tracelab", "same_code", "REGRESSION", ["PASS"])] * 10

    assert check_claim(decisions, pinned=10) == []
    assert check_claim(decisions[:9], pinned=10) == [
        "published as 10 false regressions, decisions show 9"
    ]


def test_parallel_scoring_matches_one_process_row_for_row() -> None:
    from evaluation.score import decisions_for
    from evaluation.versions import VERSIONS

    naive = ("fixed_5pct_window", "fixed_5pct_previous_run")
    serial = decisions_for(VERSIONS["v1"], workers=1, only=naive)
    parallel = decisions_for(VERSIONS["v1"], workers=3, only=naive)

    assert len(serial) == 2 * len({d["case_id"] for d in serial})
    assert parallel == serial


def test_a_version_with_its_decisions_deleted_fails_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from dataclasses import replace as swap

    from evaluation import versions
    from script import validate_evidence

    gone = swap(versions.VERSIONS["v3"], out=tmp_path / "nowhere")
    monkeypatch.setitem(versions.VERSIONS, "v3", gone)

    assert validate_evidence.main(["v3"]) == 1


def test_a_slowdown_moves_latency_up_and_its_rate_down_together() -> None:
    cand = [
        run(i, metrics={c.LAT: [100.0, 200.0], "memory_bandwidth": [10.0, 5.0]}) for i in range(2)
    ]

    _, slower = c._slower("memory_bandwidth", 1.25)([], cand, RNG)

    assert slower[0].metrics[c.LAT].values == pytest.approx((125.0, 250.0))
    assert slower[0].metrics["memory_bandwidth"].values == pytest.approx((8.0, 4.0))


def test_throughput_kinds_exist_for_each_rate_benchmark_and_skip_absent_ones() -> None:
    names = {(k.benchmark, k.name) for k in c.KINDS}
    corpus = {"matmul": [run(i, metrics={c.LAT: [1.0], c.RSS: [1.0]}) for i in range(30)]}

    assert ("membw", "slower_5%") in names
    assert ("network", "faster_10%") in names
    assert {case.kind.benchmark for case in c.cases(corpus)} == {"matmul"}


def test_canary_runs_sit_at_the_same_positions_and_are_never_transformed() -> None:
    corpus = {
        "membw": [
            run(i, metrics={c.LAT: [10.0], "memory_bandwidth": [5.0], "cpu_time": [1.0]})
            for i in range(60)
        ],
        "network": [
            run(1000 + i, metrics={c.LAT: [7.0], "network_throughput": [3.0], "cpu_time": [1.0]})
            for i in range(60)
        ],
    }

    built = [
        x
        for x in c.cases(corpus, confirmation_gap=18, canaries={"membw": "network"})
        if x.kind.benchmark == "membw" and x.kind.name == "slower_10%"
    ]

    case = built[1]
    assert case.canary is not None
    assert [r.run_id for r in case.canary.baseline] == [
        corpus["network"][i].run_id for i in range(case.start, case.start + 20)
    ]
    first_at = case.start + 20
    assert [r.run_id for r in case.canary.during[0]] == [
        corpus["network"][i].run_id for i in range(first_at, first_at + 3)
    ]
    assert all(r.metrics["network_throughput"].values == (3.0,) for r in case.canary.during[1])
    unpaired = next(
        x for x in c.cases(corpus, 18, {"membw": "network"}) if x.kind.benchmark == "network"
    )
    assert unpaired.canary is None


def test_a_control_comparator_must_have_been_fooled() -> None:
    held = [
        decision("tracelab", "same_code", "PASS", ["PASS"]),
        decision("tracelab_no_environment", "slower_2%", "REGRESSION", ["PASS", "WARNING"]),
    ]
    quiet = [
        decision("tracelab", "same_code", "PASS", ["PASS"]),
        decision("tracelab_no_environment", "same_code", "PASS", ["PASS"]),
    ]

    assert check_claim(held, control="tracelab_no_environment") == []
    [error] = check_claim(quiet, control="tracelab_no_environment")
    assert error.startswith("negative control fails: tracelab_no_environment")
