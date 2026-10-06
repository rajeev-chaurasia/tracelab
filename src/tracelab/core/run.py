"""The view of a validated run artifact that the analysis works on.

The contract reader decides whether a run is well formed. This module decides
what of it matters for a comparison, and fixes one thing the artifact does not
carry directly: a key saying whether two runs measured the same thing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from .canon import sha256_hex
from .contract import RunArtifact

# The spec fields that name what is under test rather than how it is measured.
# They differ between a candidate and its baseline by design, so a key that
# included them would make every cross-commit comparison incomparable.
UNDER_TEST = frozenset({"revision", "artifacts"})


@dataclass(frozen=True)
class Series:
    unit: str
    higher_is_better: bool
    # Measured repetitions only, in iteration order. Warmups exist in the
    # artifact as evidence of what happened and never reach a statistic.
    values: tuple[float, ...]


@dataclass(frozen=True)
class Rig:
    rig_id: str
    hardware_class: str
    arch: str
    emulated: bool
    cpu_model: str
    gpu_model: str
    kernel: str
    driver_version: str
    firmware: str
    governor: str


@dataclass(frozen=True)
class Run:
    run_id: str
    attempt: int
    benchmark: str
    revision: str
    comparison_key: str
    started_at: datetime
    status: str
    status_reason: str
    rig: Rig
    metrics: dict[str, Series]

    def with_metrics(self, extra: dict[str, Series]) -> Run:
        return replace(self, metrics={**self.metrics, **extra})


def comparison_key(spec: dict[str, Any]) -> str:
    return sha256_hex({k: v for k, v in spec.items() if k not in UNDER_TEST})


def parse_utc(stamp: str) -> datetime:
    # The contract allows up to nine fraction digits and datetime holds six,
    # so the fraction is truncated. Ordering runs needs nothing finer.
    whole, _, fraction = stamp.rstrip("Z").partition(".")
    micros = int((fraction + "000000")[:6]) if fraction else 0
    parsed = datetime.strptime(whole, "%Y-%m-%dT%H:%M:%S")
    return parsed.replace(microsecond=micros, tzinfo=UTC)


def from_artifact(artifact: RunArtifact) -> Run:
    run = artifact.run
    benchmark = run.spec.get("benchmark")
    if not isinstance(benchmark, str) or not benchmark:
        raise ValueError(f"run {run.run_id} has no spec.benchmark to group it by")
    metrics = {
        decl.name: Series(
            unit=decl.unit,
            higher_is_better=decl.direction == "higher_is_better",
            values=tuple(
                s.value
                for s in sorted(artifact.samples, key=lambda s: s.iteration)
                if s.metric == decl.name and not s.warmup
            ),
        )
        for decl in artifact.metrics
    }
    rig = run.rig
    return Run(
        run_id=run.run_id,
        attempt=run.attempt,
        benchmark=benchmark,
        revision=run.environment.git_revision,
        comparison_key=comparison_key(run.spec),
        started_at=parse_utc(run.timing.started),
        status=run.status,
        status_reason=run.status_reason,
        rig=Rig(
            rig_id=rig.rig_id,
            hardware_class=rig.hardware_class,
            arch=rig.arch,
            emulated=rig.emulated,
            cpu_model=rig.cpu_model,
            gpu_model=rig.gpu_model,
            kernel=rig.kernel,
            driver_version=rig.driver_version,
            firmware=rig.firmware,
            governor=run.environment.governor,
        ),
        metrics=metrics,
    )
