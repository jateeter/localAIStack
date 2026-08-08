#!/usr/bin/env bash

# Single source of truth for the Ollama version this repository pins.
OLLAMA_VERSION="0.32.0"

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

    if ! command -v ollama >/dev/null 2>&1; then
        _ollama_warn "Ollama is not installed."
        install_or_upgrade_ollama_pinned || return 1
    fi

    local raw_version current_version cmp
    raw_version="$(ollama --version 2>&1 || true)"
    current_version="$(extract_semver "$raw_version")"
    [[ -n "$current_version" ]] \
        || _ollama_error "Unable to parse installed Ollama version from: '$raw_version'. Expected v${required}."

    cmp="$(semver_compare "$current_version" "$required")"
    if [[ "$cmp" -ne 0 ]]; then
        _ollama_warn "Found Ollama v${current_version}; required v${required}. Reinstalling pinned version."
        install_or_upgrade_ollama_pinned || return 1
        raw_version="$(ollama --version 2>&1 || true)"
        current_version="$(extract_semver "$raw_version")"
        [[ -n "$current_version" ]] \
            || _ollama_error "Unable to parse Ollama version after install. Output: '$raw_version'"
        cmp="$(semver_compare "$current_version" "$required")"
        [[ "$cmp" -eq 0 ]] \
            || _ollama_error "Expected Ollama v${required}, found v${current_version}. Please install v${required} manually: https://ollama.com/download?version=${required}"
    fi

    _ollama_info "Using pinned Ollama v${required}"
}

if [[ "${1:-}" == "--ensure" ]]; then
    ensure_ollama_pinned_version
fi
