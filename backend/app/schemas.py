from typing import Any, Literal
from pydantic import BaseModel, Field

class TelemetryItem(BaseModel):
    event_id: str | None = None
    kind: Literal["span", "log", "metric"]
    timestamp: float
    service_name: str
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    request_id: str | None = None
    severity: str | None = None
    message: str | None = None
    operation: str | None = None
    duration_ms: float | None = None
    status_code: int | None = None
    metric_name: str | None = None
    metric_value: float | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

class TelemetryBatch(BaseModel):
    items: list[TelemetryItem] = Field(min_length=1, max_length=10000)

class InvestigationRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    incident_id: str | None = None

class ScenarioRequest(BaseModel):
    scenario: Literal["payment_timeout", "database_failure", "traffic_spike"] = "payment_timeout"
