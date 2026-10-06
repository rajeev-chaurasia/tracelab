"""The conformance fixtures, run through the real reader.

benchgrid's CI runs its writer against these. They are only worth anything if
each rejecting case is rejected for its own reason, so the code is asserted,
not just the fact of rejection.
"""

import json
from pathlib import Path

import pytest

from script import make_fixtures
from tracelab.core.contract import Code
from tracelab.ingest.store import read_store

ROOT = Path(__file__).parent / "fixtures" / "run-artifact-v1"
CASES = sorted(p for p in ROOT.iterdir() if p.is_dir())


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_case(case: Path) -> None:
    expect = json.loads((case / "expect.json").read_text())

    outcomes = list(read_store(case / "store"))

    if expect["outcome"] == "absent":
        assert outcomes == []
        return
    [outcome] = outcomes
    assert outcome.attempt == expect["attempt"]
    assert {k: str(r.code) for k, r in outcome.ignored.items()} == expect["ignored"]
    if expect["outcome"] == "accepted":
        assert outcome.rejection is None, outcome.rejection
        assert outcome.artifact is not None
    elif expect["outcome"] == "rejected":
        assert outcome.rejection is not None
        assert outcome.rejection.code == expect["code"], outcome.rejection.detail
    else:
        assert expect["outcome"] == "no_valid_attempt"
        assert outcome.artifact is None and outcome.rejection is None


def test_every_rejection_code_has_a_fixture() -> None:
    seen: set[str] = set()
    for case in CASES:
        expect = json.loads((case / "expect.json").read_text())
        seen.update(filter(None, [expect["code"], *expect["ignored"].values()]))

    assert seen == {str(c) for c in Code}


def test_committed_fixtures_are_exactly_what_the_generator_writes(tmp_path: Path) -> None:
    make_fixtures.write(tmp_path)

    def tree(root: Path) -> dict[str, bytes]:
        return {
            p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()
        }

    assert tree(tmp_path) == tree(ROOT)
