"""Run one benchmark process with every collector attached, and align the result.

The workload reports its own iterations, stamped on the monotonic clock
relative to its first warmup, and writes that origin to a file named by
TRACELAB_T0_FILE. The process sampler polls it in-process on the monotonic
clock. The system sampler runs out of process on the wall clock. A sync thread
takes wall-to-monotonic readings throughout the run, and the fitted mapping
places the system samples on the same timeline, with its error bound recorded
next to the trace.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import Collector, Sample
from .clock import MONOTONIC, WALL, Mapping, Reading, fit, sample
from .ebpf import RunQueueCollector
from .nvidia import NvidiaSmiSampler, import_nsys
from .process import ProcessSampler
from .system import SystemSampler
from .timeline import Aligned, align

# Duration-bearing metrics: the sample is stamped when the iteration ended,
# so the slice starts one value earlier.
SLICES = {"iteration_latency", "tick_interval"}


@dataclass(frozen=True)
class Recording:
    stdout: str
    returncode: int
    aligned: list[Aligned]
    mappings: dict[str, Mapping]
    origin_ns: int

    @property
    def alignment_error_bound_ns(self) -> float:
        return max((m.error_bound_ns for m in self.mappings.values()), default=0.0)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for a in self.aligned:
            out[a.sample.source] = out.get(a.sample.source, 0) + 1
        return out


def _workload_samples(stdout: str, t0_ns: int) -> list[Sample]:
    out = []
    for line in stdout.splitlines():
        record: dict[str, Any] = json.loads(line)
        end = t0_ns + int(record["t_offset_ns"])
        value = float(record["value"])
        name = record["metric"] + (" (warmup)" if record["warmup"] else "")
        if record["metric"] in SLICES:
            out.append(
                Sample(
                    "workload",
                    name,
                    end - int(value),
                    value,
                    record["unit"],
                    "monotonic",
                    int(value),
                )
            )
        else:
            out.append(Sample("workload", name, end, value, record["unit"], "monotonic"))
    return out


def record(
    command: list[str],
    *,
    sync_interval_s: float = 0.1,
    timeout_s: float = 120,
    ebpf: bool = False,
    gpu: bool = False,
    nsys: str | None = None,
) -> Recording:
    """Record one run. `gpu` samples nvidia-smi; `nsys` names an Nsight Systems
    binary to profile the workload under, whose CUDA kernels join the trace."""
    readings: list[Reading] = []
    done = threading.Event()

    def sync() -> None:
        while not done.is_set():
            readings.append(sample(MONOTONIC, WALL))
            done.wait(sync_interval_s)
        readings.append(sample(MONOTONIC, WALL))

    system = SystemSampler()
    system.start()
    external: list[Collector] = [NvidiaSmiSampler()] if gpu else []
    for c in external:
        c.start()
    syncer = threading.Thread(target=sync, daemon=True)
    syncer.start()
    origin = MONOTONIC()

    with tempfile.TemporaryDirectory() as scratch:
        t0_file = Path(scratch) / "t0"
        gate = Path(scratch) / "go"
        report = Path(scratch) / "profile"
        if nsys is not None:
            command = [
                nsys,
                "profile",
                "--trace=cuda",
                "--sample=none",
                "--cpuctxsw=none",
                "--export=sqlite",
                "--force-overwrite=true",
                "-o",
                str(report),
                *command,
            ]
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            text=True,
            env={**os.environ, "TRACELAB_T0_FILE": str(t0_file), "TRACELAB_GATE_FILE": str(gate)},
        )
        collectors: list[Collector] = [ProcessSampler(proc.pid)]
        if ebpf:
            collectors.append(RunQueueCollector(proc.pid))
        for c in collectors:
            c.start()
        gate.touch()
        stdout, _ = proc.communicate(timeout=timeout_s)
        for c in collectors:
            c.stop()
        t0 = int(t0_file.read_text()) if t0_file.exists() else origin
        kernels = import_nsys(report.with_suffix(".sqlite")) if nsys is not None else []

    for c in external:
        c.stop()
    system.stop()
    done.set()
    syncer.join()

    mappings = {"wall": fit(readings)}
    samples = [s for c in [*collectors, *external, system] for s in c.samples()] + kernels
    samples += _workload_samples(stdout, t0) if proc.returncode == 0 else []
    return Recording(stdout, proc.returncode, align(samples, mappings), mappings, origin)
