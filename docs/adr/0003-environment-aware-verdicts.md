# 0003: A regression is inconclusive when the environment moved with it

## Context

Confirmation (ADR 0002) answers a moment of noise. v3, v4 and v5 each failed
the same way it cannot answer: the machine changed for longer than the gap
between the two batches. In v4 two unrelated benchmarks collapsed at the same
indexes. In v5 the GPU's clock was still falling as it warmed. In both cases
the evidence that the machine moved was already in the data.

## Decision

Two checks, applied only to a verdict that would otherwise be REGRESSION, and
off unless a policy asks for them:

- **A canary.** A benchmark whose code the candidate does not touch, run
  interleaved with it. If the canary itself regressed past its own threshold
  during either candidate batch, the regression is INCONCLUSIVE. Only a canary
  REGRESSION counts: a WARNING is mild drift, and letting it excuse a candidate
  would hide a real ten percent slowdown behind a one percent wobble.
- **Environment metrics.** Metrics the runs carry that describe the device
  rather than the code, such as a GPU's SM clock. If a batch's median differs
  from the baseline's by more than the policy's limit, the regression is
  INCONCLUSIVE.

## Measured

Both were pre-registered and tested on fresh corpora.

v6, CPU contention covering both batches of five windows: the canary cut false
regressions there from 29 to 9, and overall from 35 to 9. The claim was zero.
All nine survivors were network candidates whose memory-bandwidth canary did
not feel the CPU contention that halved network throughput: a canary protects
only against disturbances it shares.

v7, a GPU warming from 67 to 80 degrees: the clock check cut false regressions
from 4 to 2. The two left were windows where the clock drifted 1.1 to 1.7%,
inside the 2% limit, while throughput drifted with it.

## Cost

Both checks trade catches for false alarms, and the trade is large. In v6
TraceLab caught 78 of 496 injected regressions against 210 without the checks;
in v7, 83 of 104 against 103. Every regression measured while the environment
moved becomes INCONCLUSIVE, real or not, because the data cannot tell them
apart. That is the intended behaviour, and the price is visible.

## What would do better

A canary chosen to share the candidate's bottleneck, and for devices, judging
throughput per clock cycle rather than raw throughput, which removes thermal
drift instead of refusing to judge through it. Neither is built; each would
need its own pre-registered corpus.
