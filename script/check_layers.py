"""Fail the build if tracelab.core imports anything outside its allowance.

The core holds the statistics and the decision rule. Its value is that a reader
can test and reason about a verdict without a database or a web framework in
the way, and that property decays the first time someone reaches for a
convenience from the outer layers. So it is checked rather than trusted.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

CORE = Path("src/tracelab/core")
ALLOWED_THIRD_PARTY = {"numpy", "pydantic"}
STDLIB = sys.stdlib_module_names


def violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            # A relative import stays inside core by construction.
            if node.level > 0:
                continue
            names = [node.module or ""]
        else:
            continue
        for name in names:
            root = name.split(".")[0]
            inside_core = name == "tracelab.core" or name.startswith("tracelab.core.")
            if root in STDLIB or root in ALLOWED_THIRD_PARTY or inside_core:
                continue
            found.append(f"{path}:{node.lineno}: core imports {name}")
    return found


def main() -> int:
    failures = [line for path in sorted(CORE.rglob("*.py")) for line in violations(path)]
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("layers ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
