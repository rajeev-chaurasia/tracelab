"""Recompute every published evaluation number and check each claim against it.

Four kinds of check per published version, in the order a skeptic would want:

1. The corpus, policies and results match that version's sha256 manifest.
2. Every decision is reproduced by rerunning its comparator on the corpus.
   The bootstrap is seeded per case, so a rerun agrees exactly or something
   was edited.
3. summary.json and summary.md are recomputed from decisions.jsonl and must
   match byte for byte.
4. The claim. For a version collected under scheduled contention: no TraceLab
   false regression on the windows whose first batch alone was contended, and
   at least one from the one-batch engine there. For a version whose claim
   must hold: TraceLab raises no false
   regression, and at least one weaker comparator raises one on a same-code
   case, because a corpus too quiet to fool anybody makes a clean row
   meaningless. For a version published as a failure: TraceLab's false
   regressions equal the published count exactly, so the failure can neither
   be edited away nor drift.

    uv run python -m script.validate_evidence            # every version
    uv run python -m script.validate_evidence v1 v2
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from evaluation import score
from evaluation.versions import VERSIONS, Version


def check_manifest(version: Version) -> list[str]:
    if not version.manifest.exists():
        return [f"{version.manifest} is missing"]
    recorded = set(version.manifest.read_text().splitlines())
    actual = set(score.manifest_lines(version))
    errors = [f"not in manifest or changed: {line}" for line in sorted(actual - recorded)]
    errors += [f"in manifest, absent or changed: {line}" for line in sorted(recorded - actual)]
    return errors


def check_decisions(version: Version, published: list[dict[str, Any]]) -> list[str]:
    rerun = score.decisions_for(version)
    if len(rerun) != len(published):
        return [f"{len(published)} decisions published, rerun produced {len(rerun)}"]
    return [
        f"{theirs['case_id']} {theirs['comparator']}: rerun disagrees"
        for mine, theirs in zip(rerun, published, strict=True)
        if mine != theirs
    ]


def check_summary(version: Version, published: list[dict[str, Any]]) -> list[str]:
    corpus = score.describe_corpus(score.load_corpus(version.corpus))
    summary = score.summarize(published)
    errors = []
    expected_json = json.dumps({"corpus": corpus, **summary}, indent=2, sort_keys=True) + "\n"
    if (version.out / "summary.json").read_text() != expected_json:
        errors.append("summary.json does not match the decisions")
    if (version.out / "summary.md").read_text() != score.render(summary, corpus):
        errors.append("summary.md does not match the decisions")
    return errors


def check_contention_claim(
    published: list[dict[str, Any]],
    pinned: int | None = None,
    target: str = "short_burst_first_batch",
    control: str = "tracelab_one_batch",
) -> list[str]:
    """A contention corpus's pre-registered claim, on the windows it targeted.

    TraceLab must raise no false regression on the targeted windows, and the
    control comparator, the engine without the defence under test, must raise
    at least one there, or the contention was too weak to test anything. v3
    targets first batches alone against the one-batch engine; v6 targets both
    batches against the engine without its environment checks.
    """
    summary = score.summarize(published)["comparators"]
    errors = []
    false = summary["tracelab"]["by_exposure"][target]["false_regressions"]
    if pinned is not None:
        if false != pinned:
            errors.append(
                f"published as {pinned} false regressions on {target}, decisions show {false}"
            )
    elif false != 0:
        errors.append(f"claim fails: tracelab raised {false} false regressions on {target}")
    fooled = summary.get(control, {}).get("by_exposure", {})
    if fooled.get(target, {}).get("false_regressions", 0) == 0:
        errors.append(
            f"negative control fails: {control} raised no false regression on "
            f"{target}, so the contention did not test the defence"
        )
    return errors


def check_claim(
    published: list[dict[str, Any]],
    pinned: int | None = None,
    target: str | None = None,
    control: str | None = None,
) -> list[str]:
    if any("exposure" in d for d in published):
        return check_contention_claim(
            published,
            pinned,
            target or "short_burst_first_batch",
            control or "tracelab_one_batch",
        )
    summary = score.summarize(published)["comparators"]
    false = summary["tracelab"]["false_regressions"]
    if pinned is not None:
        if false != pinned:
            return [f"published as {pinned} false regressions, decisions show {false}"]
        return []
    errors = []
    if false != 0:
        errors.append(f"claim fails: tracelab raised {false} false regressions")
    if control is not None:
        # The defence under test has to have had something to defend against.
        if summary.get(control, {}).get("false_regressions", 0) == 0:
            errors.append(
                f"negative control fails: {control} raised no false regression, so "
                "nothing in the corpus tested the defence"
            )
        return errors
    weaker = {k: v for k, v in summary.items() if not k.startswith("tracelab")}
    if not any(v["same_code_false_regressions"] > 0 for v in weaker.values()):
        errors.append(
            "negative control fails: no weaker comparator raised a false regression on a "
            "same-code case, so the corpus is too quiet to support the claim"
        )
    return errors


def validate(version: Version) -> bool:
    published = [
        json.loads(line) for line in (version.out / "decisions.jsonl").read_text().splitlines()
    ]
    checks: list[tuple[str, Callable[[], list[str]]]] = [
        ("manifest", lambda: check_manifest(version)),
        ("decisions", lambda: check_decisions(version, published)),
        ("summary", lambda: check_summary(version, published)),
        (
            "claim",
            lambda: check_claim(
                published, version.pinned_false_regressions, version.claim_target, version.control
            ),
        ),
    ]
    failed = False
    for name, check in checks:
        errors = check()
        print(f"{version.name}  {'ok' if not errors else 'FAILED':7} {name}", flush=True)
        for error in errors[:20]:
            print(f"        {error}")
        failed = failed or bool(errors)
    return not failed


def main(names: list[str]) -> int:
    chosen = [VERSIONS[n] for n in names] if names else list(VERSIONS.values())
    # Every defined version must be published. Skipping one with no decisions
    # would let a published failure be erased by deleting a single file.
    missing = [v.name for v in chosen if not (v.out / "decisions.jsonl").exists()]
    for name in missing:
        print(f"{name}  FAILED  decisions.jsonl is missing")
    results = [validate(v) for v in chosen if v.name not in missing]
    return 0 if all(results) and not missing else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
