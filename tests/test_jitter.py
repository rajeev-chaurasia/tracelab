import pytest

from tracelab.core.jitter import derive
from tracelab.core.policy import JitterPolicy

from .factories import run

MS = 1_000_000


def test_a_spike_becomes_a_deadline_miss_the_mean_would_hide() -> None:
    ticks = [19.8, 20.1, 20.0, 21.0, 19.9, 32.4]
    loop = run(metrics={"tick": [t * MS for t in ticks]})

    derived = derive(loop, JitterPolicy(source="tick", period=20 * MS, tolerance=0.25))

    assert derived.metrics["tick.deadline_miss"].values == (0, 0, 0, 0, 0, 1)
    assert derived.metrics["tick.deadline_miss"].unit == "ratio"
    assert max(derived.metrics["tick.deviation"].values) == pytest.approx(12.4 * MS)


def test_leaves_a_run_without_the_source_alone() -> None:
    plain = run()

    assert derive(plain, JitterPolicy(source="tick", period=20, tolerance=0)) is plain
