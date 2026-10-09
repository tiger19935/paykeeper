.PHONY: help sync lint typecheck test test-unit test-integration cov up down logs migrate load reconcile shell

help:
	@echo "paykeeper — common targets"
	@echo "  sync          install dependencies with uv"
	@echo "  lint          ruff check + format check"
	@echo "  typecheck     mypy --strict"
	@echo "  test          run full test suite with coverage"
	@echo "  test-unit     unit tests only"
	@echo "  test-integration integration tests only"
	@echo "  up            docker compose up (postgres + migrator + app)"
	@echo "  down          docker compose down -v"
	@echo "  logs          follow app logs"
	@echo "  migrate       run alembic upgrade head against compose postgres"
	@echo "  load          run k6 charges load test against localhost:8000"
	@echo "  reconcile     paykeeper reconcile --since 24h"

sync:
	uv sync --all-extras

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

typecheck:
	uv run mypy src

test:
	uv run pytest --cov=paykeeper --cov-report=term-missing --cov-report=xml

test-unit:
	uv run pytest tests/unit

test-integration:
	uv run pytest tests/integration -m integration

up:
	docker compose up -d --build

down:
	docker compose down -v

logs:
	docker compose logs -f app

migrate:
	docker compose run --rm migrator

load:
	k6 run load/charges.js

reconcile:
	uv run paykeeper reconcile --since 24h

shell:
	docker compose exec postgres psql -U paykeeper -d paykeeper
