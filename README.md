# localAIStack

A local AI stack that runs alongside any active RealityEngine runtime (CPP,
Scala, LSP). It hosts the RAG / LangGraph orchestration API (FastAPI +
Strawberry GraphQL), a Qdrant vector store shared with the Reality Engine,
Redis for LangGraph checkpointing, Open WebUI for chatting with native Ollama
models, and a Loki + Grafana pair for centralized log monitoring.

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

## Quick start

```bash
./scripts/setup.sh       # one-time: pull Ollama models, register ternary-bonsai:4
./scripts/start.sh       # start everything (installs Loki Docker plugin if missing)
./scripts/stop.sh        # stop everything
```

`start.sh` ensures the `loki` Docker plugin is installed+enabled before bringing
up the compose stack — the qdrant, redis, api, and open-webui containers all
use the Loki log driver and will fail to start without it.

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
  (document embeddings) and `reality-vectors` (perceptual vectors) live in this
  instance.  Any active runtime (CPP, Scala, LSP) connects from its Docker
  network via `host.docker.internal:4333`.
- **Embedding model** defaults to `ternary-bonsai:4` (registered at setup time
  from `hf.co/prism-ml/Bonsai-4B-gguf`).  Override via `.env` `EMBED_MODEL`.
  > ⚠ **Known issue:** the prism-ml repo ships only a Q1_0 (BitNet ternary)
  > GGUF, which Ollama's bundled GGML does not yet recognize — the model
  > registers and appears in the WebUI dropdown, but inference and embeddings
  > return HTTP 500 (`file_type=unknown`) until upstream Ollama ships GGML
  > with TQ1_0/BitNet support.  For a working default, set
  > `EMBED_MODEL=nomic-embed-text` + `EMBED_DIM=768` in `.env`.
- **GraphQL trigger receiver** (`services/api/routers/graphql_endpoint.py`)
  exposes `POST /graphql` for machine → AI upstream pushes.  Events are retained
  in a 128-entry ring buffer; verify with `curl http://localhost:4000/graphql/events`.
