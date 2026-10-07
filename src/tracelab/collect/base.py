"""What every collector provides, so the analysis never depends on one tool.

A collector starts before the workload, stops after it, and hands back
timestamped samples in its own clock domain. It never converts time itself:
the timeline does that once, with one fitted mapping per domain, so the
conversion and its error are in one place and measured.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Sample:
    source: str
    name: str
    # Nanoseconds on the clock named by `domain`, not yet aligned.
    t_ns: int
    value: float
    unit: str
    domain: str
    # Set for something with a duration, such as a benchmark iteration, which
    # a trace viewer draws as a slice rather than a point.
    duration_ns: int | None = None


class Collector(Protocol):
    name: str

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def samples(self) -> list[Sample]: ...

    def artifacts(self) -> list[Path]: ...
