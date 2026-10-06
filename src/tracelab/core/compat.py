"""Decide whether two runs measured the same thing on the same kind of machine.

A comparison across a driver upgrade measures the driver. Reporting that as a
regression in the code under test sends an engineer to bisect a change that
did nothing, which costs more trust than a missed regression does. So a
mismatch on a must-match field ends the comparison as INCOMPARABLE before any
statistics run.
"""

from __future__ import annotations

from dataclasses import dataclass

from .schema import RunResult

# Fields where a difference changes what is being measured.
MUST_MATCH_RUN = ("benchmark", "scenario", "config_sha256", "workload_sha256")
MUST_MATCH_ENV = ("hardware_class", "cpu_model", "gpu_model", "driver_version", "firmware")
# Fields where a difference is worth reporting but not, by itself, disqualifying.
# A kernel patch release changes the version string on every host in a fleet at
# once; refusing every comparison for a week after would be the gate failing.
SHOULD_MATCH_ENV = ("kernel", "compiler", "container_image", "collector_versions")


@dataclass(frozen=True)
class Mismatch:
    field: str
    candidate: object
    baseline: object
    disqualifying: bool

    def __str__(self) -> str:
        return f"{self.field}: candidate {self.candidate!r}, baseline {self.baseline!r}"


def mismatches(candidate: RunResult, baseline: RunResult) -> list[Mismatch]:
    found: list[Mismatch] = []
    for name in MUST_MATCH_RUN:
        a, b = getattr(candidate, name), getattr(baseline, name)
        if a != b:
            found.append(Mismatch(name, a, b, disqualifying=True))
    for names, disqualifying in ((MUST_MATCH_ENV, True), (SHOULD_MATCH_ENV, False)):
        for name in names:
            a, b = getattr(candidate.environment, name), getattr(baseline.environment, name)
            if a != b:
                found.append(Mismatch(name, a, b, disqualifying=disqualifying))
    return found


def comparable(candidate: RunResult, baseline: RunResult) -> bool:
    return not any(m.disqualifying for m in mismatches(candidate, baseline))
