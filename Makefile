.PHONY: setup start stop up down logs health query ingest models clean

# ── Lifecycle ─────────────────────────────────────────────────────────────────
setup:
	@bash scripts/setup.sh

start:
	@bash scripts/start.sh

stop:
	@bash scripts/stop.sh

restart: stop start

up:
	@ollama serve &>/tmp/ollama.log & sleep 1
	@docker compose up -d

down:
	@docker compose down

# ── Observability ─────────────────────────────────────────────────────────────
logs:
	@docker compose logs -f

logs-api:
	@docker compose logs -f api

health:
	@curl -s http://localhost:8000/health | python3 -m json.tool

models:
	@curl -s http://localhost:11434/api/tags | python3 -c \
		"import sys,json; [print(' ', m['name']) for m in json.load(sys.stdin).get('models',[])]"

# ── RAG operations ────────────────────────────────────────────────────────────
# Usage: make ingest FILE=./data/documents/spec.pdf
ingest:
	@python3 scripts/ingest.py $(FILE)

# Usage: make query Q="What is the reality engine?"
query:
	@curl -s -X POST http://localhost:8000/graph/rag \
		-H "Content-Type: application/json" \
		-d '{"question": "$(Q)"}' | python3 -m json.tool

# Usage: make agent Q="Search the knowledge base for X"
agent:
	@curl -s -X POST http://localhost:8000/graph/agent \
		-H "Content-Type: application/json" \
		-d '{"messages": [{"role": "user", "content": "$(Q)"}]}' | python3 -m json.tool

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:
	@docker compose down -v
	@rm -rf volumes/qdrant/* volumes/redis/* volumes/open-webui/*
	@echo "Volumes cleared. Run 'make setup' to reinitialize."
