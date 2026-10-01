"""Evidence retrieval for incident analysis.

The LLM never queries a backend directly. This module builds a bounded, auditable packet
from normalized telemetry, Prometheus, Tempo, anomaly records, and the dependency graph.
"""
from __future__ import annotations

import time
from typing import Any

from .remote_backends import prom_metric_to_rows, prometheus_query, tempo_search, tempo_trace
from .storage import decode, store


DEFAULT_DEPENDENCIES = {
    "api-gateway": ["user-service", "order-service"],
    "order-service": ["payment-service", "user-service", "orders-db"],
    "payment-service": ["payments-db"],
    "user-service": ["users-db"],
}


def _rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        return [decode(row) for row in store.rows(query, params)]
    except Exception:
        # A remote-backed deployment may temporarily lose the normalized-store connection.
        # Return an auditable partial packet; the caller will not claim absent evidence.
        return []


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result = []
    for item in items:
        item_id = str(item.get("id", ""))
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        result.append(item)
    return result


def _with_source(row: dict[str, Any], source: str) -> dict[str, Any]:
    item = dict(row)
    item.setdefault("source", source)
    return item


def _fact_text(item: dict[str, Any]) -> str:
    kind = item.get("kind")
    service = item.get("service_name", "unknown-service")
    if kind == "log":
        return f"{service} emitted log: {item.get('message') or 'no message'}"
    if kind == "metric":
        return f"{service} metric {item.get('metric_name', 'unknown')} was {item.get('metric_value')}"
    if kind == "span":
        return (
            f"{service} span {item.get('operation') or 'unknown operation'} took "
            f"{item.get('duration_ms')} ms and returned HTTP {item.get('status_code', 'unknown')}"
        )
    if kind == "anomaly":
        return item.get("explanation", f"{service} anomaly detected")
    return f"Observed {kind or 'telemetry'} from {service}"


def retrieve_incident_evidence(
    incident: dict[str, Any],
    dependencies: dict[str, list[str]] | None = None,
    max_items: int = 100,
) -> dict[str, Any]:
    """Retrieve a bounded evidence packet for one incident.

    Local normalized records are joined by time, service, trace ID, and request ID. Metrics
    and traces are additionally read from Prometheus and Tempo when configured. Every packet
    item keeps its original stable ID so citations can be checked without fuzzy matching.
    """
    dependencies = dependencies or DEFAULT_DEPENDENCIES
    start = float(incident.get("start_time", time.time())) - 30
    end = float(incident.get("end_time", time.time())) + 30
    affected = set(incident.get("affected_services", []))
    seed = [dict(item) for item in incident.get("evidence", []) if item.get("id")]
    trace_ids = {str(item.get("trace_id")) for item in seed if item.get("trace_id")}
    request_ids = {str(item.get("request_id")) for item in seed if item.get("request_id")}

    local = _rows(
        "SELECT * FROM telemetry WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp LIMIT ?",
        (start, end, max_items * 4),
    )
    local = [
        _with_source(row, "normalized-store")
        for row in local
        if row.get("service_name") in affected
        or row.get("trace_id") in trace_ids
        or row.get("request_id") in request_ids
    ]

    anomalies = _rows(
        "SELECT * FROM anomalies WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp LIMIT ?",
        (start, end, max_items),
    )
    anomalies = [_with_source(row, "anomaly-store") for row in anomalies if row.get("service_name") in affected]

    remote_metrics: list[dict[str, Any]] = []
    metric_names = {"aegis_requests_total", "aegis_errors_total", "aegis_request_latency_ms"}
    metric_names.update(str(item.get("metric")) for item in anomalies if item.get("metric"))
    for metric_name in sorted(metric_names):
        if not metric_name or not metric_name.replace("_", "").isalnum():
            continue
        try:
            results = prometheus_query(metric_name, start=start, end=end, step="30s")
            remote_metrics.extend(
                _with_source(row, "prometheus")
                for row in prom_metric_to_rows(results, metric_name)
                if not affected or row.get("service_name") in affected
            )
        except Exception:
            continue

    remote_traces: list[dict[str, Any]] = []
    for trace_id in list(trace_ids)[:10]:
        try:
            remote_traces.extend(_with_source(row, "tempo") for row in tempo_trace(trace_id))
        except Exception:
            continue
    if not remote_traces:
        for service in sorted(affected)[:10]:
            try:
                remote_traces.extend(_with_source(row, "tempo") for row in tempo_search(service_name=service, limit=20))
            except Exception:
                continue

    # Prefer source telemetry over the compact incident summary objects when IDs overlap.
    # The summary is used to seed trace/request correlation above, not as a substitute for
    # the original log, metric, span, or anomaly record.
    all_records = _dedupe(local + anomalies + remote_metrics + remote_traces)
    all_records.sort(key=lambda row: float(row.get("timestamp", start)))
    all_records = all_records[:max_items]
    logs = [row for row in all_records if row.get("kind") == "log"]
    metrics = [row for row in all_records if row.get("kind") == "metric"]
    traces = [row for row in all_records if row.get("kind") == "span"]
    anomaly_records = [row for row in all_records if row.get("kind") == "anomaly"]

    relationships = [
        {"source": source, "target": target, "type": "dependency"}
        for source, targets in dependencies.items()
        for target in targets
        if not affected or source in affected or target in affected
    ]

    observed = [
        {"text": _fact_text(item), "citations": [str(item["id"])]}
        for item in all_records
        if item.get("id") and item.get("kind") in {"log", "metric", "span", "anomaly"}
    ]
    root_service = incident.get("root_cause_service")
    root_candidates = [
        item
        for item in all_records
        if item.get("id")
        and item.get("service_name") == root_service
        and (
            item.get("kind") == "anomaly"
            or (item.get("status_code") or 0) >= 500
            or (item.get("duration_ms") or 0) > 500
            or str(item.get("severity") or "").upper() in {"ERROR", "CRITICAL"}
        )
    ]
    root_support = [str(item["id"]) for item in root_candidates][:8]
    if not root_support:
        root_support = [
            str(item["id"])
            for item in all_records
            if item.get("id") and item.get("service_name") == root_service
        ][:8]
    if not root_support:
        root_support = [str(item["id"]) for item in all_records if item.get("id")][:8]
    inferred = []
    if incident.get("root_cause") and root_support:
        inferred.append(
            {
                "text": str(incident["root_cause"]),
                "confidence": incident.get("confidence"),
                "citations": root_support,
            }
        )

    return {
        "window": {"start": start, "end": end},
        "telemetry": {"logs": logs, "metrics": metrics, "traces": traces},
        "anomalies": anomaly_records,
        "relationships": relationships,
        "evidence": all_records,
        "observed_facts": observed[:max_items],
        "inferred_conclusions": inferred,
        "confidence": incident.get("confidence"),
    }
