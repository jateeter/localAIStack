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
EMBED_MODEL="${EMBED_MODEL:-ternary-bonsai:4}"

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

# ── Register Ternary Bonsai 4B from local Modelfile ───────────────────────────
# The `ternary-bonsai:4` tag isn't a public Ollama model — we download the GGUF
# from huggingface.co/prism-ml/Bonsai-4B-gguf and register it via Modelfile.
# Local-file registration works on every Ollama version, whereas the
# `FROM hf.co/<user>/<repo>:<quant>` syntax requires Ollama >= 0.4.
BONSAI_DIR="$ROOT_DIR/models/ternary-bonsai"
BONSAI_MODELFILE="$BONSAI_DIR/Modelfile"
BONSAI_GGUF="$BONSAI_DIR/gguf/Bonsai-4B-Q1_0.gguf"
BONSAI_URL="https://huggingface.co/prism-ml/Bonsai-4B-gguf/resolve/main/Bonsai-4B-Q1_0.gguf"

if ollama list | awk '{print $1}' | grep -qx "ternary-bonsai:4"; then
    ok "Ternary Bonsai already registered (ternary-bonsai:4)"
else
    [[ -f "$BONSAI_MODELFILE" ]] || die "Missing $BONSAI_MODELFILE"
    if [[ ! -f "$BONSAI_GGUF" ]]; then
        info "Downloading Bonsai-4B-Q1_0.gguf (~546 MB) from HuggingFace..."
        mkdir -p "$(dirname "$BONSAI_GGUF")"
        curl -L --fail --progress-bar -o "$BONSAI_GGUF" "$BONSAI_URL" \
            || die "GGUF download failed — check connectivity to huggingface.co"
        ok "GGUF downloaded: $BONSAI_GGUF"
    else
        ok "GGUF already present: $BONSAI_GGUF"
    fi
    info "Registering ternary-bonsai:4 from $BONSAI_MODELFILE"
    ollama create ternary-bonsai:4 -f "$BONSAI_MODELFILE" \
        || die "ollama create failed — Ollama may need upgrading (>= 0.4 required for Q1_0 quant). Current: $(ollama --version 2>&1 | head -1)"
    ok "Ternary Bonsai registered — visible in Open WebUI at http://localhost:4080"
    warn "KNOWN ISSUE: prism-ml Bonsai ships only a Q1_0 (BitNet ternary) GGUF, which"
    warn "Ollama's bundled GGML does not yet recognize (file_type=unknown → load fails)."
    warn "Selecting ternary-bonsai:4 or using it for embeddings will 500 until upstream"
    warn "Ollama ships GGML with TQ1_0/BitNet support. Track: github.com/ollama/ollama"
fi

# Optional embedding model — skip silently if it's the Bonsai LLM name (already
# registered above) so setup doesn't try to `ollama pull` a non-existent tag.
if [[ "$EMBED_MODEL" != "ternary-bonsai:4" ]]; then
    info "Pulling embedding model: $EMBED_MODEL"
    ollama pull "$EMBED_MODEL"
    ok "Embedding model ready: $EMBED_MODEL"
fi

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
