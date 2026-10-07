"""Render the lake as OpenMetrics with real timestamps, for Prometheus to backfill.

Prometheus is a pull system and normally sees only the present. `promtool tsdb
create-blocks-from openmetrics` turns a file like this one into storage blocks
instead, so every run lands at the moment it actually ran and Grafana can
draw the history of a corpus collected yesterday. Each run contributes one
point per series: its median, p95 and p99 per metric, its status, and its
preflight load. Evaluation results come from the published summaries, stamped
at the time of export.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.dataset as ds


def _labels(**labels: str) -> str:
    body = ",".join(f'{k}="{_escape(v)}"' for k, v in sorted(labels.items()))
    return "{" + body + "}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _families(lake: Path, evidence: dict[str, Path], now_s: float) -> dict[str, list[str]]:
    families: dict[str, list[str]] = defaultdict(list)
    runs = ds.dataset(lake / "runs", format="parquet", partitioning="hive").to_table().to_pylist()
    started = {(r["corpus"], r["run_id"]): r["started_at"].timestamp() for r in runs}

    for r in sorted(runs, key=lambda r: r["started_at"]):
        t = r["started_at"].timestamp()
        base = {
            "corpus": r["corpus"],
            "benchmark": r["benchmark"],
            "hardware_class": r["hardware_class"],
        }
        families["tracelab_run_succeeded"].append(
            f"{_labels(**base)} {1 if r['status'] == 'SUCCEEDED' else 0} {t:.3f}"
        )
        if r["load1_before"] is not None:
            families["tracelab_run_load1"].append(f"{_labels(**base)} {r['load1_before']} {t:.3f}")

    samples = ds.dataset(lake / "samples", format="parquet", partitioning="hive")
    table = samples.to_table(
        columns=["corpus", "run_id", "benchmark", "hardware_class", "metric_name", "unit", "value"],
        filter=~ds.field("warmup"),
    )
    grouped: dict[tuple[str, ...], list[float]] = defaultdict(list)
    for row in table.to_pylist():
        key = (
            row["corpus"],
            row["run_id"],
            row["benchmark"],
            row["hardware_class"],
            row["metric_name"],
            row["unit"],
        )
        grouped[key].append(row["value"])
    for (corpus, run_id, benchmark, hardware_class, metric, unit), values in sorted(
        grouped.items(), key=lambda kv: started[(kv[0][0], kv[0][1])]
    ):
        t = started[(corpus, run_id)]
        labels = _labels(
            corpus=corpus,
            benchmark=benchmark,
            hardware_class=hardware_class,
            metric=metric,
            unit=unit,
        )
        arr = np.asarray(values)
        for stat, q in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99)):
            families[f"tracelab_run_metric_{stat}"].append(
                f"{labels} {float(np.quantile(arr, q))!r} {t:.3f}"
            )

    for version, summary_path in sorted(evidence.items()):
        summary: dict[str, Any] = json.loads(summary_path.read_text())
        for comparator, s in sorted(summary["comparators"].items()):
            labels = _labels(version=version, comparator=comparator)
            for field in (
                "false_regressions",
                "regressions_caught",
                "regressions_to_catch",
                "cases",
            ):
                families[f"tracelab_eval_{field}"].append(f"{labels} {s[field]} {now_s:.3f}")
    return families


def render(lake: Path, evidence: dict[str, Path], now_s: float) -> Iterator[str]:
    for name, lines in sorted(_families(lake, evidence, now_s).items()):
        yield f"# TYPE {name} gauge"
        # promtool requires each series' samples in time order, and the
        # families were built in run order, so a stable sort by series keeps it.
        for line in sorted(lines, key=lambda line: line.rsplit(" ", 2)[0]):
            yield f"{name}{line}"
    yield "# EOF"


def write(lake: Path, evidence: dict[str, Path], out: Path, now_s: float) -> int:
    lines = list(render(lake, evidence, now_s))
    out.write_text("\n".join(lines) + "\n")
    return len(lines)
