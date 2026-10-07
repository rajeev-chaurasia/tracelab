"""Each published evaluation, frozen with everything needed to reproduce it.

v1 is kept exactly as it came out, failure included. Its claim check is not
"the claim holds" but "the claim fails by exactly the published amount", so
the failure cannot be edited away and cannot drift without the validator
noticing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from evaluation.comparators import BASELINES, ONE_BATCH, TRACELAB, Comparator
from evaluation.contention import exposure


@dataclass(frozen=True)
class Version:
    name: str
    corpus: Path
    policies: Path
    out: Path
    confirmation_gap: int | None
    comparators: list[Comparator]
    # None means the claim must hold. A number means the published result is a
    # failure with exactly that many TraceLab false regressions, counted over
    # every case, or over the targeted windows for a contention corpus.
    pinned_false_regressions: int | None
    # Set for a corpus collected under scheduled contention. Each decision is
    # labelled with how its window was exposed, and the claim is checked on
    # the windows the schedule targeted rather than on all of them.
    exposure: Callable[[int], str] | None = None

    @property
    def manifest(self) -> Path:
        return self.out / "MANIFEST.sha256"


VERSIONS = {
    v.name: v
    for v in [
        Version(
            name="v1",
            corpus=Path("corpus/v1/store"),
            policies=Path("policies/v1"),
            out=Path("evidence/v1"),
            confirmation_gap=None,
            comparators=[TRACELAB, *BASELINES],
            pinned_false_regressions=10,
        ),
        Version(
            name="v2",
            corpus=Path("corpus/v2/store"),
            policies=Path("policies/v2"),
            out=Path("evidence/v2"),
            # About a minute of corpus time at the collection rate, chosen
            # before v2 was collected: a confirmation batch in a pipeline runs
            # after the first batch has been analysed, and a minute is also
            # the horizon of the load average that v1's failure was traced to.
            confirmation_gap=18,
            comparators=[TRACELAB, ONE_BATCH, *BASELINES],
            pinned_false_regressions=None,
        ),
        Version(
            name="v3",
            corpus=Path("corpus/v3/store"),
            # The v2 policies, unchanged: v3 tests the same method under
            # contention, not a new one.
            policies=Path("policies/v2"),
            out=Path("evidence/v3"),
            confirmation_gap=18,
            comparators=[TRACELAB, ONE_BATCH, *BASELINES],
            # Published as a failure: three false regressions on the windows
            # whose first batch alone was contended, where the pre-registered
            # claim was zero. docs/evidence.md traces each one.
            pinned_false_regressions=3,
            exposure=lambda start: exposure(start).value,
        ),
        Version(
            name="v4",
            corpus=Path("corpus/v4/store"),
            policies=Path("policies/v4"),
            out=Path("evidence/v4"),
            confirmation_gap=18,
            comparators=[TRACELAB, ONE_BATCH, *BASELINES],
            # Published as a failure: eleven false regressions, all in windows
            # whose two batches each caught a machine-wide slow run during a
            # disturbed stretch longer than the gap. docs/evidence.md traces them.
            pinned_false_regressions=11,
        ),
    ]
}
