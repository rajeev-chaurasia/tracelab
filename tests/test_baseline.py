from tracelab.core.baseline import select
from tracelab.core.compat import comparable, mismatches
from tracelab.core.policy import BenchmarkPolicy, MetricPolicy
from tracelab.core.schema import RunResult, RunStatus

from .factories import environment, run

POLICY = BenchmarkPolicy(
    benchmark="matmul",
    baseline_window=3,
    metrics=[MetricPolicy(metric="latency_ms", statistic="median", threshold=0.03)],
)


def candidate(index: int = 100) -> list[RunResult]:
    return [run(index, git_sha="b" * 40, branch="pr-184")]


def test_driver_change_is_disqualifying_and_kernel_drift_is_not() -> None:
    base = run(0, env=environment(driver_version="550.54"))

    driver = run(1, env=environment(driver_version="555.42"))
    kernel = run(2, env=environment(driver_version="550.54", kernel="test-kernel-2"))

    assert not comparable(driver, base)
    assert comparable(kernel, base)
    assert [str(m) for m in mismatches(kernel, base)] == [
        "kernel: candidate 'test-kernel-2', baseline 'test-kernel-1'"
    ]


def test_config_change_is_disqualifying() -> None:
    assert not comparable(run(1, config_sha256="d" * 64), run(0))


def test_keeps_the_newest_healthy_compatible_main_runs() -> None:
    history = [run(i) for i in range(6)]

    chosen = select(candidate(), history, POLICY)

    assert [r.run_id for r in chosen.runs] == ["run-0005", "run-0004", "run-0003"]
    assert chosen.excluded == {"older than the baseline window": 3}


def test_explains_every_exclusion() -> None:
    history = [
        run(1, branch="feature"),
        run(2, git_sha="b" * 40),
        run(3, status=RunStatus.TIMED_OUT),
        run(4, healthy=False),
        run(5, env=environment(gpu_model="other")),
        run(200),
        run(6),
        run(7, benchmark="other"),
    ]

    chosen = select(candidate(), history, POLICY)

    assert [r.run_id for r in chosen.runs] == ["run-0006"]
    assert chosen.excluded == {
        "not on the baseline branch": 1,
        "same revision as the candidate": 1,
        "status timed_out": 1,
        "marked unhealthy by the rig": 1,
        "environment: gpu_model differs": 1,
        "started after the candidate": 1,
    }
    assert chosen.disqualified_by_environment == 1
