.PHONY: install scanner frontend dev test lint eval-local

install:
	agents-cli install
	cd scanner && npm install && npx playwright install chromium
	cd frontend && npm install && npm run build

scanner:
	cd scanner && npm run scan -- --pretty --wcag-level AA --allow-private-network http://127.0.0.1:8000/demo/original/

frontend:
	cd frontend && npm run build

dev:
	ACCESSPILOT_DETERMINISTIC_SPECIALISTS=true uv run uvicorn app.fast_api_app:app --host 127.0.0.1 --port 8000 --reload

test:
	uv run pytest -q tests/unit tests/integration

lint:
	uv run ruff check app tests scripts
	uv run ruff format --check app tests scripts

eval-local:
	uv run python scripts/generate_local_eval.py
	uv run python scripts/grade_local_eval.py
