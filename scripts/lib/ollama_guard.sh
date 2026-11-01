#!/usr/bin/env bash

# Ollama is never pinned (owner rule, RealityEngine_CI#663): the host follows the
# latest public release. config/models.registry.json `runtime` holds the two
# facts this needs and nothing else:
#
#   min_version  the floor the stack needs. Below it nothing runs.
#   latest_from  the release feed the host follows.
#
# A host missing Ollama, below the floor, or behind the latest release is
# installed/upgraded to the latest release. If the feed cannot be reached, a
# host that clears the floor is used as is (with a warning); one below it is
# upgraded with the installer's default (latest) and must clear the floor
# afterwards or the scripts stop.
#
# OLLAMA_FOLLOW_LATEST=0 keeps a host that clears the floor where it is
# (offline work). It never permits running below the floor.
#
# The fallback below is used only if the registry is missing or unreadable. It
# is the same floor, so a broken registry cannot quietly lower it.
_OLLAMA_FALLBACK_MIN="0.40.13"
_OLLAMA_FALLBACK_FEED="https://api.github.com/repos/ollama/ollama/releases/latest"

_ollama_registry_file() {
    if [[ -n "${MODELS_REGISTRY_PATH:-}" ]]; then
        echo "$MODELS_REGISTRY_PATH"
        return 0
    fi
    local lib_dir
    lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    echo "$(cd "$lib_dir/../.." && pwd)/config/models.registry.json"
}

_ollama_runtime_field() {
    local field="$1" file
    file="$(_ollama_registry_file)"
    [[ -f "$file" ]] && command -v python3 >/dev/null 2>&1 || return 0
    python3 -c '
import json, sys
try:
    reg = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
runtime = reg.get("runtime")
if isinstance(runtime, dict):
    print(runtime.get(sys.argv[2]) or "")
' "$file" "$field" 2>/dev/null
}

read_min_ollama_version() {
    local version
    version="$(_ollama_runtime_field min_version)"
    echo "${version:-$_OLLAMA_FALLBACK_MIN}"
}

read_ollama_release_feed() {
    local feed
    feed="$(_ollama_runtime_field latest_from)"
    echo "${feed:-$_OLLAMA_FALLBACK_FEED}"
}

OLLAMA_MIN_VERSION="$(read_min_ollama_version)"
OLLAMA_RELEASE_FEED="$(read_ollama_release_feed)"

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

# The latest public release, from the feed; empty if it cannot be reached.
latest_ollama_version() {
    local tag
    tag="$(curl -sf --max-time 10 "$OLLAMA_RELEASE_FEED" 2>/dev/null \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tag_name",""))' 2>/dev/null || true)"
    extract_semver "${tag:-}"
}

installed_ollama_version() {
    command -v ollama >/dev/null 2>&1 || return 0
    extract_semver "$(ollama --version 2>&1 || true)"
}

# Install or upgrade through the official installer. With a version, that
# release; without one, the installer's default, which is the latest release.
install_or_upgrade_ollama() {
    local version="${1:-}" label os
    label="${version:+v$version}"
    label="${label:-the latest release}"
    os="$(uname -s)"
    case "$os" in
        Linux|Darwin) ;;
        *)
            _ollama_error "Unsupported OS '$os'. Install Ollama ${label} manually: https://ollama.com/download"
            return 1
            ;;
    esac

    if ! command -v curl >/dev/null 2>&1; then
        _ollama_error "curl is required to install Ollama ${label} automatically."
        return 1
    fi

    local install_script
    install_script="$(mktemp "${TMPDIR:-/tmp}/ollama-install.XXXXXX")"
    _ollama_info "Installing/upgrading Ollama to ${label} (may request sudo)..."
    if ! curl -fsSL "https://raw.githubusercontent.com/ollama/ollama/main/scripts/install.sh" -o "$install_script"; then
        rm -f "$install_script"
        _ollama_error "Failed to download the Ollama installer. Install ${label} manually from https://ollama.com/download"
        return 1
    fi

    local -a env_args=()
    [[ -n "$version" ]] && env_args+=("OLLAMA_VERSION=$version")
    [[ "$os" == "Darwin" ]] && env_args+=("OLLAMA_NO_START=1")
    if ! env ${env_args[@]+"${env_args[@]}"} sh "$install_script"; then
        rm -f "$install_script"
        _ollama_error "Automatic Ollama install failed on ${os}. Install ${label} manually from https://ollama.com/download"
        return 1
    fi
    rm -f "$install_script"
}

ensure_ollama_current() {
    local floor="$OLLAMA_MIN_VERSION"
    local follow="${OLLAMA_FOLLOW_LATEST:-1}"
    local latest="" current

    latest="$(latest_ollama_version)"
    if [[ -n "$latest" && "$(semver_compare "$latest" "$floor")" -lt 0 ]]; then
        _ollama_warn "Release feed reports v${latest}, below the v${floor} floor in config/models.registry.json — the floor is wrong."
    fi

    current="$(installed_ollama_version)"
    if [[ -z "$current" ]]; then
        _ollama_warn "Ollama is not installed."
        install_or_upgrade_ollama "$latest" || return 1
        current="$(installed_ollama_version)"
    elif [[ "$(semver_compare "$current" "$floor")" -lt 0 ]]; then
        _ollama_warn "Found Ollama v${current}; the stack needs v${floor} or newer."
        install_or_upgrade_ollama "$latest" || return 1
        current="$(installed_ollama_version)"
    elif [[ "$follow" != "0" && -n "$latest" && "$(semver_compare "$current" "$latest")" -lt 0 ]]; then
        _ollama_info "Ollama v${current} is behind the latest release v${latest}; upgrading."
        if install_or_upgrade_ollama "$latest"; then
            current="$(installed_ollama_version)"
        else
            _ollama_warn "Upgrade to v${latest} failed; continuing on v${current}, which clears the v${floor} floor."
        fi
    fi

    [[ -n "$current" ]] \
        || { _ollama_error "Unable to read the installed Ollama version. The stack needs v${floor} or newer."; return 1; }
    [[ "$(semver_compare "$current" "$floor")" -ge 0 ]] \
        || { _ollama_error "Ollama v${current} is below the v${floor} floor. Install the latest from https://ollama.com/download"; return 1; }

    if [[ -z "$latest" ]]; then
        _ollama_warn "Could not reach ${OLLAMA_RELEASE_FEED}; using Ollama v${current} (clears the v${floor} floor) without checking for a newer release."
    elif [[ "$follow" == "0" && "$(semver_compare "$current" "$latest")" -lt 0 ]]; then
        _ollama_warn "OLLAMA_FOLLOW_LATEST=0: staying on Ollama v${current} (latest is v${latest}, floor v${floor})."
    else
        _ollama_info "Using Ollama v${current} (latest v${latest}, floor v${floor})"
    fi
}

# Report floor vs installed vs latest. Never installs anything. Fails when the
# host is below the floor or behind the latest release.
ollama_check() {
    local floor installed latest status=0
    floor="$OLLAMA_MIN_VERSION"
    installed="$(installed_ollama_version)"
    latest="$(latest_ollama_version)"

    echo "floor     (config/models.registry.json): v${floor}"
    echo "installed (this host):                   ${installed:+v}${installed:-not installed}"
    echo "latest    (${OLLAMA_RELEASE_FEED}): ${latest:+v}${latest:-unknown}"

    if [[ -z "$installed" ]]; then
        echo ""
        echo "Ollama is not installed; 'make up' installs the latest release."
        status=1
    elif [[ "$(semver_compare "$installed" "$floor")" -lt 0 ]]; then
        echo ""
        echo "Below the floor: the stack will not start. 'make up' upgrades to the latest release."
        status=1
    elif [[ -n "$latest" && "$(semver_compare "$installed" "$latest")" -lt 0 ]]; then
        echo ""
        echo "Behind the latest release: 'make up' upgrades to v${latest}."
        status=1
    fi
    return "$status"
}

case "${1:-}" in
    --ensure) ensure_ollama_current ;;
    --check)  ollama_check ;;
    --print)  echo "$OLLAMA_MIN_VERSION" ;;
esac
