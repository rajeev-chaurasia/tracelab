import numpy as np
import pytest

from tracelab.core.describe import Summary, agree, disagreements, summarize


@pytest.mark.parametrize("n", [1, 2, 3, 7, 20, 101])
def test_matches_numpy(n: int) -> None:
    rng = np.random.default_rng(n)
    x = rng.normal(1_800_000, 40_000, n).round()

    s = summarize("ns", list(x))

    assert s.n == n
    assert s.mean is not None and agree(s.mean, float(np.mean(x)))
    for field, p in (("median", 50), ("p90", 90), ("p95", 95), ("p99", 99)):
        value = getattr(s, field)
        assert value is not None and agree(value, float(np.percentile(x, p)))
    median = float(np.median(x))
    assert s.mad is not None and agree(s.mad, float(np.median(np.abs(x - median))))
    if n >= 2:
        assert s.stddev is not None and agree(s.stddev, float(np.std(x, ddof=1)))
    else:
        assert s.stddev is None and s.cv is None


def test_empty_is_all_null_not_zero() -> None:
    s = summarize("ns", [])

    assert s.n == 0
    assert all(getattr(s, f) is None for f in ("mean", "median", "stddev", "mad", "cv"))


def test_zero_mean_has_no_cv() -> None:
    s = summarize("unitless", [-1.0, 1.0])

    assert s.stddev is not None and s.cv is None


def test_tolerance_can_be_met_at_zero() -> None:
    assert agree(0.0, 1e-13)
    assert not agree(0.0, 1e-9)
    assert agree(1e9, 1e9 + 0.5)
    assert not agree(1e9, 1e9 + 5)


def test_null_and_zero_disagree() -> None:
    truth = summarize("ns", [5.0])
    claimed = Summary.model_validate({**truth.model_dump(), "stddev": 0.0})

    assert disagreements(claimed, truth) == ["stddev 0.0 != None"]
