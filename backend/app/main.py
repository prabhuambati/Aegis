import time
from fastapi import FastAPI, Query, Request
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from .config import settings
from .schemas import TelemetryBatch, InvestigationRequest, ScenarioRequest
from .storage import store, decode
from .analysis import overview, detect_anomalies, build_incident, investigate, DEPENDENCIES, OVERVIEW_WINDOW_SECONDS
from .demo import scenario
from .remote_backends import backend_status, prometheus_query, prom_metric_to_rows, tempo_search, tempo_trace
from .config import settings

app=FastAPI(title="Aegis Observability API", version="1.0.0", description="Evidence-grounded observability and root-cause analysis for distributed systems.")
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in settings.cors_origins.split(",")], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/health")
def health():
    return {
        "status": "ok",
        "storage_backend": settings.storage_backend,
        "database": "postgresql" if settings.storage_backend == "postgres" else "sqlite",
        "llm_provider": settings.llm_provider,
        "telemetry_backends": backend_status(),
    }


@app.get("/api/v1/infrastructure/status")
def infrastructure_status():
    return {"storage_backend": settings.storage_backend, "telemetry_backends": backend_status()}
@app.post("/api/v1/telemetry/batch")
def ingest(batch: TelemetryBatch):
    return {"accepted": len(store.insert_items([x.model_dump() for x in batch.items]))}


@app.post("/otlp/v1/logs")
async def ingest_otlp_logs(request: Request):
    from .otlp_logs import parse_otlp_logs
    payload = await request.body()
    rows = parse_otlp_logs(payload)
    accepted = len(store.insert_items(rows)) if rows else 0
    return Response(content=f"{{\"accepted\":{accepted}}}", media_type="application/json")
@app.get("/api/v1/overview")
def get_overview():
    detect_anomalies()
    result = overview()
    result["storage_backend"] = settings.storage_backend
    result["telemetry_backends"] = backend_status()
    return result
@app.get("/api/v1/services")
def services():
    ov = overview()
    service_rows = []
    for name, health in ov["services"].items():
        service_rows.append({"name": name, "health": health, "dependencies": DEPENDENCIES.get(name, [])})
    known = {row["name"] for row in service_rows}
    for target in {target for targets in DEPENDENCIES.values() for target in targets} - known:
        service_rows.append({"name": target, "health": "healthy", "dependencies": []})
    return sorted(service_rows, key=lambda row: row["name"])
@app.get("/api/v1/dependencies")
def dependencies(): return [{"source":s,"target":t} for s, targets in DEPENDENCIES.items() for t in targets]
@app.get("/api/v1/anomalies")
def anomalies(): return [decode(r) for r in store.rows("SELECT * FROM anomalies ORDER BY timestamp DESC LIMIT 100")]
@app.get("/api/v1/logs")
def logs(service: str|None=None, severity: str|None=None, trace_id: str|None=None, limit: int=Query(100,le=500)):
    where=["kind='log'"]; params=[]
    for col,val in (("service_name",service),("severity",severity),("trace_id",trace_id)):
        if val: where.append(f"{col}=?"); params.append(val)
    params.append(limit); return [decode(r) for r in store.rows(f"SELECT * FROM telemetry WHERE {' AND '.join(where)} ORDER BY timestamp DESC LIMIT ?",params)]
@app.get("/api/v1/traces")
def traces(trace_id: str|None=None, service: str|None=None, limit: int=Query(200,le=1000)):
    if settings.storage_backend == "postgres" or settings.tempo_url:
        if trace_id:
            remote = tempo_trace(trace_id)
            if remote:
                return remote
        else:
            remote = tempo_search(service_name=service, limit=min(limit, 100))
            if remote:
                return remote
    if trace_id: return [decode(r) for r in store.rows("SELECT * FROM telemetry WHERE kind='span' AND trace_id=? ORDER BY timestamp",(trace_id,))]
    where=["kind='span'"]; params=[]
    if service: where.append("service_name=?"); params.append(service)
    params.append(limit)
    return [decode(r) for r in store.rows(f"SELECT * FROM telemetry WHERE {' AND '.join(where)} ORDER BY timestamp DESC LIMIT ?",params)]
@app.get("/api/v1/metrics")
def metrics(
    service: str | None = None,
    metric_name: str | None = None,
    window_seconds: int = Query(OVERVIEW_WINDOW_SECONDS, ge=1, le=3600),
    limit: int = Query(300, le=1000),
):
    end = time.time()
    start = end - window_seconds
    names = [metric_name] if metric_name else [
        "aegis_requests_total", "aegis_errors_total", "aegis_request_latency_ms",
        "request_rate", "error_rate", "latency_ms", "cpu_percent", "memory_percent",
    ]
    rows = []
    for name in names:
        selector = name + (f'{{service_name="{service}"}}' if service else "")
        try:
            rows.extend(
                prom_metric_to_rows(
                    prometheus_query(selector, start=start, end=end, step="30s"), name
                )
            )
        except Exception:
            continue
    where = ["kind='metric'", "timestamp >= ?", "timestamp <= ?"]
    params = [start, end]
    if service:
        where.append("service_name=?")
        params.append(service)
    if metric_name:
        where.append("metric_name=?")
        params.append(metric_name)
    local = [decode(row) for row in store.rows(
        f"SELECT * FROM telemetry WHERE {' AND '.join(where)} ORDER BY timestamp",
        params,
    )]
    by_id = {str(row.get("id")): row for row in rows + local if row.get("id")}
    return sorted(by_id.values(), key=lambda row: float(row.get("timestamp", 0)))[-limit:]
@app.get("/api/v1/incidents")
def incidents(): return [decode(r) for r in store.rows("SELECT * FROM incidents ORDER BY start_time DESC")]
@app.get("/api/v1/incidents/{incident_id}")
def incident(incident_id: str): return decode(store.one("SELECT * FROM incidents WHERE id=?",(incident_id,))) or {"error":"not_found"}
@app.post("/api/v1/analyze")
def analyze(): return build_incident() or {"message":"No correlated anomaly detected"}
@app.post("/api/v1/investigate")
def ask(req: InvestigationRequest): return investigate(req.question,req.incident_id)
@app.post("/api/v1/demo/reset")
def demo_reset(): store.reset(); return {"reset":True}
@app.post("/api/v1/demo/run-scenario")
def run_scenario(req: ScenarioRequest): return scenario(req.scenario)
