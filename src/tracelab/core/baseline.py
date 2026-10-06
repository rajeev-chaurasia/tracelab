"""Choose what a candidate is compared against.

Comparing against the previous run means comparing against one draw from the
noise, and inheriting whatever that run happened to catch. The baseline is
instead the most recent healthy, compatible runs of the baseline branch, so a
candidate is measured against the machine's ordinary behaviour rather than
against its last mood.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from .compat import mismatches
from .policy import BenchmarkPolicy
from .schema import RunResult, RunStatus


@dataclass(frozen=True)
class Selection:
    runs: list[RunResult]
    # Why each history run was left out, counted, so a thin baseline explains
    # itself instead of just being thin.
    excluded: Counter[str] = field(default_factory=Counter)

    @property
    def disqualified_by_environment(self) -> int:
        return sum(n for reason, n in self.excluded.items() if reason.startswith("environment"))


def select(
    candidates: list[RunResult], history: Iterable[RunResult], policy: BenchmarkPolicy
) -> Selection:
    reference = candidates[0]
    candidate_shas = {c.git_sha for c in candidates}
    earliest = min(c.started_at for c in candidates)
    excluded: Counter[str] = Counter()
    eligible: list[RunResult] = []

    for run in history:
        if run.benchmark != reference.benchmark or run.scenario != reference.scenario:
            continue
        if run.branch != policy.baseline_branch:
            excluded["not on the baseline branch"] += 1
        elif run.git_sha in candidate_shas:
            excluded["same revision as the candidate"] += 1
        elif run.status is not RunStatus.SUCCEEDED:
            excluded[f"status {run.status.value}"] += 1
        elif not run.healthy:
            excluded["marked unhealthy by the rig"] += 1
        elif run.started_at >= earliest:
            # A baseline from after the candidate would let a later fix hide a
            # regression when an old commit is re-evaluated.
            excluded["started after the candidate"] += 1
        else:
            blocking = [m for m in mismatches(reference, run) if m.disqualifying]
            if blocking:
                excluded[f"environment: {blocking[0].field} differs"] += 1
            else:
                eligible.append(run)

    eligible.sort(key=lambda r: r.started_at, reverse=True)
    kept = eligible[: policy.baseline_window]
    if len(eligible) > len(kept):
        excluded["older than the baseline window"] += len(eligible) - len(kept)
    return Selection(runs=kept, excluded=excluded)
