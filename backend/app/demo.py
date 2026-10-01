import time, uuid
from .storage import store

def emit(kind, ts, service, **kw):
    return {"event_id":f"evt-{uuid.uuid4().hex[:12]}","kind":kind,"timestamp":ts,"service_name":service,"attributes":{},**kw}

def scenario(name="payment_timeout"):
    store.reset(); base=time.time()-180; items=[]
    services=["api-gateway","order-service","payment-service","user-service"]
    for i in range(24):
        ts=base+i*5; trace=uuid.uuid4().hex; req=f"req-{i:04d}"
        items += [emit("span",ts,"api-gateway",trace_id=trace,span_id=f"gw-{i}",request_id=req,operation="GET /checkout",duration_ms=120+i%5,status_code=200),emit("span",ts+0.02,"order-service",trace_id=trace,span_id=f"ord-{i}",parent_span_id=f"gw-{i}",request_id=req,operation="POST /orders",duration_ms=85+i%4,status_code=200),emit("span",ts+0.04,"payment-service",trace_id=trace,span_id=f"pay-{i}",parent_span_id=f"ord-{i}",request_id=req,operation="POST /payments",duration_ms=70+i%3,status_code=200),emit("metric",ts,"payment-service",metric_name="cpu_percent",metric_value=38+i%4),emit("metric",ts,"api-gateway",metric_name="error_rate",metric_value=0.01)]
    for i in range(12):
        ts=base+125+i*5; trace=uuid.uuid4().hex; req=f"req-fail-{i:03d}"
        pay=850+i*35; order=pay+140; gw=order+90; status=504 if i>1 else 200
        items += [emit("log",ts,"payment-service",trace_id=trace,request_id=req,severity="ERROR",message="payment provider timeout after 800ms"),emit("span",ts,"payment-service",trace_id=trace,span_id=f"pay-f-{i}",request_id=req,operation="POST /payments",duration_ms=pay,status_code=504,message="upstream payment timeout"),emit("span",ts+0.01,"order-service",trace_id=trace,span_id=f"ord-f-{i}",parent_span_id=f"pay-f-{i}",request_id=req,operation="POST /orders",duration_ms=order,status_code=200),emit("span",ts+0.02,"api-gateway",trace_id=trace,span_id=f"gw-f-{i}",parent_span_id=f"ord-f-{i}",request_id=req,operation="GET /checkout",duration_ms=gw,status_code=status),emit("metric",ts,"payment-service",metric_name="cpu_percent",metric_value=92+i%5),emit("metric",ts,"payment-service",metric_name="error_rate",metric_value=0.42),emit("metric",ts,"order-service",metric_name="error_rate",metric_value=0.18),emit("metric",ts,"api-gateway",metric_name="error_rate",metric_value=0.31)]
    if name=="database_failure":
        for i in range(8):
            ts=base+130+i*5; trace=uuid.uuid4().hex
            items += [emit("log",ts,"orders-db",trace_id=trace,severity="ERROR",message="connection pool exhausted"),emit("span",ts,"orders-db",trace_id=trace,span_id=f"db-{i}",operation="SQL SELECT orders",duration_ms=1200,status_code=503)]
    if name=="traffic_spike":
        for i in range(60):
            ts=base+130+i; items.append(emit("metric",ts,"api-gateway",metric_name="request_rate",metric_value=180+i*3))
    store.insert_items(items)
    return {"scenario":name,"events":len(items),"message":"Normal traffic followed by injected failure telemetry. Run analysis to create the correlated incident."}
