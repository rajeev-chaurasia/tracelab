# tracelab

Performance regression analysis for benchgrid's run artifacts, built so that
its central claim can be checked rather than taken on trust.

> On real benchmark runs, TraceLab returns REGRESSION only when two batches of
> a candidate, run apart, both move past a metric's practical threshold by more
> than the run-to-run noise the baseline itself shows.

A performance gate that fires on noise gets muted within a month, and a muted
gate is worse than none because people still believe it is watching. So the
evidence here is mostly about the other side of the promise: what happens when
the code did not change and the machine was simply noisy.

## The measured result

From `evidence/v2`, scored over 300 real runs of two workloads on an Apple M4
laptop. Every case is a window of real runs, either untouched (same code) or
with a known change injected into the candidate's samples. Five comparators
judge the identical cases with the identical policy metrics.

| comparator | false regressions | same-code false regressions | false improvements | regressions caught | inconclusive on a real regression |
| --- | ---: | ---: | ---: | ---: | ---: |
| **tracelab** | **0** | **0 / 72** | 6 | 212 / 252 | 37 |
| tracelab, one batch | 2 | 0 / 72 | 6 | 218 / 252 | 31 |
| pooled bootstrap | 9 | 2 / 72 | 18 | 230 / 252 | 9 |
| fixed 5%, twenty-run window | 88 | 3 / 72 | 80 | 219 / 252 | 0 |
| fixed 5%, previous run | 110 | 4 / 72 | 61 | 211 / 252 | 0 |

The rows below TraceLab are the point. The same windows, judged by the gates
people actually write, produce false regressions, including on same-code
windows. That is what makes TraceLab's zero mean something, and the validator
fails the build if the weaker comparators ever stop producing them.

Reproduce with `uv run python -m evaluation.score v2`, check with
`uv run python -m script.validate_evidence`. Full tables per case kind are in
[evidence/v2/summary.md](evidence/v2/summary.md).

## The first result failed, and is still published

v1 scored TraceLab at **ten false regressions**, five of them on same-code
windows of a periodic loop. Each was traced to the same cause: the candidate
runs in those windows were taken while the machine was busier than during the
baseline, partly because this repository's own test suite was running. The
statistics were right about the samples, and the samples cannot say why they
moved.

[v1's evidence](evidence/v1/summary.md) is kept exactly as it came out, and
the validator pins its failure at exactly ten so it can be neither edited away
nor allowed to drift. The response, confirmation batches, is recorded in
[ADR 0002](docs/adr/0002-confirmation-batches.md). The v2 policies were
committed before the v2 corpus was collected, which the history shows.

## How to read v2 honestly

- **v2 ran on a quieter machine than v1.** Run-to-run noise of the median was
  0.22% against 1.40%, because nothing was started from this repository during
  collection. The one-batch variant also raised no same-code false regression,
  so v2 does not exercise the load burst that confirmation exists for. Its
  measured contribution here is two sub-threshold over-calls removed. A test
  of confirmation under deliberately scheduled contention is the next
  experiment, not a result.
- **The p95 interval is overconfident with three candidate runs.** Six times
  TraceLab called a confident p95 improvement on code that did not change. An
  improvement blocks nothing, but the same overconfidence pointed the other
  way is a false regression, and confirmation is what stands between the two.
  See [docs/known-misses.md](docs/known-misses.md).
- **Caution costs catches.** The pooled bootstrap caught 18 more injected
  regressions than TraceLab. It also raised nine false ones. TraceLab's
  shortfall is mostly INCONCLUSIVE on changes confined to the tail of a noisy
  sub-millisecond kernel.

## How a verdict is reached

```
benchgrid run artifact (run.json, samples.jsonl, manifest.json)
   -> contract reader: reject anything that does not match the contract
   -> analysis view of the run, warmups dropped
   -> baseline: newest 20 SUCCEEDED, compatible runs of known-good revisions
   -> comparability: INCOMPARABLE before any statistics if the measurement differs
   -> per metric: noise gate, hierarchical bootstrap, decision table
   -> rollup by metric role, then confirmation by a second batch
   -> check report and exit code
```

**Runs before samples.** Samples inside one process share a frequency state, a
cache layout and a scheduler, so they are not independent observations of the
code. The bootstrap resamples runs first and samples within them second.
Pooling them instead gives an interval that ignores the run-to-run drift that
dominates on real hardware; the pooled bootstrap row above is that mistake.

**Six verdicts, not two.** With the change signed so that positive is worse, a
95% interval, and a practical threshold per metric:

| interval and point estimate | verdict |
| --- | --- |
| entirely inside plus or minus the threshold | PASS |
| above zero, point estimate at or past the threshold | REGRESSION |
| above zero, point estimate below the threshold | WARNING |
| below zero, point estimate at or past minus the threshold | IMPROVEMENT |
| spans zero and reaches past the threshold | INCONCLUSIVE |

Before any of that, a candidate measured differently from its baseline is
INCOMPARABLE, a baseline too noisy to judge is INCONCLUSIVE, and a FAILED or
INVALID run never takes part. None of those can become REGRESSION, and only
REGRESSION fails a CI step.

**What makes two runs comparable** is TraceLab's own definition, because
benchgrid's spec hash covers the revision and can never match across commits:
the spec without its `revision` and `artifacts` keys, plus hardware class,
architecture and whether the rig is emulated. Driver and kernel drift is
reported, not disqualifying. See [ADR 0001](docs/adr/0001-comparison-key.md).

## Using it

```
uv run tracelab compare <store> --policy policies/v2/matmul.toml \
    --candidate <revision> --known-good revisions.txt
```

`revisions.txt` is the known-good set, for example the output of
`git rev-list main`. The report names the metric that decided, its interval,
the baseline and what was left out of it and why. With confirmation on, a
first-batch regression asks for a second batch instead of failing the step.

## Layout

| path | what |
| --- | --- |
| `src/tracelab/core` | contract reader, statistics, decision rule, comparability, baseline selection; numpy and pydantic only, enforced by `script/check_layers.py` |
| `src/tracelab/ingest` | reads an artifact store, highest sealed attempt only |
| `src/tracelab/cli.py`, `report.py` | the command and the check it prints |
| `evaluation/` | workloads, collector, cases, comparators, scorer |
| `corpus/`, `policies/`, `evidence/` | each published version, frozen |

## Documents

- [PLAN.md](PLAN.md), with what changed from it and why
- [docs/evidence.md](docs/evidence.md): how the corpus was collected and how to check it
- [docs/known-misses.md](docs/known-misses.md): what this does not catch
- [docs/non-goals.md](docs/non-goals.md): what is deliberately absent, including any cloud warehouse, profiler or dashboard
- [docs/adr](docs/adr): the decisions, with their reasons
