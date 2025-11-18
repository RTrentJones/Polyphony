# Polyphony - Makefile for common tasks

.PHONY: help build up down logs test clean restart health

help:
	@echo "Polyphony - Available commands:"
	@echo "  make build     - Build all Docker images"
	@echo "  make up        - Start all services"
	@echo "  make down      - Stop all services"
	@echo "  make restart   - Restart all services"
	@echo "  make logs      - View logs (all services)"
	@echo "  make health    - Check health of all services"
	@echo "  make test      - Run tests"
	@echo "  make clean     - Remove all containers and volumes (WARNING: destroys data)"
	@echo "  make shell     - Open shell in API gateway container"

build:
	docker-compose build

up:
	docker-compose up -d
	@echo "Services starting..."
	@sleep 5
	@echo "Services should be ready at:"
	@echo "  - API Gateway:  http://localhost:8000"
	@echo "  - Prometheus:   http://localhost:9090"
	@echo "  - Grafana:      http://localhost:3001"

down:
	docker-compose down

restart:
	docker-compose restart

logs:
	docker-compose logs -f

health:
	@echo "Checking service health..."
	@curl -s http://localhost:8000/health | jq . || echo "❌ API Gateway down"
	@curl -s http://localhost:8001/health | jq . || echo "❌ Orchestrator down"
	@curl -s http://localhost:8002/health | jq . || echo "❌ Hermione Agent down"
	@curl -s http://localhost:8003/health | jq . || echo "❌ Harry Agent down"
	@curl -s http://localhost:8004/health | jq . || echo "❌ Ron Agent down"
	@curl -s http://localhost:8005/health | jq . || echo "❌ Document Parser down"

test:
	pytest tests/ -v

clean:
	docker-compose down -v
	docker system prune -f

shell:
	docker-compose exec api-gateway /bin/bash

# Individual service commands
logs-api:
	docker-compose logs -f api-gateway

logs-hermione:
	docker-compose logs -f character-hermione

logs-parser:
	docker-compose logs -f document-parser

# Database commands
db-shell:
	docker-compose exec postgres psql -U postgres -d polyphony

db-init:
	docker-compose exec postgres psql -U postgres -d polyphony -f /docker-entrypoint-initdb.d/init.sql
