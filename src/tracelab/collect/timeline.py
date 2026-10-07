"""Align collected samples onto the reference clock and export them for Perfetto.

The export is the Chrome trace event JSON format, which the Perfetto UI opens
directly: counters become counter tracks, and anything with a duration becomes
a slice on its source's track. Timestamps are microseconds from the start of
the recording, so two recordings open side by side line up at zero.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import Sample
from .clock import Mapping

REFERENCE = "monotonic"


@dataclass(frozen=True)
class Aligned:
    sample: Sample
    # Nanoseconds on the reference clock.
    t_ns: int


def align(samples: Iterable[Sample], mappings: dict[str, Mapping]) -> list[Aligned]:
    out = []
    for s in samples:
        if s.domain == REFERENCE:
            t = s.t_ns
        elif s.domain in mappings:
            t = mappings[s.domain].to_reference(s.t_ns)
        else:
            raise ValueError(f"no clock mapping for domain {s.domain!r} from {s.source}")
        out.append(Aligned(s, t))
    return sorted(out, key=lambda a: a.t_ns)


def perfetto(aligned: list[Aligned], origin_ns: int, title: str) -> dict[str, Any]:
    sources = sorted({a.sample.source for a in aligned})
    pids = {source: i + 1 for i, source in enumerate(sources)}
    events: list[dict[str, Any]] = [
        {"name": "process_name", "ph": "M", "pid": pid, "args": {"name": source}}
        for source, pid in pids.items()
    ]
    for a in aligned:
        s = a.sample
        ts = (a.t_ns - origin_ns) / 1000
        if s.duration_ns is not None:
            events.append(
                {
                    "name": s.name,
                    "ph": "X",
                    "pid": pids[s.source],
                    "tid": 1,
                    "ts": ts,
                    "dur": s.duration_ns / 1000,
                    "args": {"value": s.value, "unit": s.unit},
                }
            )
        else:
            events.append(
                {
                    "name": f"{s.name} ({s.unit})",
                    "ph": "C",
                    "pid": pids[s.source],
                    "ts": ts,
                    "args": {s.name: s.value},
                }
            )
    return {"traceEvents": events, "displayTimeUnit": "ns", "otherData": {"title": title}}


def write_perfetto(aligned: list[Aligned], origin_ns: int, title: str, path: Path) -> Path:
    path.write_text(json.dumps(perfetto(aligned, origin_ns, title), separators=(",", ":")))
    return path
