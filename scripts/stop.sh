#!/usr/bin/env bash
set -euo pipefail

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${CYAN}[stop]${NC} $*"; }
ok()   { echo -e "${GREEN}[ok]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# ── Docker services ───────────────────────────────────────────────────────────
# NOTE: This stops Qdrant, which is also the unified vector store for RealityEngine_AI.
#       Stop the Reality Engine first if it is running:
#         cd ../RealityEngine_AI && ./scripts/stop.sh
info "Stopping Docker services (Qdrant, Redis, API, WebUI, Loki, Grafana)..."
docker compose down
ok "Docker services stopped"

# ── Ollama ────────────────────────────────────────────────────────────────────
# Only stop Ollama if WE started it (pid file exists).
# Avoids killing an Ollama instance the user started separately.
if [[ -f /tmp/ollama.pid ]]; then
    PID=$(cat /tmp/ollama.pid)
    if kill -0 "$PID" 2>/dev/null; then
        info "Stopping Ollama (pid $PID)..."
        kill "$PID"
        rm -f /tmp/ollama.pid
        ok "Ollama stopped"
    else
        warn "Ollama pid $PID not running — skipping"
        rm -f /tmp/ollama.pid
    fi
else
    warn "Ollama was not started by this stack — leaving it running"
fi

echo ""
echo -e "${GREEN}localAIStack stopped.${NC}"
echo "  Data persists in ./volumes/  — run ./scripts/start.sh to resume."
echo "  Qdrant (unified vector store) is now offline."
echo "  If RealityEngine_AI is running, restart it after: cd ../RealityEngine_AI && ./scripts/start.sh"
echo ""
