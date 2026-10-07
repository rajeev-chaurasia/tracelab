"""Render a comparison as the check a developer reads on their pull request.

"Benchmark failed" sends someone to rerun it. What they need instead is which
metric moved, by how much, how sure the engine is, against what baseline, on
what hardware, and what was left out and why.
"""

from __future__ import annotations

from tracelab.core.compare import Comparison, MetricResult
from tracelab.core.policy import Verdict
from tracelab.core.run import Run
from tracelab.core.stats import Mode

HEADINGS = {
    Verdict.REGRESSION: "PERFORMANCE REGRESSION",
    Verdict.WARNING: "Performance warning",
    Verdict.INCONCLUSIVE: "Inconclusive: no decision either way",
    Verdict.INCOMPARABLE: "Incomparable: the baseline measured something else",
    Verdict.IMPROVEMENT: "Performance improvement",
    Verdict.PASS: "No performance change past threshold",
}

# A GitHub check conclusion per verdict. INCONCLUSIVE and INCOMPARABLE are
# neutral rather than failures: a gate that blocks on its own uncertainty is
# the gate that gets disabled.
CONCLUSIONS = {
    Verdict.REGRESSION: "failure",
    Verdict.WARNING: "neutral",
    Verdict.INCONCLUSIVE: "neutral",
    Verdict.INCOMPARABLE: "neutral",
    Verdict.IMPROVEMENT: "success",
    Verdict.PASS: "success",
}


def value(unit: str, x: float) -> str:
    if unit == "ns":
        for scale, suffix in ((1e9, "s"), (1e6, "ms"), (1e3, "us")):
            if abs(x) >= scale:
                return f"{x / scale:.3f} {suffix}"
        return f"{x:.0f} ns"
    if unit == "bytes":
        for scale, suffix in ((2**30, "GiB"), (2**20, "MiB"), (2**10, "KiB")):
            if abs(x) >= scale:
                return f"{x / scale:.2f} {suffix}"
        return f"{x:.0f} B"
    if unit == "ratio":
        return f"{x:.2%}"
    return f"{x:.4g} {unit}"


def _change(m: MetricResult) -> tuple[str, str]:
    e = m.estimate
    if e is None:
        return "", ""
    if e.mode is Mode.RELATIVE:
        return f"{e.change:+.1%}", f"[{e.low:+.1%}, {e.high:+.1%}]"
    return f"{e.change:+.4f}", f"[{e.low:+.4f}, {e.high:+.4f}]"


def render(benchmark: str, result: Comparison, candidates: list[Run]) -> str:
    units = {name: s.unit for r in candidates for name, s in r.metrics.items()}
    lines = [f"## {HEADINGS[result.verdict]}: {benchmark}", "", result.reason, ""]
    if result.metrics:
        lines += [
            "| metric | role | baseline | candidate | change, + is worse | 95% interval "
            "| verdict |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
        for m in result.metrics:
            unit = units.get(m.policy.metric, "ratio")
            base = value(unit, m.estimate.baseline) if m.estimate else ""
            cand = value(unit, m.estimate.candidate) if m.estimate else ""
            change, interval = _change(m)
            lines.append(
                f"| {m.policy.label} | {m.policy.role.value} | {base} | {cand} | {change} "
                f"| {interval} | {m.verdict.value} |"
            )
            if m.estimate is None:
                lines.append(f"| | | | | | | {m.reason} |")
        lines.append("")
    if candidates:
        rig = candidates[0].rig
        kind = "emulated" if rig.emulated else "physical"
        lines.append(f"Hardware: {rig.hardware_class} ({rig.arch}, {kind})")
    lines.append(
        f"Candidate runs: {len(result.candidate_runs)}. Baseline runs: {len(result.baseline_runs)}."
    )
    if result.excluded:
        left_out = ", ".join(f"{n} {why}" for why, n in sorted(result.excluded.items()))
        lines.append(f"Left out of the baseline: {left_out}.")
    if result.drift:
        lines.append("Environment drift, reported but not disqualifying:")
        lines += [f"- {d}" for d in result.drift]
    return "\n".join(lines) + "\n"
