# Polyphony - Makefile for common tasks

.PHONY: help install test lint format preview dev down clean health db-shell

help:
	@echo "Polyphony - Available commands:"
	@echo "  make install   - Install Python dependencies"
	@echo "  make test      - Run the test suite (this is the CI ship gate)"
	@echo "  make lint      - black --check + ruff"
	@echo "  make format    - black + ruff --fix"
	@echo "  make dev       - Start the local dev stack (compose profile: dev)"
	@echo "  make preview   - Start the as-shipped stack (compose profile: preview)"
	@echo "  make down      - Stop the stack"
	@echo "  make health    - Check app health"
	@echo "  make clean     - Remove containers and volumes (WARNING: destroys data)"

install:
	pip install -r requirements.txt

test:
	python -m pytest tests -q --no-cov

lint:
	black --check app tests
	ruff check app tests

format:
	black app tests
	ruff check --fix app tests

dev:
	docker compose --profile dev up --build

preview:
	docker compose --profile preview up --build

down:
	docker compose --profile dev --profile preview down

health:
	@curl -s http://localhost:8000/health | python3 -m json.tool || echo "app down"

clean:
	docker compose --profile dev --profile preview down -v

db-shell:
	docker compose exec postgres psql -U postgres -d polyphony
