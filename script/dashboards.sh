#!/usr/bin/env bash
# Build the lake from every published corpus, backfill Prometheus from it, and
# start Prometheus and Grafana on localhost. Grafana: http://localhost:3000
set -euo pipefail
cd "$(dirname "$0")/.."

uv run tracelab warehouse export --lake lake \
  --store v1=corpus/v1/store --store v2=corpus/v2/store --store v3=corpus/v3/store
uv run tracelab warehouse openmetrics --lake lake --out deploy/data/tracelab.om \
  --evidence v1=evidence/v1/summary.json --evidence v2=evidence/v2/summary.json \
  --evidence v3=evidence/v3/summary.json

docker compose -f deploy/compose.yaml down
rm -rf deploy/data/prometheus && mkdir -p deploy/data/prometheus
docker run --rm --entrypoint promtool -v "$PWD/deploy/data:/data" prom/prometheus:latest \
  tsdb create-blocks-from openmetrics /data/tracelab.om /data/prometheus
docker compose -f deploy/compose.yaml up -d
echo "Grafana: http://localhost:3000"
