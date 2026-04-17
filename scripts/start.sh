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

# ── Docker services ───────────────────────────────────────────────────────────
info "Starting Docker services..."
docker compose up -d
ok "Docker services up"

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
echo ""
echo "  Unified Qdrant collections (localhost:4333):"
echo "    localai_docs     — document embeddings (768-dim, nomic-embed-text)"
echo "    reality-vectors  — RE perceptual vectors (768-dim, auto-created on RE startup)"
echo ""
echo "  To start RealityEngine_AI (uses this Qdrant):"
echo "    cd ../RealityEngine_AI && ./scripts/start.sh"
echo ""
echo "Service status:"
echo "$HEALTH"
echo ""
