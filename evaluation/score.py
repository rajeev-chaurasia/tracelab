"""Score every case with every comparator and publish every decision.

The summary is computed from decisions.jsonl and nothing else, so the
validator can recompute it from the same file and reject a summary that was
edited, and can rerun every row from the corpus and reject a decision that was.

    uv run python -m evaluation.score v2
"""

from __future__ import annotations

import hashlib
import json
import sys
import zlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from evaluation.cases import Case, cases
from evaluation.comparators import Comparator
from evaluation.versions import VERSIONS, Version
from tracelab.core.compare import Comparison
from tracelab.core.policy import BenchmarkPolicy, Verdict
from tracelab.core.run import Run, from_artifact
from tracelab.core.stats import Statistic, run_noise
from tracelab.ingest.store import read_store


def manifest_lines(version: Version) -> list[str]:
    files = sorted(
        [*version.corpus.rglob("*"), *version.out.glob("*"), *version.policies.glob("*.toml")]
    )
    return [
        f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.as_posix()}"
        for f in files
        if f.is_file() and f != version.manifest
    ]


def load_corpus(store: Path) -> dict[str, list[Run]]:
    corpus: dict[str, list[Run]] = defaultdict(list)
    for outcome in read_store(store):
        if outcome.artifact is None:
            raise SystemExit(f"corpus run {outcome.run_id} was not accepted: {outcome.rejection}")
        run = from_artifact(outcome.artifact)
        corpus[run.benchmark].append(run)
    return {name: sorted(runs, key=lambda r: r.started_at) for name, runs in corpus.items()}


def load_policies(directory: Path) -> dict[str, BenchmarkPolicy]:
    policies = [BenchmarkPolicy.load(p) for p in sorted(directory.glob("*.toml"))]
    return {p.benchmark: p for p in policies}


def seed_for(case_id: str) -> int:
    return zlib.crc32(case_id.encode())


def decide(case: Case, comparator: Comparator, policy: BenchmarkPolicy) -> dict[str, Any]:
    result: Comparison = comparator.run(case, policy, seed_for(case.case_id))
    row: dict[str, Any] = {
        "case_id": case.case_id,
        "benchmark": case.kind.benchmark,
        "kind": case.kind.name,
        "start": case.start,
        "allowed": sorted(v.value for v in case.kind.allowed),
        "regression": case.kind.regression,
        "comparator": comparator.name,
        "verdict": result.verdict.value,
        "correct": result.verdict in case.kind.allowed,
        "reason": result.reason,
        "metrics": [
            {
                "label": m.policy.label,
                "verdict": m.verdict.value,
                "change": m.estimate.change if m.estimate else None,
                "low": m.estimate.low if m.estimate else None,
                "high": m.estimate.high if m.estimate else None,
                "baseline_noise": m.baseline_noise,
            }
            for m in result.metrics
        ],
        "baseline_runs": [r.run_id for r in case.baseline],
        "candidate_runs": [r.run_id for r in case.candidates],
    }
    # Only versions with a confirmation step carry these, so v1's published
    # rows keep the exact shape they were published in.
    if case.confirmation is not None:
        row["confirmation_runs"] = [r.run_id for r in case.confirmation]
        row["confirmed_by"] = result.confirmation.verdict.value if result.confirmation else None
    return row


def decisions_for(version: Version) -> list[dict[str, Any]]:
    corpus = load_corpus(version.corpus)
    policies = load_policies(version.policies)
    return [
        decide(case, comparator, policies[case.kind.benchmark])
        for case in cases(corpus, version.confirmation_gap)
        for comparator in version.comparators
    ]


def summarize(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Every published number, derived from the decisions alone."""
    out: dict[str, Any] = {"comparators": {}}
    for name in sorted({d["comparator"] for d in decisions}):
        rows = [d for d in decisions if d["comparator"] == name]
        by_kind: dict[str, Counter[str]] = defaultdict(Counter)
        for d in rows:
            by_kind[f"{d['benchmark']}/{d['kind']}"][d["verdict"]] += 1
        false_alarms = [
            d for d in rows if d["verdict"] == "REGRESSION" and "REGRESSION" not in d["allowed"]
        ]
        # An improvement called on code that did not get faster blocks nothing,
        # but it is the same overconfidence as a false regression pointed the
        # other way, so it is counted where a reader will see it.
        false_improvements = [
            d for d in rows if d["verdict"] == "IMPROVEMENT" and "IMPROVEMENT" not in d["allowed"]
        ]
        must_catch = [d for d in rows if d["allowed"] == ["REGRESSION"]]
        same_code = [d for d in rows if d["kind"] == "same_code"]
        out["comparators"][name] = {
            "cases": len(rows),
            "correct": sum(d["correct"] for d in rows),
            "false_regressions": len(false_alarms),
            "false_improvements": len(false_improvements),
            "same_code_cases": len(same_code),
            "same_code_false_regressions": sum(d["verdict"] == "REGRESSION" for d in same_code),
            "regressions_to_catch": len(must_catch),
            "regressions_caught": sum(d["verdict"] == "REGRESSION" for d in must_catch),
            "regressions_inconclusive": sum(d["verdict"] == "INCONCLUSIVE" for d in must_catch),
            "by_kind": {k: dict(sorted(v.items())) for k, v in sorted(by_kind.items())},
        }
    return out


def describe_corpus(corpus: dict[str, list[Run]]) -> dict[str, Any]:
    """How noisy the machine actually was, so the claim's premise can be checked."""
    metric = {"matmul": "iteration_latency", "periodic": "tick_interval"}
    median = Statistic.parse("median")
    out: dict[str, Any] = {}
    for name, runs in sorted(corpus.items()):
        values = [r.metrics[metric[name]].values for r in runs]
        within = [float(np.std(v, ddof=1) / np.mean(v)) for v in values]
        out[name] = {
            "runs": len(runs),
            "samples_per_run": sorted({len(v) for v in values}),
            "first_started": runs[0].started_at.isoformat(),
            "last_started": runs[-1].started_at.isoformat(),
            "metric": metric[name],
            "between_run_noise_of_median": run_noise(values, median),
            "median_within_run_cv": float(np.median(within)),
        }
    return out


def render(summary: dict[str, Any], corpus: dict[str, Any]) -> str:
    lines = ["# Evaluation summary", "", "Generated by `evaluation/score.py`. Do not edit.", ""]
    lines += [
        "## Corpus",
        "",
        "| benchmark | runs | between-run noise of the median | median within-run CV |",
    ]
    lines += ["| --- | ---: | ---: | ---: |"]
    for name, c in corpus.items():
        lines.append(
            f"| {name} | {c['runs']} | {c['between_run_noise_of_median']:.2%} "
            f"| {c['median_within_run_cv']:.2%} |"
        )
    lines += ["", "## Headline", ""]
    lines += [
        "| comparator | correct | false regressions | same-code false regressions "
        "| false improvements | regressions caught | regressions inconclusive |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, s in summary["comparators"].items():
        lines.append(
            f"| {name} | {s['correct']}/{s['cases']} | {s['false_regressions']} "
            f"| {s['same_code_false_regressions']}/{s['same_code_cases']} "
            f"| {s['false_improvements']} "
            f"| {s['regressions_caught']}/{s['regressions_to_catch']} "
            f"| {s['regressions_inconclusive']} |"
        )
    verdicts = [v.value for v in Verdict]
    for name, s in summary["comparators"].items():
        lines += ["", f"## {name}", "", "| case kind | " + " | ".join(verdicts) + " |"]
        lines.append("| --- |" + " ---: |" * len(verdicts))
        for kind, counts in s["by_kind"].items():
            lines.append(
                f"| {kind} | " + " | ".join(str(counts.get(v, 0)) for v in verdicts) + " |"
            )
    return "\n".join(lines) + "\n"


def main(name: str) -> None:
    version = VERSIONS[name]
    decisions = decisions_for(version)
    summary = summarize(decisions)
    described = describe_corpus(load_corpus(version.corpus))
    version.out.mkdir(parents=True, exist_ok=True)
    with (version.out / "decisions.jsonl").open("w", encoding="utf-8") as handle:
        for d in decisions:
            handle.write(json.dumps(d, sort_keys=True) + "\n")
    (version.out / "summary.json").write_text(
        json.dumps({"corpus": described, **summary}, indent=2, sort_keys=True) + "\n"
    )
    (version.out / "summary.md").write_text(render(summary, described))
    version.manifest.write_text("\n".join(manifest_lines(version)) + "\n")
    print((version.out / "summary.md").read_text())


if __name__ == "__main__":
    main(sys.argv[1])
