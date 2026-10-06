"""The analysis view, built from conformance fixtures through the real reader."""

import json
from pathlib import Path

import pytest

from tracelab.core.canon import sha256_hex
from tracelab.core.run import comparison_key, from_artifact, parse_utc
from tracelab.ingest.store import read_store

FIXTURES = Path(__file__).parent / "fixtures" / "run-artifact-v1"


def load(case: str):  # type: ignore[no-untyped-def]
    [outcome] = list(read_store(FIXTURES / case / "store"))
    assert outcome.artifact is not None
    return outcome.artifact


def test_keeps_measured_samples_in_iteration_order_and_drops_warmups() -> None:
    artifact = load("valid")

    run = from_artifact(artifact)

    for decl in artifact.metrics:
        measured = sorted(
            (s for s in artifact.samples if s.metric == decl.name and not s.warmup),
            key=lambda s: s.iteration,
        )
        assert run.metrics[decl.name].values == tuple(s.value for s in measured)
        assert run.metrics[decl.name].unit == decl.unit
        assert len(run.metrics[decl.name].values) == artifact.run.summary[decl.name].n


def test_carries_identity_and_rig() -> None:
    artifact = load("valid")

    run = from_artifact(artifact)

    assert run.run_id == artifact.run.run_id
    assert run.revision == artifact.run.environment.git_revision
    assert run.benchmark == artifact.run.spec["benchmark"]
    assert run.rig.hardware_class == artifact.run.rig.hardware_class
    assert run.rig.governor == artifact.run.environment.governor


def test_comparison_key_ignores_what_is_under_test_and_nothing_else() -> None:
    spec = load("valid").run.spec
    other_commit = {**spec, "revision": "e" * 40, "artifacts": {"binary_sha256": "d" * 64}}
    more_reps = {**spec, "repetitions": spec["repetitions"] + 1}

    assert comparison_key(other_commit) == comparison_key(spec)
    assert comparison_key(more_reps) != comparison_key(spec)
    without = {k: v for k, v in spec.items() if k not in {"revision", "artifacts"}}
    assert comparison_key(spec) == sha256_hex(without)


def test_a_failed_run_maps_with_its_status_and_no_measured_values() -> None:
    run = from_artifact(load("valid_failed_warmups_only"))

    assert run.status == "FAILED"
    assert run.status_reason
    assert all(series.values == () for series in run.metrics.values())


def test_refuses_a_spec_without_a_benchmark_name() -> None:
    artifact = load("valid")
    spec = {k: v for k, v in artifact.run.spec.items() if k != "benchmark"}
    broken = type(artifact)(
        run=artifact.run.model_copy(update={"spec": spec}),
        metrics=artifact.metrics,
        samples=artifact.samples,
    )

    with pytest.raises(ValueError, match="benchmark"):
        from_artifact(broken)


@pytest.mark.parametrize(
    ("stamp", "micros"),
    [
        ("2026-10-06T23:40:02Z", 0),
        ("2026-10-06T23:40:02.5Z", 500000),
        ("2026-10-06T23:40:02.123456789Z", 123456),
    ],
)
def test_parses_every_fraction_width_the_contract_allows(stamp: str, micros: int) -> None:
    assert parse_utc(stamp).microsecond == micros


def test_fixture_specs_are_in_the_writer_shape() -> None:
    spec = json.loads(next((FIXTURES / "valid" / "store").rglob("run.json")).read_text())["spec"]

    assert {"benchmark", "revision", "artifacts", "metrics"} <= set(spec)
