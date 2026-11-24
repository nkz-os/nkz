import logging
import asyncio
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from prometheus_client import make_asgi_app

from telemetry_worker.calibration import CalibrationService
from telemetry_worker.config import Settings
from telemetry_worker.dedup import NotificationDedup
from telemetry_worker.event_sink import PostgreSQLSink
from telemetry_worker.inbox import NotificationInbox
from telemetry_worker.inbox_consumer import InboxConsumer
from telemetry_worker.geometry_backfill import run_geometry_backfill
from telemetry_worker.health_checker import HealthChecker
from telemetry_worker.observation_lookup import ObservationLookup
from telemetry_worker.notification_handler import (
    init_handler,
    router as notification_router,
)
from telemetry_worker.profiles import ProfileService
from telemetry_worker.recompute import RecomputeLoop, recompute_interval_from_env
from telemetry_worker.routers import health
from telemetry_worker.subscription_manager import (
    ensure_subscriptions_for_all_tenants,
    run_periodic_reconcile,
)

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _on_consumer_task_done(task: asyncio.Task, shutting_down: bool) -> None:
    """Done-callback of the inbox consumer task: it only ends at shutdown.

    Ending any other way (an exception, a return, a cancel) leaves this replica storing
    notifications that nobody processes: logged CRITICAL with the error.
    """
    if shutting_down:
        return
    if task.cancelled():
        reason, exc = "cancelled", None
    else:
        exc = task.exception()
        reason = "exception" if exc is not None else "returned"
    logger.critical(
        f"inbox_consumer_task_ended reason={reason} error={exc!r} - notifications are "
        f"stored in the inbox but no longer processed by this replica",
        exc_info=exc,
    )


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
        redis_password=settings.redis_password or None,
    )
    await health_checker.start()

    # Calibration service
    calibration_service = CalibrationService(
        redis_url=settings.redis_url,
        pg_pool=sink._pool,
        redis_password=settings.redis_password or None,
    )
    await calibration_service.start()

    # Notification dedup (drops duplicate Orion-LD redeliveries before write)
    dedup = NotificationDedup(
        redis_url=settings.redis_url,
        enabled=settings.telemetry_dedup_enabled,
        redis_password=settings.redis_password or None,
    )
    await dedup.start()

    # Wire dependencies into notification handler
    observation_lookup = ObservationLookup(sink._pool)
    inbox = NotificationInbox(sink._pool)
    init_handler(
        settings, profile_service, sink, health_checker, calibration_service, dedup,
        observation_lookup=observation_lookup,
        geometry_pool=sink._pool,
        inbox=inbox,
    )

    # Inbox consumer: only now, with the handler wired and the sink pool started (an
    # unwired handler would let rows be acknowledged unprocessed). Its advisory lock
    # lives on a dedicated one-connection pool (no connection until it is first
    # needed), so the lock's lifetime is independent of the sink pool.
    lock_pool = await asyncpg.create_pool(
        dsn=settings.postgres_url, min_size=0, max_size=1, command_timeout=30
    )
    consumer_stopping = asyncio.Event()
    consumer_task = asyncio.create_task(InboxConsumer(inbox, lock_pool).run())
    consumer_task.add_done_callback(
        lambda task: _on_consumer_task_done(task, shutting_down=consumer_stopping.is_set())
    )

    # Audited recompute of stored observation context (contract 8.6); only with
    # the observation lookup wired. A bad interval value fails startup loudly.
    recompute_task = None
    if observation_lookup is not None:
        recompute = RecomputeLoop(
            sink._pool, observation_lookup, interval_s=recompute_interval_from_env()
        )
        recompute_task = asyncio.create_task(recompute.run())

    # Check/create NGSI-LD subscriptions for all tenants (sync, run in executor)
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, ensure_subscriptions_for_all_tenants)
    except Exception as e:
        logger.warning(f"Auto-subscription failed (non-fatal): {e}")

    # Periodic subscription self-healing: an extra pass 240 s after start (retires
    # replaced subscriptions within minutes), then every 60 minutes.
    periodic_task = asyncio.create_task(
        run_periodic_reconcile(ensure_subscriptions_for_all_tenants)
    )

    # The geometry subscription only fires on a create or a location change, so
    # features that already exist are backfilled here, in the background: readiness
    # does not wait for it. After the subscriptions, so a change made while it runs
    # is still notified.
    backfill_task = asyncio.create_task(run_geometry_backfill(sink._pool))

    yield

    # Shutdown: stop the inbox consumer first, while what it uses is still up; rows in
    # flight stay in the inbox and are processed again (dedup covers it). Ending its
    # lock connection releases the lock for the other replica.
    consumer_stopping.set()
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass
    try:
        await asyncio.wait_for(lock_pool.close(), 10)
    except Exception as e:
        logger.warning(f"Inbox lock pool did not close cleanly: {e!r}")
        lock_pool.terminate()

    # Cancel periodic task and close pool
    periodic_task.cancel()
    try:
        await periodic_task
    except asyncio.CancelledError:
        pass
    backfill_task.cancel()
    try:
        await backfill_task
    except asyncio.CancelledError:
        pass
    if recompute_task is not None:
        recompute_task.cancel()
        try:
            await recompute_task
        except asyncio.CancelledError:
            pass
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
app.mount("/metrics", make_asgi_app())

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
