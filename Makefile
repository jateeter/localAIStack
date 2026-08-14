.PHONY: setup start stop up down logs health query ingest models models-installed ollama-check model-pull model-info provider-conformance clean

# ── Lifecycle ─────────────────────────────────────────────────────────────────
setup:
	@bash scripts/setup.sh

start:
	@bash scripts/start.sh

stop:
	@bash scripts/stop.sh

restart: stop start

up:
	@bash scripts/lib/ollama_guard.sh --ensure
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
	@curl -s http://localhost:4000/health | python3 -m json.tool

# ── Model registry ────────────────────────────────────────────────────────────
# Registry = config/models.registry.json (available), .env = selected,
# Ollama = installed. `make models` shows all three; the API serves the same
# join at GET /models.
models:
	@bash scripts/lib/models_registry.sh --list

# Tags actually pulled on the Ollama host, registry or not.
models-installed:
	@curl -s http://localhost:11434/api/tags | python3 -c \
		"import sys,json; [print(' ', m['name']) for m in json.load(sys.stdin).get('models',[])]"

ollama-check:
	@bash scripts/lib/ollama_guard.sh --check

# Usage: make model-pull ID=nemotron-3-nano-4b
model-pull:
	@bash scripts/lib/models_registry.sh --pull $(ID)

# Usage: make model-info ID=nemotron-3-nano-4b   (requires the API to be up)
model-info:
	@curl -s http://localhost:4000/models/$(ID) | python3 -m json.tool

provider-conformance:
	@node --test scripts/pe-completion-conformance.test.mjs

# ── RAG operations ────────────────────────────────────────────────────────────
# Usage: make ingest FILE=./data/documents/spec.pdf
ingest:
	@python3 scripts/ingest.py $(FILE)

# Usage: make query Q="What is the reality engine?"
query:
	@curl -s -X POST http://localhost:4000/graph/rag \
		-H "Content-Type: application/json" \
		-d '{"question": "$(Q)"}' | python3 -m json.tool

# Usage: make agent Q="Search the knowledge base for X"
agent:
	@curl -s -X POST http://localhost:4000/graph/agent \
		-H "Content-Type: application/json" \
		-d '{"messages": [{"role": "user", "content": "$(Q)"}]}' | python3 -m json.tool

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:
	@docker compose down -v
	@rm -rf volumes/qdrant/* volumes/redis/* volumes/open-webui/*
	@echo "Volumes cleared. Run 'make setup' to reinitialize."
