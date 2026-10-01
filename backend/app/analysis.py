import math, statistics, time, uuid
from collections import defaultdict
from .storage import store, decode
from .llm import explain_incident
from .evidence import retrieve_incident_evidence

OVERVIEW_WINDOW_SECONDS = 300
METRICS_BUCKET_SECONDS = 30

DEPENDENCIES = {
    "api-gateway": ["user-service", "order-service"],
    "order-service": ["payment-service", "user-service", "orders-db"],
    "payment-service": ["payments-db"],
    "user-service": ["users-db"],
}

def _telemetry(window=3600):
    return [decode(r) for r in store.rows("SELECT * FROM telemetry WHERE timestamp >= ? ORDER BY timestamp", (time.time()-window,))]

def detect_anomalies(window=3600):
    events=_telemetry(window); by=defaultdict(list)
    for e in events:
        if e["kind"]=="span" and e.get("duration_ms") is not None: by[(e["service_name"],"latency_ms")].append(e)
        if e["kind"]=="metric" and e.get("metric_name"): by[(e["service_name"],e["metric_name"])].append(e)
    found=[]
    for (service, metric), rows in by.items():
        values=[float(r.get("duration_ms") if metric=="latency_ms" else r.get("metric_value") or 0) for r in rows]
        if len(values)<3: continue
        baseline=statistics.median(values[:-max(1,min(5,len(values)//3))]) if len(values)>4 else statistics.median(values[:-1])
        latest=values[-1]; spread=statistics.pstdev(values[:-1]) if len(values)>2 else 0
        z=(latest-baseline)/max(spread,1.0)
        threshold = 500 if metric=="latency_ms" else (0.1 if metric=="error_rate" else 80 if metric in ("cpu_percent","memory_percent") else baseline*2)
        abnormal=(latest>threshold and metric in ("latency_ms","error_rate","cpu_percent","memory_percent")) or z>=2.5
        if abnormal:
            sev="critical" if (metric=="error_rate" and latest>=0.2) or z>=4 else "warning"
            evidence=[r["id"] for r in rows[-5:]]
            found.append({"id":f"an-{uuid.uuid4().hex[:10]}","created_at":time.time(),"timestamp":rows[-1]["timestamp"],"service_name":service,"metric":metric,"observed":round(latest,2),"baseline":round(baseline,2),"score":round(max(z, latest/max(threshold,1)),2),"severity":sev,"explanation":f"{metric} reached {latest:.1f} versus baseline {baseline:.1f}","evidence_ids":evidence})
    # avoid duplicate anomaly spam on repeated overview calls
    existing=store.rows("SELECT service_name, metric, timestamp FROM anomalies WHERE timestamp >= ?", (time.time()-300,))
    fresh=[a for a in found if not any(x["service_name"]==a["service_name"] and x["metric"]==a["metric"] and abs(x["timestamp"]-a["timestamp"])<1 for x in existing)]
    if fresh: store.insert_anomalies(fresh)
    return fresh or [decode(r) for r in store.rows("SELECT * FROM anomalies WHERE created_at >= ? ORDER BY timestamp", (time.time()-window,))]

def _service_rank(service, affected):
    downstream=sum(service in deps for deps in DEPENDENCIES.values())
    has_dependency=sum(dep in affected for dep in DEPENDENCIES.get(service,[]))
    return downstream*3 + (0 if has_dependency else 2)

def build_incident():
    anomalies=detect_anomalies();
    if not anomalies: return None
    affected=sorted({a["service_name"] for a in anomalies})
    # Include services represented by failed spans even when no metric anomaly exists.
    fail_rows=[decode(r) for r in store.rows("SELECT * FROM telemetry WHERE kind='span' AND status_code >= 500 ORDER BY timestamp DESC LIMIT 50")]
    for r in fail_rows:
        if r["service_name"] not in affected: affected.append(r["service_name"])
    ranked=sorted(affected,key=lambda s:(- _service_rank(s, set(affected)), min((a["timestamp"] for a in anomalies if a["service_name"]==s), default=time.time())))
    root=ranked[0] if ranked else None
    relevant=[a for a in anomalies if a["service_name"] in affected]
    start=min(a["timestamp"] for a in relevant); end=max(a["timestamp"] for a in relevant)
    evidence=[]
    for a in relevant:
        evidence.append({"type":"anomaly","id":a["id"],"service":a["service_name"],"metric":a["metric"],"observed":a["observed"],"baseline":a.get("baseline"),"explanation":a["explanation"]})
    spans=[r for r in _telemetry(3600) if r["kind"]=="span" and r["service_name"] in affected and (r.get("status_code",0)>=500 or r.get("duration_ms",0)>500)]
    evidence.extend({"type":"telemetry","id":r["id"],"service":r["service_name"],"trace_id":r.get("trace_id"),"operation":r.get("operation"),"duration_ms":r.get("duration_ms"),"status_code":r.get("status_code"),"message":r.get("message")} for r in spans[:20])
    root_an=[a for a in relevant if a["service_name"]==root]
    confidence=min(0.98,0.55+0.08*len(relevant)+0.04*len(spans))
    root_text=f"{root} latency/timeout behavior is the most likely root cause" if root else "Insufficient evidence for a root cause"
    title=f"{root or 'Distributed system'} degradation detected"
    timeline=sorted([{"timestamp":a["timestamp"],"service":a["service_name"],"event":a["explanation"]} for a in relevant]+[{"timestamp":r["timestamp"],"service":r["service_name"],"event":r.get("message") or f"HTTP {r.get('status_code')}"} for r in spans[:10]],key=lambda x:x["timestamp"])
    incident={"id":f"inc-{uuid.uuid4().hex[:10]}","created_at":time.time(),"start_time":start,"end_time":end,"title":title,"severity":"critical" if any(a["severity"]=="critical" for a in relevant) else "warning","status":"open","root_cause_service":root,"root_cause":root_text,"confidence":round(confidence,2),"affected_services":affected,"timeline":timeline,"evidence":evidence,"summary":f"A correlated degradation affected {', '.join(affected)}. The evidence points to {root_text}. This conclusion is based only on recorded anomalies and failed/slow spans.","recommendations":[f"Inspect {root} dependency health and timeout configuration.","Compare deployment/configuration changes immediately before the incident.","Trace a representative failed request end-to-end and verify recovery after mitigation."]}
    evidence_packet = retrieve_incident_evidence(incident, dependencies=DEPENDENCIES)
    packet_records = evidence_packet.get("evidence", [])
    known_ids = {str(item.get("id")) for item in evidence}
    for record in packet_records:
        record_id = str(record.get("id", ""))
        if record_id and record_id not in known_ids:
            evidence.append(record)
            known_ids.add(record_id)
    incident["evidence"] = evidence[:100]
    ai = explain_incident(incident, evidence_packet)
    incident.update({
        "ai_explanation": ai["explanation"],
        "ai_citations": ai["citations"],
        "ai_claims": ai.get("claims", []),
        "ai_provider": ai["provider"],
        "ai_grounded": ai["grounded"],
        "ai_confidence": ai.get("confidence", incident.get("confidence")),
    })
    store.insert_incident(incident); return incident

def _request_spans(events):
    """Return one representative gateway/root span per distributed request."""
    roots = [e for e in events if e["kind"] == "span" and e.get("service_name") == "api-gateway"]
    if roots:
        return roots
    seen = set()
    result = []
    for event in sorted((e for e in events if e["kind"] == "span"), key=lambda x: x["timestamp"]):
        key = event.get("trace_id") or event.get("request_id") or event["id"]
        if key not in seen:
            seen.add(key)
            result.append(event)
    return result


def _percentile(values, percentile):
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * percentile) - 1))
    return ordered[index]


def _active_incidents():
    return [decode(row) for row in store.rows("SELECT * FROM incidents WHERE status='open' ORDER BY start_time DESC")]


def _metric_series(events, window_seconds=OVERVIEW_WINDOW_SECONDS):
    """Normalize stored metric events into bounded time-series rows for the dashboard."""
    now = time.time()
    recent = [
        event for event in events
        if event["kind"] == "metric" and event["timestamp"] >= now - window_seconds
    ]
    rows = []
    for event in recent:
        rows.append({
            "id": event["id"],
            "kind": "metric",
            "timestamp": event["timestamp"],
            "service_name": event["service_name"],
            "metric_name": event.get("metric_name"),
            "metric_value": event.get("metric_value"),
            "attributes": event.get("attributes", {}),
        })
    return rows


def overview():
    events = _telemetry(3600)
    now = time.time()
    recent = [event for event in events if event["timestamp"] >= now - OVERVIEW_WINDOW_SECONDS]
    requests = _request_spans(recent)
    errors = [event for event in requests if (event.get("status_code") or 0) >= 500]
    durations = [event.get("duration_ms") for event in requests if event.get("duration_ms") is not None]
    active = _active_incidents()
    affected = {service for incident in active for service in incident.get("affected_services", [])}
    dependency_services = set(DEPENDENCIES) | {target for targets in DEPENDENCIES.values() for target in targets}
    services = sorted({event["service_name"] for event in events} | dependency_services)
    health = {}
    for service in services:
        service_requests = [event for event in requests if event.get("service_name") == service]
        has_error = any((event.get("status_code") or 0) >= 500 for event in service_requests)
        has_latency = any((event.get("duration_ms") or 0) > 500 for event in service_requests)
        if service in affected and not service_requests:
            health[service] = "failing" if any(incident.get("severity") == "critical" for incident in active) else "degraded"
        elif has_error:
            health[service] = "failing"
        elif has_latency:
            health[service] = "degraded"
        else:
            health[service] = "healthy"
    return {
        "health": "critical" if errors or active else "healthy",
        "request_rate": round(len(requests) / OVERVIEW_WINDOW_SECONDS, 3),
        "error_rate": round(len(errors) / max(len(requests), 1), 3),
        "p95_latency_ms": round(_percentile(durations, 0.95), 1),
        "services": health,
        "telemetry_count": len(events),
        "active_incidents": len(active),
        "aggregation_window_seconds": OVERVIEW_WINDOW_SECONDS,
        "metrics": _metric_series(events),
    }

def _citation_records(incident, citation_ids):
    records = {str(item.get("id")): item for item in incident.get("evidence", []) if item.get("id")}
    resolved = []
    for citation_id in citation_ids:
        item = records.get(str(citation_id))
        if not item:
            resolved.append({"id": str(citation_id), "resolved": False})
            continue
        resolved.append({
            "id": str(citation_id),
            "resolved": True,
            "source": item.get("source", "normalized-store"),
            "type": item.get("kind", item.get("type", "telemetry")),
            "service_name": item.get("service_name", item.get("service")),
            "timestamp": item.get("timestamp"),
            "trace_id": item.get("trace_id"),
            "request_id": item.get("request_id"),
            "metric_name": item.get("metric_name", item.get("metric")),
            "metric_value": item.get("metric_value", item.get("observed")),
            "operation": item.get("operation"),
            "message": item.get("message", item.get("explanation")),
            "status_code": item.get("status_code"),
            "duration_ms": item.get("duration_ms"),
        })
    return resolved


def investigate(question, incident_id=None):
    incidents=[decode(r) for r in store.rows("SELECT * FROM incidents ORDER BY start_time DESC")]
    incident=next((x for x in incidents if x["id"]==incident_id), incidents[0] if incidents else None)
    q=question.lower(); events=_telemetry(3600)
    if incident:
        ev=incident["evidence"][:8]
        if "before" in q or "happened" in q:
            ordered=sorted(incident["timeline"],key=lambda x:x["timestamp"])
            answer="Before the gateway failures, the recorded timeline was: " + "; ".join(f"{x['service']}: {x['event']}" for x in ordered[:8]) + "."
        elif "which service" in q or "caused" in q or "root" in q:
            answer=f"The most likely root cause is {incident['root_cause_service']}: {incident['root_cause']}. Confidence is {incident['confidence']:.0%}. Downstream impact was observed in {', '.join(incident['affected_services'])}."
        else:
            answer=incident.get("ai_explanation") or incident["summary"]
        citation_ids = incident.get("ai_citations") or [item.get("id") for item in ev if item.get("id")]
        return {
            "answer": answer,
            "incident_id": incident["id"],
            "citations": citation_ids,
            "citation_records": _citation_records(incident, citation_ids),
            "claims": incident.get("ai_claims", []),
            "confidence": incident.get("ai_confidence", incident.get("confidence")),
            "grounded": bool(incident.get("ai_grounded", True)),
            "provider": incident.get("ai_provider", "local-fallback"),
        }
    matching=[e for e in events if any(tok in (e.get("message") or "").lower() for tok in q.split() if len(tok)>3)]
    return {"answer":"No incident has enough correlated evidence yet. Generate demo traffic or provide a trace/request ID to narrow the investigation.","incident_id":None,"citations":matching[:5],"grounded":bool(matching)}
