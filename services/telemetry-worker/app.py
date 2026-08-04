import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from telemetry_worker.calibration import CalibrationService
from telemetry_worker.config import Settings
from telemetry_worker.dedup import NotificationDedup
from telemetry_worker.event_sink import PostgreSQLSink
from telemetry_worker.health_checker import HealthChecker
from telemetry_worker.notification_handler import (
    init_handler,
    router as notification_router,
)
from telemetry_worker.profiles import ProfileService
from telemetry_worker.routers import health
from telemetry_worker.subscription_manager import ensure_subscriptions_for_all_tenants

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Telemetry Worker starting up...")

    settings = Settings()

    # Initialize async connection pool (shared between sink and profiles)
    sink = PostgreSQLSink(
        dsn=settings.postgres_url,
        min_pool=5,
        max_pool=20,
    )
    await sink.start()

    # ProfileService gets the same pool for async DB queries
    profile_service = ProfileService(settings, pool=sink._pool)

    # Health checker (evaluates sensor thresholds from Orion healthConfig)
    health_checker = HealthChecker(
        orion_url=settings.orion_url,
        redis_url=settings.redis_url,
        context_url=settings.context_url,
    )
    await health_checker.start()

    # Calibration service
    calibration_service = CalibrationService(
        redis_url=settings.redis_url,
        pg_pool=sink._pool,
    )
    await calibration_service.start()

    # Notification dedup (drops duplicate Orion-LD redeliveries before write)
    dedup = NotificationDedup(
        redis_url=settings.redis_url,
        enabled=settings.telemetry_dedup_enabled,
    )
    await dedup.start()

    # Wire dependencies into notification handler
    init_handler(settings, profile_service, sink, health_checker, calibration_service, dedup)

    # Check/create NGSI-LD subscriptions for all tenants (sync, run in executor)
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, ensure_subscriptions_for_all_tenants)
    except Exception as e:
        logger.warning(f"Auto-subscription failed (non-fatal): {e}")

    # Periodic subscription self-healing (every 60 minutes)
    async def _periodic_subscription_check():
        while True:
            await asyncio.sleep(3600)
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, ensure_subscriptions_for_all_tenants
                )
                logger.info("Periodic subscription check completed")
            except Exception as e:
                logger.warning(f"Periodic subscription check failed: {e}")

    periodic_task = asyncio.create_task(_periodic_subscription_check())

    yield

    # Shutdown: cancel periodic task and close pool
    periodic_task.cancel()
    await dedup.stop()
    await calibration_service.stop()
    await health_checker.stop()
    await sink.stop()
    logger.info("Telemetry Worker shut down.")


app = FastAPI(
    title="Nekazari Telemetry Worker",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(health.health_router)
app.include_router(notification_router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
