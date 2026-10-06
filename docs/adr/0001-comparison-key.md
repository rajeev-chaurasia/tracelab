# 0001: What makes two runs comparable

## Decision

Two runs are comparable when all of these match:

- the comparison key: the lowercase sha256 hex of the RFC 8785 canonical form
  of `spec` with the top-level `revision` and `artifacts` keys removed,
  computed with `tracelab.core.canon.sha256_hex`
- `rig.hardware_class`
- `rig.arch`
- `rig.emulated`

A mismatch on any of them makes the comparison INCOMPARABLE before any
statistics run. Kernel, driver version, governor, CPU model, GPU model and
firmware are reported as drift on the comparison and never disqualify it.

## Why not spec_sha256

`spec_sha256` identifies one exact experiment. benchgrid's spec carries the
revision and the binary digest, so the hash differs on every commit by
construction. Used as a comparability key it would make every candidate
incomparable with every baseline, which is a gate that can never fire.

benchgrid declined to define a cross-commit key, on the reasoning that what
counts as the same measurement is a question for the consumer. This file is
that definition, and `tracelab/core/run.py` is its only implementation.

## Why exactly those two keys

`revision` and `artifacts` name what is under test: the code, the binary built
from it, and its config. Everything else in the spec says how it was measured:
the command, warmups, repetitions, timeouts, requirements, the environment
gates, and the metric declarations with their directions. A change to how
something is measured changes what the number means, so it must split the
history. A change to what is measured is the whole point of a comparison, so
it must not.

The consequence worth stating: a pull request that changes the benchmark's
config file is compared against a baseline that used the old config. That is
deliberate. The config is part of the change under test, and a slower result
caused by it is a real regression in what will ship.

## Why driver drift does not disqualify

The run artifact contract leaves driver, kernel and governor out of its
comparability set, and an experiment that needs a specific driver puts it in
`requirements`, which is inside the key. Refusing to compare across a driver
patch would also refuse every comparison for as long as a fleet upgrade takes,
and a gate that is silent for a week teaches people to stop reading it.

## Not assumed

benchgrid states that `environment.git_revision` equals `spec.revision` and
that the binary and config digests agree between `spec.artifacts` and
`environment`. The contract document does not say so yet and the reader does
not enforce it. The analysis therefore reads the revision only from
`environment.git_revision` and never relies on the two agreeing.
