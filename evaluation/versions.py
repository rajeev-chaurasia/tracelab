"""Each published evaluation, frozen with everything needed to reproduce it.

v1 is kept exactly as it came out, failure included. Its claim check is not
"the claim holds" but "the claim fails by exactly the published amount", so
the failure cannot be edited away and cannot drift without the validator
noticing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from evaluation.comparators import BASELINES, ONE_BATCH, TRACELAB, Comparator


@dataclass(frozen=True)
class Version:
    name: str
    corpus: Path
    policies: Path
    out: Path
    confirmation_gap: int | None
    comparators: list[Comparator]
    # None means the claim must hold. A number means the published result is a
    # failure with exactly that many TraceLab false regressions.
    pinned_false_regressions: int | None

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
    ]
}
