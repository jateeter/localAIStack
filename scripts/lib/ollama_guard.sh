#!/usr/bin/env bash

# The pinned Ollama version lives in config/models.registry.json under
# `runtime.pinned_version` — the same registry that declares the models. Nothing
# here hardcodes a version; the fallback below exists only so `make up` still
# works if the registry is missing or unreadable, and is deliberately old enough
# to be obviously a fallback rather than a second source of truth.
#
# The pin is a FLOOR. An older Ollama is upgraded to it; a newer one is left
# alone rather than downgraded. Export OLLAMA_STRICT_PIN=1 to require the exact
# version instead (reproducibility runs, bisecting an upstream regression).
_OLLAMA_FALLBACK_VERSION="0.32.0"

_ollama_registry_file() {
    if [[ -n "${MODELS_REGISTRY_PATH:-}" ]]; then
        echo "$MODELS_REGISTRY_PATH"
        return 0
    fi
    local lib_dir
    lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    echo "$(cd "$lib_dir/../.." && pwd)/config/models.registry.json"
}

read_pinned_ollama_version() {
    local file version
    file="$(_ollama_registry_file)"
    if [[ -f "$file" ]] && command -v python3 >/dev/null 2>&1; then
        version="$(python3 -c '
import json, sys
try:
    reg = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
runtime = reg.get("runtime")
if isinstance(runtime, dict):
    print(runtime.get("pinned_version") or "")
' "$file" 2>/dev/null)"
    fi
    echo "${version:-$_OLLAMA_FALLBACK_VERSION}"
}

OLLAMA_VERSION="$(read_pinned_ollama_version)"

_ollama_info() {
    if declare -F info >/dev/null 2>&1; then
        info "$@"
    else
        echo "[ollama] $*"
    fi
}

_ollama_warn() {
    if declare -F warn >/dev/null 2>&1; then
        warn "$@"
    else
        echo "[ollama][warn] $*"
    fi
}

_ollama_error() {
    if declare -F die >/dev/null 2>&1; then
        die "$@"
    else
        echo "[ollama][error] $*" >&2
    fi
    return 1
}

extract_semver() {
    local input="${1:-}"
    grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' <<<"$input" | head -1
}

semver_compare() {
    local left="${1:-0.0.0}" right="${2:-0.0.0}"
    local IFS=.
    local left_parts right_parts
    read -r -a left_parts <<<"$left"
    read -r -a right_parts <<<"$right"

    for i in 0 1 2; do
        local l="${left_parts[$i]:-0}" r="${right_parts[$i]:-0}"
        if ((10#$l > 10#$r)); then
            echo 1
            return 0
        elif ((10#$l < 10#$r)); then
            echo -1
            return 0
        fi
    done
    echo 0
}

install_or_upgrade_ollama_pinned() {
    local os
    os="$(uname -s)"
    case "$os" in
        Linux|Darwin) ;;
        *)
            _ollama_error "Unsupported OS '$os'. Install Ollama v${OLLAMA_VERSION} manually: https://ollama.com/download?version=${OLLAMA_VERSION}"
            return 1
            ;;
    esac

    if ! command -v curl >/dev/null 2>&1; then
        _ollama_error "curl is required to install Ollama v${OLLAMA_VERSION} automatically."
        return 1
    fi

    local install_script="/tmp/ollama-install-${OLLAMA_VERSION}.sh"
    _ollama_info "Installing/upgrading Ollama to v${OLLAMA_VERSION} (may request sudo)..."
    if ! curl -fsSL "https://raw.githubusercontent.com/ollama/ollama/main/scripts/install.sh" -o "$install_script"; then
        _ollama_error "Failed to download Ollama installer. Install manually from https://ollama.com/download?version=${OLLAMA_VERSION}"
        return 1
    fi

    chmod +x "$install_script"
    if [[ "$os" == "Darwin" ]]; then
        OLLAMA_NO_START=1 OLLAMA_VERSION="$OLLAMA_VERSION" sh "$install_script" \
            || _ollama_error "Automatic Ollama install failed on macOS. Install v${OLLAMA_VERSION} manually from https://ollama.com/download?version=${OLLAMA_VERSION}"
    else
        OLLAMA_VERSION="$OLLAMA_VERSION" sh "$install_script" \
            || _ollama_error "Automatic Ollama install failed on Linux. Install v${OLLAMA_VERSION} manually from https://ollama.com/download?version=${OLLAMA_VERSION}"
    fi
}

ensure_ollama_pinned_version() {
    local required="$OLLAMA_VERSION"
    local strict="${OLLAMA_STRICT_PIN:-0}"

    if ! command -v ollama >/dev/null 2>&1; then
        _ollama_warn "Ollama is not installed."
        install_or_upgrade_ollama_pinned || return 1
    fi

    local raw_version current_version cmp
    raw_version="$(ollama --version 2>&1 || true)"
    current_version="$(extract_semver "$raw_version")"
    [[ -n "$current_version" ]] \
        || _ollama_error "Unable to parse installed Ollama version from: '$raw_version'. Expected v${required} or newer."

    cmp="$(semver_compare "$current_version" "$required")"

    # Newer than the pin: leave it alone. Downgrading a host that has moved
    # ahead of the repo breaks the developer's other work to satisfy a floor
    # they already clear. Strict mode opts back into exact matching.
    if [[ "$cmp" -eq 1 ]]; then
        if [[ "$strict" == "1" ]]; then
            _ollama_warn "OLLAMA_STRICT_PIN=1 and found v${current_version}; reinstalling exactly v${required}."
        else
            _ollama_info "Using Ollama v${current_version} (ahead of the v${required} pin — run 'make ollama-check' to see if the pin is stale)"
            return 0
        fi
    fi

    if [[ "$cmp" -ne 0 ]]; then
        _ollama_warn "Found Ollama v${current_version}; this repo requires v${required} or newer. Installing pinned version."
        install_or_upgrade_ollama_pinned || return 1
        raw_version="$(ollama --version 2>&1 || true)"
        current_version="$(extract_semver "$raw_version")"
        [[ -n "$current_version" ]] \
            || _ollama_error "Unable to parse Ollama version after install. Output: '$raw_version'"
        cmp="$(semver_compare "$current_version" "$required")"
        [[ "$cmp" -ge 0 ]] \
            || _ollama_error "Expected Ollama v${required} or newer, found v${current_version}. Please install v${required} manually: https://ollama.com/download?version=${required}"
    fi

    _ollama_info "Using pinned Ollama v${current_version}"
}

# Report pinned vs latest-upstream vs installed. Never installs anything —
# this is the "is our pin stale?" command, and the same comparison the
# ollama-pin workflow makes on a schedule.
ollama_check() {
    local pinned installed latest
    pinned="$OLLAMA_VERSION"
    installed="$(command -v ollama >/dev/null 2>&1 && extract_semver "$(ollama --version 2>&1)" || echo "not installed")"
    latest="$(curl -sf --max-time 10 https://api.github.com/repos/ollama/ollama/releases/latest 2>/dev/null \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tag_name",""))' 2>/dev/null || true)"
    latest="$(extract_semver "${latest:-}")"

    echo "pinned    (config/models.registry.json): v${pinned}"
    if [[ "$installed" =~ ^[0-9] ]]; then
        installed="v${installed}"
    fi
    echo "installed (this host):                   ${installed}"
    echo "latest    (ollama/ollama releases):      ${latest:+v}${latest:-unknown}"

    if [[ -n "$latest" && "$(semver_compare "$latest" "$pinned")" -eq 1 ]]; then
        echo ""
        echo "Pin is behind upstream. .github/workflows/ollama-pin.yml opens a PR for this"
        echo "weekly; to bump now, set runtime.pinned_version to ${latest} in"
        echo "config/models.registry.json."
        return 1
    fi
    return 0
}

case "${1:-}" in
    --ensure) ensure_ollama_pinned_version ;;
    --check)  ollama_check ;;
    --print)  echo "$OLLAMA_VERSION" ;;
esac
