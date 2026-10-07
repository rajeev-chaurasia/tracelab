"""The command a CI step runs, end to end through a real artifact store."""

from pathlib import Path

import numpy as np
from typer.testing import CliRunner

from evaluation import workload
from evaluation.collect import write_attempt
from tracelab.cli import app

POLICY = Path("policies/matmul.toml")
CANDIDATE = "c" * 40
PREFLIGHT = {"load1": 1.0, "cpu_util": None, "mem_free": None, "gpu_util": None, "temp_c": None}


def samples(level: float, rng: np.random.Generator) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    total = workload.MATMUL["warmups"] + workload.MATMUL["repetitions"]
    for i in range(total):
        warmup = i < workload.MATMUL["warmups"]
        latency = float(round(level * (1 + rng.normal(0, 0.01))))
        out.append(_sample("iteration_latency", i, warmup, latency, "ns"))
        out.append(_sample("max_rss", i, warmup, 3.5e7, "bytes"))
    return out


def _sample(metric: str, i: int, warmup: bool, value: float, unit: str) -> dict[str, object]:
    return {
        "metric": metric,
        "iteration": i,
        "warmup": warmup,
        "value": value,
        "unit": unit,
        "t_offset_ns": i * 1000,
    }


def build_store(root: Path, candidate_level: float) -> Path:
    rng = np.random.default_rng(0)
    for i in range(23):
        is_candidate = i >= 20
        level = candidate_level if is_candidate else 600_000.0
        write_attempt(
            root,
            f"run-{i:02d}",
            "matmul",
            CANDIDATE if is_candidate else f"{i:040x}",
            samples(level * (1 + rng.normal(0, 0.005)), rng),
            binary_sha256="b" * 64,
            started_ns=1_790_000_000_000_000_000 + i * 10**10,
            finished_ns=1_790_000_000_000_000_000 + i * 10**10 + 10**9,
            preflight=(PREFLIGHT, PREFLIGHT),
        )
    return root


def invoke(store: Path, tmp_path: Path, candidate: str = CANDIDATE):  # type: ignore[no-untyped-def]
    known = tmp_path / "known-good.txt"
    known.write_text("\n".join(f"{i:040x}" for i in range(20)) + "\n")
    return CliRunner().invoke(
        app,
        [
            "compare",
            str(store),
            "--policy",
            str(POLICY),
            "--candidate",
            candidate,
            "--known-good",
            str(known),
        ],
    )


def test_a_slower_candidate_fails_the_step_with_the_evidence(tmp_path: Path) -> None:
    result = invoke(build_store(tmp_path / "store", 720_000.0), tmp_path)

    assert result.exit_code == 1, result.output
    assert "## PERFORMANCE REGRESSION: matmul" in result.output
    [row] = [
        line for line in result.output.splitlines() if line.startswith("| iteration_latency.median")
    ]
    # About 600 us against about 720 us, printed in the unit a person reads.
    assert "| critical | 6" in row
    assert " us | 7" in row
    assert row.endswith("| REGRESSION |")
    assert "Hardware: laptop-arm64" in result.output
    assert "Baseline runs: 20." in result.output
    assert "conclusion: failure" in result.output


def test_an_unchanged_candidate_does_not_fail_the_step(tmp_path: Path) -> None:
    result = invoke(build_store(tmp_path / "store", 600_000.0), tmp_path)

    assert result.exit_code == 0, result.output
    assert "REGRESSION" not in result.output.split("\n")[0]


def test_an_unknown_revision_is_a_usage_error(tmp_path: Path) -> None:
    result = invoke(build_store(tmp_path / "store", 600_000.0), tmp_path, candidate="d" * 40)

    assert result.exit_code == 2
    assert "no runs of matmul" in result.output


def test_a_rejected_run_is_reported_not_skipped_silently(tmp_path: Path) -> None:
    store = build_store(tmp_path / "store", 600_000.0)
    samples_file = store / "runs" / "run-03" / "attempt-1" / "samples.jsonl"
    samples_file.write_bytes(samples_file.read_bytes() + b"\n")

    result = invoke(store, tmp_path)

    assert "warning: run-03: rejected (manifest_size_mismatch)" in result.output
