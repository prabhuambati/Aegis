"""Small HTTP adapters for Prometheus and Tempo.
They are intentionally read-only: PostgreSQL remains the source of normalized logs/incidents,
Prometheus owns time-series metrics, and Tempo owns distributed traces."""
import time
from typing import Any
import httpx
from .config import settings


def _get(url: str, params: dict[str, Any], timeout: float = 2.0):
    try:
        response = httpx.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except Exception:
        return None


def backend_status() -> dict[str, Any]:
    result = {}
    for name, url, path in (
        ("prometheus", settings.prometheus_url, "/-/ready"),
        ("tempo", settings.tempo_url, "/ready"),
    ):
        if not url:
            result[name] = {"status": "not_configured"}
            continue
        try:
            response = httpx.get(f"{url.rstrip('/')}{path}", timeout=1.5)
            result[name] = {"status": "ready" if response.is_success else "unavailable", "http_status": response.status_code}
        except Exception as exc:
            result[name] = {"status": "unavailable", "error": type(exc).__name__}
    return result


def prometheus_query(query: str, start: float | None = None, end: float | None = None, step: str = "15s"):
    if start is not None and end is not None:
        payload = _get(
            f"{settings.prometheus_url.rstrip('/')}/api/v1/query_range",
            {"query": query, "start": start, "end": end, "step": step},
        )
    else:
        payload = _get(f"{settings.prometheus_url.rstrip('/')}/api/v1/query", {"query": query})
    return payload.get("data", {}).get("result", []) if payload else []


def tempo_search(trace_id: str | None = None, service_name: str | None = None, limit: int = 20):
    params: dict[str, Any] = {"limit": limit}
    if trace_id:
        params["trace:id"] = trace_id
    if service_name:
        params["tags"] = f"service.name={service_name}"
    payload = _get(f"{settings.tempo_url.rstrip('/')}/api/search", params)
    rows = []
    for item in (payload or {}).get("traces", []):
        rows.append({
            "id": f"tempo-{item.get('traceID', item.get('traceId', 'unknown'))}",
            "kind": "span",
            "trace_id": item.get("traceID", item.get("traceId")),
            "service_name": item.get("rootServiceName", "unknown-service"),
            "operation": item.get("rootTraceName"),
            "duration_ms": float(item.get("durationMs", 0)),
            "timestamp": float(item.get("startTimeUnixNano", 0)) / 1_000_000_000,
            "status_code": 200,
            "attributes": item,
        })
    return rows


def tempo_trace(trace_id: str):
    payload = _get(f"{settings.tempo_url.rstrip('/')}/api/traces/{trace_id}", {})
    rows = []
    for batch in (payload or {}).get("batches", []):
        resource = {x.get("key"): x.get("value", {}).get("stringValue") for x in batch.get("resource", {}).get("attributes", [])}
        service = resource.get("service.name", "unknown-service")
        for scope in batch.get("scopeSpans", []):
            for span in scope.get("spans", []):
                start = int(span.get("startTimeUnixNano", 0))
                end = int(span.get("endTimeUnixNano", start))
                rows.append({
                    "id": f"tempo-{span.get('spanId')}",
                    "kind": "span",
                    "timestamp": start / 1_000_000_000,
                    "service_name": service,
                    "trace_id": span.get("traceId", trace_id),
                    "span_id": span.get("spanId"),
                    "parent_span_id": span.get("parentSpanId"),
                    "operation": span.get("name"),
                    "duration_ms": (end - start) / 1_000_000,
                    "status_code": 500 if span.get("status", {}).get("code") == 2 else 200,
                    "attributes": span.get("attributes", []),
                })
    return rows


def prom_metric_to_rows(results: list[dict[str, Any]], metric_name: str):
    rows = []
    for result in results:
        labels = result.get("metric", {})
        values = result.get("values") or ([result.get("value")] if result.get("value") else [])
        for point in values:
            if not point or len(point) < 2:
                continue
            rows.append({
                "id": f"prom-{metric_name}-{labels.get('service_name', 'system')}-{point[0]}",
                "kind": "metric",
                "timestamp": float(point[0]),
                "service_name": labels.get("service_name", labels.get("service", "system")),
                "metric_name": metric_name,
                "metric_value": float(point[1]),
                "attributes": labels,
            })
    return rows
