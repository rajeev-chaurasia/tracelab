"""Recompute every warehouse rollup from the lake and compare, value by value.

A rollup computed by a warehouse is a claim about the data, in a dialect this
repository cannot otherwise test. This recomputes each one independently, from
the Parquet files with numpy and with the comparison engine's own noise
function, so a query bug or a warehouse bug shows up as a named disagreement
rather than as a plausible number on a dashboard. It caught one: the BigQuery
emulator returns APPROX_QUANTILES input unsorted.
"""

from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.dataset as ds

from tracelab.core.stats import Statistic, run_noise

TOLERANCE = 1e-9


def _close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=TOLERANCE, abs_tol=1e-12)


def check(results: dict[str, list[dict[str, Any]]], lake: Path) -> list[str]:
    samples = ds.dataset(lake / "samples", format="parquet", partitioning="hive").to_table(
        filter=~ds.field("warmup")
    )
    values: dict[tuple[str, ...], list[float]] = defaultdict(list)
    runs: dict[tuple[str, ...], set[str]] = defaultdict(set)
    per_run: dict[tuple[str, ...], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in samples.to_pylist():
        daily = (r["date"], r["corpus"], r["benchmark"], r["hardware_class"], r["metric_name"])
        values[daily].append(r["value"])
        runs[daily].add(r["run_id"])
        series = (r["corpus"], r["benchmark"], r["hardware_class"], r["metric_name"])
        per_run[series][r["run_id"]].append(r["value"])

    errors: list[str] = []
    seen: set[tuple[str, ...]] = set()
    for row in results["daily_metric_rollup"]:
        key = (
            row["date"],
            row["corpus"],
            row["benchmark"],
            row["hardware_class"],
            row["metric_name"],
        )
        seen.add(key)
        v = np.asarray(values[key])
        expected = {
            "samples": len(v),
            "runs": len(runs[key]),
            "mean": float(v.mean()),
            "p50": float(np.quantile(v, 0.5)),
            "p95": float(np.quantile(v, 0.95)),
            "p99": float(np.quantile(v, 0.99)),
        }
        for field, want in expected.items():
            if not _close(float(row[field]), want):
                errors.append(
                    f"daily_metric_rollup {key} {field}: warehouse {row[field]}, lake {want}"
                )
    for missing in sorted(set(values) - seen):
        errors.append(f"daily_metric_rollup is missing {missing}")

    median = Statistic.parse("median")
    for row in results["run_to_run_noise"]:
        series = (row["corpus"], row["benchmark"], row["hardware_class"], row["metric_name"])
        by_run = list(per_run[series].values())
        medians = np.asarray([float(np.quantile(r, 0.5)) for r in by_run])
        expected = {
            "runs": len(by_run),
            "median_of_run_medians": float(np.quantile(medians, 0.5)),
            "cv_of_run_medians": float(np.std(medians, ddof=1) / np.mean(medians)),
            # The engine's own gate, so the warehouse and the comparison agree
            # on what "too noisy" means.
            "robust_noise": run_noise(by_run, median),
        }
        for field, want in expected.items():
            if not _close(float(row[field]), want):
                errors.append(
                    f"run_to_run_noise {series} {field}: warehouse {row[field]}, lake {want}"
                )
    return errors
