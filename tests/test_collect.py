"""Collectors, clock alignment and the trace export, against known answers."""

import json
import sys
from itertools import count
from pathlib import Path

import pytest

from tracelab.collect.base import Sample
from tracelab.collect.clock import Reading, fit, read, sample
from tracelab.collect.recording import record
from tracelab.collect.timeline import align, perfetto


def test_fit_recovers_a_known_offset_and_drift() -> None:
    # The other clock runs 10 ppm fast and starts 5 s ahead of the reference.
    readings = [Reading(t, round(5e9 + t * 1.00001), 20) for t in range(0, 10**10, 10**9)]

    mapping = fit(readings)

    assert mapping.to_reference(round(5e9 + 7e9 * 1.00001)) == pytest.approx(7e9, abs=2)
    assert mapping.max_residual_ns < 2
    assert mapping.error_bound_ns < 25


def test_a_single_reading_gives_an_offset_with_its_own_uncertainty() -> None:
    mapping = fit([Reading(100, 1100, 7)])

    assert mapping.to_reference(1500) == 500
    assert mapping.error_bound_ns == 7


def test_fit_refuses_no_readings() -> None:
    with pytest.raises(ValueError):
        fit([])


def test_a_sandwich_bounds_where_the_other_clock_was_read() -> None:
    ticks = count(0, 10)

    reading = read(lambda: next(ticks), lambda: 999)

    assert reading == Reading(reference_ns=5, other_ns=999, uncertainty_ns=5)


def test_sample_keeps_the_tightest_sandwich() -> None:
    widths = iter([0, 50, 50, 51, 51, 52])
    reading = sample(lambda: next(widths), lambda: 0, count=2)

    assert reading.uncertainty_ns == 1


def test_align_places_every_domain_on_the_reference_and_refuses_unknown_ones() -> None:
    mapping = fit([Reading(1000, 51000, 0), Reading(2000, 52000, 0)])
    samples = [
        Sample("a", "x", 1500, 1.0, "count", "monotonic"),
        Sample("b", "y", 51200, 2.0, "count", "wall"),
    ]

    aligned = align(samples, {"wall": mapping})

    assert [(a.sample.source, a.t_ns) for a in aligned] == [("b", 1200), ("a", 1500)]
    with pytest.raises(ValueError, match="no clock mapping"):
        align([Sample("c", "z", 1, 1.0, "count", "gpu")], {})


def test_perfetto_export_has_a_track_per_source_slices_and_counters() -> None:
    mapping = fit([Reading(0, 0, 0)])
    aligned = align(
        [
            Sample("workload", "iteration_latency", 1000, 500.0, "ns", "monotonic", 500),
            Sample("process", "rss", 3000, 42.0, "bytes", "monotonic"),
        ],
        {"wall": mapping},
    )

    trace = perfetto(aligned, origin_ns=0, title="t")

    by_phase = {e["ph"]: e for e in trace["traceEvents"]}
    assert {e["args"]["name"] for e in trace["traceEvents"] if e["ph"] == "M"} == {
        "workload",
        "process",
    }
    assert by_phase["X"]["ts"] == 1.0
    assert by_phase["X"]["dur"] == 0.5
    assert by_phase["C"]["args"] == {"rss": 42.0}


WORKLOAD = """
import json, os, time
t0 = time.monotonic_ns()
open(os.environ["TRACELAB_T0_FILE"], "w").write(str(t0))
for i in range(3):
    start = time.perf_counter_ns()
    sum(range(200_000))
    elapsed = time.perf_counter_ns() - start
    print(json.dumps({"metric": "iteration_latency", "iteration": i, "warmup": i == 0,
                      "value": float(elapsed), "unit": "ns",
                      "t_offset_ns": time.monotonic_ns() - t0}))
time.sleep(0.3)
"""


def test_a_recording_aligns_three_sources_and_reports_its_error_bound(tmp_path: Path) -> None:
    rec = record([sys.executable, "-c", WORKLOAD])

    assert rec.returncode == 0
    assert set(rec.counts()) == {"workload", "process", "system"}
    slices = [a for a in rec.aligned if a.sample.duration_ns is not None]
    assert [a.sample.name for a in slices] == [
        "iteration_latency (warmup)",
        "iteration_latency",
        "iteration_latency",
    ]
    assert all(a.t_ns >= rec.origin_ns for a in slices)
    # Generous for a loaded CI runner; a quiet laptop measures well under a microsecond.
    assert 0 < rec.alignment_error_bound_ns < 1_000_000
    assert rec.mappings["wall"].readings >= 2
    trace = perfetto(rec.aligned, rec.origin_ns, "test")
    assert json.loads(json.dumps(trace))["traceEvents"]
