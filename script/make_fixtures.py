"""Generate the run artifact conformance fixtures.

Every rejecting case starts from the valid run and breaks exactly one rule,
then reseals the manifest unless the manifest is the thing being broken. A case
that broke two rules would pass whichever check happened to run first and
prove nothing about the other.

The output is committed. tests/test_fixtures.py regenerates it and fails on
any difference, so a hand edit to a fixture cannot survive.

    uv run python script/make_fixtures.py
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tracelab.core.canon import sha256_hex
from tracelab.core.describe import percentile, summarize

OUT = Path("tests/fixtures/run-artifact-v1")
RUN_ID = "exp_01JBV3Q8ZK4M7N2P5R6S9T0W1X"

Json = dict[str, Any]
Files = dict[str, bytes]

REVISION = "3f2a9c1e7b4d5a6f8091a2b3c4d5e6f708192a3b"
BINARY_SHA256 = hashlib.sha256(b"bench/matmul").hexdigest()
CONFIG_SHA256 = hashlib.sha256(b"config").hexdigest()

# The shape benchgrid's internal/spec writes, with its omitempty fields left
# out, so the analysis tests can read these specs the way they read real ones.
BASE_SPEC: Json = {
    "benchmark": "matmul_256",
    "revision": REVISION,
    "command": ["{binary}", "--size", "256"],
    "warmups": 3,
    "repetitions": 20,
    "timeout_seconds": 120,
    "requirements": {"arch": "x86_64", "hardware_class": "cpu-a", "allow_emulated": False},
    "environment": {"cpu_governor": "performance", "max_load1": 1.5, "max_cpu_util": 0.25},
    "metrics": [
        {"name": "iteration_latency", "unit": "ns", "direction": "lower_is_better"},
        {"name": "max_rss", "unit": "bytes", "direction": "lower_is_better"},
    ],
    "artifacts": {"binary_sha256": BINARY_SHA256, "config_sha256": CONFIG_SHA256},
}

RIG: Json = {
    "rig_id": "rig-07",
    "hardware_class": "cpu-a",
    "arch": "x86_64",
    "os": "linux",
    "kernel": "6.8.0",
    "cpu_model": "AMD EPYC 7B13",
    "cpu_cores": 16,
    "mem_bytes": 68719476736,
    "gpu_vendor": "",
    "gpu_model": "",
    "gpu_memory_bytes": 0,
    "driver_version": "",
    "firmware": "",
    "emulated": False,
}

ENVIRONMENT: Json = {
    "git_revision": REVISION,
    "binary_sha256": BINARY_SHA256,
    "config_sha256": CONFIG_SHA256,
    "governor": "performance",
    "preflight_before": {
        "load1": 0.12,
        "cpu_util": 0.03,
        "mem_free": 60129542144,
        "gpu_util": None,
        "temp_c": 41.5,
    },
    "preflight_after": {
        "load1": 0.98,
        "cpu_util": 0.11,
        "mem_free": 60011282432,
        "gpu_util": None,
        "temp_c": 58.0,
    },
}

TIMING: Json = {
    "lease_acquired": "2026-10-06T23:40:01.123456789Z",
    # No fraction at all. benchgrid never writes this, but the contract says a
    # consumer accepts it, so the reader is held to that.
    "started": "2026-10-06T23:40:02Z",
    "finished": "2026-10-06T23:40:31.5Z",
}


def make_samples(warmups: int, repetitions: int, constant: bool = False) -> list[Json]:
    rng = random.Random(20261006)
    rows: list[Json] = []
    t = 0
    for i in range(warmups + repetitions):
        warm = i < warmups
        latency = 1_800_000 if constant else int(rng.gauss(1_800_000, 40_000))
        if warm and not constant:
            latency += 350_000
        rss = 52_428_800 + (0 if constant else rng.randrange(0, 65_536))
        rows.append(_sample("iteration_latency", i, warm, latency, "ns", t))
        rows.append(_sample("max_rss", i, warm, rss, "bytes", t))
        t += latency + 15_000
    return rows


def _sample(metric: str, i: int, warm: bool, value: float, unit: str, t: int) -> Json:
    return {
        "metric": metric,
        "iteration": i,
        "warmup": warm,
        "value": value,
        "unit": unit,
        "t_offset_ns": t,
    }


def summary_for(spec: Json, samples: list[Json]) -> Json:
    out: Json = {}
    for metric in spec["metrics"]:
        measured = [
            s["value"] for s in samples if s["metric"] == metric["name"] and not s["warmup"]
        ]
        out[metric["name"]] = summarize(metric["unit"], measured).model_dump()
    return out


def make_run(
    spec: Json,
    samples: list[Json],
    attempt: int = 2,
    status: str = "SUCCEEDED",
    reason: str = "",
) -> Json:
    return {
        "schema_version": "benchgrid.run/v1",
        "run_id": RUN_ID,
        "attempt": attempt,
        "fence": 109,
        "status": status,
        "status_reason": reason,
        "spec_sha256": sha256_hex(spec),
        "spec": spec,
        "rig": copy.deepcopy(RIG),
        "environment": copy.deepcopy(ENVIRONMENT),
        "timing": copy.deepcopy(TIMING),
        "summary": summary_for(spec, samples),
    }


def encode(run: Json, samples: list[Json], extra: Files | None = None) -> Files:
    # ensure_ascii keeps every non-ASCII character as an escape, so no editor
    # or tool that normalizes Unicode can silently change the bytes.
    files: Files = {
        "run.json": (json.dumps(run, indent=2, ensure_ascii=True) + "\n").encode(),
        "samples.jsonl": "".join(json.dumps(s) + "\n" for s in samples).encode(),
    }
    files.update(extra or {})
    return files


def manifest_for(files: Files) -> Json:
    return {
        "schema_version": "benchgrid.manifest/v1",
        "files": [
            {"path": p, "sha256": hashlib.sha256(b).hexdigest(), "size": len(b)}
            for p, b in sorted(files.items())
        ],
    }


def seal(files: Files, manifest: Json | None = None) -> Files:
    body = json.dumps(manifest or manifest_for(files), indent=2) + "\n"
    return {**files, "manifest.json": body.encode()}


def base_spec(**changes: Any) -> Json:
    spec = copy.deepcopy(BASE_SPEC)
    spec.update(changes)
    return spec


def stress_spec() -> Json:
    spec = base_spec(
        environment={"max_gpu_util": 1e-07, "max_temp_c": 100.0, "max_load1": 0.000001},
    )
    spec["requirements"]["tags"] = ["\u20ac", "\U0001f600", "\ufb33", "\u00f6"]
    return spec


def valid_files(attempt: int = 2) -> Files:
    samples = make_samples(3, 20)
    return seal(encode(make_run(base_spec(), samples, attempt=attempt), samples))


def _run_and_samples(attempt: int) -> tuple[Json, list[Json]]:
    samples = make_samples(3, 20)
    return make_run(base_spec(), samples, attempt=attempt), samples


@dataclass
class Case:
    name: str
    rule: str
    outcome: str
    attempt: int | None
    code: str | None = None
    attempts: dict[str, Files] = field(default_factory=dict)
    ignored: dict[str, str] = field(default_factory=dict)


CASES: list[Case] = []


def case(
    rule: str, outcome: str, attempt: int | None, code: str | None = None, **ignored: str
) -> Callable[[Callable[[], dict[str, Files]]], None]:
    def register(build: Callable[[], dict[str, Files]]) -> None:
        CASES.append(Case(build.__name__, rule, outcome, attempt, code, build(), dict(ignored)))

    return register


def mutated_run(change: Callable[[Json, list[Json]], None], reseal_summary: bool = False) -> Files:
    """The valid attempt-2 with change applied to run.json and samples, resealed."""
    samples = make_samples(3, 20)
    run = make_run(base_spec(), samples)
    change(run, samples)
    if reseal_summary:
        run["summary"] = summary_for(run["spec"], samples)
    return seal(encode(run, samples))


# Accepted.


@case("The baseline valid run.", "accepted", 2)
def valid() -> dict[str, Files]:
    return {"attempt-2": valid_files()}


@case(
    "FAILED with an empty samples.jsonl: every summary is n 0 with nulls, never zeros.",
    "accepted",
    2,
)
def valid_failed_empty() -> dict[str, Files]:
    run = make_run(base_spec(), [], status="FAILED", reason="timeout")
    return {"attempt-2": seal(encode(run, []))}


@case("Warmups only: the summary excludes warmups, so n is 0.", "accepted", 2)
def valid_failed_warmups_only() -> dict[str, Files]:
    samples = make_samples(3, 0)
    run = make_run(base_spec(), samples, status="FAILED", reason="timeout")
    return {"attempt-2": seal(encode(run, samples))}


@case("One measured sample: stddev and cv are null.", "accepted", 2)
def valid_single_sample() -> dict[str, Files]:
    samples = make_samples(3, 1)
    return {"attempt-2": seal(encode(make_run(base_spec(repetitions=1), samples), samples))}


@case("Constant data: mad, stddev and cv are exactly 0.", "accepted", 2)
def valid_constant() -> dict[str, Files]:
    samples = make_samples(3, 20, constant=True)
    return {"attempt-2": seal(encode(make_run(base_spec(), samples), samples))}


@case("An additional listed file in a subdirectory is allowed.", "accepted", 2)
def valid_extra_file() -> dict[str, Files]:
    samples = make_samples(3, 20)
    extra = {"profile/cpu.pb.gz": b"\x1f\x8b\x08\x00not really a profile"}
    return {"attempt-2": seal(encode(make_run(base_spec(), samples), samples, extra))}


@case(
    "spec_sha256 survives non-ASCII strings and numbers whose ECMAScript form is not repr.",
    "accepted",
    2,
)
def valid_canon_stress() -> dict[str, Files]:
    # Values only: benchgrid's spec has no free-form map, so a valid spec cannot
    # carry the keys that test UTF-16 ordering. tests/test_canon.py covers those.
    samples = make_samples(3, 20)
    return {"attempt-2": seal(encode(make_run(stress_spec(), samples), samples))}


@case("An experiment without a config file has config_sha256 null.", "accepted", 2)
def valid_no_config() -> dict[str, Files]:
    samples = make_samples(3, 20)
    spec = base_spec(artifacts={"binary_sha256": BINARY_SHA256})
    run = make_run(spec, samples)
    run["environment"]["config_sha256"] = None
    return {"attempt-2": seal(encode(run, samples))}


@case(
    "attempt-10 outranks attempt-9 numerically; unsealed attempt-11 does not exist.",
    "accepted",
    10,
)
def valid_attempt_order() -> dict[str, Files]:
    unsealed = encode(*_run_and_samples(attempt=11))
    return {"attempt-9": valid_files(9), "attempt-10": valid_files(10), "attempt-11": unsealed}


@case("A run with no sealed attempt must not be read, listed or reported.", "absent", None)
def absent_unsealed() -> dict[str, Files]:
    return {"attempt-1": encode(*_run_and_samples(attempt=1))}


# Rejected by the store.


@case(
    "Only the highest sealed attempt counts; a corrupt one is not replaced by an older one.",
    "rejected",
    2,
    "manifest_digest_mismatch",
)
def superseded_by_corrupt() -> dict[str, Files]:
    corrupt = valid_files()
    corrupt["samples.jsonl"] = corrupt["samples.jsonl"].replace(b'"value": 1', b'"value": 2', 1)
    return {"attempt-1": valid_files(1), "attempt-2": corrupt}


@case(
    "Attempt numbers are unpadded decimal.",
    "no_valid_attempt",
    None,
    None,
    **{"attempt-02": "attempt_dir_name"},
)
def attempt_dir_padded() -> dict[str, Files]:
    return {"attempt-02": valid_files()}


@case(
    "The directory is named by attempt, never by fence.",
    "rejected",
    109,
    "path_mismatch",
)
def path_named_by_fence() -> dict[str, Files]:
    return {"attempt-109": valid_files()}


# Rejected by the manifest.


def _manifest_case(change: Callable[[Files, Json], None]) -> dict[str, Files]:
    samples = make_samples(3, 20)
    files = encode(make_run(base_spec(), samples), samples)
    manifest = manifest_for(files)
    change(files, manifest)
    return {"attempt-2": seal(files, manifest)}


@case("Every listed digest must match.", "rejected", 2, "manifest_digest_mismatch")
def manifest_digest_mismatch() -> dict[str, Files]:
    def change(files: Files, _: Json) -> None:
        body = files["samples.jsonl"]
        files["samples.jsonl"] = body.replace(b'"value": 1', b'"value": 2', 1)

    return _manifest_case(change)


@case("Every listed size must match.", "rejected", 2, "manifest_size_mismatch")
def manifest_size_mismatch() -> dict[str, Files]:
    def change(files: Files, _: Json) -> None:
        files["samples.jsonl"] += b"\n"

    return _manifest_case(change)


@case("files is sorted by path.", "rejected", 2, "manifest_unsorted")
def manifest_unsorted() -> dict[str, Files]:
    return _manifest_case(lambda _, m: m["files"].reverse())


@case("Every listed file must be present.", "rejected", 2, "manifest_missing_file")
def manifest_missing_file() -> dict[str, Files]:
    def change(_: Files, m: Json) -> None:
        entry = {"path": "profile/cpu.pb.gz", "sha256": "0" * 64, "size": 0}
        m["files"] = [entry, *m["files"]]

    return _manifest_case(change)


@case("Every present file must be listed.", "rejected", 2, "manifest_unlisted_file")
def manifest_unlisted_file() -> dict[str, Files]:
    def change(files: Files, _: Json) -> None:
        files["manifest.json.tmp"] = b"{}"

    return _manifest_case(change)


@case("The manifest schema version is benchgrid.manifest/v1.", "rejected", 2, "manifest_invalid")
def manifest_schema_version() -> dict[str, Files]:
    return _manifest_case(lambda _, m: m.update(schema_version="benchgrid.manifest/v2"))


# Rejected by run.json.


@case("The run schema version is benchgrid.run/v1.", "rejected", 2, "run_schema_version")
def run_schema_version() -> dict[str, Files]:
    return {"attempt-2": mutated_run(lambda r, _: r.update(schema_version="benchgrid.run/v2"))}


@case("emulated is a JSON boolean, not a string.", "rejected", 2, "run_json_invalid")
def run_json_string_bool() -> dict[str, Files]:
    return {"attempt-2": mutated_run(lambda r, _: r["rig"].update(emulated="false"))}


@case("A missing config is null, never an empty string.", "rejected", 2, "run_json_invalid")
def config_sha256_empty() -> dict[str, Files]:
    return {"attempt-2": mutated_run(lambda r, _: r["environment"].update(config_sha256=""))}


@case(
    "Fields are exactly the contract's; a renamed field fails.", "rejected", 2, "run_json_invalid"
)
def run_json_renamed_field() -> dict[str, Files]:
    def change(run: Json, _: list[Json]) -> None:
        run["rig"]["cpu"] = run["rig"].pop("cpu_model")

    return {"attempt-2": mutated_run(change)}


@case("run_id and attempt in run.json match the path.", "rejected", 2, "path_mismatch")
def path_mismatch() -> dict[str, Files]:
    return {"attempt-2": mutated_run(lambda r, _: r.update(attempt=3))}


@case("Timestamps are UTC and end in Z.", "rejected", 2, "timestamp_not_utc")
def timestamp_offset() -> dict[str, Files]:
    return {
        "attempt-2": mutated_run(
            lambda r, _: r["timing"].update(started="2026-10-06T16:40:02-07:00")
        )
    }


@case("A FAILED run carries a reason.", "rejected", 2, "status_reason")
def status_reason_missing() -> dict[str, Files]:
    return {"attempt-2": mutated_run(lambda r, _: r.update(status="FAILED"))}


@case("A SUCCEEDED run carries no reason.", "rejected", 2, "status_reason")
def status_reason_on_success() -> dict[str, Files]:
    return {"attempt-2": mutated_run(lambda r, _: r.update(status_reason="timeout"))}


@case(
    "Utilisation is a ratio in 0..1, never a percentage.", "rejected", 2, "preflight_out_of_range"
)
def preflight_percentage() -> dict[str, Files]:
    return {
        "attempt-2": mutated_run(
            lambda r, _: r["environment"]["preflight_after"].update(cpu_util=11)
        )
    }


# Rejected by the spec.


def _spec_case(change: Callable[[Json, list[Json]], None]) -> dict[str, Files]:
    samples = make_samples(3, 20)
    spec = base_spec()
    change(spec, samples)
    return {"attempt-2": seal(encode(make_run(spec, samples), samples))}


@case("The unit vocabulary is closed; ms is not in it.", "rejected", 2, "unit_unknown")
def unit_ms() -> dict[str, Files]:
    def change(spec: Json, samples: list[Json]) -> None:
        spec["metrics"][0]["unit"] = "ms"
        for s in samples:
            if s["metric"] == "iteration_latency":
                s["unit"] = "ms"
                s["value"] = s["value"] / 1e6

    return _spec_case(change)


@case("direction is lower_is_better or higher_is_better.", "rejected", 2, "spec_metrics_invalid")
def spec_direction_invalid() -> dict[str, Files]:
    return _spec_case(lambda spec, _: spec["metrics"][0].update(direction="lower"))


@case(
    "spec_sha256 is over the JCS form, not json.dumps(sort_keys=True).",
    "rejected",
    2,
    "spec_sha256_mismatch",
)
def spec_sha256_python_sorted() -> dict[str, Files]:
    # On a plain ASCII spec the naive form happens to equal JCS, so this case
    # uses the stress spec and checks that the two really do differ.
    samples = make_samples(3, 20)
    run = make_run(stress_spec(), samples)
    naive = json.dumps(run["spec"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    run["spec_sha256"] = hashlib.sha256(naive.encode()).hexdigest()
    assert run["spec_sha256"] != sha256_hex(run["spec"])
    return {"attempt-2": seal(encode(run, samples))}


@case(
    "spec_sha256 covers the spec as it appears in run.json.", "rejected", 2, "spec_sha256_mismatch"
)
def spec_edited_after_hash() -> dict[str, Files]:
    return {"attempt-2": mutated_run(lambda r, _: r["spec"].update(timeout_seconds=300))}


# Rejected by the samples.


@case("value is a JSON number.", "rejected", 2, "samples_invalid")
def sample_value_string() -> dict[str, Files]:
    def change(_: Json, samples: list[Json]) -> None:
        samples[10]["value"] = str(samples[10]["value"])

    return {"attempt-2": mutated_run(change)}


@case("Every sample's metric is declared in the spec.", "rejected", 2, "sample_metric_undeclared")
def sample_metric_undeclared() -> dict[str, Files]:
    def change(_: Json, samples: list[Json]) -> None:
        samples.append(_sample("throughput", 0, False, 555.0, "ops_per_s", 0))

    return {"attempt-2": mutated_run(change)}


@case("Every sample's unit matches its declared unit.", "rejected", 2, "sample_unit_mismatch")
def sample_unit_mismatch() -> dict[str, Files]:
    return {"attempt-2": mutated_run(lambda _, s: s[12].update(unit="count"))}


@case(
    "iteration is contiguous from 0 within each metric.",
    "rejected",
    2,
    "iteration_not_contiguous",
)
def iteration_gap() -> dict[str, Files]:
    def change(_: Json, samples: list[Json]) -> None:
        samples[:] = [
            s for s in samples if not (s["metric"] == "iteration_latency" and s["iteration"] == 7)
        ]

    return {"attempt-2": mutated_run(change, reseal_summary=True)}


# Rejected by the summary.


@case("summary has exactly the declared metrics.", "rejected", 2, "summary_metrics_mismatch")
def summary_missing_metric() -> dict[str, Files]:
    return {"attempt-2": mutated_run(lambda r, _: r["summary"].pop("max_rss"))}


@case("summary unit matches the declared unit.", "rejected", 2, "summary_unit_mismatch")
def summary_unit_mismatch() -> dict[str, Files]:
    return {
        "attempt-2": mutated_run(
            lambda r, _: r["summary"]["iteration_latency"].update(unit="count")
        )
    }


@case("A summary number that disagrees is rejected.", "rejected", 2, "summary_disagrees")
def summary_tampered_mean() -> dict[str, Files]:
    def change(run: Json, _: list[Json]) -> None:
        run["summary"]["iteration_latency"]["mean"] *= 0.99

    return {"attempt-2": mutated_run(change)}


@case("The summary excludes warmups.", "rejected", 2, "summary_disagrees")
def summary_includes_warmups() -> dict[str, Files]:
    def change(run: Json, samples: list[Json]) -> None:
        values = [s["value"] for s in samples if s["metric"] == "iteration_latency"]
        run["summary"]["iteration_latency"] = summarize("ns", values).model_dump()

    return {"attempt-2": mutated_run(change)}


@case(
    "Percentiles are type 7 linear, not the nearest lower rank.", "rejected", 2, "summary_disagrees"
)
def summary_lower_rank_percentile() -> dict[str, Files]:
    def change(run: Json, samples: list[Json]) -> None:
        x = sorted(
            s["value"] for s in samples if s["metric"] == "iteration_latency" and not s["warmup"]
        )
        # Only p99 changes, so this case fails on the percentile rule alone.
        assert percentile(x, 99) != x[int((len(x) - 1) * 0.99)]
        run["summary"]["iteration_latency"]["p99"] = float(x[int((len(x) - 1) * 0.99)])

    return {"attempt-2": mutated_run(change)}


@case("A single sample has stddev null, not 0.", "rejected", 2, "summary_disagrees")
def summary_zero_for_null() -> dict[str, Files]:
    samples = make_samples(3, 1)
    run = make_run(base_spec(repetitions=1), samples)
    run["summary"]["iteration_latency"].update(stddev=0.0, cv=0.0)
    return {"attempt-2": seal(encode(run, samples))}


def write(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    for c in CASES:
        root = out / c.name
        for attempt_dir, files in c.attempts.items():
            for rel, body in files.items():
                path = root / "store" / "runs" / RUN_ID / attempt_dir / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
        expect = {
            "rule": c.rule,
            "outcome": c.outcome,
            "attempt": c.attempt,
            "code": c.code,
            "ignored": c.ignored,
        }
        (root / "expect.json").write_text(json.dumps(expect, indent=2) + "\n")


if __name__ == "__main__":
    write(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)
