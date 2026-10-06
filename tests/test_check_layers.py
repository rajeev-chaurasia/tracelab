from pathlib import Path

from script.check_layers import violations


def test_flags_an_outer_dependency(tmp_path: Path) -> None:
    source = tmp_path / "leaky.py"
    source.write_text("import duckdb\nfrom tracelab.store import lake\n")

    found = violations(source)

    assert len(found) == 2
    assert "duckdb" in found[0]
    assert "tracelab.store" in found[1]


def test_allows_stdlib_numpy_pydantic_and_core(tmp_path: Path) -> None:
    source = tmp_path / "clean.py"
    source.write_text(
        "import math\nimport numpy as np\nfrom pydantic import BaseModel\n"
        "from tracelab.core.stats import x\nfrom .schema import y\n"
    )

    assert violations(source) == []
