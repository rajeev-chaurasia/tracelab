# Known misses

What TraceLab does not catch, gets wrong, or cannot know, written for someone
trying to break it. Each entry says what was measured where something was.

## A change in the machine that coincides with the candidate's runs

This is the failure v1 published. In five same-code windows the candidate runs
missed more deadlines than the baseline because the machine was busier while
they ran, and the engine called it a regression. It was right about the
samples. Nothing in the samples says why.

Confirmation narrows this to a change that lasts long enough to cover both
batches and is absent from the twenty baseline runs before them. A background
job that runs for five minutes starting just after the baseline was taken
still reads as a regression. The defence for that is on the rig: benchgrid's
per-iteration environment gate marks such a run INVALID, and an INVALID run
never reaches a comparison.

## Preflight readings are recorded and not used

Every run artifact carries load, CPU and GPU utilisation, free memory and
temperature before and after the run. The analysis reads none of them. On the
development machine only the one-minute load average is readable, and it
barely moved at the first window v1 got wrong, so a gate built on it would
have been tuned to the failure it was meant to explain. It is left out rather
than half built.

## Confirmation costs a second batch

Every first-batch regression asks for three more runs on scarce hardware and
delays the verdict until they finish. A real regression is reported later than
a gate without confirmation would report it. That is the price of not failing
a pull request for a busy minute.

## Batches are inferred from start order

The run artifact has no batch id. The command treats the earliest runs of the
candidate revision as the first batch and the next ones as the confirmation.
Runs from two separate CI attempts at the same revision are therefore merged
in time order, which is right when they really were sequential and wrong when
someone reran the first batch.

## Three candidate runs give a coarse view of the candidate's own spread

Resampling three runs with replacement produces only ten distinct sets of
runs, so the candidate side of the interval understates its run-to-run
variance. The baseline's twenty runs carry most of the width. A candidate
whose own runs are unusually spread is judged a little more confidently than
it should be.

## Several critical metrics, each at 95%

Each metric gets its own 95% interval, and a run fails if any critical metric
regresses. Nothing corrects for testing several at once. The practical
threshold does most of that work, since an interval has to clear zero and a
point estimate has to reach the threshold, but the family-wise rate is not
5% and is not claimed to be.

## Absolute-mode metrics skip the noise gate

A relative noise measure means nothing for a rate that sits at zero, so the
deadline-miss rate is judged by its interval alone. A baseline whose miss rate
jumps between zero and a few percent from run to run is not refused as too
noisy; it only produces a wide interval.

## What the evaluation cannot show

- **One machine.** An Apple M4 laptop in ordinary use, not an isolated rig.
  CPU workloads only. No GPU metric in the corpus is real.
- **Injected changes are transforms of real samples.** A scaled latency, an
  inflated tail, a wider spread, displaced ticks. A real code change can
  reshape a distribution in ways none of these do.
- **Same code is the only ground truth for "no change".** Windows of one
  corpus are compared with each other, so every same-code case shares the
  machine, the day and the build.
