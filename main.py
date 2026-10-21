"""AUTODO — supervision réseau unifiée : tableau de bord, tickets, synchronisation Orange.

Un seul processus, un seul worker (jetons, cache du tableau de bord et ordonnanceur sont en mémoire).
"""
import asyncio
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import anyio.to_thread
from apscheduler.executors.pool import ThreadPoolExecutor as ApsThreadPool
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core import accounts, analytics, auth, constants, db, governance, monitoring, notify
from core.config import settings
from core.logger import get_logger

logger = get_logger(__name__)
BASE = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== Démarrage d'AUTODO ===")
    anyio.to_thread.current_default_thread_limiter().total_tokens = 20     # borne le pool de threads de l'API
    constants.reload_database_configurations()
    if not db.ensure_schema():
        raise RuntimeError("Base locale inaccessible ou migrations en échec — arrêt")
    accounts.ensure_initial_admin()                 # crée l'admin initial si aucun compte (changement de mot de passe imposé)
    governance.ensure_initial_version()             # point de retour : la configuration au premier démarrage
    app.state.sse_subscribers = set()
    app.state.sse_subscribers_lock = asyncio.Lock()

    scheduler = BackgroundScheduler(timezone=constants.CAMEROON_TZ, executors={"default": ApsThreadPool(6)},
                                    job_defaults={"coalesce": True, "misfire_grace_time": 120})
    constants.scheduler = scheduler
    monitoring.register_jobs(scheduler)
    scheduler.start()
    tick = asyncio.create_task(monitoring.sse_tick_task(app))
    logger.info("Ordonnanceur démarré (%d tâches)", len(scheduler.get_jobs()))
    try:
        yield
    finally:
        logger.info("=== Arrêt d'AUTODO ===")
        tick.cancel()
        with suppress(asyncio.CancelledError):
            await tick
        scheduler.shutdown(wait=True)                       # laisse le cycle en cours se terminer proprement
        analytics.flush_visits()
        notify.shutdown()
        db.dispose_all()


app = FastAPI(title="AUTODO Unified Network Monitoring System", version="3.0.0", lifespan=lifespan)

# CORS : même origine par défaut (aucun en-tête). Origines explicites via CORS_ORIGINS, jamais de joker avec cookies.
if settings.cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Content-Type", "X-Client-Id"],
                       expose_headers=["X-Client-Id"])

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


# Compression : le JSON du tableau de bord pèse ≈ 113 Ko brut / 19 Ko compressé ; × 25-50 spectateurs rechargés au même instant.
# (le flux SSE text/event-stream est exclu par Starlette)
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)


@app.middleware("http")
async def csrf_origin_check(request: Request, call_next):
    """Refuse toute requête qui modifie l'état si son en-tête Origin n'est pas notre hôte (défense en profondeur, en plus de SameSite)."""
    if not auth.origin_allowed(request):
        return JSONResponse({"detail": "Origine refusée"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith("/static/"):
        resp.headers.setdefault("Cache-Control", "no-cache")           # revalidation par ETag (304) à chaque chargement
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    return resp


app.state.templates = Jinja2Templates(directory=str(BASE / "templates"))      # échoue vite si le dossier manque
templates = app.state.templates
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

from routers.account import account_router      # noqa: E402
from routers.admin import admin_router          # noqa: E402
from routers.dashboard import dashboard_router  # noqa: E402
from routers.tickets import tickets_router      # noqa: E402

app.include_router(account_router, prefix="", tags=["compte"])
app.include_router(admin_router, prefix="", tags=["supervision-admin"])
app.include_router(tickets_router, prefix="/tickets", tags=["tickets"])
app.include_router(dashboard_router, prefix="", tags=["dashboard"])

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("APP_HOST", "127.0.0.1")           # derrière un reverse proxy TLS ; "0.0.0.0" pour exposer directement
    logger.info("Écoute sur %s:%s", host, settings.app_port)
    uvicorn.run("main:app", host=host, port=settings.app_port, reload=False, timeout_graceful_shutdown=15,
                proxy_headers=bool(settings.trusted_proxies), forwarded_allow_ips=",".join(settings.trusted_proxies) or None)
