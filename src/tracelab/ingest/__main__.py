"""Check every run in a store and print one JSON line per run.

benchgrid's CI points this at the store its writer produced. The exit status is
nonzero if any run was rejected, so the writer cannot drift from the reader
without a red build on its own side.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .store import read_store


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m tracelab.ingest <store>", file=sys.stderr)
        return 2
    failed = False
    for outcome in read_store(Path(argv[0])):
        line: dict[str, object] = {"run_id": outcome.run_id, "attempt": outcome.attempt}
        if outcome.artifact is not None:
            line["outcome"] = "accepted"
        elif outcome.rejection is not None:
            failed = True
            line["outcome"] = "rejected"
            line["code"] = str(outcome.rejection.code)
            line["detail"] = outcome.rejection.detail
        else:
            line["outcome"] = "no_valid_attempt"
        if outcome.ignored:
            failed = True
            line["ignored"] = {name: str(r.code) for name, r in outcome.ignored.items()}
        print(json.dumps(line, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
