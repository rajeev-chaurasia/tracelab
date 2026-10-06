"""Write the RunResult JSON Schema that BenchGrid is built against.

The file is committed and a test fails when it drifts from the model, so a
change to the contract is a reviewed diff in both repositories rather than a
silent break discovered at ingest.
"""

from __future__ import annotations

import json
from pathlib import Path

from tracelab.core.schema import RunResult

CONTRACT = Path("contract/run_result.schema.json")


def render() -> str:
    return json.dumps(RunResult.model_json_schema(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    CONTRACT.write_text(render(), encoding="utf-8")
    print(f"wrote {CONTRACT}")
