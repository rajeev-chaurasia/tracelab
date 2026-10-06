"""Reading runs out of a benchgrid artifact store laid out on a filesystem.

The store owns two rules the core cannot see from bytes alone: an attempt
without manifest.json does not exist, and only the highest sealed attempt of a
run is ever a result. If that highest attempt is rejected, the run has no
result. Falling back to a lower attempt would publish a number benchgrid itself
superseded, and would do it silently.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from tracelab.core.contract import MANIFEST, Code, Rejection, RunArtifact, validate_attempt

# Decimal with no padding, as the contract writes it. attempt-02 is not another
# spelling of attempt-2; two spellings would let one attempt appear twice.
ATTEMPT_DIR = re.compile(r"^attempt-([1-9][0-9]*)$")


@dataclass(frozen=True)
class RunOutcome:
    run_id: str
    attempt: int | None
    artifact: RunArtifact | None = None
    rejection: Rejection | None = None
    # Sealed directories that could not take part in selection at all, with why.
    ignored: dict[str, Rejection] = field(default_factory=dict)


def read_store(store: Path) -> Iterator[RunOutcome]:
    runs = store / "runs"
    if not runs.is_dir():
        return
    for run_dir in sorted(p for p in runs.iterdir() if p.is_dir()):
        outcome = read_run(run_dir)
        if outcome is not None:
            yield outcome


def read_run(run_dir: Path) -> RunOutcome | None:
    """None when the run has no sealed attempt, because the contract says such
    a directory must not be reported at all."""
    sealed: dict[int, Path] = {}
    ignored: dict[str, Rejection] = {}
    for child in sorted(run_dir.iterdir()):
        if not child.is_dir() or not (child / MANIFEST).is_file():
            continue
        match = ATTEMPT_DIR.match(child.name)
        if match is None:
            ignored[child.name] = Rejection(
                Code.ATTEMPT_DIR_NAME, f"{child.name} is not attempt-<n> with n unpadded"
            )
            continue
        sealed[int(match.group(1))] = child
    if not sealed:
        return None if not ignored else RunOutcome(run_dir.name, None, ignored=ignored)
    attempt = max(sealed)
    try:
        artifact = validate_attempt(run_dir.name, attempt, _read_files(sealed[attempt]))
    except Rejection as rejection:
        return RunOutcome(run_dir.name, attempt, rejection=rejection, ignored=ignored)
    return RunOutcome(run_dir.name, attempt, artifact=artifact, ignored=ignored)


def _read_files(attempt_dir: Path) -> dict[str, bytes]:
    return {
        path.relative_to(attempt_dir).as_posix(): path.read_bytes()
        for path in sorted(attempt_dir.rglob("*"))
        if path.is_file()
    }
