"""The RunResult contract BenchGrid emits, and the flat rows TraceLab stores.

A number on its own is not a result. Every observation travels with the code,
the build, the configuration and the machine that produced it, because the
first question about any regression is whether the two runs were comparable at
all, and that question cannot be answered after the provenance is dropped.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION: Literal[1] = 1


class RunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class Artifact(BaseModel):
    """A profiler trace, log or flamegraph kept in object storage, not in rows."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    uri: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EnvironmentManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hardware_class: str
    machine_id: str
    cpu_model: str
    gpu_model: str | None = None
    ram_gb: float
    kernel: str
    driver_version: str | None = None
    firmware: str | None = None
    compiler: str | None = None
    container_image: str | None = None
    collector_versions: dict[str, str] = Field(default_factory=dict)
    thermal_state: str | None = None


class Series(BaseModel):
    """Every raw observation of one metric in one run, never a summary of them.

    Summaries are computed at comparison time from these values. Storing a
    median instead would make the bootstrap impossible and the published
    numbers impossible to recompute.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    unit: str
    values: list[float] = Field(min_length=1)
    timestamps_ns: list[int] | None = None

    @model_validator(mode="after")
    def _timestamps_align(self) -> Series:
        if self.timestamps_ns is not None and len(self.timestamps_ns) != len(self.values):
            raise ValueError("timestamps_ns must have one entry per value")
        return self


class RunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    run_id: str
    experiment_id: str
    benchmark: str
    scenario: str
    git_sha: str
    build_id: str
    branch: str
    config_sha256: str
    workload_sha256: str
    started_at: datetime
    status: RunStatus
    # False when preflight failed or the environment drifted during the run.
    # A run can succeed and still be unfit to compare against.
    healthy: bool
    environment: EnvironmentManifest
    metrics: dict[str, Series]
    artifacts: list[Artifact] = Field(default_factory=list)

    def with_metrics(self, extra: dict[str, Series]) -> RunResult:
        return self.model_copy(update={"metrics": {**self.metrics, **extra}})


class MetricPoint(NamedTuple):
    """One observation, flattened for columnar storage.

    A NamedTuple rather than a model because a corpus produces millions of
    these and validating each one again would buy nothing: they are derived
    from a RunResult that was already validated.
    """

    run_id: str
    timestamp_ns: int
    sample_index: int
    metric_name: str
    value: float
    unit: str
    component: str
    hardware_class: str
    machine_id: str
    git_sha: str
    build_id: str
    benchmark: str
    scenario: str


def normalize(run: RunResult) -> list[MetricPoint]:
    started_ns = int(run.started_at.timestamp() * 1_000_000_000)
    points: list[MetricPoint] = []
    for name, series in sorted(run.metrics.items()):
        # "perception.frame_latency_ms" belongs to the perception component;
        # an unprefixed metric belongs to the benchmark as a whole.
        component = name.split(".", 1)[0] if "." in name else run.benchmark
        stamps = series.timestamps_ns or [started_ns] * len(series.values)
        for index, (value, stamp) in enumerate(zip(series.values, stamps, strict=True)):
            points.append(
                MetricPoint(
                    run_id=run.run_id,
                    timestamp_ns=stamp,
                    sample_index=index,
                    metric_name=name,
                    value=value,
                    unit=series.unit,
                    component=component,
                    hardware_class=run.environment.hardware_class,
                    machine_id=run.environment.machine_id,
                    git_sha=run.git_sha,
                    build_id=run.build_id,
                    benchmark=run.benchmark,
                    scenario=run.scenario,
                )
            )
    return points
