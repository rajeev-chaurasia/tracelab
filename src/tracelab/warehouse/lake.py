"""Flatten an artifact store into a date-partitioned Parquet lake.

Two tables, laid out the way a BigQuery table partitioned on the run date and
clustered on benchmark, hardware class and metric would be:

    runs/date=YYYY-MM-DD/part-0.parquet      one row per run, its rig and status
    samples/date=YYYY-MM-DD/part-0.parquet   one row per sample, warmups included

Every run comes through the contract reader, so a run the contract rejects
never reaches the lake. Warmups are kept and flagged, because the lake is the
record of what happened and a query decides what to exclude, not the loader.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.dataset as ds

from tracelab.core.contract import RunArtifact
from tracelab.core.run import comparison_key, parse_utc
from tracelab.ingest.store import read_store

RUNS = pa.schema(
    pa.field(name, kind)
    for name, kind in [
        ("date", pa.string()),
        ("corpus", pa.string()),
        ("run_id", pa.string()),
        ("attempt", pa.int64()),
        ("benchmark", pa.string()),
        ("revision", pa.string()),
        ("comparison_key", pa.string()),
        ("started_at", pa.timestamp("us", tz="UTC")),
        ("finished_at", pa.timestamp("us", tz="UTC")),
        ("status", pa.string()),
        ("status_reason", pa.string()),
        ("rig_id", pa.string()),
        ("hardware_class", pa.string()),
        ("arch", pa.string()),
        ("emulated", pa.bool_()),
        ("cpu_model", pa.string()),
        ("gpu_model", pa.string()),
        ("kernel", pa.string()),
        ("driver_version", pa.string()),
        ("governor", pa.string()),
        ("load1_before", pa.float64()),
        ("load1_after", pa.float64()),
        ("samples", pa.int64()),
    ]
)

SAMPLES = pa.schema(
    pa.field(name, kind)
    for name, kind in [
        ("date", pa.string()),
        ("corpus", pa.string()),
        ("run_id", pa.string()),
        ("benchmark", pa.string()),
        ("revision", pa.string()),
        ("hardware_class", pa.string()),
        ("metric_name", pa.string()),
        ("unit", pa.string()),
        ("higher_is_better", pa.bool_()),
        ("iteration", pa.int64()),
        ("warmup", pa.bool_()),
        ("value", pa.float64()),
        ("t_offset_ns", pa.int64()),
        ("started_at", pa.timestamp("us", tz="UTC")),
    ]
)


@dataclass(frozen=True)
class Export:
    runs: int
    samples: int
    rejected: list[str]
    partitions: list[str]


def _rows(corpus: str, artifact: RunArtifact) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    run = artifact.run
    started = parse_utc(run.timing.started)
    date = started.astimezone(UTC).strftime("%Y-%m-%d")
    benchmark = str(run.spec.get("benchmark", ""))
    direction = {m.name: m.direction == "higher_is_better" for m in artifact.metrics}
    run_row = {
        "date": date,
        "corpus": corpus,
        "run_id": run.run_id,
        "attempt": run.attempt,
        "benchmark": benchmark,
        "revision": run.environment.git_revision,
        "comparison_key": comparison_key(run.spec),
        "started_at": started,
        "finished_at": parse_utc(run.timing.finished),
        "status": run.status,
        "status_reason": run.status_reason,
        "rig_id": run.rig.rig_id,
        "hardware_class": run.rig.hardware_class,
        "arch": run.rig.arch,
        "emulated": run.rig.emulated,
        "cpu_model": run.rig.cpu_model,
        "gpu_model": run.rig.gpu_model,
        "kernel": run.rig.kernel,
        "driver_version": run.rig.driver_version,
        "governor": run.environment.governor,
        "load1_before": run.environment.preflight_before.load1,
        "load1_after": run.environment.preflight_after.load1,
        "samples": len(artifact.samples),
    }
    sample_rows = [
        {
            "date": date,
            "corpus": corpus,
            "run_id": run.run_id,
            "benchmark": benchmark,
            "revision": run.environment.git_revision,
            "hardware_class": run.rig.hardware_class,
            "metric_name": s.metric,
            "unit": s.unit,
            "higher_is_better": direction[s.metric],
            "iteration": s.iteration,
            "warmup": s.warmup,
            "value": s.value,
            "t_offset_ns": s.t_offset_ns,
            "started_at": started,
        }
        for s in artifact.samples
    ]
    return run_row, sample_rows


def export(stores: dict[str, Path], lake: Path) -> Export:
    """Write every accepted run of every named store into the lake.

    Stores are named because run ids are unique only within one: every
    corpus starts at corpus-matmul-0000, and (corpus, run_id) is the key.
    """
    runs: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    rejected: list[str] = []
    for corpus, store in stores.items():
        for outcome in read_store(store):
            if outcome.artifact is None:
                rejected.append(f"{corpus}/{outcome.run_id}")
                continue
            run_row, sample_rows = _rows(corpus, outcome.artifact)
            runs.append(run_row)
            samples.extend(sample_rows)
    for name, rows, schema in (("runs", runs, RUNS), ("samples", samples, SAMPLES)):
        table = pa.Table.from_pylist(rows, schema=schema)
        ds.write_dataset(
            table,
            lake / name,
            format="parquet",
            partitioning=ds.partitioning(pa.schema([("date", pa.string())]), flavor="hive"),
            existing_data_behavior="delete_matching",
        )
    return Export(
        runs=len(runs),
        samples=len(samples),
        rejected=rejected,
        partitions=sorted({r["date"] for r in runs}),
    )
