.PHONY: serve lab audit

# Start the mock client API on http://127.0.0.1:8099
serve:
	python -m uvicorn mock_api.server:app --host 127.0.0.1 --port 8099 --reload

# Start the decision lab on http://127.0.0.1:8000 (needs `make serve` in another terminal)
lab:
	python -m uvicorn src.service:app --host 127.0.0.1 --port 8000

# Peek at the side effects your skill has produced against a running server
audit:
	@curl -s http://127.0.0.1:8099/_audit | python -m json.tool
