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
    """Polls one process and its descendants on a thread, on the monotonic clock.

    Descendants count because a profiler such as nsys launches the workload
    as its child; polling only the launcher would measure the launcher.
    """

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
                tree = [self._process, *self._process.children(recursive=True)]
                now = MONOTONIC()
                user = system = 0.0
                rss = voluntary = involuntary = 0
                for proc in tree:
                    try:
                        with proc.oneshot():
                            cpu = proc.cpu_times()
                            user, system = user + cpu.user, system + cpu.system
                            rss += proc.memory_info().rss
                            ctx = proc.num_ctx_switches()
                            voluntary += ctx.voluntary
                            involuntary += ctx.involuntary
                    except psutil.NoSuchProcess:
                        continue
            except psutil.NoSuchProcess:
                # The process finished between polls; its last reading is
                # already recorded and there is nothing more to see.
                return
            self._samples.extend(
                Sample("process", name, now, float(value), unit, "monotonic")
                for name, value, unit in (
                    ("cpu_user", user * 1e9, "ns"),
                    ("cpu_system", system * 1e9, "ns"),
                    ("rss", rss, "bytes"),
                    ("ctx_voluntary", voluntary, "count"),
                    ("ctx_involuntary", involuntary, "count"),
                )
            )
            self._stop.wait(self.interval_s)
