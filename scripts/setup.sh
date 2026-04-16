#!/usr/bin/env bash
set -euo pipefail

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${CYAN}[setup]${NC} $*"; }
ok()    { echo -e "${GREEN}[ok]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }
die()   { echo -e "${RED}[error]${NC} $*"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$ROOT_DIR"

# ── Prerequisites ──────────────────────────────────────────────────────────────
info "Checking prerequisites..."
command -v ollama >/dev/null || die "Ollama not found. Install from https://ollama.ai"
command -v docker  >/dev/null || die "Docker not found."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 required."
ok "Prerequisites satisfied"

# ── .env ──────────────────────────────────────────────────────────────────────
if [[ ! -f .env ]]; then
    cp .env.example .env
    ok "Created .env from .env.example"
else
    warn ".env already exists — skipping"
fi

source .env
LLM_MODEL="${LLM_MODEL:-llama3.1:8b-q4_K_M}"
EMBED_MODEL="${EMBED_MODEL:-nomic-embed-text}"

# ── Start Ollama (native) ─────────────────────────────────────────────────────
info "Starting Ollama service..."
if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    ollama serve &>/tmp/ollama.log &
    OLLAMA_PID=$!
    info "Waiting for Ollama to start (pid $OLLAMA_PID)..."
    for i in $(seq 1 20); do
        sleep 1
        curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 && break
    done
    curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 || die "Ollama failed to start. Check /tmp/ollama.log"
fi
ok "Ollama is running"

# ── Pull models ────────────────────────────────────────────────────────────────
info "Pulling LLM model: $LLM_MODEL"
ollama pull "$LLM_MODEL"
ok "LLM model ready: $LLM_MODEL"

info "Pulling embedding model: $EMBED_MODEL"
ollama pull "$EMBED_MODEL"
ok "Embedding model ready: $EMBED_MODEL"

# ── Start Docker stack ────────────────────────────────────────────────────────
info "Starting Docker services (Qdrant, Redis, API, Open WebUI)..."
docker compose pull --quiet qdrant redis open-webui
docker compose up --build -d
ok "Docker stack started"

# ── Wait for API health ───────────────────────────────────────────────────────
info "Waiting for API to become healthy..."
for i in $(seq 1 30); do
    sleep 2
    STATUS=$(curl -sf http://localhost:4000/health 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null || echo "waiting")
    [[ "$STATUS" == "ok" ]] && break
done

FINAL=$(curl -sf http://localhost:4000/health 2>/dev/null || echo '{"status":"unreachable"}')
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  localAIStack ready${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  API:         http://localhost:4000"
echo "  API docs:    http://localhost:4000/docs"
echo "  Open WebUI:  http://localhost:4080"
echo "  Qdrant UI:   http://localhost:4333/dashboard"
echo "  Ollama:      http://localhost:11434"
echo ""
echo "  Health:      $FINAL"
echo ""
echo "  Next steps:"
echo "    make ingest FILE=./data/documents/your_doc.pdf"
echo "    make query  Q='What is in my knowledge base?'"
echo ""
