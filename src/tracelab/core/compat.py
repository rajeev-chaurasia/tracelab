"""Decide whether two runs measured the same thing on the same kind of machine.

The must-match set is the one the run artifact contract names: the measurement
part of the spec, the hardware class, the architecture, and whether the rig
was emulated. A mismatch there ends the comparison as INCOMPARABLE before any
statistics run, because a regression reported across, say, an emulated and a
physical rig sends an engineer to bisect a change that did nothing.

Kernel, driver and governor are reported but do not disqualify, as the
contract specifies. An experiment that needs a particular driver says so in its
requirements, which are part of the spec, so a requirement change already
changes the comparison key.
"""

from __future__ import annotations

from dataclasses import dataclass

from .run import Run

MUST_MATCH = ("comparison_key", "rig.hardware_class", "rig.arch", "rig.emulated")
ANNOTATE = (
    "rig.kernel",
    "rig.driver_version",
    "rig.governor",
    "rig.cpu_model",
    "rig.gpu_model",
    "rig.firmware",
)


@dataclass(frozen=True)
class Mismatch:
    field: str
    candidate: object
    baseline: object
    disqualifying: bool

    def __str__(self) -> str:
        return f"{self.field}: candidate {self.candidate!r}, baseline {self.baseline!r}"


def _get(run: Run, path: str) -> object:
    value: object = run
    for part in path.split("."):
        value = getattr(value, part)
    return value


def mismatches(candidate: Run, baseline: Run) -> list[Mismatch]:
    return [
        Mismatch(path, a, b, disqualifying)
        for names, disqualifying in ((MUST_MATCH, True), (ANNOTATE, False))
        for path in names
        if (a := _get(candidate, path)) != (b := _get(baseline, path))
    ]


def blocking(candidate: Run, baseline: Run) -> Mismatch | None:
    return next((m for m in mismatches(candidate, baseline) if m.disqualifying), None)
