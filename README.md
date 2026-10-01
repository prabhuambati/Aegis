# Aegis — AI-Powered Observability for Distributed Applications

Aegis is a local-first, evidence-grounded observability platform for distributed systems. It collects normalized logs, metrics, and distributed spans; correlates them through trace/request IDs and service dependencies; detects anomalies with deterministic statistics; forms incidents; and explains likely root cause with citations to the telemetry that supports every conclusion.

## What is implemented

- FastAPI backend with OpenAPI docs at `/docs`.
- Common telemetry schema for spans, logs, and metrics.
- SQLite persistence with indexes for time, service, and trace lookups, plus a PostgreSQL backend with JSONB attributes and equivalent indexes. The active backend is selected by `STORAGE_BACKEND`.
- Production-style signal routing: PostgreSQL stores normalized logs/incidents, Prometheus stores Collector-exported metrics, and Tempo stores Collector-exported traces.
- Collector-facing OTLP log ingestion at `/otlp/v1/logs`, with protobuf decoding into the common telemetry schema.
- Dependency graph for API Gateway → Order/User → Payment/DB services.
- Median/baseline and z-score anomaly detection for latency, error rate, resource utilization, and request-rate signals.
- Incident formation with affected services, timeline, evidence records, confidence, and recommendations.
- Explainable root-cause ranking that prefers the earliest anomalous upstream service rather than downstream symptoms.
- Investigation endpoint that retrieves incident evidence before responding and marks answers as grounded.
- Four-service demo environment with configurable latency/error/crash/timeout injection and OpenTelemetry export hooks.
- React dashboard for overview, dependency map, logs, traces, metrics, incidents, and evidence-grounded investigation.
- Docker Compose stack for backend, frontend, OpenTelemetry Collector, and persistent local data.
- Unit/API tests covering ingestion, trace filtering, scenario analysis, root-cause selection, and citations.

## Architecture

```text
┌──────────────────────────────────────────────────────────────┐
│ Demo distributed application                                │
│ API Gateway → Order Service → Payment Service → Payments DB  │
│             └──────────────→ User Service → Users DB         │
└───────────────────────────┬──────────────────────────────────┘
                            │ OpenTelemetry spans/logs/metrics
                            ▼
                 ┌──────────────────────┐
                 │ OTel Collector       │
                 │ OTLP HTTP / gRPC     │
                 └──────────┬───────────┘
                            ▼
                 ┌──────────────────────┐
                 │ Aegis Ingestion API  │
                 │ Normalize + validate │
                 └──────────┬───────────┘
                            ▼
                 ┌──────────────────────┐
                 │ Storage               │
                 │ SQLite / PostgreSQL   │
                 └──────────┬───────────┘
                            ▼
       ┌────────────────────┴────────────────────┐
       │ Feature extraction + deterministic ML    │
       │ baselines → anomalies → correlation      │
       │ → dependency-aware incident formation     │
       └────────────────────┬────────────────────┘
                            ▼
                 ┌──────────────────────┐
                 │ Grounded analysis    │
                 │ retrieval + local    │
                 │ explanation / LLM opt│
                 └──────────┬───────────┘
                            ▼
                 ┌──────────────────────┐
                 │ React incident UI    │
                 │ maps, traces, logs,   │
                 │ metrics, investigation│
                 └──────────────────────┘
```

## Quick start: no Docker

Requirements: Python 3.12+, Node 20+.

```bash
cd ai-observability
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

# terminal 1
cd backend
uvicorn app.main:app --reload --port 8000

# terminal 2
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The backend API and interactive documentation are at http://localhost:8000/docs.

Run the failure scenario from the UI or with:

```bash
curl -X POST http://localhost:8000/api/v1/demo/run-scenario \
  -H 'content-type: application/json' \
  -d '{"scenario":"payment_timeout"}'
curl -X POST http://localhost:8000/api/v1/analyze
```

The incident should identify `payment-service` as the probable root cause, with slow/failed payment spans, payment error-rate anomalies, downstream order latency, and API Gateway 504 spans as evidence.

## Quick start: Docker Compose

```bash
docker compose up --build
```

Open http://localhost:8080. The backend is at http://localhost:8000, Prometheus at http://localhost:9090, Tempo at http://localhost:3200, PostgreSQL at `localhost:5432`, and the Collector accepts OTLP on ports 4317/4318. Use `docker compose down -v` to remove demo data.

The Compose path is now a real signal pipeline:

```text
Demo payment service
  └─ OTLP HTTP traces, metrics, logs
       └─ OpenTelemetry Collector
            ├─ traces → Tempo
            ├─ metrics → Prometheus scrape endpoint
            └─ logs → Aegis /otlp/v1/logs → PostgreSQL
```

`demo-payment` and `traffic-generator` are included in Compose. They generate real OTLP signals without needing an API key. The dashboard/API continues to use the deterministic fixture endpoint for the multi-service causal scenario, while live service signals are queryable through the infrastructure status, metrics, logs, and traces endpoints.

Use `docker compose down -v` to remove demo data.

### Infrastructure smoke test

With Docker running and the Compose stack started, run:

```bash
chmod +x scripts/smoke_test.sh
./scripts/smoke_test.sh
```

The script checks backend health, PostgreSQL readiness, Tempo readiness, Prometheus query success, and that live OTLP traffic is visible through the Aegis logs/traces API. It waits for the bundled traffic generator before querying the signal backends.

Equivalent manual checks:

```bash
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/api/v1/infrastructure/status

docker compose exec -T postgres pg_isready -U aegis -d aegis
curl -fsS http://localhost:3200/ready
curl -fsS --get http://localhost:9090/api/v1/query \
  --data-urlencode 'query=aegis_requests_total'
curl -fsS 'http://localhost:8000/api/v1/logs?limit=5'
curl -fsS 'http://localhost:8000/api/v1/traces?limit=5'
```

## Demo services

The shared service can be run multiple times with environment variables:

```bash
SERVICE_NAME=payment-service FAILURE_MODE=latency \
  uvicorn demo_services.service:app --port 8004
SERVICE_NAME=order-service UPSTREAM_URL=http://localhost:8004/health \
  uvicorn demo_services.service:app --port 8003
```

Supported `FAILURE_MODE` values: `latency`, `error`, and `crash`. The `OTEL_EXPORTER_OTLP_ENDPOINT` variable enables OTLP HTTP export when the optional exporter package is installed. The deterministic `/api/v1/demo/run-scenario` endpoint is the recommended end-to-end demo because it makes the timeline reproducible without requiring background traffic processes.

## API surface

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Liveness and provider/storage status |
| GET | `/api/v1/infrastructure/status` | PostgreSQL, Prometheus, and Tempo readiness |
| POST | `/api/v1/telemetry/batch` | Ingest normalized telemetry |
| POST | `/otlp/v1/logs` | Collector OTLP protobuf log ingestion |
| GET | `/api/v1/overview` | Health, request rate, errors, P95, incidents |
| GET | `/api/v1/services` | Service health and dependencies |
| GET | `/api/v1/dependencies` | Graph edges |
| GET | `/api/v1/logs` | Filtered structured logs |
| GET | `/api/v1/traces` | Recent spans or one trace |
| GET | `/api/v1/metrics` | Metric samples |
| GET | `/api/v1/anomalies` | Detected anomalies with evidence IDs |
| POST | `/api/v1/analyze` | Form a correlated incident |
| GET | `/api/v1/incidents` | Incident queue |
| GET | `/api/v1/incidents/{id}` | Full incident evidence/timeline |
| POST | `/api/v1/investigate` | Evidence-grounded natural-language investigation |
| POST | `/api/v1/demo/run-scenario` | Inject deterministic demo failures |

## Evidence and AI safety model

Numerical anomaly detection and incident correlation do not call an LLM. They use telemetry values, robust medians, dispersion, service relationships, span status, and temporal order. Before any explanation is generated, the backend builds a bounded evidence packet from normalized PostgreSQL telemetry, Prometheus metrics, Tempo traces, stored anomalies, and the dependency graph. Records remain identifiable by stable evidence IDs.

The optional LLM receives only the computed incident and retrieved evidence packet. It cannot select the root cause or change the deterministic confidence score. The response contract separates `observed` facts from `inferred` conclusions; inferred claims must include their own confidence and citations. Every explanation sentence must contain citation markers such as `[evt-123]`, and every marker must refer to an evidence ID in the packet. Root-cause inferences must agree with the deterministic RCA service and cite failed/slow/anomalous evidence for that service.

## Optional evidence-grounded LLM explanation

The LLM is deliberately downstream of deterministic anomaly detection and RCA. A request is sent only when `LLM_PROVIDER=openai` or `openai-compatible` and `OPENAI_API_KEY` is configured. The prompt contains the incident, retrieved logs/metrics/traces/anomalies/relationships, allowed citation IDs, and the observed-versus-inferred contract.

A response is rejected and replaced with the local explanation if:

- the provider is unavailable or times out;
- the response is malformed;
- claims are missing or lack citations;
- any citation is not an ID from the supplied evidence packet;
- an explanation sentence lacks a citation marker; or
- an inferred root-cause claim conflicts with deterministic RCA or lacks root-cause evidence.

The fallback remains available by default with `LLM_PROVIDER=local`, and no external credentials are required for local development.

Enable it with:

```bash
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=your-key
```

For an OpenAI-compatible local gateway, also set `LLM_BASE_URL`. The API returns `ai_provider`, `ai_grounded`, `ai_explanation`, and `ai_citations` on incidents. Without credentials, the default remains `local-fallback` and the existing tests remain offline.

### Preparing a future provider key

No provider key is required for the current project. The Compose backend now passes these variables through from a local `.env` file while keeping the key unset by default:

```dotenv
LLM_PROVIDER=local
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
LLM_TIMEOUT_SECONDS=15
OPENAI_API_KEY=
```

When a key is available, change `LLM_PROVIDER` to `openai`, set `OPENAI_API_KEY` in the untracked root `.env`, and recreate only the backend:

```bash
docker compose up -d --build backend
curl -fsS http://localhost:8000/health
```

The key is never committed, printed, returned by the health endpoint, or included in telemetry. If the key is missing, the provider is unreachable, or its response fails citation validation, Aegis automatically uses the evidence-grounded local fallback. Do not put credentials directly in `docker-compose.yml`, source files, screenshots, or chat messages.

## Tests

```bash
cd backend
python -m pytest -q
```

Expected result: all backend tests pass. The suite now covers telemetry evidence retrieval, valid citations, unsupported claims, provider failures, malformed responses, ingestion, trace filtering, scenario analysis, root-cause selection, and persistence. The test harness uses a temporary SQLite database and does not require Docker, an API key, or external services.

## Production hardening roadmap

This repository now has a real single-node telemetry foundation, but it is not a claim that the Compose stack is production-scale. Before production use:

1. Add Collector durable queues, TLS, authentication, batching, redaction processors, and backpressure limits.
2. Add Prometheus remote-write/long-term retention and scale Tempo storage beyond local blocks.
3. Add authentication, tenant isolation, rate limits, audit logs, secrets management, and PII scrubbing.
4. Add an LLM adapter with structured output validation, evidence IDs as mandatory citations, refusal on unsupported claims, and offline evaluation sets.
5. Add alert routing, deduplication, incident state transitions, deployment/change events, and distributed worker queues.
6. Load-test ingestion, benchmark detection windows, and add browser-level dashboard tests.
