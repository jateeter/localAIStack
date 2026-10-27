# The localAIStack API image. Built from the repository root, so the image
# carries what the API reads at run time: the machine definitions and health
# tables under data/, and the model registry. It runs with no bind mount, which
# is how the RealityEngine release bundle runs it (RealityEngine_CI
# release/images.json, image re-localai-api; ReleaseRoadmap B2):
#
#   docker build -f services/api/Dockerfile .
#
# The development compose still mounts the source over /app and adds --reload.

# The latest Python. It sat on 3.11 while unstructured (<3.14) was the Markdown
# loader; Markdown is now read as text (core/health_docs.py) and nothing else
# needed it.
#
# Wheels are built in a stage of their own. Two dependencies publish no cp315
# wheel yet (PyYAML 6.0.3, and zstandard 0.25.0 via langchain-core -> langsmith)
# and need a C compiler to build from source; the compiler stays in this stage,
# out of the runtime image. Where a cp315 wheel exists it is downloaded, not
# built, so this stage shrinks to a download as upstream catches up.
FROM python:3.15-slim AS wheels

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY services/api/requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

FROM python:3.15-slim

WORKDIR /app

# curl for the compose healthcheck. The loaders left (pypdf, docx2txt, text)
# are pure Python; libmagic1, poppler-utils and tesseract-ocr were unstructured's.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY services/api/requirements.txt .
# --no-index: the runtime installs exactly what the wheels stage resolved.
RUN --mount=type=bind,from=wheels,source=/wheels,target=/wheels \
    pip install --no-cache-dir --no-index --find-links /wheels -r requirements.txt

COPY services/api/ .
COPY data/machines/ /app/data/machines/
COPY data/health/ /app/data/health/
COPY config/models.registry.json /app/config/models.registry.json
# The index of the generated per-domain machines (data/machines/generated/). The
# bridge imports them only through it: without it the image ran 11 fewer localAI
# machines than the compose stack, which mounts config/ (RealityEngine_CI rc3 E1).
COPY config/domain-machines.index.json /app/config/domain-machines.index.json

ENV LOCALAI_MACHINES_DIR=/app/data/machines \
    LOCALAI_STATE_DIR=/app/state \
    MODELS_REGISTRY_PATH=/app/config/models.registry.json \
    LOCALAI_DOMAIN_MACHINES_INDEX=/app/config/domain-machines.index.json

# Unprivileged. The state directory is the only thing the API writes; it exists
# here owned by the app user, so a fresh named volume mounted on it is created
# writable by that user (Docker seeds a new volume from the image path).
RUN groupadd -r -g 1001 appuser \
    && useradd -r -u 1001 -g appuser appuser \
    && mkdir -p /app/state \
    && chown appuser:appuser /app/state
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8000/health >/dev/null || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
