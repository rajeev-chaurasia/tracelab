# tracelab

Performance regression analysis for benchgrid's run artifacts, built so that
its central claim can be checked rather than taken on trust.

> On real benchmark runs, TraceLab returns REGRESSION only when two batches of
> a candidate, run apart, both move past a metric's practical threshold by more
> than the run-to-run noise the baseline itself shows. On a quiet machine that
> held false regressions at zero. Under real contention it removed most of
> them, not all: what is left comes from changes in the machine that outlast
> the gap between the two batches.

A performance gate that fires on noise gets muted within a month, and a muted
gate is worse than none because people still believe it is watching. So the
evidence here is mostly about the other side of the promise: what happens when
the code did not change and the machine was simply noisy.

There are three published evaluations. Two of them are failures, and both are
kept exactly as they came out.

## v3: real contention on a schedule fixed in advance

From `evidence/v3`: 546 real runs on an Apple M4 laptop, collected while the
collector started one busy loop per core at fixed run indexes. Nine short
bursts each hit exactly one window's first batch, with its baseline and its
confirmation batch clean. One long burst covered both batches of one window.
The schedule, the claim and the negative control were committed before the
corpus was collected.

On the nine targeted windows, across every case kind where a regression would
be wrong:

| comparator | false regressions | injected regressions caught |
| --- | ---: | ---: |
| **tracelab** | **3 / 153** | 53 / 63 |
| tracelab, one batch | 31 / 153 | 61 / 63 |
| pooled bootstrap | 42 / 153 | 63 / 63 |
| fixed 5%, twenty-run window | 74 / 153 | 63 / 63 |
| fixed 5%, previous run | 64 / 153 | 61 / 63 |

**The pre-registered claim was zero, and it failed by three.** Confirmation
removed 28 of the 31 false regressions the bursts caused. Over the whole v3
corpus it took TraceLab from 59 false regressions to 20, against 96 for the
pooled bootstrap and over 300 for either fixed gate.

Each of the three is traced in [docs/evidence.md](docs/evidence.md). Two are
one periodic-loop window, counted under two case kinds, whose baseline ran in
a quiet stretch of the machine and whose two batches both ran after an
unscheduled shift that raised the share of
late ticks from about 0.3% to about 13% for the remaining nine minutes of
collection. The third is a real 2% slowdown, below the 3% threshold, that the
machine's drift pushed past it in both batches. Neither is something a second
batch can see through, and both are entries in
[docs/known-misses.md](docs/known-misses.md) that v3 measured rather than
argued.

## v2: a quiet machine

From `evidence/v2`: 300 real runs, nothing scheduled.

| comparator | false regressions | same-code false regressions | false improvements | regressions caught |
| --- | ---: | ---: | ---: | ---: |
| **tracelab** | **0** | **0 / 72** | 6 | 212 / 252 |
| tracelab, one batch | 2 | 0 / 72 | 6 | 218 / 252 |
| pooled bootstrap | 9 | 2 / 72 | 18 | 230 / 252 |
| fixed 5%, twenty-run window | 88 | 3 / 72 | 80 | 219 / 252 |
| fixed 5%, previous run | 110 | 4 / 72 | 61 | 211 / 252 |

The machine was quiet, with run-to-run noise of the median at 0.22%, so v2
shows the method holding when nothing goes wrong and says little about
confirmation, which is why v3 exists. Six times TraceLab called a confident
p95 improvement on unchanged code; that overconfidence with three candidate
runs is a known miss.

## v1: the first result, a failure

From `evidence/v1`: ten false regressions with no confirmation step, five of
them on same-code windows taken while this repository's own test suite was
loading the machine. That failure is why confirmation exists
([ADR 0002](docs/adr/0002-confirmation-batches.md)).

## Checking any of this

```
uv run python -m evaluation.score v3
uv run python -m script.validate_evidence
```

The validator reruns every comparator on every case of every version and
requires an exact match, recomputes the summaries byte for byte, and checks
each claim. A version published as a failure is pinned to its exact count, so
the failure can neither be edited away nor drift. The weaker comparators are
required to produce false regressions on the same windows; if they stop, the
corpus is too quiet to support the claim and the build fails.

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
| `corpus/`, `policies/`, `evidence/` | each published version, frozen; v3 reuses the v2 policies |
| `evaluation/contention.py` | the contention schedule v3 was collected under |

## Documents

- [PLAN.md](PLAN.md), with what changed from it and why
- [docs/evidence.md](docs/evidence.md): how the corpus was collected and how to check it
- [docs/known-misses.md](docs/known-misses.md): what this does not catch
- [docs/non-goals.md](docs/non-goals.md): what is deliberately absent, including any cloud warehouse, profiler or dashboard
- [docs/adr](docs/adr): the decisions, with their reasons
