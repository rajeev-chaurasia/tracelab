"""Builders for runs with known contents, so a test states only what it varies."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from tracelab.core.run import Rig, Run, Series, comparison_key

EPOCH = datetime(2026, 9, 1, tzinfo=UTC)
CANDIDATE = "f" * 40
# Every history revision a test builds is on the baseline branch unless the
# test says otherwise.
KNOWN_GOOD = frozenset(f"{i:040x}" for i in range(1000))

# The shape benchgrid's internal/spec writes, not a simplification of it.
SPEC: dict[str, Any] = {
    "benchmark": "matmul",
    "revision": "0" * 40,
    "command": ["bench/matmul", "--size", "256"],
    "warmups": 3,
    "repetitions": 30,
    "timeout_seconds": 120,
    "requirements": {"arch": "arm64", "hardware_class": "cpu-arm64", "allow_emulated": False},
    "environment": {},
    "metrics": [{"name": "latency", "unit": "ns", "direction": "lower_is_better"}],
    "artifacts": {"binary_sha256": "b" * 64},
}
KEY = comparison_key(SPEC)


def rig(**overrides: Any) -> Rig:
    fields: dict[str, Any] = {
        "rig_id": "rig-01",
        "hardware_class": "cpu-arm64",
        "arch": "arm64",
        "emulated": False,
        "cpu_model": "test-cpu",
        "gpu_model": "",
        "kernel": "25.0.0",
        "driver_version": "",
        "firmware": "",
        "governor": "performance",
    }
    fields.update(overrides)
    return Rig(**fields)


def run(
    index: int = 0,
    *,
    metrics: dict[str, list[float]] | None = None,
    higher_is_better: frozenset[str] = frozenset(),
    unit: str = "ns",
    **overrides: Any,
) -> Run:
    built = Run(
        run_id=f"exp-{index:04d}",
        attempt=1,
        benchmark="matmul",
        revision=f"{index:040x}",
        comparison_key=KEY,
        started_at=EPOCH + timedelta(minutes=index),
        status="SUCCEEDED",
        status_reason="",
        rig=rig(),
        metrics={
            name: Series(unit=unit, higher_is_better=name in higher_is_better, values=tuple(v))
            for name, v in (metrics or {"latency": [10.0, 11.0, 12.0]}).items()
        },
    )
    return replace(built, **overrides)
