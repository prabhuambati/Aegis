# Aegis architecture notes

## Correlation contract

Every span may carry `trace_id`, `span_id`, `parent_span_id`, and `request_id`. Logs and metrics can carry the same trace/request identifiers in their attributes. The backend stores these identifiers as indexed columns and preserves the original attributes JSON for vendor-specific fields.

The incident engine groups anomalies within a common time window, joins failed/slow spans, and uses the explicit dependency graph. Root-cause ranking favors a service that is anomalous early and is upstream of affected services. It does not infer a cause from an error message alone.

## Signal ownership

- PostgreSQL owns normalized logs, manually ingested telemetry, anomalies, and incidents.
- Prometheus owns Collector-exported metric series. The Collector exposes an internal scrape endpoint on `:8889`; Prometheus scrapes that endpoint.
- Tempo owns distributed traces. The Aegis API queries Tempo's search and trace endpoints and normalizes the response for the dashboard.
- The OpenTelemetry Collector receives OTLP HTTP/gRPC and fans out logs to `/otlp/v1/logs`, metrics to its Prometheus exporter, and traces to Tempo.

The backend chooses SQLite by default for fast unit tests and local development. Compose sets `STORAGE_BACKEND=postgres` and configures the Prometheus/Tempo URLs. Both storage implementations expose the same contract to the anomaly and incident engine.

## Demonstration causality

The payment-timeout fixture emits normal checkout traces, then:

1. Payment spans move from ~70ms to 850ms+ and return 504.
2. Order spans increase by the payment duration plus orchestration overhead.
3. Gateway spans increase again and begin returning 504.
4. Payment CPU and error-rate metrics rise.
5. The anomaly engine produces payment latency/error/CPU anomalies plus downstream order/gateway signals.
6. The incident engine selects `payment-service`, includes the trace-linked spans and anomalies, and describes the gateway 504 as downstream impact.

The Compose `demo-payment` and `traffic-generator` services separately prove the live OTLP path. The deterministic fixture remains available because it creates a stable, multi-service causal sequence for repeatable RCA tests.

## Deployment boundary

The Compose stack now contains PostgreSQL, Prometheus, Tempo, the OpenTelemetry Collector, backend, frontend, a live instrumented demo service, and a traffic generator. It is suitable for local infrastructure validation, not yet multi-node production. TLS, auth, durable queues, remote-write/long-term retention, PII scrubbing, alert routing, and load testing remain hardening work.
