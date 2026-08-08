#!/usr/bin/env bash
set -euo pipefail

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info() { echo -e "${CYAN}[start]${NC} $*"; }
ok()   { echo -e "${GREEN}[ok]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
die()  { echo -e "${RED}[error]${NC} $*"; exit 1; }

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

[[ -f .env ]] || { warn ".env not found — copying from .env.example"; cp .env.example .env; }
# shellcheck source=/dev/null
set -a; source .env; set +a

# ── Ollama (native) ───────────────────────────────────────────────────────────
if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    ok "Ollama already running"
else
    info "Starting Ollama..."
    ollama serve >>/tmp/ollama.log 2>&1 &
    echo $! > /tmp/ollama.pid
    for i in $(seq 1 15); do
        sleep 1
        curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 && break
    done
    curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 || die "Ollama failed to start — check /tmp/ollama.log"
    ok "Ollama started (pid $(cat /tmp/ollama.pid))"
fi

# ── Loki Docker plugin ────────────────────────────────────────────────────────
# The Loki log driver is a host-level Docker plugin.  The qdrant/redis/api/
# open-webui services use `logging.driver: loki` so this plugin MUST be enabled
# before `docker compose up` — otherwise container creation fails.
if docker plugin ls --format '{{.Name}} {{.Enabled}}' | grep -qE '^loki.* true$'; then
    ok "Loki Docker plugin enabled"
elif docker plugin ls --format '{{.Name}}' | grep -qE '^loki'; then
    info "Loki Docker plugin present but disabled — enabling..."
    docker plugin enable loki >/dev/null
    ok "Loki Docker plugin enabled"
else
    info "Installing Loki Docker plugin (one-time)..."
    docker plugin install grafana/loki-docker-driver:latest --alias loki --grant-all-permissions >/dev/null
    ok "Loki Docker plugin installed"
fi

# ── Docker services ───────────────────────────────────────────────────────────
# --build ensures the api image is rebuilt whenever services/api/requirements.txt
# or the Dockerfile change.  Build cache makes this a no-op when nothing moved.
info "Starting Docker services..."
docker compose up -d --build
ok "Docker services up"

# ── Wait for Loki + Grafana ──────────────────────────────────────────────────
# Mirrors startUniverse.sh Phase 3: confirm the logging stack is ready before
# reporting status, so the banner URLs reflect reachable services.
info "Waiting for Loki..."
for i in $(seq 1 30); do
    curl -sf http://localhost:4100/ready >/dev/null 2>&1 && break
    sleep 2
done
curl -sf http://localhost:4100/ready >/dev/null 2>&1 && ok "Loki ready" \
    || warn "Loki not ready after 60s — check:  docker logs localai_loki"

info "Waiting for Grafana..."
for i in $(seq 1 30); do
    curl -sf http://localhost:4002/api/health >/dev/null 2>&1 && break
    sleep 2
done
curl -sf http://localhost:4002/api/health >/dev/null 2>&1 && ok "Grafana ready" \
    || warn "Grafana not ready after 60s — check:  docker logs localai_grafana"

# ── Wait for API ──────────────────────────────────────────────────────────────
info "Waiting for API..."
for i in $(seq 1 30); do
    sleep 2
    STATUS=$(curl -sf http://localhost:4000/health 2>/dev/null \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || echo "waiting")
    [[ "$STATUS" == "ok" ]] && break
done

HEALTH=$(curl -sf http://localhost:4000/health 2>/dev/null \
    | python3 -c "
import sys, json
d = json.load(sys.stdin)
svc = d.get('services', {})
for k, v in svc.items():
    print(f'  {k:<12} {v}')
" 2>/dev/null || echo "  API unreachable")

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  localAIStack running${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  API       http://localhost:4000"
echo "  Docs      http://localhost:4000/docs"
echo "  WebUI     http://localhost:4080"
echo "  Qdrant    http://localhost:4333/dashboard"
echo "  Ollama    http://localhost:11434"
echo "  Grafana   http://localhost:4002           (localAIStack Overview dashboard)"
echo "  Loki API  http://localhost:4100"
echo ""
# Resolve the actual configured embed model / dim (fall back to defaults) for the banner.
EMBED_MODEL_DISPLAY="${EMBED_MODEL:-ternary-bonsai:4}"
EMBED_DIM_DISPLAY="${EMBED_DIM:-768}"
echo "  Unified Qdrant collections (localhost:4333):"
echo "    localai_docs     — document embeddings (${EMBED_DIM_DISPLAY}-dim, ${EMBED_MODEL_DISPLAY})"
echo "    reality-vectors  — RE perceptual vectors (${EMBED_DIM_DISPLAY}-dim, auto-created on RE startup)"
echo ""
echo "  To start RealityEngine_AI (uses this Qdrant):"
echo "    cd ../RealityEngine_AI && ./scripts/start.sh"
echo ""
echo "Service status:"
echo "$HEALTH"
echo ""
# Surface the Bonsai limitation so users don't hit a silent 500 from the
# embedding pipeline or the WebUI model dropdown.  Remove this block once
# Ollama bundles GGML with TQ1_0/BitNet support.
if [[ "$EMBED_MODEL_DISPLAY" == "ternary-bonsai:4" ]]; then
    echo -e "${YELLOW}  ⚠  ternary-bonsai:4 is registered but CANNOT currently run:${NC}"
    echo    "     prism-ml's Q1_0 (BitNet ternary) GGUF is unrecognized by Ollama's"
    echo    "     bundled GGML (file_type=unknown).  Selecting it in Open WebUI or"
    echo    "     using it as EMBED_MODEL returns HTTP 500.  Override in .env, e.g.:"
    echo    "       EMBED_MODEL=nomic-embed-text   EMBED_DIM=768"
    echo ""
fi
