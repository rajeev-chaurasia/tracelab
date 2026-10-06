import pytest

from tracelab.core.jitter import derive, intervals_ms
from tracelab.core.policy import JitterPolicy

from .factories import run


def test_intervals_from_tick_timestamps() -> None:
    assert intervals_ms([0, 20_000_000, 41_000_000]) == [20.0, 21.0]


def test_a_spike_becomes_a_deadline_miss_the_mean_would_hide() -> None:
    loop = run(metrics={"loop.interval_ms": [19.8, 20.1, 20.0, 21.0, 19.9, 32.4]})

    derived = derive(loop, JitterPolicy(source="loop.interval_ms", period_ms=20, tolerance=0.25))

    assert derived.metrics["loop.interval_ms.deadline_miss"].values == [0, 0, 0, 0, 0, 1]
    deviation = derived.metrics["loop.interval_ms.deviation"].values
    assert max(deviation) == pytest.approx(12.4)


def test_leaves_a_run_without_the_source_alone() -> None:
    plain = run()

    assert (
        derive(plain, JitterPolicy(source="loop.interval_ms", period_ms=20, tolerance=0)) is plain
    )
