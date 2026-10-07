-- The noise the comparison engine gates on, computed in the warehouse: the
-- spread of per-run medians across runs, per corpus, benchmark and metric,
-- both as a coefficient of variation and as the robust median-absolute-
-- deviation form the engine itself uses. A metric whose robust noise here
-- exceeds its policy threshold cannot support a decision at that threshold.
WITH per_sample AS (
  SELECT
    corpus, benchmark, hardware_class, metric_name, run_id,
    PERCENTILE_CONT(value, 0.5) OVER (
      PARTITION BY corpus, benchmark, hardware_class, metric_name, run_id
    ) AS run_median
  FROM `{dataset}.samples`
  WHERE NOT warmup
),
per_run AS (
  SELECT DISTINCT corpus, benchmark, hardware_class, metric_name, run_id, run_median
  FROM per_sample
),
centred AS (
  SELECT
    *,
    PERCENTILE_CONT(run_median, 0.5) OVER series AS center
  FROM per_run
  WINDOW series AS (PARTITION BY corpus, benchmark, hardware_class, metric_name)
),
deviations AS (
  SELECT
    *,
    PERCENTILE_CONT(ABS(run_median - center), 0.5) OVER (
      PARTITION BY corpus, benchmark, hardware_class, metric_name
    ) AS mad
  FROM centred
)
SELECT
  corpus,
  benchmark,
  hardware_class,
  metric_name,
  COUNT(*) AS runs,
  ANY_VALUE(center) AS median_of_run_medians,
  SAFE_DIVIDE(STDDEV_SAMP(run_median), AVG(run_median)) AS cv_of_run_medians,
  SAFE_DIVIDE(1.4826 * ANY_VALUE(mad), ABS(ANY_VALUE(center))) AS robust_noise
FROM deviations
GROUP BY corpus, benchmark, hardware_class, metric_name
ORDER BY corpus, benchmark, metric_name
