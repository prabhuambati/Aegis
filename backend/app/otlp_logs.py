import gzip
from typing import Any


def any_value(value) -> Any:
    kind = value.WhichOneof("value")
    if kind == "string_value":
        return value.string_value
    if kind == "bool_value":
        return value.bool_value
    if kind == "int_value":
        return value.int_value
    if kind == "double_value":
        return value.double_value
    if kind == "bytes_value":
        return value.bytes_value.hex()
    return None


def attributes(values) -> dict[str, Any]:
    return {item.key: any_value(item.value) for item in values}


def resource_name(resource) -> str:
    values = attributes(resource.attributes)
    return str(values.get("service.name", "unknown-service"))


def parse_otlp_logs(payload: bytes) -> list[dict[str, Any]]:
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest

    # The Collector's OTLP/HTTP exporter uses gzip compression by default.
    # Accept both compressed and uncompressed protobuf requests so the route
    # remains interoperable with OTLP clients that disable compression.
    if payload[:2] == b"\x1f\x8b":
        payload = gzip.decompress(payload)

    request = ExportLogsServiceRequest()
    request.ParseFromString(payload)
    rows = []
    for resource_logs in request.resource_logs:
        service = resource_name(resource_logs.resource)
        resource_attrs = attributes(resource_logs.resource.attributes)
        for scope_logs in resource_logs.scope_logs:
            for record in scope_logs.log_records:
                record_attrs = attributes(record.attributes)
                timestamp = (record.time_unix_nano or record.observed_time_unix_nano) / 1_000_000_000
                body = any_value(record.body)
                rows.append(
                    {
                        "kind": "log",
                        "timestamp": timestamp,
                        "service_name": service,
                        "trace_id": record.trace_id.hex() if record.trace_id else None,
                        "span_id": record.span_id.hex() if record.span_id else None,
                        "severity": record.severity_text or str(record.severity_number),
                        "message": str(body) if body is not None else None,
                        "request_id": record_attrs.get("request_id") or record_attrs.get("http.request_id"),
                        "attributes": {**resource_attrs, **record_attrs},
                    }
                )
    return rows
