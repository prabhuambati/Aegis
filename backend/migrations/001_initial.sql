CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS telemetry (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('span', 'log', 'metric')),
  timestamp DOUBLE PRECISION NOT NULL,
  service_name TEXT NOT NULL,
  trace_id TEXT,
  span_id TEXT,
  parent_span_id TEXT,
  request_id TEXT,
  severity TEXT,
  message TEXT,
  operation TEXT,
  duration_ms DOUBLE PRECISION,
  status_code INTEGER,
  metric_name TEXT,
  metric_value DOUBLE PRECISION,
  attributes_json JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_telemetry_time ON telemetry(timestamp);
CREATE INDEX IF NOT EXISTS idx_telemetry_service ON telemetry(service_name, timestamp);
CREATE INDEX IF NOT EXISTS idx_telemetry_trace ON telemetry(trace_id, timestamp);

CREATE TABLE IF NOT EXISTS anomalies (
  id TEXT PRIMARY KEY,
  created_at DOUBLE PRECISION NOT NULL,
  timestamp DOUBLE PRECISION NOT NULL,
  service_name TEXT NOT NULL,
  metric TEXT NOT NULL,
  observed DOUBLE PRECISION,
  baseline DOUBLE PRECISION,
  score DOUBLE PRECISION NOT NULL,
  severity TEXT NOT NULL,
  explanation TEXT NOT NULL,
  evidence_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS incidents (
  id TEXT PRIMARY KEY,
  created_at DOUBLE PRECISION NOT NULL,
  start_time DOUBLE PRECISION NOT NULL,
  end_time DOUBLE PRECISION NOT NULL,
  title TEXT NOT NULL,
  severity TEXT NOT NULL,
  status TEXT NOT NULL,
  root_cause_service TEXT,
  root_cause TEXT,
  confidence DOUBLE PRECISION,
  affected_services_json JSONB NOT NULL,
  timeline_json JSONB NOT NULL,
  evidence_json JSONB NOT NULL,
  summary TEXT NOT NULL,
  recommendations_json JSONB NOT NULL,
  ai_explanation TEXT,
  ai_citations_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  ai_provider TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_start ON incidents(start_time DESC);
