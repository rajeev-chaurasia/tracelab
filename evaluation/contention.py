"""Real CPU contention on a schedule fixed before v3 was collected.

v2 could not test confirmation, because nothing happened to the machine while
it ran. v3 makes something happen: at fixed run indexes the collector starts
one busy-loop process per core and stops them again, so a known set of runs is
measured under real contention. Nothing about the samples is altered; the
contention is in the machine.

The schedule is built so each burst lands where it tests one thing:

- A short burst covers exactly the first batch of one window, with that
  window's twenty baseline runs and its confirmation batch clean. This is v1's
  failure on purpose. Confirmation should absorb it.
- A long burst covers both batches of one window, with its baseline clean.
  This is the known miss on purpose. Confirmation is not expected to absorb
  it, and no prediction is made.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum

from evaluation.cases import BASELINE_RUNS, CANDIDATE_RUNS

GAP = 18
SHORT = CANDIDATE_RUNS
# Spaced so a short burst's window has twenty clean runs before its first
# batch and a clean confirmation batch after it.
SHORT_EVERY = BASELINE_RUNS + CANDIDATE_RUNS + 1
FIRST_SHORT = BASELINE_RUNS
SHORT_COUNT = 9
LONG_START = FIRST_SHORT + SHORT_EVERY * SHORT_COUNT
# From the long window's first batch to the end of its confirmation batch.
LONG = CANDIDATE_RUNS + GAP + CANDIDATE_RUNS + GAP // 3
RUNS = LONG_START + LONG + 7


@dataclass(frozen=True)
class Burst:
    kind: str
    start: int
    stop: int

    def covers(self, index: int) -> bool:
        return self.start <= index < self.stop


SCHEDULE = [
    *(
        Burst("short", FIRST_SHORT + k * SHORT_EVERY, FIRST_SHORT + k * SHORT_EVERY + SHORT)
        for k in range(SHORT_COUNT)
    ),
    Burst("long", LONG_START, LONG_START + LONG),
]


# v6: long bursts only, each covering one window's first batch, the gap and its
# confirmation batch, with that window's baseline clean. Confirmation cannot
# see through these by design; the canary rule is what they test.
LONG_EVERY = 45
LONG_TARGETS = [k * LONG_EVERY for k in range(5)]
LONG_SCHEDULE = [
    Burst(
        "long",
        start + BASELINE_RUNS,
        start + BASELINE_RUNS + CANDIDATE_RUNS + GAP + CANDIDATE_RUNS,
    )
    for start in LONG_TARGETS
]
LONG_RUNS = LONG_TARGETS[-1] + BASELINE_RUNS + 2 * CANDIDATE_RUNS + GAP + 4

SCHEDULES = {"short": SCHEDULE, "long": LONG_SCHEDULE}


def under_contention(index: int, schedule: list[Burst] = SCHEDULE) -> bool:
    return any(b.covers(index) for b in schedule)


class Exposure(StrEnum):
    # The first batch ran under a short burst; baseline and confirmation clean.
    SHORT_FIRST_BATCH = "short_burst_first_batch"
    # Both batches ran under the long burst; baseline clean.
    LONG_BOTH_BATCHES = "long_burst_both_batches"
    # No run in the window was under contention.
    CLEAN = "clean"
    # Contention touched the window some other way: baseline, or one batch only
    # in a way the schedule did not target.
    PARTIAL = "partial"


def exposure(start: int, gap: int = GAP, schedule: list[Burst] = SCHEDULE) -> Exposure:
    baseline = range(start, start + BASELINE_RUNS)
    first = range(start + BASELINE_RUNS, start + BASELINE_RUNS + CANDIDATE_RUNS)
    second_at = first.stop + gap
    second = range(second_at, second_at + CANDIDATE_RUNS)
    hit = {
        name: {b.kind for b in schedule for i in idx if b.covers(i)}
        for name, idx in (("baseline", baseline), ("first", first), ("second", second))
    }
    first_all_short = all(any(b.kind == "short" and b.covers(i) for b in schedule) for i in first)
    both_all_long = all(
        any(b.kind == "long" and b.covers(i) for b in schedule) for i in [*first, *second]
    )
    if not any(hit.values()):
        return Exposure.CLEAN
    if first_all_short and not hit["baseline"] and not hit["second"]:
        return Exposure.SHORT_FIRST_BATCH
    if both_all_long and not hit["baseline"]:
        return Exposure.LONG_BOTH_BATCHES
    return Exposure.PARTIAL


class Hogs:
    """One busy loop per core, started and stopped around scheduled runs."""

    def __init__(self) -> None:
        self.procs: list[subprocess.Popen[bytes]] = []

    @property
    def running(self) -> bool:
        return bool(self.procs)

    def start(self) -> None:
        self.procs = [
            subprocess.Popen([sys.executable, "-c", "while True: pass"])
            for _ in range(os.cpu_count() or 1)
        ]

    def stop(self) -> None:
        for proc in self.procs:
            proc.kill()
        for proc in self.procs:
            proc.wait()
        self.procs = []
