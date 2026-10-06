import numpy as np
import pytest

from tracelab.core.stats import (
    Estimate,
    Mode,
    Statistic,
    compare,
    pooled,
    resample,
    run_noise,
)

MEDIAN = Statistic.parse("median")


def median_change(
    baseline: list[list[float]], candidate: list[list[float]], rng: np.random.Generator, n_boot: int
) -> Estimate:
    return compare(
        baseline,
        candidate,
        MEDIAN,
        higher_is_better=False,
        mode=Mode.RELATIVE,
        confidence=0.95,
        n_boot=n_boot,
        rng=rng,
    )


def drifting_runs(
    rng: np.random.Generator, n_runs: int, per_run: int, drift: float, shift: float = 0.0
) -> list[list[float]]:
    """Runs whose level moves between processes, the way real hardware does."""
    levels = 10.0 * (1 + shift) * (1 + rng.normal(0, drift, n_runs))
    return [list(level * (1 + rng.normal(0, 0.005, per_run))) for level in levels]


@pytest.mark.parametrize(
    ("name", "quantile"),
    [("mean", None), ("median", 0.5), ("p95", 0.95), ("p99.9", 0.999), ("max", 1.0)],
)
def test_parses_statistics(name: str, quantile: float | None) -> None:
    assert Statistic.parse(name).quantile == pytest.approx(quantile)


@pytest.mark.parametrize("name", ["p0", "p100", "p", "average", "q95"])
def test_rejects_unknown_statistics(name: str) -> None:
    with pytest.raises(ValueError):
        Statistic.parse(name)


def test_unequal_runs_keep_their_own_sample_counts() -> None:
    runs = [[1.0], [2.0, 2.0, 2.0]]

    boot = resample(runs, Statistic.parse("mean"), 500, np.random.default_rng(0))

    # Both picks of the short run give 1, one of each gives (1 + 6) / 4, and
    # both picks of the long run give 2. Padding leaking in would give others.
    assert set(np.round(boot, 9)) <= {1.0, 1.75, 2.0}
    assert len(set(np.round(boot, 9))) == 3


def test_interval_reflects_run_to_run_drift_that_pooling_hides() -> None:
    rng = np.random.default_rng(1)
    baseline = drifting_runs(rng, 20, 30, drift=0.04)
    candidate = drifting_runs(rng, 3, 30, drift=0.04)

    nested = median_change(baseline, candidate, rng, 2000)
    flat = median_change(pooled(baseline), pooled(candidate), rng, 2000)

    assert (nested.high - nested.low) > 3 * (flat.high - flat.low)


def test_same_code_intervals_cover_zero_at_roughly_the_stated_rate() -> None:
    """Over many same-code draws, a 95% interval should exclude zero rarely.

    The pooled interval excludes zero most of the time on the same data, which
    is the false alarm rate a gate built on it would have.
    """
    rng = np.random.default_rng(2)
    nested_misses = flat_misses = 0
    trials = 200
    for _ in range(trials):
        baseline = drifting_runs(rng, 20, 30, drift=0.03)
        candidate = drifting_runs(rng, 5, 30, drift=0.03)
        nested = median_change(baseline, candidate, rng, 400)
        flat = median_change(pooled(baseline), pooled(candidate), rng, 400)
        nested_misses += not nested.low <= 0 <= nested.high
        flat_misses += not flat.low <= 0 <= flat.high

    assert nested_misses / trials < 0.15
    assert flat_misses / trials > 0.5


def test_change_is_signed_so_positive_means_worse() -> None:
    rng = np.random.default_rng(3)
    baseline = [[10.0] * 10] * 5
    slower = [[11.0] * 10] * 3
    median = Statistic.parse("median")

    latency = compare(
        baseline,
        slower,
        median,
        higher_is_better=False,
        mode=Mode.RELATIVE,
        confidence=0.95,
        n_boot=200,
        rng=rng,
    )
    throughput = compare(
        baseline,
        slower,
        median,
        higher_is_better=True,
        mode=Mode.RELATIVE,
        confidence=0.95,
        n_boot=200,
        rng=rng,
    )

    assert latency.change == pytest.approx(0.1)
    assert throughput.change == pytest.approx(-0.1)


def test_absolute_mode_for_rates_near_zero() -> None:
    rng = np.random.default_rng(4)
    baseline = [[0.0] * 100] * 5
    candidate = [[0.0] * 98 + [1.0] * 2] * 3

    estimate = compare(
        baseline,
        candidate,
        Statistic.parse("mean"),
        higher_is_better=False,
        mode=Mode.ABSOLUTE,
        confidence=0.95,
        n_boot=200,
        rng=rng,
    )

    assert estimate.change == pytest.approx(0.02)
    assert estimate.mode is Mode.ABSOLUTE


def test_relative_mode_refuses_a_zero_baseline() -> None:
    with pytest.raises(ValueError, match="absolute"):
        compare(
            [[0.0]],
            [[1.0]],
            Statistic.parse("mean"),
            higher_is_better=False,
            mode=Mode.RELATIVE,
            confidence=0.95,
            n_boot=10,
            rng=np.random.default_rng(0),
        )


def test_resample_refuses_an_empty_run() -> None:
    with pytest.raises(ValueError):
        resample([[1.0], []], Statistic.parse("mean"), 10, np.random.default_rng(0))


def test_run_noise_is_not_moved_by_one_bad_run() -> None:
    steady = [[10.0 + 0.1 * i] * 5 for i in range(9)]

    # A standard deviation over the same per-run medians would exceed 0.5.
    assert run_noise([*steady, [30.0] * 5], Statistic.parse("median")) < 0.05
    assert run_noise([[0.0], [0.0]], Statistic.parse("median")) == float("inf")
