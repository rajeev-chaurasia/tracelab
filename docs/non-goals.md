# Non-goals

Each of these is a real feature of a real performance platform, and each is
deliberately absent. The list is here so the absences read as decisions.

## No cloud warehouse, object store or dashboard

There is no BigQuery, no GCS and no Grafana in this repository, and nothing
here has been run against a cloud project. The artifact store is a directory
in benchgrid's layout, read by the contract reader. A warehouse would make
history queries fast and would not change a single verdict, and the verdicts
are what this repository is trying to make believable.

## No profiler

TraceLab says that a metric moved, by how much and how sure it is. It does not
capture Perfetto, perf or Nsight traces. The contract lists extra files such
as profiler output in each run's manifest, so a report can link to them; the
linking is not built.

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
