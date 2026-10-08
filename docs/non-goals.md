# Non-goals

Each of these is a real feature of a real performance platform, and each is
deliberately absent. The list is here so the absences read as decisions.

## No standing cloud deployment

BigQuery has been used for real, in a GCP project's `tracelab` dataset, and
two GPU VMs were created for the GPU evaluations. All of it was deleted once
the results were recorded in `evidence/`. Nothing runs there continuously: no
scheduled loads, no hosted Grafana, no GCS bucket. Corpora live in the
repository and dashboards run on localhost.

## No sampling profiler

The collectors record what a workload did and when: iterations, CPU time,
context switches, scheduler waits, GPU activity. None of them samples stacks,
so there are no flame graphs and no per-function attribution. Nsight Systems
and perf can produce those, and the run artifact can carry their output as
extra files; turning it into stacks is not built.

## No scheduling

Which rig runs what, and when, is benchgrid's job. TraceLab consumes sealed
run artifacts and never reaches back. Confirmation needs a second batch, and
TraceLab asks for one in its report rather than scheduling it.

## No automatically tuned thresholds

Thresholds live in a policy file, are written by a person, and are reviewed
like code. Both versions of the policies were committed before the corpus they
are scored on was collected, which is visible in the history.

## No correction of a rig's environment

A run measured under load is not adjusted, rescaled or partially used.
Whether a run's environment held is the rig's decision, recorded as the run's
status, and an INVALID run is evidence, never an input.

## No pass or fail without a reason

Every verdict carries the metric that decided it, its interval and the
threshold it was judged against. There is no mode that returns a bare boolean.
