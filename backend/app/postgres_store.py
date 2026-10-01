import json
import threading
from contextlib import contextmanager
from typing import Any

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - exercised only when postgres mode is selected without deps
    psycopg = None
    dict_row = None


POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, timestamp DOUBLE PRECISION NOT NULL,
 service_name TEXT NOT NULL, trace_id TEXT, span_id TEXT, parent_span_id TEXT,
 request_id TEXT, severity TEXT, message TEXT, operation TEXT, duration_ms DOUBLE PRECISION,
 status_code INTEGER, metric_name TEXT, metric_value DOUBLE PRECISION, attributes_json JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_telemetry_time ON telemetry(timestamp);
CREATE INDEX IF NOT EXISTS idx_telemetry_service ON telemetry(service_name, timestamp);
CREATE INDEX IF NOT EXISTS idx_telemetry_trace ON telemetry(trace_id, timestamp);
CREATE TABLE IF NOT EXISTS anomalies (
 id TEXT PRIMARY KEY, created_at DOUBLE PRECISION NOT NULL, timestamp DOUBLE PRECISION NOT NULL,
 service_name TEXT NOT NULL, metric TEXT NOT NULL, observed DOUBLE PRECISION, baseline DOUBLE PRECISION,
 score DOUBLE PRECISION NOT NULL, severity TEXT NOT NULL, explanation TEXT NOT NULL, evidence_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb
);
CREATE TABLE IF NOT EXISTS incidents (
 id TEXT PRIMARY KEY, created_at DOUBLE PRECISION NOT NULL, start_time DOUBLE PRECISION NOT NULL,
 end_time DOUBLE PRECISION NOT NULL, title TEXT NOT NULL, severity TEXT NOT NULL, status TEXT NOT NULL,
 root_cause_service TEXT, root_cause TEXT, confidence DOUBLE PRECISION,
 affected_services_json JSONB NOT NULL, timeline_json JSONB NOT NULL, evidence_json JSONB NOT NULL,
 summary TEXT NOT NULL, recommendations_json JSONB NOT NULL,
 ai_explanation TEXT, ai_citations_json JSONB NOT NULL DEFAULT '[]'::jsonb,
 ai_claims_json JSONB NOT NULL DEFAULT '[]'::jsonb, ai_provider TEXT,
 ai_grounded BOOLEAN NOT NULL DEFAULT FALSE
);
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS ai_explanation TEXT;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS ai_citations_json JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS ai_claims_json JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS ai_provider TEXT;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS ai_grounded BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_incidents_start ON incidents(start_time DESC);
"""


class PostgresStore:
    """PostgreSQL implementation of the small storage contract used by analysis.py."""

    def __init__(self, url: str):
        if psycopg is None:
            raise RuntimeError("PostgreSQL mode requires psycopg[binary].")
        self.url = url
        self._lock = threading.RLock()
        with self.connect() as conn:
            conn.execute(POSTGRES_SCHEMA)
            conn.commit()

    @contextmanager
    def connect(self):
        conn = psycopg.connect(self.url, row_factory=dict_row)
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _sql(query: str) -> str:
        return query.replace("?", "%s")

    def reset(self):
        with self._lock, self.connect() as conn:
            conn.execute("TRUNCATE telemetry, anomalies, incidents")
            conn.commit()

    def insert_items(self, items: list[dict[str, Any]]) -> list[str]:
        import uuid
        ids = []
        with self._lock, self.connect() as conn:
            for item in items:
                event_id = item.get("event_id") or str(uuid.uuid4())
                ids.append(event_id)
                conn.execute(
                    """INSERT INTO telemetry
                    (id,kind,timestamp,service_name,trace_id,span_id,parent_span_id,request_id,severity,message,operation,duration_ms,status_code,metric_name,metric_value,attributes_json)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (id) DO UPDATE SET
                      kind=EXCLUDED.kind, timestamp=EXCLUDED.timestamp, service_name=EXCLUDED.service_name,
                      trace_id=EXCLUDED.trace_id, span_id=EXCLUDED.span_id, parent_span_id=EXCLUDED.parent_span_id,
                      request_id=EXCLUDED.request_id, severity=EXCLUDED.severity, message=EXCLUDED.message,
                      operation=EXCLUDED.operation, duration_ms=EXCLUDED.duration_ms, status_code=EXCLUDED.status_code,
                      metric_name=EXCLUDED.metric_name, metric_value=EXCLUDED.metric_value,
                      attributes_json=EXCLUDED.attributes_json""",
                    (
                        event_id, item["kind"], item["timestamp"], item["service_name"], item.get("trace_id"),
                        item.get("span_id"), item.get("parent_span_id"), item.get("request_id"), item.get("severity"),
                        item.get("message"), item.get("operation"), item.get("duration_ms"), item.get("status_code"),
                        item.get("metric_name"), item.get("metric_value"), json.dumps(item.get("attributes", {})),
                    ),
                )
            conn.commit()
        return ids

    def rows(self, query: str, params=()):
        with self.connect() as conn:
            return list(conn.execute(self._sql(query), params).fetchall())

    def one(self, query: str, params=()):
        with self.connect() as conn:
            return conn.execute(self._sql(query), params).fetchone()

    def insert_anomalies(self, rows):
        with self._lock, self.connect() as conn:
            for item in rows:
                conn.execute(
                    """INSERT INTO anomalies
                    (id,created_at,timestamp,service_name,metric,observed,baseline,score,severity,explanation,evidence_ids_json)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (id) DO UPDATE SET observed=EXCLUDED.observed, baseline=EXCLUDED.baseline,
                    score=EXCLUDED.score, severity=EXCLUDED.severity, explanation=EXCLUDED.explanation,
                    evidence_ids_json=EXCLUDED.evidence_ids_json""",
                    (
                        item["id"], item["created_at"], item["timestamp"], item["service_name"], item["metric"],
                        item.get("observed"), item.get("baseline"), item["score"], item["severity"],
                        item["explanation"], json.dumps(item.get("evidence_ids", [])),
                    ),
                )
            conn.commit()

    def insert_incident(self, incident):
        with self._lock, self.connect() as conn:
            conn.execute(
                """INSERT INTO incidents
                (id,created_at,start_time,end_time,title,severity,status,root_cause_service,root_cause,confidence,
                 affected_services_json,timeline_json,evidence_json,summary,recommendations_json,ai_explanation,ai_citations_json,ai_claims_json,ai_provider,ai_grounded)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status, root_cause=EXCLUDED.root_cause,
                confidence=EXCLUDED.confidence, affected_services_json=EXCLUDED.affected_services_json,
                timeline_json=EXCLUDED.timeline_json, evidence_json=EXCLUDED.evidence_json,
                summary=EXCLUDED.summary, recommendations_json=EXCLUDED.recommendations_json,
                ai_explanation=EXCLUDED.ai_explanation, ai_citations_json=EXCLUDED.ai_citations_json,
                ai_claims_json=EXCLUDED.ai_claims_json, ai_provider=EXCLUDED.ai_provider,
                ai_grounded=EXCLUDED.ai_grounded""",
                (
                    incident["id"], incident["created_at"], incident["start_time"], incident["end_time"],
                    incident["title"], incident["severity"], incident["status"], incident.get("root_cause_service"),
                    incident.get("root_cause"), incident.get("confidence"), json.dumps(incident["affected_services"]),
                    json.dumps(incident["timeline"]), json.dumps(incident["evidence"]), incident["summary"],
                    json.dumps(incident["recommendations"]), incident.get("ai_explanation"),
                    json.dumps(incident.get("ai_citations", [])), json.dumps(incident.get("ai_claims", [])),
                    incident.get("ai_provider"), bool(incident.get("ai_grounded", False)),
                ),
            )
            conn.commit()
