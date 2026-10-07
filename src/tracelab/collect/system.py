"""Machine-wide CPU utilisation, sampled by a separate process on the wall clock.

It runs out of process and stamps wall time on purpose, the way an external
tool would: perf, a hardware monitor or a GPU sampler does not share the
workload's clock or address space. The timeline has to align it like any
other foreign source, so the alignment is exercised on every recorded run,
not only in a test.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .base import Sample

SAMPLER = """
import json, sys, time
import psutil
interval = float(sys.argv[1])
psutil.cpu_percent(percpu=True)
print(json.dumps({"ready": time.time_ns()}), flush=True)
while True:
    time.sleep(interval)
    per_cpu = psutil.cpu_percent(percpu=True)
    print(json.dumps({"t": time.time_ns(), "per_cpu": per_cpu}), flush=True)
"""


class SystemSampler:
    name = "system"

    def __init__(self, interval_s: float = 0.05) -> None:
        self.interval_s = interval_s
        self._proc: subprocess.Popen[str] | None = None
        self._lines: list[str] = []

    def start(self) -> None:
        self._proc = subprocess.Popen(
            [sys.executable, "-c", SAMPLER, str(self.interval_s)],
            stdout=subprocess.PIPE,
            text=True,
        )
        assert self._proc.stdout is not None
        # Wait until it is sampling, so the first workload iteration is covered.
        self._proc.stdout.readline()

    def stop(self) -> None:
        if self._proc is None:
            return
        self._proc.terminate()
        out, _ = self._proc.communicate()
        self._lines = out.splitlines()

    def samples(self) -> list[Sample]:
        out: list[Sample] = []
        for line in self._lines:
            record = json.loads(line)
            per_cpu = record["per_cpu"]
            out.append(
                Sample(
                    "system",
                    "cpu_util",
                    record["t"],
                    sum(per_cpu) / len(per_cpu) / 100,
                    "ratio",
                    "wall",
                )
            )
            out.append(
                Sample(
                    "system", "cpu_util_max_core", record["t"], max(per_cpu) / 100, "ratio", "wall"
                )
            )
        return out

    def artifacts(self) -> list[Path]:
        return []
