# Evidence

What is published under `corpus/` and `evidence/`, how it was produced, how to
check it, and what it cannot show.

## What is published

Each version has three parts, all committed:

| part | v1 | v2 |
| --- | --- | --- |
| corpus of sealed run artifacts | `corpus/v1/store`, 200 runs | `corpus/v2/store`, 300 runs |
| policies, committed before the corpus | `policies/v1` | `policies/v2` |
| every decision, and summaries derived from them | `evidence/v1` | `evidence/v2` |

A decision row names the case, the comparator, the verdict, every metric's
interval, and the run ids on each side, so any single verdict can be traced to
the samples that produced it. `summary.json` and `summary.md` are computed from
`decisions.jsonl` alone.

## How the corpus was collected

`evaluation/collect.py` runs each benchmark as a fresh process, wraps its
samples in benchgrid's run artifact contract, and reads the result straight
back through the contract reader before sealing it. A run the reader would
reject stops collection. All 500 runs are accepted by
`python -m tracelab.ingest`.

The two benchmarks are interleaved, so slow drift in the machine lands in both
alike. The machine is an Apple M4 laptop, described in every run as what it
is: `hardware_class` `laptop-arm64`, not emulated, governor `unmanaged`.

- **v1** was collected while the machine was in ordinary use, which included
  this repository's own test suite running partway through. That load is in
  the corpus and is the cause of v1's published failure.
- **v2** was collected with nothing else started from this repository while it
  ran. Other work on the machine continued. Its run-to-run noise is several
  times lower than v1's, and that matters for reading its result; see below.

## How cases are built

Twenty runs are the baseline and the next three the candidate, sliding by
three runs. In v2 a confirmation batch of three more runs starts eighteen runs
after the first batch ends, about a minute later. The corpus is all one
revision, so an untouched window is a same-code comparison, and a transformed
window has a known change. The transforms are listed in `evaluation/cases.py`
with the verdicts that count as correct for each.

## Reproducing and checking

```
uv run python -m evaluation.score v2
uv run python -m script.validate_evidence
```

The validator checks each version's sha256 manifest, reruns every comparator
on every case and requires an exact match, recomputes both summaries byte for
byte, and then checks the claim. CI runs it on every push.

For v2 the claim check requires zero TraceLab false regressions and at least
one same-code false regression from a weaker comparator. For v1, which is
published as a failure, it requires exactly the ten false regressions that were
published.

## Changes to the harness after a result

Recorded here because each one could have been a way to move a number.

- **After v2 was first scored, `pooled_bootstrap` was found to be inheriting
  v2's confirmation flag** without being given a confirmation batch, which
  held every first-batch regression at INCONCLUSIVE and made it look like a
  comparator that catches nothing and never false-alarms. It now runs single
  batch, as in v1. No TraceLab decision changed.
- **False improvements were added to the summary** once v2 showed TraceLab
  calling a confident p95 improvement on same-code windows. v1's decisions are
  unchanged; its summary files were regenerated from them to carry the new
  column, and its manifest updated for those two files.
