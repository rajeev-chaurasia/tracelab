"""Recompute every published evaluation number and check the claim against it.

Four kinds of check, in the order a skeptic would want them:

1. The corpus, policies and results match the sha256 manifest.
2. Every decision is reproduced by rerunning its comparator on the corpus.
   The bootstrap is seeded per case, so a rerun agrees exactly or something
   was edited.
3. summary.json and summary.md are recomputed from decisions.jsonl and must
   match byte for byte.
4. The claim holds, and so does the negative control: at least one weaker
   comparator must raise a false regression on a same-code case. If none
   does, the corpus is too quiet to fool anybody and a clean TraceLab row
   would mean nothing.

    uv run python script/validate_evidence.py
"""

from __future__ import annotations

import json
import sys
from typing import Any

from evaluation import score
from evaluation.cases import cases
from evaluation.comparators import COMPARATORS


def check_manifest() -> list[str]:
    if not score.MANIFEST.exists():
        return [f"{score.MANIFEST} is missing"]
    recorded = score.MANIFEST.read_text().splitlines()
    actual = score.manifest_lines()
    errors = [f"not in manifest or changed: {line}" for line in sorted(set(actual) - set(recorded))]
    errors += [
        f"in manifest but absent or changed: {line}" for line in sorted(set(recorded) - set(actual))
    ]
    return errors


def check_decisions(published: list[dict[str, Any]]) -> list[str]:
    corpus, policies = score.load_corpus(), score.load_policies()
    rerun = [
        score.decide(case, comparator, policies[case.kind.benchmark])
        for case in cases(corpus)
        for comparator in COMPARATORS
    ]
    if len(rerun) != len(published):
        return [f"{len(published)} decisions published, rerun produced {len(rerun)}"]
    errors = []
    for mine, theirs in zip(rerun, published, strict=True):
        if mine != theirs:
            errors.append(f"{theirs['case_id']} {theirs['comparator']}: rerun disagrees")
    return errors


def check_summary(published: list[dict[str, Any]]) -> list[str]:
    corpus = score.describe_corpus(score.load_corpus())
    summary = score.summarize(published)
    errors = []
    expected_json = json.dumps({"corpus": corpus, **summary}, indent=2, sort_keys=True) + "\n"
    if (score.OUT / "summary.json").read_text() != expected_json:
        errors.append("summary.json does not match the decisions")
    if (score.OUT / "summary.md").read_text() != score.render(summary, corpus):
        errors.append("summary.md does not match the decisions")
    return errors


def check_claim(published: list[dict[str, Any]]) -> list[str]:
    summary = score.summarize(published)["comparators"]
    errors = []
    false = summary["tracelab"]["false_regressions"]
    if false != 0:
        errors.append(f"claim fails: tracelab raised {false} false regressions")
    weaker = {k: v for k, v in summary.items() if k != "tracelab"}
    if not any(v["same_code_false_regressions"] > 0 for v in weaker.values()):
        errors.append(
            "negative control fails: no weaker comparator raised a false regression on a "
            "same-code case, so the corpus is too quiet to support the claim"
        )
    return errors


def main() -> int:
    published = [
        json.loads(line) for line in (score.OUT / "decisions.jsonl").read_text().splitlines()
    ]
    failed = False
    for name, check in (
        ("manifest", check_manifest),
        ("decisions", lambda: check_decisions(published)),
        ("summary", lambda: check_summary(published)),
        ("claim", lambda: check_claim(published)),
    ):
        errors = check()
        print(f"{'ok' if not errors else 'FAILED':7} {name}")
        for error in errors[:20]:
            print(f"        {error}")
        failed = failed or bool(errors)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
