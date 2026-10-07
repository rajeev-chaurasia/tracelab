"""Sample a process's CPU time, memory and context switches while it runs.

These explain a latency number rather than restate it: a slower iteration
with more involuntary context switches was preempted, one with more system
time was in the kernel, one with growing RSS was allocating.
"""

from __future__ import annotations

import threading
from pathlib import Path

import psutil

from .base import Sample
from .clock import MONOTONIC


class ProcessSampler:
    """Polls one process on a thread, stamping on the monotonic clock."""

    name = "process"

    def __init__(self, pid: int, interval_s: float = 0.01) -> None:
        self.pid = pid
        self.interval_s = interval_s
        self._samples: list[Sample] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._process = psutil.Process(self.pid)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def samples(self) -> list[Sample]:
        return list(self._samples)

    def artifacts(self) -> list[Path]:
        return []

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with self._process.oneshot():
                    now = MONOTONIC()
                    cpu = self._process.cpu_times()
                    rss = self._process.memory_info().rss
                    switches = self._process.num_ctx_switches()
            except psutil.NoSuchProcess:
                # The process finished between polls; its last reading is
                # already recorded and there is nothing more to see.
                return
            self._samples.extend(
                Sample("process", name, now, float(value), unit, "monotonic")
                for name, value, unit in (
                    ("cpu_user", cpu.user * 1e9, "ns"),
                    ("cpu_system", cpu.system * 1e9, "ns"),
                    ("rss", rss, "bytes"),
                    ("ctx_voluntary", switches.voluntary, "count"),
                    ("ctx_involuntary", switches.involuntary, "count"),
                )
            )
            self._stop.wait(self.interval_s)
