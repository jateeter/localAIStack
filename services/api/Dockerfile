# The localAIStack API image. Built from the repository root, so the image
# carries what the API reads at run time: the machine definitions and health
# tables under data/, and the model registry. It runs with no bind mount, which
# is how the RealityEngine release bundle runs it (RealityEngine_CI
# release/images.json, image re-localai-api; ReleaseRoadmap B2):
#
#   docker build -f services/api/Dockerfile .
#
# The development compose still mounts the source over /app and adds --reload.

# Keep the runtime on Python 3.11 until the document-loader dependency set
# supports Python 3.14. In particular, unstructured==0.25.2 declares <3.14.
FROM python:3.11-slim

WORKDIR /app

# System deps for unstructured + document parsing
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libmagic1 \
    poppler-utils \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY services/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/api/ .
COPY data/machines/ /app/data/machines/
COPY data/health/ /app/data/health/
COPY config/models.registry.json /app/config/models.registry.json

ENV LOCALAI_MACHINES_DIR=/app/data/machines \
    LOCALAI_STATE_DIR=/app/state \
    MODELS_REGISTRY_PATH=/app/config/models.registry.json

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
