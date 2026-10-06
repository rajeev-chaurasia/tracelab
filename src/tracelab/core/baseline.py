"""Choose what a candidate is compared against.

Comparing against the previous run means comparing against one draw from the
noise, and inheriting whatever that run happened to catch. The baseline is
instead the most recent succeeded, compatible runs of known-good revisions, so
a candidate is measured against the machine's ordinary behaviour rather than
against its last mood.

Which revisions are known good is a fact about git history, not about a run,
and the run artifact rightly does not carry a branch. The caller supplies the
set, typically from `git rev-list` on the baseline branch.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field

from .compat import blocking
from .policy import BenchmarkPolicy
from .run import Run


@dataclass(frozen=True)
class Selection:
    runs: list[Run]
    # Why each history run was left out, counted, so a thin baseline explains
    # itself instead of just being thin.
    excluded: Counter[str] = field(default_factory=Counter)

    @property
    def disqualified_by_environment(self) -> int:
        return sum(n for reason, n in self.excluded.items() if reason.startswith("environment"))


def select(
    candidates: list[Run],
    history: Iterable[Run],
    policy: BenchmarkPolicy,
    baseline_revisions: Collection[str],
) -> Selection:
    reference = candidates[0]
    candidate_revisions = {c.revision for c in candidates}
    earliest = min(c.started_at for c in candidates)
    excluded: Counter[str] = Counter()
    eligible: list[Run] = []

    for run in history:
        if run.benchmark != reference.benchmark:
            continue
        if run.revision in candidate_revisions:
            excluded["same revision as the candidate"] += 1
        elif run.revision not in baseline_revisions:
            excluded["revision not known good"] += 1
        elif run.status != "SUCCEEDED":
            # FAILED and INVALID runs are accepted artifacts, kept as evidence
            # of what happened. The contract forbids them as baseline inputs.
            excluded[f"status {run.status}"] += 1
        elif run.started_at >= earliest:
            # A baseline from after the candidate would let a later fix hide a
            # regression when an old commit is re-evaluated.
            excluded["started after the candidate"] += 1
        elif (mismatch := blocking(reference, run)) is not None:
            excluded[f"environment: {mismatch.field} differs"] += 1
        else:
            eligible.append(run)

    eligible.sort(key=lambda r: r.started_at, reverse=True)
    kept = eligible[: policy.baseline_window]
    if len(eligible) > len(kept):
        excluded["older than the baseline window"] += len(eligible) - len(kept)
    return Selection(runs=kept, excluded=excluded)
