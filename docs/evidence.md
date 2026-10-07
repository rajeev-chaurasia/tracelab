# Evidence

What is published under `corpus/` and `evidence/`, how it was produced, how to
check it, and what it cannot show.

## What is published

Each version has three parts, all committed:

| part | v1 | v2 | v3 | v4 | v5 |
| --- | --- | --- | --- | --- | --- |
| corpus of sealed run artifacts | `corpus/v1/store`, 200 runs | `corpus/v2/store`, 300 runs | `corpus/v3/store`, 546 runs, and `corpus/v3/contention.jsonl` | `corpus/v4/store`, 300 runs, 30 with sealed traces | `corpus/v5/store`, 120 runs on an L4, 12 with sealed traces |
| policies, committed before the corpus | `policies/v1` | `policies/v2` | `policies/v2`, unchanged | `policies/v4` | `policies/v5` |
| every decision, and summaries derived from them | `evidence/v1` | `evidence/v2` | `evidence/v3` | `evidence/v4` | `evidence/v5` |

A decision row names the case, the comparator, the verdict, every metric's
interval, and the run ids on each side, so any single verdict can be traced to
the samples that produced it. `summary.json` and `summary.md` are computed from
`decisions.jsonl` alone.

## How the corpus was collected

`evaluation/collect.py` runs each benchmark as a fresh process, wraps its
samples in benchgrid's run artifact contract, and reads the result straight
back through the contract reader before sealing it. A run the reader would
reject stops collection. All 1,466 runs are accepted by
`python -m tracelab.ingest`.

The benchmarks of a corpus are interleaved, so slow drift in the machine lands
in all of them alike. v1 to v4 ran on an Apple M4 laptop, described in every
run as what it is: `hardware_class` `laptop-arm64`, not emulated, governor
`unmanaged`. v5 ran on a GCP `g2-standard-4` VM with one NVIDIA L4, driver
580.178.04, described as `gcp-g2-standard-4-l4`; the VM was deleted after
collection.

- **v1** was collected while the machine was in ordinary use, which included
  this repository's own test suite running partway through. That load is in
  the corpus and is the cause of v1's published failure.
- **v2** was collected with nothing else started from this repository while it
  ran. Other work on the machine continued. Its run-to-run noise is several
  times lower than v1's, and that matters for reading its result; see below.
- **v3** was collected the same way as v2, plus scheduled contention: before
  each scheduled run index the collector starts one busy-loop process per core
  and waits a second, and stops them after the burst's last run. Every start
  and stop is logged with its time in `corpus/v3/contention.jsonl`, and all
  ten bursts fired at their scheduled indexes. Contended runs were measurably
  slower: about 6.5% on the matmul median, and the periodic loop missed about
  12.6% of its deadlines against 8.7% in uncontended runs.

## How cases are built

Twenty runs are the baseline and the next three the candidate, sliding by
three runs. In v2 and v3 a confirmation batch of three more runs starts eighteen runs
after the first batch ends, about a minute later. The corpus is all one
revision, so an untouched window is a same-code comparison, and a transformed
window has a known change. The transforms are listed in `evaluation/cases.py`
with the verdicts that count as correct for each.

## v3, traced case by case

The pre-registered claim was no TraceLab false regression on the nine windows
whose first batch alone ran under a short burst. There were three.

- **`periodic/same_code/w96` and `periodic/noisy_baseline_same_code/w96`.**
  The first batch (runs 116 to 118) ran under a short burst; the confirmation
  batch (runs 137 to 139) was clean by schedule and still showed about eleven
  points more late ticks than the baseline. Uncontended runs show why. From
  run 30 to run 119 the loop missed about 0.3% of its deadlines; from run 120
  to the end of collection, about 13%, with the load average rising from
  about 8 to 11. The same level appears in runs 0 to 29. None of the
  collector's bursts lasted more than two minutes, so this was a change in the
  machine, not in the schedule. The baseline sat in the quiet stretch and both
  batches in the busy one, which is the sustained-change miss.
- **`matmul/latency_+2%/w48`.** A real 2% slowdown, below the 3% threshold.
  The first batch ran under a burst; the clean confirmation batch measured the
  median about 3% slower than the baseline taken earlier, so both batches
  cleared the threshold. The change was real and the verdict overstated it.

Outside the targeted windows TraceLab raised seventeen more false
regressions, in three groups:

- **Eight under the long burst.** Matmul windows 216, 219 and 222 have both
  batches inside it, across several case kinds. Windows 219 and 222 are
  labelled partial only because their baselines touch the burst's first runs.
  This is the miss the long burst was scheduled to measure.
- **Five across the unscheduled shift.** Periodic windows 99, 102 and 105,
  whose baselines fall mostly before run 120 and whose batches fall after it.
- **Four real 2% slowdowns pushed past the threshold** by drift, in matmul
  windows 3, 129, 150 and 198.

## v4, traced case by case

v4 is the first evaluation judged on rates, memory bandwidth and loopback
network throughput, where higher is better, and the first corpus whose runs
carry their aligned traces. Its policies required zero TraceLab false
regressions. There were eleven, against 37 for the pooled bootstrap and 141
and 208 for the fixed gates.

All eleven are in windows 57 to 72. Between runs 83 and 118 the machine had a
disturbed stretch of about two minutes: at indexes 85, 87, 92, 93, 98, 116 and
118 both workloads collapsed together, memory bandwidth to as little as 20
GB/s against about 75, and network throughput to 8 GB/s against 20. Two
unrelated benchmarks falling at the same index is the machine, not the code.
The stretch was longer than the one-minute confirmation gap, so in those
windows each batch caught a slow run and confirmation agreed with itself. It
is the sustained-disturbance miss for the third time, this time unscheduled.

v4 also shows the cost of the noise gate honestly. Memory bandwidth on this
machine moves 11.6% from run to run, against a 5% limit, so most memory
bandwidth cases are INCONCLUSIVE, and TraceLab caught 91 of 288 injected
regressions against 242 and 248 for the fixed gates. Those gates bought their
catches with 141 and 208 false ones.

## v5, on a real GPU

v5 ran the CUDA workload, ten fp16 4096 by 4096 multiplies per iteration timed
with CUDA events, on an L4 in a GCP VM, with nvidia-smi telemetry sealed in
every tenth run. The GPU was far quieter than the laptop: run-to-run noise of
the median 0.59%. TraceLab passed all 26 same-code windows and caught 102 of
104 injected regressions.

Its claim of zero false regressions failed by three, all in the 2% slowdown
case, all in the first windows. The telemetry in the sealed traces says why.
Over the first forty runs the L4 heated from 37 to 52 degrees and its SM clock
under load fell from 1,005 to 960 MHz, and throughput fell with it, about 2.5%,
before both settled. A baseline taken while it was still cool made a real 2%
slowdown measure past 3% in both batches.

v5's negative control also fails, and the pin does not hide that. On a GPU
this stable the fixed 5% window gate raised no same-code false regression
either, so v5 cannot tell the methods apart. What it shows is narrower: on a
quiet device TraceLab does not invent regressions, and it does not escape a
device that has not reached steady state.

## Reproducing and checking

```
uv run python -m evaluation.score v3
uv run python -m script.validate_evidence
```

The validator checks each version's sha256 manifest, reruns every comparator
on every case and requires an exact match, recomputes both summaries byte for
byte, and then checks the claim. CI runs it on every push.

For v2 the claim check requires zero TraceLab false regressions and at least
one same-code false regression from a weaker comparator. For v1, v3, v4 and
v5, which are published as failures, it requires exactly the published count:
ten overall for v1, three on the targeted windows for v3, where the one-batch
engine must also still raise at least one, eleven overall for v4, and three
overall for v5.

## Changes to the harness after a result

Recorded here because each one could have been a way to move a number.

- **After v2 was first scored, `pooled_bootstrap` was found to be inheriting
  v2's confirmation flag** without being given a confirmation batch, which
  held every first-batch regression at INCONCLUSIVE and made it look like a
  comparator that catches nothing and never false-alarms. It now runs single
  batch, as in v1. No TraceLab decision changed.
- **After an independent review, the headline reason was fixed to name the
  metric that decided the verdict.** That changed the `reason` text of 99 of
  11,383 published decisions and no verdict, interval or count; each version
  was republished and revalidated, and the diff was checked field by field.
- **False improvements were added to the summary** once v2 showed TraceLab
  calling a confident p95 improvement on same-code windows. v1's decisions are
  unchanged; its summary files were regenerated from them to carry the new
  column, and its manifest updated for those two files.
