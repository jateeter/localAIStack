from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from core import metrics

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics() -> PlainTextResponse:
    """Prometheus text exposition (core/metrics.py; localAIStack#180)."""
    return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8")
