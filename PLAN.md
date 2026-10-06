# tracelab, implementation plan

## 1. The claim this repo has to earn

Every performance regression gate makes the same promise: if a change made the
code slower, the build goes red. Almost none of them show what happens on the
other side of that promise, when the code did not change and the machine was
simply noisy. A gate that fires on noise gets muted within a month, and a muted
gate is worse than none because people still believe it is watching.

This project makes one claim and spends most of its effort making it checkable:

> On real benchmark runs from a noisy machine, the comparison engine returns
> REGRESSION only when a candidate moved past the practical threshold by more
> than the run-to-run noise the baseline itself shows. On same-code comparisons
> it raises no regressions, while a naive threshold gate and a sample-pooled
> bootstrap given the identical runs both do. On injected changes with known
> ground truth, every miss and every INCONCLUSIVE is published with its reason.

Three things follow from that wording:

1. **Real runs, not generated distributions.** A comparator tuned against
   Gaussian noise it generated itself proves nothing about a laptop with
   frequency scaling and a browser open. The corpus is real executions of real
   workloads, each a separate process, with every raw sample committed.
2. **Run-to-run noise is the noise that matters.** Thirty samples inside one
   process are not thirty independent observations of the code; they share one
   frequency state, one cache layout, one scheduler mood. A comparator that
   pools them as if they were independent will be confidently wrong. The
   bootstrap resamples runs first and samples second.
3. **A negative control.** "Zero false regressions" means nothing on a corpus too
   quiet to fool anybody. Weaker comparators run against the same corpus, and
   the validator fails if they stop producing false regressions, because that
   would mean the corpus no longer contains the noise the claim is about.

## 2. Non-goals, decided before the features

Written first so the absences read as decisions. Expanded in
`docs/non-goals.md` once the code exists to judge them against.

- **No BigQuery, no GCS, no Grafana in this repository.** The storage layer is a
  date-partitioned Parquet lake queried through DuckDB, laid out the way a
  BigQuery table would be partitioned and clustered. The production mapping is
  documented, not claimed. Nothing here has been run against a cloud project.
- **No profiler.** TraceLab links a verdict to profiler artifacts by URI and
  checksum. It does not capture Perfetto or Nsight traces itself.
- **No GPU metrics from real hardware.** The development machine has no NVIDIA
  GPU. The schema carries GPU fields; the corpus does not populate them.
- **No scheduling.** Deciding which rig runs what is BenchGrid's job. TraceLab
  consumes a `RunResult` contract and never reaches back.
- **No auto-tuned thresholds.** Thresholds are written in a policy file by a
  person and reviewed like code.

## 3. Shape

```
RunResult (BenchGrid contract, JSON)
   -> normalize -> MetricPoint rows        tracelab.core.schema
   -> lake (Parquet, partitioned by date)  tracelab.store
   -> baseline selection                   tracelab.core.baseline
   -> comparability check                  tracelab.core.compat
   -> hierarchical bootstrap per metric    tracelab.core.stats
   -> per-metric verdict, benchmark rollup tracelab.core.policy
   -> check report (markdown, exit code)   tracelab.report
```

`tracelab.core` imports the standard library, numpy and pydantic and nothing
else. A script fails the build if that stops being true, so the statistics can
be tested and reasoned about without a database or a web framework in the way.

## 4. The decision rule

Each metric carries a direction and a practical threshold T. The relative
change is signed so that positive always means worse. With a 95% bootstrap
interval [lo, hi] and point estimate p:

| condition | verdict |
| --- | --- |
| -T < lo and hi < T | PASS: confidently inside the band nobody cares about |
| lo > 0 and p >= T | REGRESSION |
| lo > 0 and p < T | WARNING: real, probably smaller than anyone cares about |
| hi < 0 and p <= -T | IMPROVEMENT |
| hi < 0 and p > -T | PASS |
| otherwise | INCONCLUSIVE: the interval spans zero and reaches past T |

Before any of that: a candidate whose environment does not match the baseline
on a must-match field is INCOMPARABLE, a baseline whose per-run noise exceeds
the policy limit is INCONCLUSIVE, and too few runs on either side is
INCONCLUSIVE. None of those can become REGRESSION.

## 5. Evaluation

A corpus of real runs per workload, collected sequentially so the drift a real
machine shows over minutes is in it. Cases are built by sliding a window: twenty
runs as baseline, the next three as candidate.

- **Same-code cases.** Window untouched. Ground truth: no regression.
- **Injected cases.** The candidate samples are transformed: uniform shifts of
  +2%, +5%, +10%, +20%, an improvement, tail-only inflation, variance-only
  inflation, injected deadline misses, a memory increase. Ground truth per
  metric is written next to each transform.
- **Hostile baselines.** Per-run drift injected into the baseline, and a
  candidate environment with a different driver.

Three comparators score every case: TraceLab, a naive gate (candidate mean
against baseline mean, 5%), and an iid bootstrap that pools every sample. The
published table is the full confusion matrix for all three, not an accuracy
number.

## 6. Order of work

1. Project skeleton and every gate in CI from the first commit.
2. Schema and the `RunResult` contract.
3. Hierarchical bootstrap, then the decision rule, each tested against cases
   with known answers.
4. Comparability and baseline selection.
5. Jitter metrics from tick timestamps.
6. Real workloads and the collected corpus.
7. Evaluation harness, three comparators, published evidence.
8. Validator that recomputes every published number and rejects an edited one.
9. Lake, CLI and ingest API.
10. Documentation written adversarially.
