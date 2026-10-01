"""OpenTelemetry bootstrap for the demo services.
The services emit all three signals to the Collector when OTEL_EXPORTER_OTLP_ENDPOINT is set."""
import logging
import os


def configure(service):
    from opentelemetry import metrics, trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry._logs import set_logger_provider

    resource = Resource.create({"service.name": service, "deployment.environment": "demo"})
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")

    tracer_provider = TracerProvider(resource=resource)
    if endpoint:
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces"))
        )
    trace.set_tracer_provider(tracer_provider)

    if endpoint:
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=endpoint.rstrip("/") + "/v1/metrics"), export_interval_millis=5000
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    else:
        meter_provider = MeterProvider(resource=resource)
    metrics.set_meter_provider(meter_provider)

    logger_provider = LoggerProvider(resource=resource)
    if endpoint:
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint.rstrip("/") + "/v1/logs"))
        )
    set_logger_provider(logger_provider)
    otel_handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    logger = logging.getLogger(service)
    logger.setLevel(logging.INFO)
    if not any(isinstance(h, LoggingHandler) for h in logger.handlers):
        logger.addHandler(otel_handler)

    meter = metrics.get_meter(service)
    return {
        "tracer": trace.get_tracer(service),
        "meter": meter,
        "logger": logger,
        "requests": meter.create_counter("aegis_requests", description="Demo requests", unit="1"),
        "errors": meter.create_counter("aegis_errors", description="Demo error responses", unit="1"),
        "latency": meter.create_histogram("aegis_request_latency_ms", description="Request latency", unit="ms"),
    }
