.PHONY: check lint format typecheck test scan openapi

check: lint typecheck test scan

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .

typecheck:
	mypy app tests

test:
	pytest

scan:
	python scripts/scan_forbidden.py

openapi:
	python scripts/export_openapi.py
