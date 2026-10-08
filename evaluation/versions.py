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

from evaluation.comparators import BASELINES, NO_ENVIRONMENT, ONE_BATCH, TRACELAB, Comparator
from evaluation.contention import LONG_SCHEDULE, exposure


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
    # Which other benchmark serves as each benchmark's canary.
    canaries: dict[str, str] | None = None
    # The claim's scope: the exposure label it is checked on, if any, and the
    # comparator that has to be fooled there for the check to mean anything.
    claim_target: str | None = None
    control: str | None = None

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
            claim_target="short_burst_first_batch",
            control="tracelab_one_batch",
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
        Version(
            name="v5",
            corpus=Path("corpus/v5/store"),
            policies=Path("policies/v5"),
            out=Path("evidence/v5"),
            confirmation_gap=18,
            comparators=[TRACELAB, ONE_BATCH, *BASELINES],
            # Published as a failure: three real 2% slowdowns called past the 3%
            # threshold in the earliest windows, while the L4 was still heating
            # and its clock settling. Its negative control also fails, which the
            # pin does not hide: docs/evidence.md says the corpus cannot tell
            # the methods apart.
            pinned_false_regressions=3,
        ),
        Version(
            name="v6",
            corpus=Path("corpus/v6/store"),
            policies=Path("policies/v6"),
            out=Path("evidence/v6"),
            confirmation_gap=18,
            comparators=[TRACELAB, NO_ENVIRONMENT, ONE_BATCH, *BASELINES],
            # Published as a failure: nine false regressions on the targeted
            # windows, every one a network candidate whose memory-bandwidth
            # canary did not feel the CPU contention that halved network
            # throughput. docs/evidence.md traces them.
            pinned_false_regressions=9,
            exposure=lambda start: exposure(start, schedule=LONG_SCHEDULE).value,
            canaries={"membw": "network", "network": "membw"},
            claim_target="long_burst_both_batches",
            control="tracelab_no_environment",
        ),
        Version(
            name="v7",
            corpus=Path("corpus/v7/store"),
            policies=Path("policies/v7"),
            out=Path("evidence/v7"),
            confirmation_gap=18,
            comparators=[TRACELAB, NO_ENVIRONMENT, ONE_BATCH, *BASELINES],
            # Published as a failure: two real 2% slowdowns called past 3%
            # where the clock drifted 1.1 to 1.7%, inside the 2% limit, while
            # throughput drifted with it. The check removed the warm-up
            # windows' others; docs/evidence.md has both halves.
            pinned_false_regressions=2,
            control="tracelab_no_environment",
        ),
    ]
}
