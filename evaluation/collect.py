"""Collect the evaluation corpus: real runs, written as sealed run artifacts.

Each run is one fresh process of `evaluation.workload`, wrapped in the run
artifact contract benchgrid writes, then read straight back through
tracelab's own reader. A run the reader rejects stops collection, so the
corpus cannot contain anything the production ingest path would refuse.

The rig is this development machine, described as what it is. It is not a
benchmark rig and nothing here claims otherwise: there is no governor control,
no thermal gate and no isolation, which is exactly why its noise is useful.

    uv run python -m evaluation.collect --runs 150 --store corpus/v2/store
    uv run python -m evaluation.collect --runs 273 --store corpus/v3/store --contention
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from evaluation import workload
from evaluation.contention import Hogs, under_contention
from tracelab.collect.recording import record
from tracelab.collect.timeline import perfetto
from tracelab.core.canon import sha256_hex
from tracelab.core.contract import validate_attempt
from tracelab.core.describe import summarize

STORE = Path("corpus/v2/store")
WORKLOAD = Path("evaluation/workload.py")


def _sysctl(name: str) -> str:
    return subprocess.run(
        ["sysctl", "-n", name], capture_output=True, text=True, check=True
    ).stdout.strip()


def _stamp(ns: int) -> str:
    seconds, fraction = divmod(ns, 1_000_000_000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds)) + f".{fraction:09d}Z"


def _preflight() -> dict[str, Any]:
    # Only load average can be read without privileges here. The contract
    # says a reading the rig cannot take is null, never zero.
    return {
        "load1": os.getloadavg()[0],
        "cpu_util": None,
        "mem_free": None,
        "gpu_util": None,
        "temp_c": None,
    }


RIG: dict[str, Any] = {
    "rig_id": "dev-laptop",
    "hardware_class": "laptop-arm64",
    "arch": platform.machine(),
    "os": sys.platform,
    "kernel": platform.release(),
    "cpu_model": _sysctl("machdep.cpu.brand_string") if sys.platform == "darwin" else "",
    "cpu_cores": os.cpu_count() or 0,
    "mem_bytes": int(_sysctl("hw.memsize")) if sys.platform == "darwin" else 0,
    "gpu_vendor": "",
    "gpu_model": "",
    "gpu_memory_bytes": 0,
    "driver_version": "",
    "firmware": "",
    "emulated": False,
}

METRICS = {
    "matmul": [
        {"name": "iteration_latency", "unit": "ns", "direction": "lower_is_better"},
        {"name": "max_rss", "unit": "bytes", "direction": "lower_is_better"},
    ],
    "periodic": [{"name": "tick_interval", "unit": "ns", "direction": "lower_is_better"}],
    # Rates are in ops_per_s, the contract's only rate unit, where one op is
    # one byte moved. The metric name carries that meaning.
    "membw": [
        {"name": "iteration_latency", "unit": "ns", "direction": "lower_is_better"},
        {"name": "memory_bandwidth", "unit": "ops_per_s", "direction": "higher_is_better"},
        {"name": "cpu_time", "unit": "ns", "direction": "lower_is_better"},
    ],
    "network": [
        {"name": "iteration_latency", "unit": "ns", "direction": "lower_is_better"},
        {"name": "network_throughput", "unit": "ops_per_s", "direction": "higher_is_better"},
        {"name": "cpu_time", "unit": "ns", "direction": "lower_is_better"},
    ],
}
SHAPE = {
    "matmul": workload.MATMUL,
    "periodic": workload.PERIODIC,
    "membw": workload.MEMBW,
    "network": workload.NETWORK,
}


def spec(benchmark: str, revision: str, binary_sha256: str) -> dict[str, Any]:
    return {
        "benchmark": benchmark,
        "revision": revision,
        "command": ["python", "-m", "evaluation.workload", benchmark],
        "warmups": SHAPE[benchmark]["warmups"],
        "repetitions": SHAPE[benchmark]["repetitions"],
        "timeout_seconds": 60,
        "requirements": {"arch": RIG["arch"], "hardware_class": RIG["hardware_class"]},
        "environment": {},
        "metrics": METRICS[benchmark],
        "artifacts": {"binary_sha256": binary_sha256},
    }


def write_attempt(
    store: Path,
    run_id: str,
    benchmark: str,
    revision: str,
    samples: list[dict[str, Any]],
    *,
    binary_sha256: str,
    started_ns: int,
    finished_ns: int,
    preflight: tuple[dict[str, Any], dict[str, Any]],
    status: str = "SUCCEEDED",
    status_reason: str = "",
    rig: dict[str, Any] | None = None,
    extra: dict[str, bytes] | None = None,
) -> Path:
    """Seal one attempt in the contract's layout, after the reader accepts it."""
    run_spec = spec(benchmark, revision, binary_sha256)
    summary = {
        m["name"]: summarize(
            m["unit"], [s["value"] for s in samples if s["metric"] == m["name"] and not s["warmup"]]
        ).model_dump()
        for m in METRICS[benchmark]
    }
    run = {
        "schema_version": "benchgrid.run/v1",
        "run_id": run_id,
        "attempt": 1,
        "fence": 0,
        "status": status,
        "status_reason": status_reason,
        "spec_sha256": sha256_hex(run_spec),
        "spec": run_spec,
        "rig": rig or RIG,
        "environment": {
            "git_revision": revision,
            "binary_sha256": binary_sha256,
            "config_sha256": None,
            "governor": "unmanaged",
            "preflight_before": preflight[0],
            "preflight_after": preflight[1],
        },
        "timing": {
            "lease_acquired": _stamp(started_ns),
            "started": _stamp(started_ns),
            "finished": _stamp(finished_ns),
        },
        "summary": summary,
    }
    files = {
        "run.json": (json.dumps(run, indent=2, sort_keys=True) + "\n").encode(),
        "samples.jsonl": "".join(json.dumps(s, sort_keys=True) + "\n" for s in samples).encode(),
        # Profiler output and traces ride along as extra files, which the
        # contract lists in the manifest like any other.
        **(extra or {}),
    }
    manifest = {
        "schema_version": "benchgrid.manifest/v1",
        "files": [
            {"path": path, "sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
            for path, body in sorted(files.items())
        ],
    }
    files["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    validate_attempt(run_id, 1, files)

    attempt = store / "runs" / run_id / "attempt-1"
    attempt.mkdir(parents=True, exist_ok=False)
    # Sealed last, as the contract requires, so an interrupted collection
    # leaves a directory the reader ignores rather than a partial run.
    for path in [*sorted(p for p in files if p != "manifest.json"), "manifest.json"]:
        (attempt / path).write_bytes(files[path])
    return attempt


def collect_one(
    benchmark: str, index: int, revision: str, store: Path = STORE, trace: bool = False
) -> Path:
    timeout = spec(benchmark, revision, "")["timeout_seconds"]
    command = [sys.executable, "-m", "evaluation.workload", benchmark]
    before = _preflight()
    started = time.time_ns()
    extra: dict[str, bytes] = {}
    if trace:
        recording = record(command, timeout_s=timeout)
        stdout, returncode = recording.stdout, recording.returncode
        extra["trace.json"] = json.dumps(
            perfetto(recording.aligned, recording.origin_ns, f"{benchmark} run {index}"),
            separators=(",", ":"),
        ).encode()
        extra["alignment.json"] = json.dumps(
            {
                "error_bound_ns": recording.alignment_error_bound_ns,
                "mappings": {
                    domain: {
                        "readings": m.readings,
                        "slope": m.slope,
                        "max_uncertainty_ns": m.max_uncertainty_ns,
                        "max_residual_ns": m.max_residual_ns,
                    }
                    for domain, m in recording.mappings.items()
                },
                "samples": recording.counts(),
            },
            sort_keys=True,
        ).encode()
    else:
        proc = subprocess.run(command, capture_output=True, timeout=timeout)
        stdout, returncode = proc.stdout.decode(), proc.returncode
    finished = time.time_ns()
    after = _preflight()
    ok = returncode == 0
    return write_attempt(
        store,
        f"corpus-{benchmark}-{index:04d}",
        benchmark,
        revision,
        [json.loads(line) for line in stdout.splitlines()],
        binary_sha256=hashlib.sha256(WORKLOAD.read_bytes()).hexdigest(),
        started_ns=started,
        finished_ns=finished,
        preflight=(before, after),
        status="SUCCEEDED" if ok else "FAILED",
        status_reason="" if ok else f"exit:{returncode}",
        extra=extra,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=150)
    parser.add_argument("--store", type=Path, default=STORE)
    parser.add_argument(
        "--contention",
        action="store_true",
        help="start and stop CPU contention on the schedule in evaluation/contention.py",
    )
    parser.add_argument(
        "--benchmarks",
        nargs="+",
        default=["matmul", "periodic"],
        choices=sorted(METRICS),
        help="benchmarks to interleave at each index",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="record every run with all collectors and seal its aligned trace in the artifact",
    )
    args = parser.parse_args()
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    hogs = Hogs()
    log = args.store.parent / "contention.jsonl"
    try:
        for index in range(args.runs):
            if args.contention:
                _switch(hogs, under_contention(index), index, log)
            # Interleaved, so slow drift in the machine lands in both corpora alike.
            for benchmark in args.benchmarks:
                path = collect_one(benchmark, index, revision, args.store, args.trace)
                print(path, flush=True)
    finally:
        if hogs.running:
            _switch(hogs, False, args.runs, log)


def _switch(hogs: Hogs, want: bool, index: int, log: Path) -> None:
    if want == hogs.running:
        return
    if want:
        hogs.start()
        # Let every busy loop reach the scheduler before the first measured
        # run, so a burst's first run is contended like its last.
        time.sleep(1)
    else:
        hogs.stop()
    event = {"event": "start" if want else "stop", "before_index": index, "t_ns": time.time_ns()}
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
