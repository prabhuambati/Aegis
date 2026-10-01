import os, tempfile, time
from fastapi.testclient import TestClient
os.environ["OBS_DB_PATH"] = os.path.join(tempfile.gettempdir(), "aegis-test.db")
from app.main import app
from app.storage import store

client=TestClient(app)

def setup_function(): store.reset()

def test_payment_timeout_produces_grounded_root_cause():
    r=client.post('/api/v1/demo/run-scenario',json={'scenario':'payment_timeout'}); assert r.status_code==200
    incident=client.post('/api/v1/analyze').json()
    assert incident['root_cause_service']=='payment-service'
    assert incident['confidence']>0.5
    assert any(e.get('service')=='payment-service' for e in incident['evidence'])

def test_ingestion_and_trace_filter():
    payload={'items':[{'kind':'span','timestamp':1,'service_name':'test','trace_id':'trace-1','span_id':'span-1','duration_ms':12,'status_code':200}]}
    assert client.post('/api/v1/telemetry/batch',json=payload).json()['accepted']==1
    assert client.get('/api/v1/traces?trace_id=trace-1').json()[0]['trace_id']=='trace-1'

def test_investigation_is_grounded():
    client.post('/api/v1/demo/run-scenario',json={'scenario':'payment_timeout'}); inc=client.post('/api/v1/analyze').json()
    ans=client.post('/api/v1/investigate',json={'question':'Which service caused payment failures?','incident_id':inc['id']}).json()
    assert ans['grounded'] is True and ans['citations']


def test_overview_uses_request_level_window_and_active_incident_health():
    client.post('/api/v1/demo/run-scenario', json={'scenario': 'payment_timeout'})
    incident = client.post('/api/v1/analyze').json()
    result = client.get('/api/v1/overview').json()
    assert result['aggregation_window_seconds'] == 300
    assert result['health'] == 'critical'
    assert result['active_incidents'] == 1
    assert result['error_rate'] == round(10 / 36, 3)
    assert result['request_rate'] == round(36 / 300, 3)
    assert result['p95_latency_ms'] >= 1400
    assert result['services']['payment-service'] == 'failing'
    assert any(metric['metric_name'] == 'cpu_percent' for metric in result['metrics'])
    assert incident['root_cause_service'] == 'payment-service'


def test_metrics_are_bounded_and_include_normalized_series():
    client.post('/api/v1/demo/run-scenario', json={'scenario': 'payment_timeout'})
    rows = client.get('/api/v1/metrics?window_seconds=300').json()
    names = {row['metric_name'] for row in rows}
    assert {'cpu_percent', 'error_rate'} <= names
    assert all(row['timestamp'] >= time.time() - 300 for row in rows)


def test_services_include_dependency_targets_and_investigation_resolves_citations():
    client.post('/api/v1/demo/run-scenario', json={'scenario': 'payment_timeout'})
    incident = client.post('/api/v1/analyze').json()
    services = {row['name'] for row in client.get('/api/v1/services').json()}
    assert {'orders-db', 'users-db', 'payments-db'} <= services
    answer = client.post('/api/v1/investigate', json={
        'question': 'Why did checkout latency increase?',
        'incident_id': incident['id'],
    }).json()
    assert answer['citation_records']
    assert all(record['resolved'] for record in answer['citation_records'])
    assert {record['id'] for record in answer['citation_records']} <= set(answer['citations'])


def test_llm_rejects_unsupported_citations(monkeypatch):
    from types import SimpleNamespace
    from app import llm

    class FakeResponse:
        def raise_for_status(self): pass
        def json(self):
            return {'choices': [{'message': {'content': '{"explanation":"unsupported","citations":["not-evidence"]}'}}]}

    monkeypatch.setattr(llm, 'settings', SimpleNamespace(
        llm_provider='openai', openai_api_key='test-key', llm_base_url='http://llm',
        llm_model='test', llm_timeout_seconds=1,
    ))
    monkeypatch.setattr(llm.httpx, 'post', lambda *args, **kwargs: FakeResponse())
    result=llm.explain_incident({'summary':'safe local explanation','evidence':[{'id':'ev-1'}]})
    assert result['provider']=='local-fallback'
    assert result['citations']==['ev-1']
