# 0002: A regression blocks only when a second batch agrees

## Context

v1 published TraceLab failing its own claim: ten false regressions, five of
them on same-code windows of the periodic loop. Each was traced to the same
cause. The candidate runs in those windows were taken while the machine was
busier than during the baseline, so they really did miss more deadlines. The
statistics were correct about the samples, and the samples do not say why.

## Options considered

**Gate on the recorded environment.** Every run carries preflight readings,
and comparing the candidates' load with the baseline's is cheap. On the
development machine the only reading available is the one-minute load
average, which barely moved at the first window v1 got wrong. A gate tuned
until it caught v1's failures would be fitted to them and would prove nothing.

**Narrow the claim.** Say the engine is only right when both sides ran in an
equally healthy environment. True, and it hands the whole problem to the rig.

**Confirm before blocking.** Run the candidate again later and require both
batches to agree. Used in practice because it attacks the cause directly: a
burst of load is a property of a moment, and slower code is a property of
every moment.

## Decision

With `confirm_regressions` on, a first-batch REGRESSION becomes INCONCLUSIVE
with `needs_confirmation` set. When a confirmation batch is supplied it is
compared against the same baseline with an independent random stream. The
verdict is REGRESSION only if the second batch is also a REGRESSION and the two
share at least one blocking metric. Otherwise it is INCONCLUSIVE, with both
batches' verdicts in the reason.

The flag defaults to off, so a policy written before confirmation existed
keeps the meaning it was evaluated under. v1's policies do not set it, and v1
still reproduces bit for bit.

## Why the same metric

Two batches that each regress on a different metric are two unconfirmed
signals, not one confirmed one. Requiring overlap stops a latency blip in the
first batch and a memory blip in the second from adding up to a block.

## Cost

Three more runs on every first-batch regression, and a verdict that arrives a
batch later. Same-code first-batch regressions cost the same three runs and
end as INCONCLUSIVE instead of a red build. v2 measures how often each
happens.

## What it does not fix

A change in the machine that lasts across both batches and is absent from the
baseline still reads as a regression. See docs/known-misses.md.
