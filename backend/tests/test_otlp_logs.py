import gzip

from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest

from app.otlp_logs import parse_otlp_logs


def make_payload() -> bytes:
    request = ExportLogsServiceRequest()
    resource_logs = request.resource_logs.add()
    resource_logs.resource.attributes.add(key="service.name").value.string_value = "payment-service"
    record = resource_logs.scope_logs.add().log_records.add()
    record.time_unix_nano = 1_700_000_000_000_000_000
    record.severity_text = "ERROR"
    record.body.string_value = "upstream timeout"
    return request.SerializeToString()


def test_parse_otlp_logs_accepts_gzip_payload():
    rows = parse_otlp_logs(gzip.compress(make_payload()))
    assert len(rows) == 1
    assert rows[0]["service_name"] == "payment-service"
    assert rows[0]["severity"] == "ERROR"
    assert rows[0]["message"] == "upstream timeout"


def test_parse_otlp_logs_accepts_uncompressed_payload():
    rows = parse_otlp_logs(make_payload())
    assert len(rows) == 1
    assert rows[0]["message"] == "upstream timeout"
