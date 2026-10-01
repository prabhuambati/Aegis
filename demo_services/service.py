"""One configurable FastAPI demo service. Run with SERVICE_NAME and UPSTREAM_URL."""
import os
import random
import time
from fastapi import FastAPI, Response, Request
import httpx
from .telemetry import configure

SERVICE = os.getenv("SERVICE_NAME", "api-gateway")
UPSTREAM = os.getenv("UPSTREAM_URL", "")
FAILURE = os.getenv("FAILURE_MODE", "")
app = FastAPI(title=SERVICE)
telemetry = configure(SERVICE)
tracer = telemetry["tracer"]


@app.get("/health")
def health():
    return {"service": SERVICE, "status": "ok"}


@app.get("/checkout")
async def checkout(request: Request, response: Response):
    started = time.perf_counter()
    attrs = {"http.route": "/checkout", "service.name": SERVICE}
    with tracer.start_as_current_span("GET /checkout", attributes=attrs) as span:
        telemetry["requests"].add(1, {"route": "/checkout"})
        request_id = request.headers.get("x-request-id", f"demo-{int(time.time() * 1000)}")
        try:
            if FAILURE == "crash":
                response.status_code = 503
                raise RuntimeError("injected crash")
            if FAILURE == "latency":
                time.sleep(1.0)
            if UPSTREAM:
                try:
                    async with httpx.AsyncClient(timeout=0.8) as client:
                        await client.get(UPSTREAM, headers={"x-request-id": request_id})
                except Exception as exc:
                    response.status_code = 504
                    telemetry["errors"].add(1, {"route": "/checkout", "status_code": "504"})
                    telemetry["logger"].error("upstream timeout", extra={"request_id": request_id, "error.type": type(exc).__name__})
                    span.record_exception(exc)
                    span.set_attribute("http.status_code", 504)
                    return {"error": "upstream timeout", "service": SERVICE}
            if FAILURE == "error" or random.random() < 0.01:
                response.status_code = 500
                telemetry["errors"].add(1, {"route": "/checkout", "status_code": "500"})
                telemetry["logger"].error("injected internal failure", extra={"request_id": request_id})
                span.set_attribute("http.status_code", 500)
                return {"error": "injected failure"}
            elapsed = round((time.perf_counter() - started) * 1000, 1)
            span.set_attribute("http.status_code", 200)
            span.set_attribute("request.id", request_id)
            telemetry["logger"].info("checkout completed", extra={"request_id": request_id, "duration_ms": elapsed})
            return {"service": SERVICE, "latency_ms": elapsed}
        except Exception as exc:
            response.status_code = 503
            telemetry["errors"].add(1, {"route": "/checkout", "status_code": "503"})
            telemetry["logger"].exception("service failure", extra={"request_id": request_id})
            span.record_exception(exc)
            span.set_attribute("http.status_code", 503)
            return {"error": str(exc), "service": SERVICE}
        finally:
            elapsed = round((time.perf_counter() - started) * 1000, 1)
            if elapsed and elapsed < 100000:
                telemetry["latency"].record(elapsed, {"route": "/checkout"})
