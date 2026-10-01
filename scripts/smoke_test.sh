#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${AEGIS_URL:-http://localhost:8000}"
PROM_URL="${PROMETHEUS_URL:-http://localhost:9090}"
TEMPO_URL="${TEMPO_URL:-http://localhost:3200}"

printf '1/7 backend health\n'
curl -fsS "$BASE_URL/health" | python3 -m json.tool

printf '2/7 infrastructure readiness\n'
curl -fsS "$BASE_URL/api/v1/infrastructure/status" | python3 -m json.tool

printf '3/7 PostgreSQL readiness\n'
docker compose exec -T postgres pg_isready -U aegis -d aegis

printf '4/7 Tempo readiness\n'
curl -fsS "$TEMPO_URL/ready"
printf '\n'

printf '5/7 generate live OTLP traffic\n'
sleep "${AEGIS_TRAFFIC_WAIT_SECONDS:-8}"

printf '6/7 Prometheus metric query\n'
curl -fsS --get "$PROM_URL/api/v1/query" --data-urlencode 'query=aegis_requests_total' | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["status"] == "success"; assert d["data"]["result"], "Prometheus returned no aegis_requests_total samples"; print(json.dumps(d, indent=2))'

printf '7/7 logs, metrics, and traces through Aegis API\n'
curl -fsS "$BASE_URL/api/v1/logs?limit=5" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert isinstance(d, list) and d, "Aegis API returned no log records"; print("log_records=", len(d))'
curl -fsS "$BASE_URL/api/v1/metrics?limit=5" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert isinstance(d, list) and d, "Aegis API returned no metric records"; print("metric_records=", len(d))'
curl -fsS "$BASE_URL/api/v1/traces?limit=5" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert isinstance(d, list) and d, "Aegis API returned no trace records"; print("trace_records=", len(d))'

printf 'SMOKE_TEST_OK\n'
