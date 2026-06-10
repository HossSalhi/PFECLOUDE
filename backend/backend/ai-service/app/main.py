import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api.routes import router, set_monitor
from app.api.mlops_routes import mlops_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.middleware.api_key_middleware import ApiKeyMiddleware
from app.middleware.trace_middleware import TraceIdMiddleware
from app.services.autonomous_monitor import AutonomousMonitor

_startup_logger = logging.getLogger(__name__)


async def _check_upstream(url: str, name: str) -> None:
    """Warn (not crash) if an upstream dependency is unreachable at startup."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.get(url)
        _startup_logger.info("Startup check OK: %s (%s)", name, url)
    except Exception as exc:
        _startup_logger.warning(
            "Startup check FAILED: %s unreachable at %s — %s. "
            "Service starts anyway; predictions will degrade until connectivity is restored.",
            name, url, exc,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # Non-blocking connectivity check — warns if Prometheus/Loki are down at boot
    await asyncio.gather(
        _check_upstream(f"{settings.prometheus_base_url}/-/ready", "Prometheus"),
        _check_upstream(f"{settings.loki_base_url}/ready", "Loki"),
        return_exceptions=True,
    )

    monitor = AutonomousMonitor(settings)
    set_monitor(monitor)
    monitor.start()
    yield
    await monitor.stop()


settings = get_settings()
configure_logging(settings.log_level)

app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
# Middleware stack (applied in reverse order: TraceId first, then ApiKey)
app.add_middleware(ApiKeyMiddleware, api_key=settings.api_key)
app.add_middleware(TraceIdMiddleware)
app.include_router(router)
app.include_router(mlops_router)
