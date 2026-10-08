"""Command line entry points.

    tracelab compare <store> --policy policies/v2/matmul.toml \
        --candidate <revision> --known-good revisions.txt
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from tracelab.collect.recording import record as run_recording
from tracelab.collect.timeline import write_perfetto
from tracelab.core.baseline import select
from tracelab.core.compare import compare as run_compare
from tracelab.core.policy import BenchmarkPolicy, Verdict
from tracelab.core.run import Run, from_artifact
from tracelab.ingest.store import read_store
from tracelab.report import CONCLUSIONS, render

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def main() -> None:
    """Performance regression analysis for benchgrid run artifacts."""


CORE_FILES = {"run.json", "samples.jsonl", "manifest.json"}


def load_runs(store: Path) -> tuple[list[Run], list[str], dict[str, list[Path]]]:
    runs: list[Run] = []
    problems: list[str] = []
    extras: dict[str, list[Path]] = {}
    for outcome in read_store(store):
        if outcome.artifact is None:
            # A rejected run is reported, never silently skipped, and never
            # replaced by a lower attempt. The reader owns that rule.
            code = outcome.rejection.code if outcome.rejection else "no valid attempt"
            problems.append(f"{outcome.run_id}: rejected ({code})")
            continue
        runs.append(from_artifact(outcome.artifact))
        # Traces and profiler output sealed with the run. The reader has
        # already checked every listed file's size and digest.
        attempt = store / "runs" / outcome.run_id / f"attempt-{outcome.attempt}"
        listed = json.loads((attempt / "manifest.json").read_text())["files"]
        extras[outcome.run_id] = [
            attempt / f["path"] for f in listed if f["path"] not in CORE_FILES
        ]
    return runs, problems, extras


@app.command()
def record(
    command: Annotated[list[str], typer.Argument(help="benchmark command, after --")],
    out: Annotated[Path, typer.Option(help="where to write the Perfetto trace")] = Path(
        "trace.json"
    ),
    ebpf: Annotated[
        bool, typer.Option(help="add eBPF run-queue latency; Linux with privileges only")
    ] = False,
    gpu: Annotated[bool, typer.Option(help="sample nvidia-smi during the run")] = False,
    nsys: Annotated[
        str | None, typer.Option(help="Nsight Systems binary to profile CUDA kernels with")
    ] = None,
) -> None:
    """Run a benchmark with every collector attached and write one aligned trace.

    The benchmark prints run artifact samples on stdout. The trace opens in
    the Perfetto UI; the alignment error bound is printed so a reader knows
    how far apart two events on different tracks can be trusted to be.
    """
    recording = run_recording(command, ebpf=ebpf, gpu=gpu, nsys=nsys)
    write_perfetto(recording.aligned, recording.origin_ns, " ".join(command), out)
    counts = ", ".join(f"{n} {source}" for source, n in sorted(recording.counts().items()))
    typer.echo(f"wrote {out}: {counts} samples")
    typer.echo(f"alignment error bound: {recording.alignment_error_bound_ns / 1000:.2f} us")
    raise typer.Exit(recording.returncode)


@app.command()
def compare(
    store: Annotated[Path, typer.Argument(help="benchgrid artifact store")],
    policy: Annotated[Path, typer.Option(help="benchmark policy TOML")],
    candidate: Annotated[str, typer.Option(help="revision under test")],
    known_good: Annotated[
        Path,
        typer.Option(help="file of known-good revisions, one per line, e.g. git rev-list main"),
    ],
    seed: Annotated[int, typer.Option(help="bootstrap seed")] = 0,
) -> None:
    """Compare a candidate revision's runs against a baseline and print a check report.

    Exits 1 on REGRESSION and 0 otherwise, so a CI step fails only on a
    regression the engine is confident about.
    """
    bench = BenchmarkPolicy.load(policy)
    runs, problems, extras = load_runs(store)
    same_benchmark = [r for r in runs if r.benchmark == bench.benchmark]
    candidates = sorted(
        (r for r in same_benchmark if r.revision == candidate), key=lambda r: r.started_at
    )
    if not candidates:
        typer.echo(f"no runs of {bench.benchmark} at revision {candidate} in {store}", err=True)
        raise typer.Exit(2)
    revisions = {line.strip() for line in known_good.read_text().splitlines() if line.strip()}
    confirmation: list[Run] | None = None
    if bench.confirm_regressions:
        # The run artifact carries no batch id, so batches are inferred from
        # start order: the earliest runs are the first batch and the ones after
        # them the confirmation. See docs/known-misses.md.
        size = bench.min_candidate_runs
        candidates, confirmation = candidates[:size], candidates[size : 2 * size] or None
    selection = select(
        candidates, (r for r in same_benchmark if r.revision != candidate), bench, revisions
    )
    result = run_compare(candidates, selection, bench, seed=seed, confirmation=confirmation)

    linked = {r.run_id: extras.get(r.run_id, []) for r in candidates + (confirmation or [])}
    typer.echo(render(bench.benchmark, result, candidates, linked), nl=False)
    for problem in problems:
        typer.echo(f"warning: {problem}", err=True)
    typer.echo(f"conclusion: {CONCLUSIONS[result.verdict]}", err=True)
    raise typer.Exit(1 if result.verdict is Verdict.REGRESSION else 0)


warehouse = typer.Typer(no_args_is_help=True, help="Lake, BigQuery and dashboard exports.")
app.add_typer(warehouse, name="warehouse")


def _named(pairs: list[str]) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for pair in pairs:
        name, sep, path = pair.partition("=")
        if not sep:
            raise typer.BadParameter(f"expected name=path, got {pair!r}")
        out[name] = Path(path)
    return out


@warehouse.command("export")
def warehouse_export(
    lake: Annotated[Path, typer.Option(help="lake directory to write")],
    store: Annotated[list[str], typer.Option(help="name=path of an artifact store")],
) -> None:
    """Flatten artifact stores into a date-partitioned Parquet lake."""
    from tracelab.warehouse.lake import export

    result = export(_named(store), lake)
    typer.echo(
        f"{result.runs} runs, {result.samples} samples, partitions {', '.join(result.partitions)}"
    )
    for run in result.rejected:
        typer.echo(f"warning: {run}: rejected by the contract reader, left out", err=True)


@warehouse.command("openmetrics")
def warehouse_openmetrics(
    lake: Annotated[Path, typer.Option(help="lake directory to read")],
    out: Annotated[Path, typer.Option(help="OpenMetrics file to write")],
    evidence: Annotated[
        list[str], typer.Option(help="version=path of a published summary.json")
    ] = [],  # noqa: B006
) -> None:
    """Render the lake as timestamped OpenMetrics for a Prometheus backfill."""
    import time

    from tracelab.warehouse.openmetrics import write

    out.parent.mkdir(parents=True, exist_ok=True)
    lines = write(lake, _named(evidence), out, time.time())
    typer.echo(f"wrote {out}: {lines} lines")


@warehouse.command("bigquery")
def warehouse_bigquery(
    lake: Annotated[Path, typer.Option(help="lake directory to load")],
    project: Annotated[str, typer.Option()] = "tracelab",
    dataset: Annotated[str, typer.Option()] = "perf",
    endpoint: Annotated[
        str | None, typer.Option(help="emulator URL; omit for real BigQuery")
    ] = None,
    out: Annotated[Path | None, typer.Option(help="write rollup results as JSON")] = None,
    check: Annotated[
        bool, typer.Option(help="recompute every rollup from the lake and fail on any difference")
    ] = True,
) -> None:
    """Create partitioned, clustered tables, load the lake, and run every rollup."""
    from tracelab.warehouse.bigquery import ROLLUPS, Warehouse
    from tracelab.warehouse.check import check as recompute

    wh = Warehouse.connect(project, dataset, endpoint)
    wh.create(lake)
    loaded = wh.load(lake)
    typer.echo(f"loaded {loaded}")
    results = {name: wh.rollup(name) for name in ROLLUPS}
    for name, rows in results.items():
        typer.echo(f"{name}: {len(rows)} rows")
    if out is not None:
        out.write_text(json.dumps(results, indent=2, sort_keys=True, default=str) + "\n")
    if check:
        disagreements = recompute(results, lake)
        for line in disagreements:
            typer.echo(f"disagrees: {line}", err=True)
        typer.echo(f"rollups recomputed from the lake: {len(disagreements)} disagreements")
        raise typer.Exit(1 if disagreements else 0)
