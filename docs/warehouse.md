# Warehouse and dashboards

The analysis reads artifact stores directly. The warehouse is for history:
queries across every corpus, and dashboards over them.

```
uv run tracelab warehouse export --lake lake --store v1=corpus/v1/store ...
docker run -d -p 127.0.0.1:9050:9050 ghcr.io/goccy/bigquery-emulator --project=tracelab
uv run tracelab warehouse bigquery --lake lake --endpoint http://localhost:9050
script/dashboards.sh
```

## The lake

`tracelab warehouse export` reads every run through the contract reader and
writes two Parquet tables, partitioned by run date the way a BigQuery table
would be: `runs`, one row per run with its rig, status and preflight load, and
`samples`, one row per sample with warmups kept and flagged. Run ids repeat
across corpora, so `(corpus, run_id)` is the key. All 1,046 published runs
export to 264,110 samples in about 3 MB.

## BigQuery

`tracelab warehouse bigquery` creates the two tables partitioned on
`started_at` and clustered on benchmark, hardware class and metric or status,
loads the lake, and runs three rollups written in BigQuery's dialect:

| rollup | what it answers |
| --- | --- |
| `daily_metric_rollup` | each metric's runs, samples, mean, p50, p95 and p99 per day, corpus and benchmark |
| `run_health` | how many runs succeeded, failed or were invalid, failure rate, run duration, preflight load |
| `run_to_run_noise` | the spread of per-run medians, as a coefficient of variation and as the robust noise the comparison engine gates on |

It has been run two ways. Against the BigQuery emulator, locally and in CI.
And against BigQuery itself, in the `tracelab` dataset of a GCP project: four
corpora, 1,346 runs and 293,810 samples, loaded with free Parquet load jobs
that replace the tables so a rerun cannot double them, in about 21 seconds.

Every rollup is then recomputed from the Parquet files with numpy and with the
engine's own noise function, value by value, and the command fails on any
difference. On BigQuery all three rollups agree with the lake to within 1e-9,
with no disagreements, and so do they on the emulator.

The dataset was deleted once the run was recorded, so nothing is left running
or stored in the project; `tracelab warehouse bigquery --project <id>` rebuilds
it from the lake in under a minute. `evidence/warehouse/` keeps what BigQuery
returned and what it reported about itself: both tables partitioned by day on `started_at` and clustered as
designed, and dry-run byte counts. At two days of data partitioning has little
to prune. A filter inside the busier day scans more than no filter, because it
reads the `started_at` column too, while a filter that excludes a day cuts the
scan from 6.6 MB to 0.7 MB.

The rollup check found a real problem on its first run against the emulator,
which returns the
input of `APPROX_QUANTILES` unsorted, so its "p99" was an arbitrary sample. The
same probe on BigQuery itself, recorded in `evidence/warehouse`, returns the
sorted answer:

```
SELECT APPROX_QUANTILES(x, 4) FROM UNNEST([1.0, 2.0, 10.0, 100.0, 3.0]) AS x
emulator  -> [1.0, 2.0, 10.0, 100.0, 3.0]
BigQuery  -> [1.0, 2.0, 3.0, 10.0, 100.0]
```

The rollups use `PERCENTILE_CONT` as a window function instead. It is exact on
both, interpolates the way numpy does, and so lets the check demand agreement
rather than a tolerance.

## Dashboards

`script/dashboards.sh` builds the lake, renders it as OpenMetrics with every
sample stamped at the moment its run started, converts that into Prometheus
storage blocks with `promtool tsdb create-blocks-from openmetrics`, and starts
Prometheus and Grafana on localhost with a provisioned dashboard. Each point
is one real run: per-run median, p95 and p99 latency, the periodic loop's p99
interval, preflight load, runs completed per minute, and the published
evaluation results per version and comparator, each stamped when its corpus
finished collecting.

![The TraceLab dashboard over the three published corpora](img/dashboard.jpg)

The v3 contention bursts are visible as the scattered high points in the
latency panels, and the evaluation panels match `evidence/*/summary.json`
exactly.
