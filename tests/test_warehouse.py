"""Lake export, rollup recomputation and the Prometheus backfill format."""

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.dataset as ds
import pytest

from tracelab.core.stats import Statistic, run_noise
from tracelab.warehouse.check import check
from tracelab.warehouse.lake import export
from tracelab.warehouse.openmetrics import render

from .test_cli import build_store


@pytest.fixture(scope="module")
def lake(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("wh")
    store = build_store(root / "store", *[720_000.0] * 3)
    result = export({"t1": store}, root / "lake")
    assert result.runs == 23
    return root / "lake"


def measured(lake: Path) -> list[dict[str, Any]]:
    table = ds.dataset(lake / "samples", format="parquet", partitioning="hive").to_table(
        filter=~ds.field("warmup")
    )
    return table.to_pylist()


def test_export_keeps_every_sample_flagged_and_keys_runs_by_corpus(lake: Path) -> None:
    runs = ds.dataset(lake / "runs", format="parquet", partitioning="hive").to_table().to_pylist()
    samples = ds.dataset(lake / "samples", format="parquet", partitioning="hive").to_table()

    assert {r["corpus"] for r in runs} == {"t1"}
    assert len({(r["corpus"], r["run_id"]) for r in runs}) == 23
    # 5 warmups and 200 repetitions, two metrics each, per run.
    assert samples.num_rows == 23 * 205 * 2
    assert samples.column("warmup").to_pylist().count(True) == 23 * 5 * 2
    assert all(p.name.startswith("date=") for p in (lake / "samples").iterdir())


def truthful_rollups(lake: Path) -> dict[str, list[dict[str, Any]]]:
    rows = measured(lake)
    keys = sorted(
        {
            (r["date"], r["corpus"], r["benchmark"], r["hardware_class"], r["metric_name"])
            for r in rows
        }
    )
    daily = []
    for key in keys:
        group = [
            r
            for r in rows
            if (r["date"], r["corpus"], r["benchmark"], r["hardware_class"], r["metric_name"])
            == key
        ]
        v = np.asarray([r["value"] for r in group])
        daily.append(
            dict(
                zip(
                    ["date", "corpus", "benchmark", "hardware_class", "metric_name"],
                    key,
                    strict=True,
                )
            )
            | {
                "samples": len(v),
                "runs": len({r["run_id"] for r in group}),
                "mean": v.mean(),
                "p50": np.quantile(v, 0.5),
                "p95": np.quantile(v, 0.95),
                "p99": np.quantile(v, 0.99),
            }
        )
    noise = []
    for corpus, bench, hw, metric in sorted({k[1:] for k in keys}):
        by_run: dict[str, list[float]] = {}
        for r in rows:
            if (r["corpus"], r["benchmark"], r["hardware_class"], r["metric_name"]) == (
                corpus,
                bench,
                hw,
                metric,
            ):
                by_run.setdefault(r["run_id"], []).append(r["value"])
        medians = np.asarray([np.quantile(v, 0.5) for v in by_run.values()])
        noise.append(
            {
                "corpus": corpus,
                "benchmark": bench,
                "hardware_class": hw,
                "metric_name": metric,
                "runs": len(by_run),
                "median_of_run_medians": np.quantile(medians, 0.5),
                "cv_of_run_medians": np.std(medians, ddof=1) / np.mean(medians),
                "robust_noise": run_noise(list(by_run.values()), Statistic.parse("median")),
            }
        )
    return {"daily_metric_rollup": daily, "run_to_run_noise": noise, "run_health": []}


def test_check_accepts_correct_rollups_and_names_a_tampered_value(lake: Path) -> None:
    rollups = truthful_rollups(lake)
    assert check(rollups, lake) == []

    rollups["daily_metric_rollup"][0]["p95"] *= 1.001
    rollups["run_to_run_noise"].pop()

    [error] = check(rollups, lake)
    assert "p95: warehouse" in error


def test_check_names_a_missing_rollup_row(lake: Path) -> None:
    rollups = truthful_rollups(lake)
    rollups["daily_metric_rollup"].pop()

    [error] = check(rollups, lake)
    assert error.startswith("daily_metric_rollup is missing")


def test_openmetrics_is_grouped_typed_time_ordered_and_terminated(
    lake: Path, tmp_path: Path
) -> None:
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "comparators": {
                    "tracelab": {
                        "false_regressions": 0,
                        "regressions_caught": 5,
                        "regressions_to_catch": 6,
                        "cases": 9,
                    }
                }
            }
        )
    )

    lines = list(render(lake, {"vX": summary}, now_s=2_000_000_000.0))

    assert lines[-1] == "# EOF"
    families = [line.split()[2] for line in lines if line.startswith("# TYPE")]
    assert families == sorted(families)
    assert "tracelab_eval_false_regressions" in families
    series: dict[str, list[float]] = {}
    for line in lines:
        if not line.startswith("#"):
            name_labels, _, stamp = line.rsplit(" ", 2)
            series.setdefault(name_labels, []).append(float(stamp))
    assert all(stamps == sorted(stamps) for stamps in series.values())
    assert any('metric="iteration_latency"' in key for key in series)


@pytest.mark.skipif(
    not os.environ.get("TRACELAB_BQ_ENDPOINT"), reason="needs a BigQuery emulator endpoint"
)
def test_rollups_on_the_emulator_match_the_lake(lake: Path) -> None:
    from tracelab.warehouse.bigquery import ROLLUPS, Warehouse

    wh = Warehouse.connect("tracelab", "test_rollups", os.environ["TRACELAB_BQ_ENDPOINT"])
    wh.create(lake)
    assert wh.load(lake) == {"runs": 23, "samples": 23 * 205 * 2}
    results = {name: wh.rollup(name) for name in ROLLUPS}

    assert check(results, lake) == []
    assert results["run_health"][0]["succeeded"] == 23
