from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router, set_monitor
from app.api.mlops_routes import mlops_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.services.autonomous_monitor import AutonomousMonitor


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    monitor = AutonomousMonitor(settings)
    set_monitor(monitor)
    monitor.start()
    yield
    await monitor.stop()


settings = get_settings()
configure_logging(settings.log_level)

app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
app.include_router(router)
app.include_router(mlops_router)
