"""
UNIFIED NETWORK MONITORING & DASHBOARD APPLICATION
Combines ticket monitoring system with real-time dashboard analytics
Single application running on one port with multiple interfaces
Configuration managed centrally from dashboard
"""

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from pathlib import Path

import dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Load environment variables
dotenv.load_dotenv(Path(__file__).parent / ".env")

# Import constants and utils (use module imports to avoid star-imports and circular imports)
from core import constants, utils
from core.logger import get_logger

# Centralized logger for this module
logger = get_logger(__name__)


# ============================================================================
# LIFESPAN MANAGEMENT
# ============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle"""
    global scheduler, orange_api_client

    logger.info("=== Starting AUTODO Unified Network Monitoring System ===")

    # Verify database setup
    if not utils.verify_database_setup():
        logger.critical("Database setup verification failed. Exiting.")

    # Create logs table for criteria access tracking
    utils.create_logs_table()

    # Initialize logs DB pool (synchronous, for dashboard functions)
    utils.init_daily_users_table()

    # Initialize Orange API client (ticket system)
    from core.ticket_manager import OrangeAPIClient

    orange_api_client = OrangeAPIClient()

    # Initialize SSE state
    app.state.sse_subscribers = set()
    app.state.sse_subscribers_lock = asyncio.Lock()

    # Initialize scheduler
    scheduler = BackgroundScheduler(timezone=constants.CAMEROON_TZ)

    # Set globals in constants
    constants.scheduler = scheduler
    constants.orange_api_client = orange_api_client

    # Schedule background monitoring tasks
    from core.background_task import (
        monitor_orange_ticket_status, retry_tickets_missing_orange_ids,
        run_unified_data_collection_and_processing)

    config = utils.load_config()
    # Unified task that handles both monitoring and dashboard data collection
    scheduler.add_job(
        run_unified_data_collection_and_processing,
        trigger="cron",
        minute="1-59/5",
        timezone=constants.CAMEROON_TZ,
        max_instances=1,
        replace_existing=True,
        id="unified_data_collection_job",
        next_run_time=datetime.now(constants.CAMEROON_TZ),  # 👈 exécution immédiate
        args=[config, orange_api_client],
    )

    scheduler.add_job(
        monitor_orange_ticket_status,
        trigger="cron",
        minute="0,17,32,47",
        timezone=constants.CAMEROON_TZ,
        args=[orange_api_client],
    )

    scheduler.add_job(
        retry_tickets_missing_orange_ids,
        trigger="cron",
        minute="12,42",
        timezone=constants.CAMEROON_TZ,
        args=[orange_api_client],
    )

    # Lancer l'émetteur périodique (toutes les 5 minutes + 2 minutes)
    async def sse_tick_task():
        try:
            while True:
                now = datetime.now(constants.CAMEROON_TZ)
                # Prochaine borne multiple de 5 minutes + 2 minutes (ex: 10:02, 10:07, 10:12, ...)
                base = now.replace(second=0, microsecond=0)
                current_minute = now.minute
                # Si on est entre XX:03 et XX:07, on vise XX:07
                # Si on est entre XX:08 et XX:12, on vise XX:12, etc.
                minutes_block = ((current_minute + 2) // 5) * 5 + 2

                # Gérer le passage à l'heure suivante si nécessaire
                add_hours = minutes_block // 60
                minutes_block = minutes_block % 60
                next_time = (base + timedelta(hours=add_hours)).replace(
                    minute=minutes_block
                )
                sleep_seconds = max(1, int((next_time - now).total_seconds()))

                # 👉 Ici : on attend le temps OU une annulation
                await asyncio.sleep(sleep_seconds)

                # Vérifier s'il y a eu des changements avant d'envoyer l'événement
                now_time = datetime.now(constants.CAMEROON_TZ)
                if now_time.minute % 5 == 2 and now_time.second == 0:
                    asyncio.create_task(
                        utils._sse_publish(
                            {
                                "type": "scheduled_update",
                                "at": now_time.isoformat(timespec="seconds"),
                            },
                            app=app,
                        )
                    )
        except asyncio.CancelledError:
            logger.info("SSE task cancelled")
            raise
        except Exception as e:
            logger.error(f"Erreur sse_tick_task: {e}")
            await asyncio.sleep(5)  # Attendre un peu avant de réessayer


    scheduler.start()

    app.state.sse_scheduler = asyncio.create_task(sse_tick_task())

    logger.info("System initialization complete")

    yield  # App is running

    # Shutdown
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("✅ Scheduler shutdown complete")

    # Cancel SSE scheduler
    try:
        if hasattr(app.state, "sse_scheduler"):
            app.state.sse_scheduler.cancel()
            await app.state.sse_scheduler
    except asyncio.CancelledError:
        logger.debug("sse_scheduler cancelled during shutdown")
    except Exception as e:
        logger.exception(f"Error cancelling sse_scheduler: {e}")

    # Notify SSE subscribers about shutdown and give them a short grace period to disconnect
    subscribers = getattr(app.state, "sse_subscribers", set())
    lock = getattr(app.state, "sse_subscribers_lock", None)

    # If we have a lock, use it; otherwise operate safely on a snapshot
    async def _notify_shutdown_and_clear(snapshot):
        for q in snapshot:
            with suppress(Exception):
                try:
                    q.put_nowait({"type": "shutdown"})
                except asyncio.QueueFull:
                    # force-drop one item then push shutdown
                    with suppress(Exception):
                        _ = q.get_nowait()
                        q.put_nowait({"type": "shutdown"})

    # If we have a lock, use it; otherwise operate safely on a snapshot
    if lock is None:
        subs_snapshot = list(subscribers)
        await _notify_shutdown_and_clear(subs_snapshot)
        # give a short time for generators to process shutdown event
        await asyncio.sleep(0.5)
        # clear subscribers
        subscribers.clear()
    else:
        async with lock:
            subs_snapshot = list(app.state.sse_subscribers)
            await _notify_shutdown_and_clear(subs_snapshot)
        # dehors du lock, attendre encore un peu (les generators libèreront leurs queues)
        await asyncio.sleep(0.5)
        # nettoyage final sous lock pour éviter race avec nouveaux abonnés (toutefois scheduler est annulé)
        async with lock:
            app.state.sse_subscribers.clear()

    logger.info("Lifespan shutdown completed: SSE subscribers notified and cleaned.")


# ============================================================================
# FASTAPI APP INITIALIZATION
# ============================================================================
app = FastAPI(
    title="AUTODO Unified Network Monitoring System", version="2.0.0", lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Client-Id"],
)

# Templates and static files
templates_dir = Path(__file__).parent / "templates"
static_dir = Path(__file__).parent / "static"

if templates_dir.exists():
    templates = Jinja2Templates(directory=str(templates_dir))
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ============================================================================
# ROUTERS IMPORT
# ============================================================================
from routers.dashboard import dashboard_router
from routers.tickets import tickets_router

app.include_router(tickets_router, prefix="/tickets", tags=["tickets"])
app.include_router(dashboard_router, prefix="", tags=["dashboard"])


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================
if __name__ == "__main__":
    import uvicorn

    try:
        logger.info(f"Starting AUTODO Unified App on port {constants.APP_PORT}")
        uvicorn.run("main:app", host="0.0.0.0", port=constants.APP_PORT, reload=False, timeout_graceful_shutdown=10)
    except KeyboardInterrupt:
        logger.info("✅ Shutdown complete")
