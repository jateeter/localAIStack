# localAIStack

A local AI stack that runs alongside any active RealityEngine runtime (CPP,
Scala, LSP). It hosts the RAG / LangGraph orchestration API (FastAPI +
Strawberry GraphQL), a Qdrant vector store shared with the Reality Engine,
Redis for LangGraph checkpointing, Open WebUI for chatting with native Ollama
models, and a Loki + Prometheus + Grafana trio for centralized log and metrics
monitoring.

## Service URLs

| Service | URL | Description |
|---|---|---|
| API (REST + GraphQL) | http://localhost:4000 | RAG / LangGraph orchestration |
| API Docs | http://localhost:4000/docs | OpenAPI explorer |
| Open WebUI | http://localhost:4080 | Chat UI against native Ollama |
| Qdrant | http://localhost:4333/dashboard | Vector DB (unified with Reality Engine) |
| Ollama | http://localhost:11434 | Native LLM runtime (Metal on macOS) |
| **Grafana** | **http://localhost:4002** | **localAIStack Overview dashboard** |
| **Loki** | **http://localhost:4100** | **Log aggregation API** |
| **Prometheus** | **http://localhost:4090** | **Metrics scraping (Loki + API)** |

## Pinned service versions

All observability and vector services use explicit, stable image pins to ensure
deterministic builds:

| Service | Image | Pinned version |
|---|---|---|
| Qdrant | `qdrant/qdrant` | `v1.20.0` |
| Loki | `grafana/loki` | `3.4.2` |
| Grafana | `grafana/grafana` | `11.6.1` |
| Prometheus | `prom/prometheus` | `v2.53.4` (LTS) |
| Redis | `redis` | `7.4-alpine` |

## Quick start

```bash
./scripts/setup.sh       # one-time: pull Ollama models, register ternary-bonsai:4
./scripts/start.sh       # start everything (installs Loki Docker plugin if missing)
./scripts/stop.sh        # stop everything
```

`start.sh` ensures the `loki` Docker plugin is installed+enabled before bringing
up the compose stack — the qdrant, redis, api, and open-webui containers all
use the Loki log driver and will fail to start without it.

Ollama is not pinned. `setup.sh` and `start.sh` (plus `make up`) keep the host
on the latest Ollama release: they read the release feed, and a host that is
missing Ollama or behind the latest release is installed or upgraded through the
official installer. The stack's minimum is `runtime.min_version` in
`config/models.registry.json`. Below it nothing starts. If the upgrade cannot
bring the host up to the minimum, the scripts stop with a manual-install
instruction. `make ollama-check` reports minimum, installed and latest without
installing anything. `OLLAMA_FOLLOW_LATEST=0` leaves a host that clears the
minimum where it is (offline work).

## Model registry

`config/models.registry.json` is the single source of truth for every local
model the stack knows how to run. It is *declarative* — listing a model does not
download it. Three separate facts get joined at read time:

| Fact | Lives in | Means |
|---|---|---|
| available | `config/models.registry.json` | the stack has curated metadata for it |
| selected | `.env` (`LLM_MODEL` / `EMBED_MODEL`) | the API will use it |
| installed | the Ollama host (`/api/tags`) | the weights are actually pulled |

```bash
make models                              # registry table, * marks installed
make model-pull ID=nemotron-3-nano-4b-mlx    # pull a registered model
make model-info ID=nemotron-3-nano-4b-mlx    # full entry (API must be up)
curl -s localhost:4000/models | python3 -m json.tool
curl -s 'localhost:4000/models?role=embedding' | python3 -m json.tool
```

`GET /models` returns the join, so the three common failure modes are
distinguishable in one response: `installed: false` (never pulled),
`unregistered_selections` (a `.env` tag no entry claims — usually a typo), and
`fits_host: false` (bigger than this machine's RAM). `setup.sh` runs the same
check against your `.env` selections and warns without blocking — overriding the
registry is legitimate while testing.

### Registered models

| ID | Tag | Role | Size | Context | Notes |
|---|---|---|---|---|---|
| `tinyllama-mlx` | `tinyllama-mlx` | llm | 2.2 GB | 2K | Former dev default — MLX runner; no tools or JSON mode |
| `llama3.1-8b` | `llama3.1:8b` | llm | 4.9 GB | 128K | General chat/RAG baseline |
| `llama3.2-3b` | `llama3.2:3b` | llm | 2.0 GB | 128K | Fast smoke tests |
| `mistral-7b` | `mistral:7b-instruct-q4_K_M` | llm | 4.4 GB | 32K | RAG grading A/B |
| `phi3.5-mini` | `phi3.5:3.8b` | llm | 2.2 GB | 128K | No tool-calling |
| `nemotron-3-nano-4b-mlx` | `nemotron-3-nano:4b-mlx` | llm | 2.9 GB | 256K | **Default** — RealityEngine universe model on **MLX**; tools + thinking |
| `nemotron-3-nano-4b` | `nemotron-3-nano:4b` | llm | 2.8 GB | 256K | The same model as GGUF; runs on llama.cpp |
| `nemotron-3-nano-30b` | `nemotron-3-nano:30b` | llm | 24 GB | 1M | Needs ≥48 GB RAM |
| `embeddinggemma-2-270m` | `embeddinggemma-2:270m` | embedding | 378 MB | 256K | **Default** embedder — runs on **MLX**, 768-dim |
| `nomic-embed-text` | `nomic-embed-text` | embedding | 274 MB | 2K | Previous default; runs on ggml, 768-dim |
| `ternary-bonsai-4b` | `ternary-bonsai:4` | embedding | 546 MB | 32K | **broken** — see known issue below |

**NVIDIA Nemotron 3 Nano 4B**, in its MLX build (`nemotron-3-nano-4b-mlx`), is the default LLM for
RealityEngine development and testing: hybrid Mamba-2/MoE, native tool-calling
and reasoning modes, and a 256K context at 2.9 GB. On an M1 Pro (Ollama 0.40.11)
the MLX build generates about 36 tokens/s against 25 for the GGUF
`nemotron-3-nano:4b`. The agent graph (`bind_tools`) works against it. Ollama
refused `format` (`json` and schema) for the family before 0.40.11 with
`structured output is unavailable`, so the PEs omit `format` for it
(RealityEngine_CI `docs/OLLAMA_INTEGRATION.md`).

**TinyLlama 1.1B Chat** (`tinyllama-mlx`) was the previous default. It is built
locally from bf16 safetensors and served by Ollama's MLX runner (about 110
tok/s), with a 2048-token context and no tools or structured output.

Switch the model on command — this validates the id against the model registry,
pulls (or builds) it, and writes the selection to `.env`:

```bash
make model-use ID=nemotron-3-nano-4b-mlx  # or nemotron-3-nano-4b, llama3.1-8b, ...
make stop && make start                  # restart so the API and PEs pick it up
make model-use ID=nomic-embed-text       # an embedding model sets EMBED_MODEL
```

One run can also override without touching `.env`: `LLM_MODEL=llama3.1:8b make start`.

Fresh `.env` files select `LLM_MODEL=nemotron-3-nano:4b-mlx`, and `setup.sh` pulls it
through the model registry (an existing `.env` keeps whatever it selects). The
30B variant is registered but not for this baseline: `nemotron-3-nano:latest`
resolves to it, which is why the registry pins explicit `:4b-mlx` / `:4b` / `:30b` tags.

### Adding a model

1. Append an entry to `config/models.registry.json` (stable kebab-case `id`,
   exact Ollama `tag`).
2. `make model-pull ID=<id>`
3. Point `.env` at it only if it should become the selected llm/embedder.

### Defaults run on MLX

Owner rule (RealityEngine_CI#593): each role's **default** model must run on
MLX; any other model may run on MLX or ggml. Each registry entry declares the
`runner` it was observed on, `GET /models` reports Ollama's live runner beside it
(`live_runner_source` says whether that is `loaded`, from `/api/ps`, which is what
runs the model, or only `declared`, from `/api/tags`, which is what Ollama set for
the pulled artifact: the same tag has been MLX on one Mac and llama.cpp on another),
and a test fails if a role's default does not declare `mlx`. Today: the LLM
default `nemotron-3-nano:4b-mlx` and the embedding default `embeddinggemma-2:270m`
run on MLX; `nemotron-3-nano:4b` runs on llama.cpp, and `llama3.1:8b` and
`nomic-embed-text` on ggml, so none of them is a default.

### The default RAG corpus

The API answers from a corpus out of the box. At every start it reconciles the
git-tracked `data/health/documents/*.md` into both collections the graphs read:
`localai_docs` (`/rag/query`, `/graph/rag`, the agent's `rag_search`) and
`health_docs` (the agent's `health_search`). It logs one `rag_default_corpus`
line per collection with what it expected, added, kept and removed.

- **A reconcile, not an append.** A chunk is identified by its source and a hash
  of its text, so a restart re-embeds nothing, an edited document replaces its
  old chunks, and a removed one leaves the index.
- **Your documents stay yours.** Anything ingested through `/rag/ingest/text` or
  `/rag/ingest/file` is never touched.
- **Read as text.** The documents are plain Markdown: front matter is dropped,
  headings are kept, and the same splitter as `scripts/ingest_health_docs.py`
  and `make reembed` cuts them (`core/health_docs.py`).
- `RAG_SEED_ON_STARTUP=false` turns it off; `LOCALAI_HEALTH_DOCS_DIR` points it
  at another directory.

### Changing the embedding model

Vectors from two embedding models do not mix, even at the same dimension. So a
collection says which model filled it, and changing the model is a migration:

- **Names.** The application uses a logical name, `localai_docs` or
  `health_docs`, which is a Qdrant alias onto `<name>__<model>`
  (`localai_docs__embeddinggemma-2-270m`). The model is readable from the name and
  the vector size from the collection's own config. (This was designed on Qdrant 1.14,
  which has no collection metadata.)
- **Loud, not silent.** At startup the API probes the model's real output
  dimension (`ollama show embeddinggemma-2:270m` says 512; the model returns 768)
  and logs `embedding_dimension_mismatch` if it differs from `EMBED_DIM`. A
  collection filled by another model, or a populated collection from before this
  existed (model unrecorded), raises `CollectionMismatch` naming the fix, and
  `/health` reports it under `services.embedding`.
- **Re-embed without losing data.** `make model-use ID=<embedder>` then
  `make reembed` (`scripts/reembed.py run`). It builds `<name>__<new-model>`
  beside the live collection from the source documents, checks retrieval
  (recall@k of each document's title) against the old collection, switches the
  alias in one atomic call, and keeps the old collection. `rollback` is a second
  switch; `drop-old` deletes the rest; `status` shows every collection.
  `health_docs` is rebuilt from `data/health/documents/` plus every point in it
  the default corpus does not own, so a document ingested by hand survives.
  `localai_docs` has no source directory and is rebuilt from its own points (the
  text is in each payload), so nothing in it is lost either. Only points with no
  stored text cannot be re-embedded; they are refused unless you pass
  `--allow-data-loss` to leave them out.
- **The startup seed deletes only what is provably its own.** A point is the
  seed's if it carries the `seed` tag (every chunk of the default corpus does,
  whichever path wrote it) or is an untagged copy with the loader's exact
  fingerprint (source `health/<file>.md`, category `<file>` with spaces). A
  document someone ingested under a seed file name is left alone and counted as
  `foreign` in the `rag_default_corpus` log line.
- **A pre-existing (legacy) collection** cannot hold an alias of its own name, so
  it is snapshotted, deleted, and replaced; `rollback` restores it from the
  snapshot into `<name>__legacy`. Use `--old-model <tag>` to compare retrieval
  with the model that filled it.

## Provider completion conformance

Validate the RealityEngine PE completion callback contract without a live
OpenAI key:

```bash
make provider-conformance
node scripts/pe-completion-conformance.mjs --mode native --stub --dry-run
node scripts/pe-completion-conformance.mjs --mode openai-chat --stub --dry-run
```

For live Ollama probing, omit `--stub`. Native mode calls `/api/chat`; OpenAI
compatible mode calls `/v1/chat/completions`. Omit `--dry-run` to POST the
validated completion body to `$PE_URL/api/integrations/completions`.

## Logging (Loki + Grafana)

All containerised services ship logs to Loki via the Docker `loki` log driver
with labels `app=localaistack` and `service=<name>`.  Grafana is
auto-provisioned with the Loki datasource and the **localAIStack Overview**
dashboard (panels: per-service log rate, error rate, RAG/LangGraph API logs,
GraphQL trigger events from the Reality Engine bridge, Qdrant, Redis, WebUI).

### Example LogQL queries

```logql
# All localAIStack logs
{app="localaistack"}

# API logs only
{app="localaistack", service="api"}

# Errors across the stack
{app="localaistack"} |~ "(?i)error|(?i)exception|(?i)traceback"

# Upstream triggers pushed from Reality Engine machines
{app="localaistack", service="api"} |~ "(?i)graphql|updateProcessState|ragStatusCode"

# Log rate per service (1-minute windows)
sum by (service) (count_over_time({app="localaistack"}[1m]))
```

### Ollama logs

Ollama runs **natively on the host** (Metal acceleration) rather than in a
container, so its logs are not shipped into Loki.  Tail them directly:

```bash
tail -f /tmp/ollama.log
```

## Architecture notes

- **Qdrant is unified** with the Reality Engine stack: both `localai_docs`
  (document embeddings) and `reality-events` (perceptual vectors) live in this
  instance.  Any active runtime (CPP, Scala, LSP) connects from its Docker
  network via `host.docker.internal:4333`.
- **Model metadata** lives in `config/models.registry.json`, mounted into the
  api container at `/app/config` and served at `GET /models`. See
  [Model registry](#model-registry).
- **Embedding model** defaults to `embeddinggemma-2:270m` (MLX, 768-dim); see
  [Changing the embedding model](#changing-the-embedding-model).  Override via
  `.env` `EMBED_MODEL`, then `make reembed`.
  > ⚠ `ternary-bonsai:4` is registered but **broken** (prism-ml ships only a
  > Q1_0 BitNet GGUF that Ollama's bundled GGML does not recognise; inference
  > and embeddings return HTTP 500). It was this repo's embedding default until
  > RealityEngine_CI#593; do not select it.
- **GraphQL trigger receiver** (`services/api/routers/graphql_endpoint.py`)
  exposes `POST /graphql` for machine → AI upstream pushes.  Events are retained
  in a 128-entry ring buffer; verify with `curl http://localhost:4000/graphql/events`.
