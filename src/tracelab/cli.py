"""Command line entry points.

    tracelab compare <store> --policy policies/v2/matmul.toml \
        --candidate <revision> --known-good revisions.txt
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

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


def load_runs(store: Path) -> tuple[list[Run], list[str]]:
    runs: list[Run] = []
    problems: list[str] = []
    for outcome in read_store(store):
        if outcome.artifact is None:
            # A rejected run is reported, never silently skipped, and never
            # replaced by a lower attempt. The reader owns that rule.
            code = outcome.rejection.code if outcome.rejection else "no valid attempt"
            problems.append(f"{outcome.run_id}: rejected ({code})")
            continue
        runs.append(from_artifact(outcome.artifact))
    return runs, problems


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
    runs, problems = load_runs(store)
    mine = [r for r in runs if r.benchmark == bench.benchmark]
    candidates = sorted((r for r in mine if r.revision == candidate), key=lambda r: r.started_at)
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
    selection = select(candidates, (r for r in mine if r.revision != candidate), bench, revisions)
    result = run_compare(candidates, selection, bench, seed=seed, confirmation=confirmation)

    typer.echo(render(bench.benchmark, result, candidates), nl=False)
    for problem in problems:
        typer.echo(f"warning: {problem}", err=True)
    typer.echo(f"conclusion: {CONCLUSIONS[result.verdict]}", err=True)
    raise typer.Exit(1 if result.verdict is Verdict.REGRESSION else 0)
