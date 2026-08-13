#!/usr/bin/env bash

# Shell reader for config/models.registry.json — the same registry the API
# serves at GET /models. Sourced by setup.sh (selection validation) and invoked
# directly by `make models` / `make model-pull`.
#
# Uses python3 for JSON parsing; the repo already requires it for `make health`.

MODELS_REGISTRY_PATH="${MODELS_REGISTRY_PATH:-}"

_registry_info() {
    if declare -F info >/dev/null 2>&1; then info "$@"; else echo "[models] $*"; fi
}

_registry_warn() {
    if declare -F warn >/dev/null 2>&1; then warn "$@"; else echo "[models][warn] $*"; fi
}

_registry_err() {
    if declare -F die >/dev/null 2>&1; then die "$@"; else echo "[models][error] $*" >&2; fi
    return 1
}

registry_file() {
    if [[ -n "$MODELS_REGISTRY_PATH" ]]; then
        echo "$MODELS_REGISTRY_PATH"
        return 0
    fi
    local lib_dir root_dir
    lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    root_dir="$(cd "$lib_dir/../.." && pwd)"
    echo "$root_dir/config/models.registry.json"
}

# registry_query <python-expression over `models` (list) and `reg` (dict)>
_registry_query() {
    local file
    file="$(registry_file)"
    [[ -f "$file" ]] || { _registry_err "Model registry not found: $file"; return 1; }
    python3 - "$file" "$1" <<'PY'
import json, sys
path, expr = sys.argv[1], sys.argv[2]
try:
    reg = json.load(open(path))
except json.JSONDecodeError as exc:
    sys.exit(f"model registry {path} is not valid JSON: {exc}")
models = reg.get("models", [])
out = eval(expr, {"reg": reg, "models": models})  # noqa: S307 — trusted local expressions only
if isinstance(out, (list, tuple)):
    print("\n".join(str(x) for x in out))
elif out is not None:
    print(out)
PY
}

# All registered model ids, in file order.
registry_ids() {
    _registry_query '[m["id"] for m in models]'
}

# registry_field <id> <field> — one field of one entry ("" when absent).
registry_field() {
    _registry_query "next((m.get(\"$2\", \"\") for m in models if m[\"id\"] == \"$1\"), \"\")"
}

# registry_id_for_tag <ollama-tag> — reverse lookup over tag + aliases,
# tolerating the implicit ':latest' suffix on either side.
registry_id_for_tag() {
    _registry_query "next((m[\"id\"] for m in models if {\"$1\", \"$1\".removesuffix(\":latest\")} & ({m[\"tag\"], *m.get(\"aliases\", [])} | {t.removesuffix(\":latest\") for t in {m[\"tag\"], *m.get(\"aliases\", [])}})), \"\")"
}

# Aligned table of every entry, marking which are installed locally.
registry_table() {
    local installed=""
    if command -v ollama >/dev/null 2>&1; then
        installed="$(ollama list 2>/dev/null | awk 'NR>1 {print $1}' | tr '\n' ',')"
    fi
    INSTALLED_TAGS="$installed" python3 - "$(registry_file)" <<'PY'
import json, os, sys
reg = json.load(open(sys.argv[1]))
installed = {t for t in os.environ.get("INSTALLED_TAGS", "").split(",") if t}
installed |= {t.removesuffix(":latest") for t in installed}
print(f"registry v{reg['version']}  ({len(reg['models'])} models, runtime={reg.get('runtime','ollama')})")
print(f"{'':2} {'ID':<22} {'TAG':<30} {'ROLE':<10} {'SIZE':>7}  {'CTX':>8}  STATUS")
for m in reg["models"]:
    tag = m["tag"]
    mark = "*" if tag in installed or f"{tag}:latest" in installed else " "
    size = f"{m['size_gb']:.1f}G" if m.get("size_gb") else "-"
    n = m.get("context_length")
    ctx = "-" if not n else (f"{n // 1024**2}M" if n >= 1024**2 else f"{n // 1024}K")
    print(f"{mark:2} {m['id']:<22} {tag:<30} {m['role']:<10} {size:>7}  {ctx:>8}  {m['status']}")
print("\n* = installed on this Ollama host.  Pull one with: make model-pull ID=<id>")
PY
}

# registry_pull <id> — pull a library model, or build a modelfile-sourced one.
registry_pull() {
    local id="$1"
    [[ -n "$id" ]] || { _registry_err "registry_pull requires a model id"; return 1; }

    local tag kind status
    tag="$(registry_field "$id" tag)" || return 1
    [[ -n "$tag" ]] || {
        _registry_err "'$id' is not in the registry. Known ids: $(registry_ids | tr '\n' ' ')"
        return 1
    }
    kind="$(_registry_query "next((m[\"source\"][\"kind\"] for m in models if m[\"id\"] == \"$id\"), \"\")")"
    status="$(registry_field "$id" status)"

    [[ "$status" == "broken" ]] && _registry_warn "'$id' is marked status=broken: $(registry_field "$id" notes)"

    if [[ "$kind" != "ollama-library" ]]; then
        _registry_err "'$id' has source.kind=$kind and is not pullable from the Ollama library. See scripts/setup.sh for its build path."
        return 1
    fi

    _registry_info "Pulling $tag ($(registry_field "$id" name), $(registry_field "$id" size_gb) GB)"
    ollama pull "$tag"
}

# registry_check_selection <role> <tag> — warn (never fail) when .env points at
# a tag the registry does not know, or at one marked broken. Selection stays the
# user's call; this only makes an unregistered or known-bad pick visible.
registry_check_selection() {
    local role="$1" tag="$2" id
    [[ -n "$tag" ]] || return 0
    id="$(registry_id_for_tag "$tag")" || return 0
    if [[ -z "$id" ]]; then
        _registry_warn "$role model '$tag' is not in config/models.registry.json — add an entry so /models can report it."
        return 0
    fi
    local status
    status="$(registry_field "$id" status)"
    if [[ "$status" == "broken" ]]; then
        _registry_warn "$role model '$tag' ($id) is marked status=broken: $(registry_field "$id" notes)"
    else
        _registry_info "$role model '$tag' → registry entry '$id' ($status)"
    fi
}

# CLI entrypoints so the Makefile can call this file directly.
case "${1:-}" in
    --list)  registry_table ;;
    --pull)  registry_pull "${2:-}" ;;
    --ids)   registry_ids ;;
esac
