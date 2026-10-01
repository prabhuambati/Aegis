import json, os, sqlite3, threading, uuid
from contextlib import contextmanager
from typing import Any
from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, timestamp REAL NOT NULL,
 service_name TEXT NOT NULL, trace_id TEXT, span_id TEXT, parent_span_id TEXT,
 request_id TEXT, severity TEXT, message TEXT, operation TEXT, duration_ms REAL,
 status_code INTEGER, metric_name TEXT, metric_value REAL, attributes_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_telemetry_time ON telemetry(timestamp);
CREATE INDEX IF NOT EXISTS idx_telemetry_service ON telemetry(service_name, timestamp);
CREATE INDEX IF NOT EXISTS idx_telemetry_trace ON telemetry(trace_id, timestamp);
CREATE TABLE IF NOT EXISTS anomalies (
 id TEXT PRIMARY KEY, created_at REAL NOT NULL, timestamp REAL NOT NULL, service_name TEXT NOT NULL,
 metric TEXT NOT NULL, observed REAL NOT NULL, baseline REAL, score REAL NOT NULL,
 severity TEXT NOT NULL, explanation TEXT NOT NULL, evidence_ids_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS incidents (
 id TEXT PRIMARY KEY, created_at REAL NOT NULL, start_time REAL NOT NULL, end_time REAL NOT NULL,
 title TEXT NOT NULL, severity TEXT NOT NULL, status TEXT NOT NULL, root_cause_service TEXT,
 root_cause TEXT, confidence REAL, affected_services_json TEXT NOT NULL, timeline_json TEXT NOT NULL,
 evidence_json TEXT NOT NULL, summary TEXT NOT NULL, recommendations_json TEXT NOT NULL,
 ai_explanation TEXT, ai_citations_json TEXT NOT NULL DEFAULT '[]', ai_claims_json TEXT NOT NULL DEFAULT '[]', ai_provider TEXT,
 ai_grounded INTEGER NOT NULL DEFAULT 0
);
"""

class Store:
    def __init__(self, path: str = settings.db_path):
        self.path = path
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            for statement in (
                "ALTER TABLE incidents ADD COLUMN ai_explanation TEXT",
                "ALTER TABLE incidents ADD COLUMN ai_citations_json TEXT NOT NULL DEFAULT '[]'",
                "ALTER TABLE incidents ADD COLUMN ai_claims_json TEXT NOT NULL DEFAULT '[]'",
                "ALTER TABLE incidents ADD COLUMN ai_provider TEXT",
                "ALTER TABLE incidents ADD COLUMN ai_grounded INTEGER NOT NULL DEFAULT 0",
            ):
                try:
                    conn.execute(statement)
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
            conn.commit()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try: yield conn
        finally: conn.close()

    def reset(self):
        with self._lock, self.connect() as c:
            c.executescript("DELETE FROM telemetry; DELETE FROM anomalies; DELETE FROM incidents;")
            c.commit()

    def insert_items(self, items: list[dict[str, Any]]) -> list[str]:
        ids=[]
        with self._lock, self.connect() as c:
            for item in items:
                eid = item.get("event_id") or str(uuid.uuid4()); ids.append(eid)
                c.execute("""INSERT OR REPLACE INTO telemetry
                (id,kind,timestamp,service_name,trace_id,span_id,parent_span_id,request_id,severity,message,operation,duration_ms,status_code,metric_name,metric_value,attributes_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    eid,item["kind"],item["timestamp"],item["service_name"],item.get("trace_id"),item.get("span_id"),item.get("parent_span_id"),item.get("request_id"),item.get("severity"),item.get("message"),item.get("operation"),item.get("duration_ms"),item.get("status_code"),item.get("metric_name"),item.get("metric_value"),json.dumps(item.get("attributes",{}))))
            c.commit()
        return ids

    def rows(self, query: str, params=()):
        with self.connect() as c: return [dict(r) for r in c.execute(query, params).fetchall()]
    def one(self, query: str, params=()):
        with self.connect() as c:
            r=c.execute(query,params).fetchone(); return dict(r) if r else None
    def insert_anomalies(self, rows):
        with self._lock, self.connect() as c:
            for a in rows:
                c.execute("INSERT OR REPLACE INTO anomalies VALUES (?,?,?,?,?,?,?,?,?,?,?)", (a["id"],a["created_at"],a["timestamp"],a["service_name"],a["metric"],a.get("observed"),a.get("baseline"),a["score"],a["severity"],a["explanation"],json.dumps(a.get("evidence_ids",[]))))
            c.commit()
    def insert_incident(self, incident):
        with self._lock, self.connect() as c:
            c.execute("""INSERT OR REPLACE INTO incidents
                (id,created_at,start_time,end_time,title,severity,status,root_cause_service,root_cause,confidence,
                 affected_services_json,timeline_json,evidence_json,summary,recommendations_json,ai_explanation,ai_citations_json,ai_claims_json,ai_provider,ai_grounded)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                incident["id"], incident["created_at"], incident["start_time"], incident["end_time"], incident["title"],
                incident["severity"], incident["status"], incident.get("root_cause_service"), incident.get("root_cause"),
                incident.get("confidence"), json.dumps(incident["affected_services"]), json.dumps(incident["timeline"]),
                json.dumps(incident["evidence"]), incident["summary"], json.dumps(incident["recommendations"]),
                incident.get("ai_explanation"), json.dumps(incident.get("ai_citations", [])),
                json.dumps(incident.get("ai_claims", [])), incident.get("ai_provider"),
                int(bool(incident.get("ai_grounded", False))),
            ))
            c.commit()

def build_store():
    if settings.storage_backend == "postgres":
        from .postgres_store import PostgresStore
        return PostgresStore(settings.postgres_url)
    return Store()


store = build_store()


def decode(row: dict[str, Any] | None):
    if not row: return row
    for k in ("attributes_json", "evidence_ids_json", "affected_services_json", "timeline_json", "evidence_json", "recommendations_json", "ai_citations_json", "ai_claims_json"):
        if k in row:
            value = row.pop(k)
            row[k.removesuffix("_json")] = json.loads(value) if isinstance(value, str) else value
    return row
