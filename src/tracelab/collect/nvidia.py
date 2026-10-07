"""NVIDIA GPU telemetry: nvidia-smi sampling and Nsight Systems trace import.

Both were written against the documented formats first, then run on an
NVIDIA L4 in a GCP VM. That run found one gap the fixtures had hidden: a
profile with no memory copies has no memcpy table at all. It also confirmed
the time origin: a host wall-clock stamp taken just after a device
synchronize lands 12 to 16 microseconds after session start plus the last
kernel's end, which is the synchronize's own return latency.

nvidia-smi in CSV query mode prints one line per GPU per interval, stamped
with local wall-clock time to the millisecond. An Nsight Systems export to
SQLite stores CUPTI kernel and memcpy activity with nanosecond timestamps
relative to the session start, and the session's UTC start time in
TARGET_INFO_SESSION_START_TIME; adding the two places each event on the wall
clock, where the timeline's fitted mapping takes over.
"""

from __future__ import annotations

import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

from .base import Sample

QUERY = (
    "timestamp,index,utilization.gpu,utilization.memory,memory.used,"
    "power.draw,temperature.gpu,clocks.sm"
)
FIELDS = [
    ("gpu_util", "ratio", 0.01),
    ("gpu_memory_util", "ratio", 0.01),
    ("gpu_memory_used", "bytes", float(2**20)),
    ("gpu_power", "unitless", 1.0),
    ("gpu_temperature", "celsius", 1.0),
    ("gpu_sm_clock", "unitless", 1.0),
]


def parse_smi(output: str) -> list[Sample]:
    """Lines of `nvidia-smi --query-gpu=QUERY --format=csv,noheader,nounits`."""
    out: list[Sample] = []
    for line in output.splitlines():
        cells = [c.strip() for c in line.split(",")]
        if len(cells) != 2 + len(FIELDS):
            continue
        stamp = datetime.strptime(cells[0], "%Y/%m/%d %H:%M:%S.%f")
        # nvidia-smi prints local time with no zone, so it is read as local.
        t_ns = int(stamp.timestamp() * 1e9)
        gpu = cells[1]
        for (name, unit, scale), cell in zip(FIELDS, cells[2:], strict=True):
            # A reading the GPU does not support prints as "[N/A]" or "[Not
            # Supported]"; the contract's rule is null rather than zero, so
            # it is left out rather than recorded as 0.
            if cell.startswith("["):
                continue
            out.append(Sample(f"gpu{gpu}", name, t_ns, float(cell) * scale, unit, "wall"))
    return out


class NvidiaSmiSampler:
    name = "nvidia-smi"

    def __init__(self, interval_ms: int = 100, binary: str = "nvidia-smi") -> None:
        self.interval_ms = interval_ms
        self.binary = binary
        self._proc: subprocess.Popen[str] | None = None
        self._output = ""

    def start(self) -> None:
        self._proc = subprocess.Popen(
            [
                self.binary,
                f"--query-gpu={QUERY}",
                "--format=csv,noheader,nounits",
                f"-lms={self.interval_ms}",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )

    def stop(self) -> None:
        if self._proc is None:
            return
        self._proc.terminate()
        self._output, _ = self._proc.communicate(timeout=10)

    def samples(self) -> list[Sample]:
        return parse_smi(self._output)

    def artifacts(self) -> list[Path]:
        return []


def import_nsys(sqlite_path: Path) -> list[Sample]:
    """Kernels and memory copies from an `nsys export --type sqlite` file."""
    db = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        (origin,) = db.execute("SELECT utcEpochNs FROM TARGET_INFO_SESSION_START_TIME").fetchone()
        names = dict(db.execute("SELECT id, value FROM StringIds"))
        out: list[Sample] = []
        for start, end, device, stream, name_id in db.execute(
            "SELECT start, end, deviceId, streamId, demangledName "
            "FROM CUPTI_ACTIVITY_KIND_KERNEL ORDER BY start"
        ):
            out.append(
                Sample(
                    f"gpu{device}.stream{stream}",
                    names.get(name_id, f"kernel {name_id}"),
                    origin + start,
                    float(end - start),
                    "ns",
                    "wall",
                    end - start,
                )
            )
        tables = {name for (name,) in db.execute("SELECT name FROM sqlite_master")}
        # nsys writes the memcpy table only when the profile saw a copy.
        copies = (
            db.execute(
                "SELECT start, end, deviceId, bytes, copyKind FROM CUPTI_ACTIVITY_KIND_MEMCPY "
                "ORDER BY start"
            )
            if "CUPTI_ACTIVITY_KIND_MEMCPY" in tables
            else []
        )
        for start, end, device, nbytes, kind in copies:
            out.append(
                Sample(
                    f"gpu{device}.memcpy",
                    f"memcpy kind {kind}",
                    origin + start,
                    float(nbytes),
                    "bytes",
                    "wall",
                    end - start,
                )
            )
        return out
    finally:
        db.close()
