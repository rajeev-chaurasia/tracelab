"""Builders for runs with known contents, so a test states only what it varies."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from tracelab.core.schema import EnvironmentManifest, RunResult, RunStatus, Series

EPOCH = datetime(2026, 9, 1, tzinfo=UTC)


def environment(**overrides: Any) -> EnvironmentManifest:
    fields: dict[str, Any] = {
        "hardware_class": "cpu-arm64",
        "machine_id": "rig-01",
        "cpu_model": "test-cpu",
        "ram_gb": 16.0,
        "kernel": "test-kernel-1",
        "compiler": "cpython-3.13",
    }
    fields.update(overrides)
    return EnvironmentManifest(**fields)


def run(
    index: int = 0,
    *,
    metrics: dict[str, list[float]] | None = None,
    unit: str = "ms",
    env: EnvironmentManifest | None = None,
    **overrides: Any,
) -> RunResult:
    fields: dict[str, Any] = {
        "run_id": f"run-{index:04d}",
        "experiment_id": f"exp-{index:04d}",
        "benchmark": "matmul",
        "scenario": "n256",
        "git_sha": "a" * 40,
        "build_id": "build-1",
        "branch": "main",
        "config_sha256": "c" * 64,
        "workload_sha256": "w" * 64,
        "started_at": EPOCH + timedelta(minutes=index),
        "status": RunStatus.SUCCEEDED,
        "healthy": True,
        "environment": env or environment(),
        "metrics": {
            name: Series(unit=unit, values=values)
            for name, values in (metrics or {"latency_ms": [10.0, 11.0, 12.0]}).items()
        },
    }
    fields.update(overrides)
    return RunResult(**fields)
