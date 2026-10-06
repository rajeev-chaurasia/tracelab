"""Turn an interval into a verdict, and per-metric verdicts into one for the run.

A gate that answers only pass or fail has to round every uncertain case to one
of them. Rounding to fail is how performance gates get muted; rounding to pass
is how regressions ship. INCONCLUSIVE and INCOMPARABLE exist so that neither
rounding happens silently.
"""

from __future__ import annotations

import tomllib
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .stats import Estimate, Mode, Statistic


class Verdict(StrEnum):
    IMPROVEMENT = "IMPROVEMENT"
    PASS = "PASS"
    WARNING = "WARNING"
    REGRESSION = "REGRESSION"
    INCONCLUSIVE = "INCONCLUSIVE"
    INCOMPARABLE = "INCOMPARABLE"


class MetricClass(StrEnum):
    # Fails the run on regression.
    CRITICAL = "critical"
    # Warns on regression, fails only when the policy says guardrails block.
    GUARDRAIL = "guardrail"
    # Reported, never changes the run verdict.
    INFORMATIONAL = "informational"


class MetricPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metric: str
    statistic: str
    # Direction is not configured here. It comes from the metric declaration
    # in the run's spec, so a policy cannot disagree with the benchmark about
    # which way is better.
    threshold: float = Field(gt=0)
    mode: Mode = Mode.RELATIVE
    role: MetricClass = MetricClass.CRITICAL
    # Overrides the benchmark-wide limit, for metrics that are noisier by
    # nature, like a p99 over thirty samples.
    max_baseline_noise: float | None = None

    @field_validator("statistic")
    @classmethod
    def _known_statistic(cls, value: str) -> str:
        Statistic.parse(value)
        return value

    @property
    def label(self) -> str:
        return f"{self.metric}.{self.statistic}"


class JitterPolicy(BaseModel):
    """Derive deadline metrics from a periodic loop's observed intervals."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    # In the source metric's own unit, which the contract fixes per metric.
    period: float = Field(gt=0)
    # An interval longer than period * (1 + tolerance) is a missed deadline.
    tolerance: float = Field(ge=0)


class BenchmarkPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    benchmark: str
    baseline_window: int = Field(default=20, ge=1)
    min_baseline_runs: int = Field(default=5, ge=2)
    # One candidate run cannot show its own run-to-run spread, so its interval
    # would carry only within-run noise. Three is the least that can.
    min_candidate_runs: int = Field(default=3, ge=1)
    confidence: float = Field(default=0.95, gt=0, lt=1)
    n_boot: int = Field(default=2000, ge=100)
    max_baseline_noise: float = Field(default=0.05, gt=0)
    guardrails_block: bool = False
    jitter: JitterPolicy | None = None
    metrics: list[MetricPolicy] = Field(min_length=1)

    @classmethod
    def load(cls, path: Path) -> BenchmarkPolicy:
        with path.open("rb") as handle:
            return cls.model_validate(tomllib.load(handle))


def classify(estimate: Estimate, threshold: float) -> Verdict:
    """The decision table from PLAN.md section 4, in the same order."""
    lo, hi, change = estimate.low, estimate.high, estimate.change
    if -threshold < lo and hi < threshold:
        return Verdict.PASS
    if lo > 0:
        return Verdict.REGRESSION if change >= threshold else Verdict.WARNING
    if hi < 0:
        return Verdict.IMPROVEMENT if change <= -threshold else Verdict.PASS
    return Verdict.INCONCLUSIVE


def rollup(verdicts: list[tuple[MetricClass, Verdict]], guardrails_block: bool) -> Verdict:
    """One verdict for the run, from the strongest signal among its metrics."""
    blocking = {MetricClass.CRITICAL} | ({MetricClass.GUARDRAIL} if guardrails_block else set())
    counted = [(role, v) for role, v in verdicts if role is not MetricClass.INFORMATIONAL]

    if any(v is Verdict.INCOMPARABLE for _, v in counted):
        return Verdict.INCOMPARABLE
    if any(v is Verdict.REGRESSION and role in blocking for role, v in counted):
        return Verdict.REGRESSION
    if any(v is Verdict.INCONCLUSIVE and role is MetricClass.CRITICAL for role, v in counted):
        return Verdict.INCONCLUSIVE
    if any(v in {Verdict.REGRESSION, Verdict.WARNING} for _, v in counted):
        return Verdict.WARNING
    if any(v is Verdict.IMPROVEMENT and role is MetricClass.CRITICAL for role, v in counted):
        return Verdict.IMPROVEMENT
    return Verdict.PASS
