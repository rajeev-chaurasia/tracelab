import pytest
from pydantic import ValidationError

from script.export_contract import CONTRACT, render
from tracelab.core.schema import Series, normalize

from .factories import run


def test_committed_contract_matches_the_model() -> None:
    assert CONTRACT.read_text(encoding="utf-8") == render(), (
        "contract drifted; run script/export_contract.py and review the diff"
    )


def test_normalize_keeps_every_sample_with_its_provenance() -> None:
    result = run(metrics={"perception.latency_ms": [1.0, 2.0], "rss_mb": [50.0]})

    points = normalize(result)

    assert [(p.metric_name, p.sample_index, p.value) for p in points] == [
        ("perception.latency_ms", 0, 1.0),
        ("perception.latency_ms", 1, 2.0),
        ("rss_mb", 0, 50.0),
    ]
    assert points[0].component == "perception"
    assert points[2].component == "matmul"
    assert {p.git_sha for p in points} == {"a" * 40}
    assert {p.hardware_class for p in points} == {"cpu-arm64"}


def test_normalize_uses_sample_timestamps_when_present() -> None:
    result = run().with_metrics(
        {"tick_ms": Series(unit="ms", values=[20.0, 21.0], timestamps_ns=[5, 9])}
    )

    stamps = [p.timestamp_ns for p in normalize(result) if p.metric_name == "tick_ms"]

    assert stamps == [5, 9]


def test_rejects_a_summary_in_place_of_samples() -> None:
    with pytest.raises(ValidationError):
        Series(unit="ms", values=[])


def test_rejects_misaligned_timestamps() -> None:
    with pytest.raises(ValidationError):
        Series(unit="ms", values=[1.0, 2.0], timestamps_ns=[1])


def test_rejects_unknown_fields_so_contract_drift_is_loud() -> None:
    payload = run().model_dump()
    payload["surprise"] = 1
    with pytest.raises(ValidationError):
        type(run()).model_validate(payload)
