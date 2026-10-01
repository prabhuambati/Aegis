import json
from types import SimpleNamespace

import httpx

from app import evidence, llm


INCIDENT = {
    "title": "Checkout degradation",
    "severity": "critical",
    "start_time": 100.0,
    "end_time": 110.0,
    "root_cause_service": "payment-service",
    "root_cause": "payment-service latency/timeout behavior is the most likely root cause",
    "confidence": 0.91,
    "affected_services": ["api-gateway", "order-service", "payment-service"],
    "evidence": [{"id": "seed-span", "trace_id": "trace-1", "request_id": "req-1"}],
}


def packet():
    return {
        "telemetry": {"logs": [], "metrics": [], "traces": []},
        "anomalies": [],
        "relationships": [],
        "evidence": [
            {"id": "span-1", "kind": "span", "service_name": "payment-service", "trace_id": "trace-1"},
            {"id": "log-1", "kind": "log", "service_name": "payment-service", "message": "payment timeout"},
        ],
        "observed_facts": [{"text": "Payment timed out", "citations": ["span-1"]}],
        "inferred_conclusions": [{
            "text": INCIDENT["root_cause"],
            "confidence": 0.91,
            "citations": ["span-1"],
        }],
        "confidence": 0.91,
    }


def provider_settings():
    return SimpleNamespace(
        llm_provider="openai",
        openai_api_key="test-key",
        llm_base_url="http://llm.test/v1",
        llm_model="test-model",
        llm_timeout_seconds=1,
    )


def fake_response(payload):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": json.dumps(payload)}}]}

    return Response()


def test_retrieval_collects_logs_metrics_traces_anomalies_and_relationships(monkeypatch):
    telemetry = [
        {"id": "log-1", "kind": "log", "timestamp": 105, "service_name": "payment-service", "message": "timeout"},
        {"id": "metric-1", "kind": "metric", "timestamp": 105, "service_name": "payment-service", "metric_name": "error_rate", "metric_value": 0.42},
        {"id": "span-1", "kind": "span", "timestamp": 105, "service_name": "payment-service", "trace_id": "trace-1", "duration_ms": 850, "status_code": 504},
    ]
    anomaly = [{"id": "an-1", "kind": "anomaly", "timestamp": 105, "service_name": "payment-service", "metric": "error_rate", "explanation": "error rate increased"}]
    monkeypatch.setattr(evidence, "_rows", lambda query, params: telemetry if "telemetry" in query else anomaly)
    monkeypatch.setattr(evidence, "prometheus_query", lambda *args, **kwargs: [])
    monkeypatch.setattr(evidence, "tempo_trace", lambda *args, **kwargs: [])
    monkeypatch.setattr(evidence, "tempo_search", lambda *args, **kwargs: [])

    result = evidence.retrieve_incident_evidence(INCIDENT)

    assert {row["id"] for row in result["telemetry"]["logs"]} == {"log-1"}
    assert {row["id"] for row in result["telemetry"]["metrics"]} == {"metric-1"}
    assert {row["id"] for row in result["telemetry"]["traces"]} == {"span-1"}
    assert {row["id"] for row in result["anomalies"]} == {"an-1"}
    assert {edge["target"] for edge in result["relationships"]} >= {"order-service", "payments-db"}
    assert all(claim["citations"] for claim in result["observed_facts"])


def test_valid_provider_response_requires_and_preserves_claim_citations(monkeypatch):
    monkeypatch.setattr(llm, "settings", provider_settings())
    monkeypatch.setattr(llm.httpx, "post", lambda *args, **kwargs: fake_response({
        "explanation": "Payment latency was observed [span-1]. Payment-service is the most likely root cause [span-1].",
        "claims": [
            {"type": "observed", "text": "Payment latency was observed", "citations": ["span-1"]},
            {"type": "inferred", "text": "payment-service is the most likely root cause", "confidence": 0.91, "citations": ["span-1"]},
        ],
        "citations": ["span-1"],
        "confidence": 0.91,
    }))

    result = llm.explain_incident(INCIDENT, packet())

    assert result["provider"] == "openai"
    assert result["grounded"] is True
    assert result["citations"] == ["span-1"]
    assert {claim["type"] for claim in result["claims"]} == {"observed", "inferred"}


def test_unsupported_claim_falls_back_even_with_a_valid_citation(monkeypatch):
    monkeypatch.setattr(llm, "settings", provider_settings())
    monkeypatch.setattr(llm.httpx, "post", lambda *args, **kwargs: fake_response({
        "explanation": "The database is the most likely root cause [span-1].",
        "claims": [{"type": "inferred", "text": "database is the most likely root cause", "confidence": 0.99, "citations": ["span-1"]}],
        "citations": ["span-1"],
        "confidence": 0.99,
    }))

    result = llm.explain_incident(INCIDENT, packet())

    assert result["provider"] == "local-fallback"
    assert result["grounded"] is True
    assert result["citations"] == ["span-1"]


def test_provider_failure_falls_back_without_raising(monkeypatch):
    monkeypatch.setattr(llm, "settings", provider_settings())
    monkeypatch.setattr(llm.httpx, "post", lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ConnectError("offline")))

    result = llm.explain_incident(INCIDENT, packet())

    assert result["provider"] == "local-fallback"
    assert result["grounded"] is True


def test_missing_credentials_use_local_fallback_without_network(monkeypatch):
    missing_key = provider_settings()
    missing_key.openai_api_key = ""
    monkeypatch.setattr(llm, "settings", missing_key)

    def unexpected_request(*args, **kwargs):
        raise AssertionError("provider must not be contacted without credentials")

    monkeypatch.setattr(llm.httpx, "post", unexpected_request)
    result = llm.explain_incident(INCIDENT, packet())

    assert result["provider"] == "local-fallback"
    assert result["grounded"] is True


def test_malformed_provider_response_falls_back(monkeypatch):
    monkeypatch.setattr(llm, "settings", provider_settings())
    monkeypatch.setattr(llm.httpx, "post", lambda *args, **kwargs: fake_response({"explanation": "missing contract"}))

    result = llm.explain_incident(INCIDENT, packet())

    assert result["provider"] == "local-fallback"
    assert result["grounded"] is True
