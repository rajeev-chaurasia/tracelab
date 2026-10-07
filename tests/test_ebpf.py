"""The eBPF collector's parsing, and the attribution built on it.

Loading eBPF programs needs Linux and privileges, so CI cannot run the
collector itself. These tests pin the line format the bpftrace program
prints and what the analysis makes of it; the published traces under
evidence/traces were recorded for real with script/ebpf_record.sh.
"""

from evaluation.attribution import attribute
from tracelab.collect.ebpf import SCRIPT, parse

OUTPUT = """\
Attaching 4 probes...
runq 1000500 500
offcpu 1001000 0
runq 1003000 2000
offcpu 1004000 1
lost 12 events
"""


def test_parse_turns_waits_into_slices_and_switches_into_counts() -> None:
    samples = parse(OUTPUT)

    waits = [s for s in samples if s.name == "runq_latency"]
    switches = [s for s in samples if s.name == "preempted"]
    assert [(w.t_ns, w.duration_ns) for w in waits] == [(1_000_000, 500), (1_001_000, 2000)]
    assert [s.value for s in switches] == [1.0, 0.0]
    assert all(s.domain == "monotonic" for s in samples)


def test_the_program_times_waits_after_preemption_not_only_after_wakeup() -> None:
    # Under contention a preempted thread returns to a run queue with no
    # wakeup event; a program that only timed wakeups saw one wait in 150 ticks.
    assert "args->prev_state == 0" in SCRIPT
    assert "@woke[args->prev_pid] = nsecs" in SCRIPT


def trace(ticks_ms: list[float], waits: list[tuple[float, float]]) -> dict[str, object]:
    events: list[dict[str, object]] = [
        {"ph": "M", "pid": 1, "name": "process_name", "args": {"name": "workload"}},
        {"ph": "M", "pid": 2, "name": "process_name", "args": {"name": "ebpf"}},
    ]
    t = 0.0
    for d in ticks_ms:
        events.append(
            {"ph": "X", "pid": 1, "name": "tick_interval", "ts": t * 1000, "dur": d * 1000}
        )
        t += d
    for start_ms, dur_ms in waits:
        events.append(
            {
                "ph": "X",
                "pid": 2,
                "name": "runq_latency",
                "ts": start_ms * 1000,
                "dur": dur_ms * 1000,
            }
        )
    return {"traceEvents": events}


def test_attribution_finds_waits_inside_late_ticks() -> None:
    # Ticks at 0, 20, 50, 70 ms; the third is 10 ms late and holds a 12 ms wait.
    result = attribute(trace([20, 30, 20, 20], [(25.0, 12.0), (55.0, 0.01)]))

    assert result["late_ticks"] == 1
    assert result["worst_wait_in_tick_us"]["late_median"] == 12_000
    assert result["late_wait_to_overrun_ratio"] == 1.2
