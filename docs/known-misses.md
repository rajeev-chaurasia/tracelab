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

v3 measured this rather than leaving it argued. A scheduled burst covering
both batches of a window produced false regressions in every case kind it
touched. An unscheduled shift in the machine, which raised the periodic loop's
late ticks from about 0.3% to about 13% for nine minutes, produced seven
more, two of them on windows the claim covered. See docs/evidence.md.

## A canary protects only against what it feels

v6 measured this. A memory-bandwidth canary barely moved under CPU contention
that halved its partner's network throughput, so it did not excuse the network
candidates' false regressions. The canary has to share the candidate's
bottleneck, and nothing here chooses one that does.

## A fixed device limit leaves slow drift through

v7's clock check refused the warm-up windows but not two where the clock had
drifted 1.1 to 1.7%, inside its 2% limit, while throughput drifted with it.
Judging throughput per clock cycle would remove the drift rather than refuse to
judge through it; it is not built.

## The environment checks cost catches

In v6 TraceLab caught 78 of 496 injected regressions with its checks and 210
without; in v7, 83 of 104 against 103. A regression measured while the
machine moved is refused whether or not it is real.

## A sub-threshold change plus drift can clear the threshold

A real 2% slowdown is below the 3% threshold and should not block. When the
machine drifts by another percent between the baseline and both batches, the
candidate measures past 3% twice and confirmation agrees with itself. v3 shows
this five times. The change was real; the verdict overstated its size.

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

## Two GPUs, briefly

The NVIDIA collectors and the CUDA workload have run on two L4 VMs, on one
driver, for under an hour each before they were deleted. A different GPU,
driver or nsys version could format something differently; the first real run
already found one difference from the documented format. nvidia-smi reports
utilisation, which is how often a kernel was running, not how full the SMs
were; occupancy comes from a separate Nsight Compute profile of a few launches.
And v5 and v7 both show the device drifting while it warms, which a benchmark
that does not wait for steady state will mistake for the code. The two L4s did
not even agree on their own clock: one ran near 1 GHz, the other near 0.9 GHz
under load, from the same image.

## eBPF follows one thread, in a VM

The run-queue collector filters on the workload's main thread, so waits on a
helper thread, such as a BLAS pool, are not seen. The recordings were made in
Docker Desktop's Linux VM, so the scheduler being measured shares its CPUs
with a hypervisor; on bare metal the quiet numbers would likely be lower.

## The rollups are checked against two days of data

The BigQuery SQL has run on the emulator and on BigQuery, and both agree with
the lake. But every corpus was collected within two days, so the rollups have
never been exercised across months of partitions, and nothing here measures
how their cost grows with history.

## What the evaluation cannot show

- **One machine.** An Apple M4 laptop in ordinary use, not an isolated rig.
  CPU workloads only. No GPU metric in the corpus is real.
- **Injected changes are transforms of real samples.** A scaled latency, an
  inflated tail, a wider spread, displaced ticks. A real code change can
  reshape a distribution in ways none of these do.
- **Same code is the only ground truth for "no change".** Windows of one
  corpus are compared with each other, so every same-code case shares the
  machine, the day and the build.
