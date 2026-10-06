from tracelab.core.baseline import select
from tracelab.core.compat import blocking, mismatches
from tracelab.core.policy import BenchmarkPolicy, MetricPolicy
from tracelab.core.run import Run

from .factories import CANDIDATE, KNOWN_GOOD, run
from .factories import rig as make_rig

POLICY = BenchmarkPolicy(
    benchmark="matmul",
    baseline_window=3,
    metrics=[MetricPolicy(metric="latency", statistic="median", threshold=0.03)],
)


def candidate(index: int = 100) -> list[Run]:
    return [run(index, revision=CANDIDATE)]


def test_emulated_rig_is_disqualifying_and_driver_drift_is_only_noted() -> None:
    base = run(0)

    emulated = run(1, rig=make_rig(emulated=True))
    driver = run(2, rig=make_rig(driver_version="555.42"))

    assert blocking(emulated, base) is not None
    assert blocking(driver, base) is None
    assert [str(m) for m in mismatches(driver, base)] == [
        "rig.driver_version: candidate '555.42', baseline ''"
    ]


def test_a_different_measurement_spec_is_disqualifying() -> None:
    mismatch = blocking(run(1, comparison_key="0" * 64), run(0))

    assert mismatch is not None
    assert mismatch.field == "comparison_key"


def test_keeps_the_newest_succeeded_compatible_known_good_runs() -> None:
    history = [run(i) for i in range(6)]

    chosen = select(candidate(), history, POLICY, KNOWN_GOOD)

    assert [r.run_id for r in chosen.runs] == ["exp-0005", "exp-0004", "exp-0003"]
    assert chosen.excluded == {"older than the baseline window": 3}


def test_explains_every_exclusion() -> None:
    history = [
        run(1, revision="e" * 40),
        run(2, revision=CANDIDATE),
        run(3, status="FAILED", status_reason="timeout"),
        run(4, status="INVALID", status_reason="preflight:load1"),
        run(5, rig=make_rig(hardware_class="gpu-a")),
        run(200),
        run(6),
        run(7, benchmark="other"),
    ]

    chosen = select(candidate(), history, POLICY, KNOWN_GOOD)

    assert [r.run_id for r in chosen.runs] == ["exp-0006"]
    assert chosen.excluded == {
        "revision not known good": 1,
        "same revision as the candidate": 1,
        "status FAILED": 1,
        "status INVALID": 1,
        "environment: rig.hardware_class differs": 1,
        "started after the candidate": 1,
    }
    assert chosen.disqualified_by_environment == 1
