"""The contention schedule has to put each burst where it claims to."""

from evaluation import contention as c
from evaluation.cases import BASELINE_RUNS, CANDIDATE_RUNS, STRIDE
from script.validate_evidence import check_contention_claim


def windows() -> range:
    return range(0, c.RUNS - (BASELINE_RUNS + 2 * CANDIDATE_RUNS + c.GAP) + 1, STRIDE)


def test_each_short_burst_targets_exactly_one_window_with_clean_neighbours() -> None:
    targeted = [s for s in windows() if c.exposure(s) is c.Exposure.SHORT_FIRST_BATCH]

    assert len(targeted) == c.SHORT_COUNT
    for start in targeted:
        first = range(start + BASELINE_RUNS, start + BASELINE_RUNS + CANDIDATE_RUNS)
        assert all(c.under_contention(i) for i in first)
        assert not any(c.under_contention(i) for i in range(start, start + BASELINE_RUNS))
        second = first.stop + c.GAP
        assert not any(c.under_contention(i) for i in range(second, second + CANDIDATE_RUNS))


def test_the_long_burst_covers_both_batches_of_one_window() -> None:
    [start] = [s for s in windows() if c.exposure(s) is c.Exposure.LONG_BOTH_BATCHES]

    first = start + BASELINE_RUNS
    second = first + CANDIDATE_RUNS + c.GAP
    assert all(c.under_contention(i) for i in range(first, second + CANDIDATE_RUNS))
    assert not any(c.under_contention(i) for i in range(start, first))


def test_a_window_with_no_contention_is_clean() -> None:
    assert c.exposure(10_000) is c.Exposure.CLEAN


def test_hogs_start_and_stop_cleanly() -> None:
    hogs = c.Hogs()

    hogs.start()
    procs = list(hogs.procs)
    hogs.stop()

    assert procs
    assert all(p.returncode is not None for p in procs)
    assert not hogs.running


def row(comparator: str, exposure: str, verdict: str) -> dict[str, object]:
    return {
        "comparator": comparator,
        "benchmark": "matmul",
        "kind": "same_code",
        "verdict": verdict,
        "allowed": ["PASS"],
        "correct": verdict == "PASS",
        "exposure": exposure,
    }


TARGET = "short_burst_first_batch"


def test_contention_claim_holds_when_confirmation_absorbs_what_fooled_one_batch() -> None:
    rows = [
        row("tracelab", TARGET, "INCONCLUSIVE"),
        row("tracelab_one_batch", TARGET, "REGRESSION"),
    ]

    assert check_contention_claim(rows) == []


def test_contention_claim_fails_on_a_tracelab_false_regression() -> None:
    rows = [row("tracelab", TARGET, "REGRESSION"), row("tracelab_one_batch", TARGET, "REGRESSION")]

    assert check_contention_claim(rows) == [
        f"claim fails: tracelab raised 1 false regressions on {TARGET}"
    ]


def test_contention_control_fails_when_the_bursts_fooled_nobody() -> None:
    rows = [row("tracelab", TARGET, "PASS"), row("tracelab_one_batch", TARGET, "PASS")]

    [error] = check_contention_claim(rows)
    assert error.startswith("negative control fails")


def test_long_burst_windows_carry_no_claim() -> None:
    rows = [
        row("tracelab", TARGET, "PASS"),
        row("tracelab_one_batch", TARGET, "REGRESSION"),
        row("tracelab", "long_burst_both_batches", "REGRESSION"),
    ]

    assert check_contention_claim(rows) == []
