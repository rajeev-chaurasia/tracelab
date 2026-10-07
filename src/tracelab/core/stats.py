"""Bootstrap intervals for the change between a baseline and a candidate.

The resampling is hierarchical: runs first, then samples within each chosen
run. Samples inside one process share a frequency state, a cache layout and a
scheduler, so they are not independent observations of the code. Pooling them
and resampling as if they were produces an interval that reflects only the
within-run spread and ignores the run-to-run drift that dominates on real
hardware. That interval is narrow, confident and wrong, and it is the failure
this module exists to avoid. `pooled` is kept only so the evaluation can show
the difference on the same data.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


class Mode(StrEnum):
    # A relative change for latencies and sizes, where 5% means the same thing
    # at 2 ms and at 200 ms.
    RELATIVE = "relative"
    # An absolute change for rates that sit near zero, where a relative change
    # from 0.001 to 0.002 would read as a 100% regression.
    ABSOLUTE = "absolute"


@dataclass(frozen=True)
class Statistic:
    """Mean, or a quantile in [0, 1]. Parsed from names like "p95" or "median"."""

    name: str
    quantile: float | None

    @classmethod
    def parse(cls, name: str) -> Statistic:
        if name == "mean":
            return cls(name, None)
        if name == "median":
            return cls(name, 0.5)
        if name == "max":
            return cls(name, 1.0)
        if name.startswith("p") and name[1:].replace(".", "", 1).isdigit():
            q = float(name[1:]) / 100
            if 0 < q < 1:
                return cls(name, q)
        raise ValueError(f"unknown statistic {name!r}")

    def of(self, values: FloatArray, axis: int | None = None, *, padded: bool = True) -> FloatArray:
        # The NaN-aware reductions are several times slower, and only padding
        # for runs of unequal length ever introduces a NaN.
        if self.quantile is None:
            mean = np.nanmean if padded else np.mean
            return np.asarray(mean(values, axis=axis), dtype=np.float64)
        quantile = np.nanquantile if padded else np.quantile
        return np.asarray(quantile(values, self.quantile, axis=axis), dtype=np.float64)


@dataclass(frozen=True)
class Estimate:
    baseline: float
    candidate: float
    # Signed so that positive always means worse, whatever the metric's
    # direction, so the decision rule never has to know which way is up.
    change: float
    low: float
    high: float
    mode: Mode


def _padded(runs: Sequence[Sequence[float]]) -> tuple[FloatArray, NDArray[np.int64]]:
    lengths = np.array([len(r) for r in runs], dtype=np.int64)
    if (lengths == 0).any():
        raise ValueError("every run needs at least one sample")
    grid = np.full((len(runs), int(lengths.max())), np.nan)
    for i, values in enumerate(runs):
        grid[i, : len(values)] = values
    return grid, lengths


def resample(
    runs: Sequence[Sequence[float]],
    statistic: Statistic,
    n_boot: int,
    rng: np.random.Generator,
) -> FloatArray:
    """Bootstrap distribution of the pooled statistic, resampling runs then samples.

    Returns one statistic per bootstrap replicate. Runs of unequal length are
    padded with NaN and the NaN-aware reductions skip the padding, so a run
    keeps its own sample count inside every replicate.
    """
    grid, lengths = _padded(runs)
    n_runs, width = grid.shape
    chosen = rng.integers(0, n_runs, size=(n_boot, n_runs))
    chosen_lengths = lengths[chosen]
    draws = rng.random((n_boot, n_runs, width))
    picks = np.floor(draws * chosen_lengths[..., None]).astype(np.int64)
    values = grid[chosen[..., None], picks]
    ragged = bool((lengths != width).any())
    if ragged:
        # A replicate takes as many samples from a run as that run actually had.
        values[np.arange(width)[None, None, :] >= chosen_lengths[..., None]] = np.nan
    return statistic.of(values.reshape(n_boot, n_runs * width), axis=1, padded=ragged)


def pooled(runs: Sequence[Sequence[float]]) -> list[list[float]]:
    """Merge every run into one, so resampling treats all samples as independent."""
    return [[v for r in runs for v in r]]


def compare(
    baseline: Sequence[Sequence[float]],
    candidate: Sequence[Sequence[float]],
    statistic: Statistic,
    *,
    higher_is_better: bool,
    mode: Mode,
    confidence: float,
    n_boot: int,
    rng: np.random.Generator,
) -> Estimate:
    base_obs = float(statistic.of(np.concatenate([np.asarray(r, float) for r in baseline])))
    cand_obs = float(statistic.of(np.concatenate([np.asarray(r, float) for r in candidate])))
    if mode is Mode.RELATIVE and base_obs <= 0:
        raise ValueError("a relative change needs a positive baseline; use absolute mode")

    base_boot = resample(baseline, statistic, n_boot, rng)
    cand_boot = resample(candidate, statistic, n_boot, rng)
    sign = -1.0 if higher_is_better else 1.0

    if mode is Mode.RELATIVE:
        point = sign * (cand_obs - base_obs) / base_obs
        boot = sign * (cand_boot - base_boot) / base_boot
    else:
        point = sign * (cand_obs - base_obs)
        boot = sign * (cand_boot - base_boot)

    tail = (1 - confidence) / 2
    low, high = np.quantile(boot, [tail, 1 - tail])
    return Estimate(
        baseline=base_obs,
        candidate=cand_obs,
        change=float(point),
        low=float(low),
        high=float(high),
        mode=mode,
    )


def run_noise(runs: Sequence[Sequence[float]], statistic: Statistic) -> float:
    """Robust coefficient of variation of the per-run statistic across runs.

    Median and MAD rather than mean and standard deviation, so one run that
    caught a background job does not by itself declare the whole baseline
    too noisy to use.
    """
    per_run = np.array([float(statistic.of(np.asarray(r, float))) for r in runs])
    center = float(np.median(per_run))
    if center == 0:
        return float("inf")
    mad = float(np.median(np.abs(per_run - center)))
    return 1.4826 * mad / abs(center)
