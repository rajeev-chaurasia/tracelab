-- Run outcomes per day and benchmark: how much of the bench time produced a
-- result that can be compared, and how much was lost to failures or to an
-- environment that did not hold.
SELECT
  date,
  corpus,
  benchmark,
  hardware_class,
  COUNT(*) AS runs,
  COUNTIF(status = 'SUCCEEDED') AS succeeded,
  COUNTIF(status = 'FAILED') AS failed,
  COUNTIF(status = 'INVALID') AS invalid,
  SAFE_DIVIDE(COUNTIF(status != 'SUCCEEDED'), COUNT(*)) AS failure_rate,
  AVG(TIMESTAMP_DIFF(finished_at, started_at, MILLISECOND)) AS mean_run_ms,
  AVG(load1_before) AS mean_load1_before
FROM `{dataset}.runs`
GROUP BY date, corpus, benchmark, hardware_class
ORDER BY date, corpus, benchmark
