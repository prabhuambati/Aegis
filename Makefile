install:
	python3 -m pip install -r backend/requirements.txt
run-backend:
	cd backend && uvicorn app.main:app --reload --port 8000
run-frontend:
	cd frontend && npm install && npm run dev
infra-up:
	docker compose up -d --build
infra-down:
	docker compose down -v
scenario:
	curl -s -X POST http://localhost:8000/api/v1/demo/run-scenario -H 'content-type: application/json' -d '{"scenario":"payment_timeout"}'
	curl -s -X POST http://localhost:8000/api/v1/analyze
test:
	cd backend && pytest -q
