"""Run the reproducible payment-timeout incident from a shell."""
import json, urllib.request

def post(url, body):
    req=urllib.request.Request(url, data=json.dumps(body).encode(), headers={'content-type':'application/json'}, method='POST')
    with urllib.request.urlopen(req) as r: return json.load(r)

base='http://127.0.0.1:8000'
print(json.dumps(post(base+'/api/v1/demo/run-scenario', {'scenario':'payment_timeout'}), indent=2))
incident=post(base+'/api/v1/analyze', {})
print(json.dumps({k:incident.get(k) for k in ('id','title','root_cause_service','root_cause','confidence','affected_services')}, indent=2))
