-- Daily distribution of every measured metric, per corpus, benchmark and
-- hardware class. Warmups are excluded here, in the query, rather than at
-- load time, so the table keeps the full record.
--
-- PERCENTILE_CONT rather than APPROX_QUANTILES: it is exact, it interpolates
-- the same way numpy does, so the result can be checked against the lake
-- with no tolerance, and the BigQuery emulator returns its input unsorted
-- for APPROX_QUANTILES. docs/warehouse.md has the probe that showed it.
WITH measured AS (
  SELECT
    date, corpus, benchmark, hardware_class, metric_name, unit, run_id, value,
    PERCENTILE_CONT(value, 0.5) OVER keys AS p50,
    PERCENTILE_CONT(value, 0.95) OVER keys AS p95,
    PERCENTILE_CONT(value, 0.99) OVER keys AS p99
  FROM `{dataset}.samples`
  WHERE NOT warmup
  WINDOW keys AS (PARTITION BY date, corpus, benchmark, hardware_class, metric_name, unit)
)
SELECT
  date,
  corpus,
  benchmark,
  hardware_class,
  metric_name,
  unit,
  COUNT(DISTINCT run_id) AS runs,
  COUNT(*) AS samples,
  AVG(value) AS mean,
  ANY_VALUE(p50) AS p50,
  ANY_VALUE(p95) AS p95,
  ANY_VALUE(p99) AS p99
FROM measured
GROUP BY date, corpus, benchmark, hardware_class, metric_name, unit
ORDER BY date, corpus, benchmark, metric_name
