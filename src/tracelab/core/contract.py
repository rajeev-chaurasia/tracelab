"""Validation of one attempt directory against benchgrid's run artifact contract.

The contract lives in benchgrid/docs/run-artifact.md. This module takes the
attempt's files as bytes and either returns a parsed run or raises Rejection
with a stable code. It never repairs anything: a run that needs repair is a
run whose numbers cannot be trusted, and the place to fix it is the writer.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from .canon import CanonError, sha256_hex
from .describe import Summary, disagreements, summarize

RUN_SCHEMA = "benchgrid.run/v1"
MANIFEST_SCHEMA = "benchgrid.manifest/v1"
MANIFEST = "manifest.json"
RUN = "run.json"
SAMPLES = "samples.jsonl"

UNITS = frozenset({"ns", "bytes", "ops_per_s", "ratio", "celsius", "count", "unitless"})

HEX64 = r"^[0-9a-f]{64}$"
HEX40 = r"^[0-9a-f]{40}$"
# benchgrid writes nine fraction digits, but the contract tells consumers to
# accept zero to nine, so a run from any other writer is not rejected over
# formatting alone.
UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z$")


class Code(StrEnum):
    MANIFEST_INVALID = "manifest_invalid"
    MANIFEST_UNSORTED = "manifest_unsorted"
    MANIFEST_MISSING_FILE = "manifest_missing_file"
    MANIFEST_UNLISTED_FILE = "manifest_unlisted_file"
    MANIFEST_SIZE_MISMATCH = "manifest_size_mismatch"
    MANIFEST_DIGEST_MISMATCH = "manifest_digest_mismatch"
    RUN_JSON_INVALID = "run_json_invalid"
    RUN_SCHEMA_VERSION = "run_schema_version"
    PATH_MISMATCH = "path_mismatch"
    TIMESTAMP_NOT_UTC = "timestamp_not_utc"
    STATUS_REASON = "status_reason"
    UNIT_UNKNOWN = "unit_unknown"
    PREFLIGHT_OUT_OF_RANGE = "preflight_out_of_range"
    SPEC_METRICS_INVALID = "spec_metrics_invalid"
    SPEC_SHA256_MISMATCH = "spec_sha256_mismatch"
    SAMPLES_INVALID = "samples_invalid"
    SAMPLE_METRIC_UNDECLARED = "sample_metric_undeclared"
    SAMPLE_UNIT_MISMATCH = "sample_unit_mismatch"
    ITERATION_NOT_CONTIGUOUS = "iteration_not_contiguous"
    SUMMARY_METRICS_MISMATCH = "summary_metrics_mismatch"
    SUMMARY_UNIT_MISMATCH = "summary_unit_mismatch"
    SUMMARY_DISAGREES = "summary_disagrees"
    # Raised by the store reader, which owns directory names; listed here so
    # every code a consumer can see is in one place.
    ATTEMPT_DIR_NAME = "attempt_dir_name"


class Rejection(Exception):
    def __init__(self, code: Code, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class _Model(BaseModel):
    # Strict, so a string "3" is never quietly accepted as the integer 3, and
    # closed, so a misspelled field fails loudly instead of reading as absent.
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ManifestEntry(_Model):
    path: str
    sha256: str = Field(pattern=HEX64)
    size: int = Field(ge=0)


class Manifest(_Model):
    schema_version: Literal["benchgrid.manifest/v1"]
    files: list[ManifestEntry]


class MetricDecl(_Model):
    name: str = Field(min_length=1)
    unit: str
    direction: Literal["lower_is_better", "higher_is_better"]


class Rig(_Model):
    rig_id: str
    hardware_class: str
    arch: str
    os: str
    kernel: str
    cpu_model: str
    cpu_cores: int
    mem_bytes: int
    gpu_vendor: str
    gpu_model: str
    # 0 when there is no GPU. A descriptor of the hardware, not a reading, so
    # the contract's null-for-unmeasured rule does not apply to it.
    gpu_memory_bytes: int
    driver_version: str
    firmware: str
    emulated: StrictBool


class Preflight(_Model):
    load1: float | None
    cpu_util: float | None
    mem_free: int | None
    gpu_util: float | None
    temp_c: float | None


class Environment(_Model):
    git_revision: str = Field(pattern=HEX40)
    binary_sha256: str = Field(pattern=HEX64)
    # null when the experiment has no config file. Never "", which would read
    # as the digest of something.
    config_sha256: str | None = Field(pattern=HEX64)
    governor: str
    preflight_before: Preflight
    preflight_after: Preflight


class Timing(_Model):
    lease_acquired: str
    started: str
    finished: str


class RunJson(_Model):
    schema_version: str
    run_id: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    fence: int = Field(ge=0)
    status: Literal["SUCCEEDED", "FAILED", "INVALID"]
    status_reason: str
    spec_sha256: str = Field(pattern=HEX64)
    spec: dict[str, Any]
    rig: Rig
    environment: Environment
    timing: Timing
    summary: dict[str, Summary]


class Sample(_Model):
    metric: str
    iteration: int = Field(ge=0)
    warmup: StrictBool
    value: float
    unit: str
    t_offset_ns: int = Field(ge=0)


@dataclass(frozen=True)
class RunArtifact:
    run: RunJson
    metrics: tuple[MetricDecl, ...]
    samples: tuple[Sample, ...]


def validate_attempt(run_id: str, attempt: int, files: Mapping[str, bytes]) -> RunArtifact:
    """Validate a sealed attempt. files maps each path relative to the attempt
    directory, manifest.json included, to its contents."""
    _check_manifest(files)
    run = _parse_run(files[RUN])
    if run.run_id != run_id or run.attempt != attempt:
        raise Rejection(
            Code.PATH_MISMATCH,
            f"run.json says {run.run_id}/attempt-{run.attempt}, "
            f"directory is {run_id}/attempt-{attempt}",
        )
    _check_status(run)
    _check_timing(run.timing)
    _check_preflight(run.environment)
    metrics = _check_spec(run)
    samples = _parse_samples(files[SAMPLES], metrics)
    _check_summary(run, metrics, samples)
    return RunArtifact(run=run, metrics=metrics, samples=samples)


def _check_manifest(files: Mapping[str, bytes]) -> None:
    try:
        manifest = Manifest.model_validate(json.loads(files[MANIFEST]))
    except (ValueError, ValidationError) as err:
        raise Rejection(Code.MANIFEST_INVALID, str(err)) from err
    listed = [entry.path for entry in manifest.files]
    if MANIFEST in listed or len(set(listed)) != len(listed):
        raise Rejection(Code.MANIFEST_INVALID, "manifest lists itself or a path twice")
    if listed != sorted(listed):
        raise Rejection(Code.MANIFEST_UNSORTED, f"files are not sorted by path: {listed}")
    present = set(files) - {MANIFEST}
    for required in (RUN, SAMPLES):
        if required not in listed:
            raise Rejection(Code.MANIFEST_MISSING_FILE, f"{required} is not listed")
    for entry in manifest.files:
        if entry.path not in present:
            raise Rejection(Code.MANIFEST_MISSING_FILE, f"{entry.path} is listed but absent")
        body = files[entry.path]
        if len(body) != entry.size:
            raise Rejection(
                Code.MANIFEST_SIZE_MISMATCH, f"{entry.path} is {len(body)} bytes, not {entry.size}"
            )
        if hashlib.sha256(body).hexdigest() != entry.sha256:
            raise Rejection(Code.MANIFEST_DIGEST_MISMATCH, f"{entry.path} digest differs")
    unlisted = sorted(present - set(listed))
    if unlisted:
        raise Rejection(Code.MANIFEST_UNLISTED_FILE, f"present but not listed: {unlisted}")


def _parse_run(body: bytes) -> RunJson:
    try:
        raw = json.loads(body)
    except ValueError as err:
        raise Rejection(Code.RUN_JSON_INVALID, str(err)) from err
    # Checked before the model so a future v2 run is reported as a version the
    # reader does not know rather than as a pile of field errors.
    version = raw.get("schema_version") if isinstance(raw, dict) else None
    if version != RUN_SCHEMA:
        raise Rejection(Code.RUN_SCHEMA_VERSION, f"schema_version is {version!r}")
    try:
        return RunJson.model_validate(raw)
    except ValidationError as err:
        raise Rejection(Code.RUN_JSON_INVALID, str(err)) from err


def _check_status(run: RunJson) -> None:
    if run.status == "SUCCEEDED" and run.status_reason:
        raise Rejection(Code.STATUS_REASON, "SUCCEEDED carries a reason")
    if run.status != "SUCCEEDED" and not run.status_reason:
        raise Rejection(Code.STATUS_REASON, f"{run.status} carries no reason")


def _check_timing(timing: Timing) -> None:
    for name in ("lease_acquired", "started", "finished"):
        value = getattr(timing, name)
        if not UTC_TIMESTAMP.match(value):
            raise Rejection(Code.TIMESTAMP_NOT_UTC, f"{name} is {value!r}")
        # The pattern accepts 2026-13-45; parsing the second-resolution prefix
        # catches that without depending on fromisoformat's nanosecond support.
        try:
            datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError as err:
            raise Rejection(Code.TIMESTAMP_NOT_UTC, f"{name} is {value!r}") from err


def _check_preflight(env: Environment) -> None:
    for when in ("preflight_before", "preflight_after"):
        reading: Preflight = getattr(env, when)
        for name in ("cpu_util", "gpu_util"):
            value = getattr(reading, name)
            if value is not None and not 0 <= value <= 1:
                raise Rejection(
                    Code.PREFLIGHT_OUT_OF_RANGE, f"{when}.{name} is {value}, not a ratio in 0..1"
                )
        for name in ("load1", "mem_free"):
            value = getattr(reading, name)
            if value is not None and value < 0:
                raise Rejection(Code.PREFLIGHT_OUT_OF_RANGE, f"{when}.{name} is {value}")


def _check_spec(run: RunJson) -> tuple[MetricDecl, ...]:
    declared = run.spec.get("metrics")
    if not isinstance(declared, list):
        raise Rejection(Code.SPEC_METRICS_INVALID, "spec.metrics is not a list")
    try:
        metrics = tuple(MetricDecl.model_validate(m) for m in declared)
    except ValidationError as err:
        raise Rejection(Code.SPEC_METRICS_INVALID, str(err)) from err
    names = [m.name for m in metrics]
    if not names or len(set(names)) != len(names):
        raise Rejection(Code.SPEC_METRICS_INVALID, f"metrics are empty or repeated: {names}")
    for m in metrics:
        if m.unit not in UNITS:
            raise Rejection(Code.UNIT_UNKNOWN, f"spec metric {m.name} has unit {m.unit!r}")
    try:
        digest = sha256_hex(run.spec)
    except CanonError as err:
        raise Rejection(Code.SPEC_SHA256_MISMATCH, f"spec cannot be canonicalized: {err}") from err
    if digest != run.spec_sha256:
        raise Rejection(
            Code.SPEC_SHA256_MISMATCH, f"recomputed {digest}, run.json says {run.spec_sha256}"
        )
    return metrics


def _parse_samples(body: bytes, metrics: tuple[MetricDecl, ...]) -> tuple[Sample, ...]:
    units = {m.name: m.unit for m in metrics}
    samples: list[Sample] = []
    seen: dict[str, list[int]] = defaultdict(list)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as err:
        raise Rejection(Code.SAMPLES_INVALID, str(err)) from err
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            raise Rejection(Code.SAMPLES_INVALID, f"line {lineno} is blank")
        try:
            sample = Sample.model_validate(json.loads(line))
        except (ValueError, ValidationError) as err:
            raise Rejection(Code.SAMPLES_INVALID, f"line {lineno}: {err}") from err
        if sample.unit not in UNITS:
            raise Rejection(Code.UNIT_UNKNOWN, f"line {lineno} has unit {sample.unit!r}")
        if sample.metric not in units:
            raise Rejection(
                Code.SAMPLE_METRIC_UNDECLARED, f"line {lineno} metric {sample.metric!r}"
            )
        if sample.unit != units[sample.metric]:
            raise Rejection(
                Code.SAMPLE_UNIT_MISMATCH,
                f"line {lineno} {sample.metric} in {sample.unit}, spec says {units[sample.metric]}",
            )
        seen[sample.metric].append(sample.iteration)
        samples.append(sample)
    for metric, iterations in seen.items():
        if sorted(iterations) != list(range(len(iterations))):
            raise Rejection(
                Code.ITERATION_NOT_CONTIGUOUS,
                f"{metric} iterations are not 0..{len(iterations) - 1} exactly once",
            )
    return tuple(samples)


def _check_summary(
    run: RunJson, metrics: tuple[MetricDecl, ...], samples: tuple[Sample, ...]
) -> None:
    declared = {m.name for m in metrics}
    if set(run.summary) != declared:
        raise Rejection(
            Code.SUMMARY_METRICS_MISMATCH,
            f"summary has {sorted(run.summary)}, spec declares {sorted(declared)}",
        )
    for m in metrics:
        claimed = run.summary[m.name]
        if claimed.unit not in UNITS:
            raise Rejection(Code.UNIT_UNKNOWN, f"summary {m.name} has unit {claimed.unit!r}")
        if claimed.unit != m.unit:
            raise Rejection(
                Code.SUMMARY_UNIT_MISMATCH, f"{m.name} summary in {claimed.unit}, spec {m.unit}"
            )
        measured = [s.value for s in samples if s.metric == m.name and not s.warmup]
        found = disagreements(claimed, summarize(m.unit, measured))
        if found:
            raise Rejection(Code.SUMMARY_DISAGREES, f"{m.name}: {'; '.join(found)}")
