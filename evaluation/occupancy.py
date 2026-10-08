"""Summarise the Nsight Compute occupancy report recorded on the L4.

uv run python -m evaluation.occupancy evidence/traces/gpu-occupancy.csv
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

from tracelab.collect.nvidia import parse_ncu


def main(path: str) -> None:
    launches = parse_ncu(Path(path).read_text())
    out = Path(path).with_suffix(".json")
    out.write_text(json.dumps([asdict(o) for o in launches], indent=2) + "\n")
    for o in launches:
        print(
            f"{o.launch}  {o.kernel[:60]:60}  regs {o.registers_per_thread:5.0f}  "
            f"theoretical {o.theoretical_pct:6.2f}%  achieved {o.achieved_pct:6.2f}%  "
            f"limited by {o.limited_by}"
        )


if __name__ == "__main__":
    main(sys.argv[1])
