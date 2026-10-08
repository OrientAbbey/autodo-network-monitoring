"""ROUTES TABLEAU DE BORD — pages, API de données, SSE, configuration, administration.

Règle : aucun accès base de données dans la boucle d'événements. Les endpoints qui touchent MySQL sont des ``def``
(exécutés dans le pool de threads, borné à 20) ou passent par ``asyncio.to_thread``.
"""
import asyncio
import os
from urllib.parse import quote

import orjson
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse, StreamingResponse

from core import analytics, auth, constants, db, monitoring, tickets
from core.auth import get_real_ip
from core.config import ConfigParams, load_config, now_local, save_config_updates, settings
from core.logger import get_logger

logger = get_logger(__name__)
dashboard_router = APIRouter()


def _json(data, status_code: int = 200, headers=None) -> Response:
    return Response(orjson.dumps(data, default=str), status_code=status_code, media_type="application/json",
                    headers=dict(headers) if headers else None)


# ---------------------------------------------------------------- pages
@dashboard_router.get("/")
async def index(request: Request):
    return request.app.state.templates.TemplateResponse(request, "dashboard.html")


@dashboard_router.get("/data-management")
async def data_management_page(request: Request):
    return request.app.state.templates.TemplateResponse(request, "data_management.html")


# ---------------------------------------------------------------- données
@dashboard_router.post("/api/dashboard_data")
async def api_dashboard_data(request: Request, response: Response):
    """Cache pré-calculé + alertes. Jamais de calcul lourd dans la requête : 503 tant que le 1er cycle n'est pas fini."""
    body = await request.body()
    try:
        filters = orjson.loads(body) if body else {}
    except orjson.JSONDecodeError:
        raise HTTPException(400, "JSON invalide") from None
    if not isinstance(filters, dict):
        raise HTTPException(400, "Corps JSON attendu : objet")
    analytics.track_user(request, response.headers)                 # tampon mémoire : aucune E/S
    with constants.DASHBOARD_CACHE_LOCK:
        cached = dict(constants.DASHBOARD_CACHE)
    if not cached:
        raise HTTPException(503, "Données en cours de préparation", headers={"Retry-After": "10"})
    cached["alertes"] = await asyncio.to_thread(tickets.dashboard_alerts, filters)
    return _json(cached, headers=response.headers)


@dashboard_router.get("/events")
async def sse_events(request: Request):
    app = request.app
    if len(app.state.sse_subscribers) >= settings.max_sse_subscribers:
        raise HTTPException(503, "Trop de connexions temps réel")
    queue: asyncio.Queue = asyncio.Queue(maxsize=6)
    async with app.state.sse_subscribers_lock:
        app.state.sse_subscribers.add(queue)

    async def gen():
        try:
            yield f"event: hello\ndata: {orjson.dumps({'type': 'hello'}).decode()}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"event: update\ndata: {orjson.dumps(ev).decode()}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            async with app.state.sse_subscribers_lock:
                app.state.sse_subscribers.discard(queue)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- configuration
@dashboard_router.get("/api/config")
async def get_config():
    return _json(load_config())


@dashboard_router.post("/update_config")
async def update_config(new_config: ConfigParams, request: Request, _=auth.require_session(auth.SCOPE_CONFIG)):
    """Authentification requise ; champs absents conservés ; écriture atomique ; ancienne/nouvelle valeur journalisées."""
    updates = new_config.model_dump(exclude_unset=True)
    before = load_config()
    try:
        await asyncio.to_thread(save_config_updates, updates)
    except Exception:
        logger.exception("Échec de l'enregistrement de la configuration")
        raise HTTPException(500, "Échec de l'enregistrement de la configuration") from None
    changed = {k: (before.get(k), v) for k, v in updates.items() if before.get(k) != v}
    ip = get_real_ip(request)
    logger.info("Configuration modifiée par %s : %s", ip, changed or "aucun changement")
    await asyncio.to_thread(analytics.log_action, ip, "criteria_change")
    task = asyncio.create_task(monitoring.sse_publish(
        {"type": "config_updated", "at": now_local().isoformat(timespec="seconds")}, request.app))
    bg = request.app.state.__dict__.setdefault("bg_tasks", set())      # référence conservée jusqu'à la fin de la tâche
    bg.add(task)
    task.add_done_callback(bg.discard)
    return {"message": "Configuration mise à jour avec succès.", "changed": list(changed)}


@dashboard_router.api_route("/api/auth", methods=["GET", "POST"])
async def auth_endpoint(request: Request, response: Response):
    if request.method == "GET":
        if not auth.sessions.valid(request.cookies.get(auth.COOKIE_NAMES[auth.SCOPE_CONFIG]), auth.SCOPE_CONFIG):
            raise HTTPException(401, "Token expired or invalid")
        return {"valid": True}
    try:
        data = await request.json()
    except Exception:
        data = {}
    auth.login(request, response, data.get("password") if isinstance(data, dict) else None,
               os.getenv("CONFIG_PASSWORD"), auth.SCOPE_CONFIG, settings.token_ttl)
    return {"ok": True}


# ---------------------------------------------------------------- liens ExNet (le nom d'hôte reste côté serveur)
@dashboard_router.get("/go/{kind}/{db_key}")
async def go_exnet(kind: str, db_key: str, end_date: int = 0, c: str | None = None):
    templates = {"traffic": constants.EXNET_TRAFFIC_LINK, "loss": constants.EXNET_LOSS_LINK,
                 "tcp_service_access": constants.EXNET_TCP_SERVICE_ACCESS_LINK, "tcp_tts": constants.EXNET_TCP_TTS_LINK,
                 "dns": constants.EXNET_DNS_LINK}
    cfg = constants.DB_CONFIGS.get(db_key)
    if kind not in templates or not cfg:
        raise HTTPException(404, "Lien inconnu")
    c_param = c if c == "SGi" else constants.DB_KEY_TO_CPARAM.get(db_key, "")        # liste blanche
    url = templates[kind].format(db_host=cfg["host"], c_param=quote(c_param)).replace("__END_TIMESTAMP__", str(int(end_date)))
    return RedirectResponse(url, status_code=302)


# ---------------------------------------------------------------- exploitation
@dashboard_router.get("/health")
def health_check():
    """200 si la base locale répond ; 'degraded' si l'ordonnanceur est arrêté (le corps détaille)."""
    db_ok = db.ping_local()
    sched = constants.scheduler
    sched_ok = bool(sched and sched.running)
    body = {"status": "healthy" if (db_ok and sched_ok) else "degraded", "database": db_ok, "scheduler": sched_ok,
            "timestamp": now_local().isoformat()}
    return _json(body, status_code=200 if db_ok else 503)


@dashboard_router.get("/api/status")
def api_status(_=auth.require_any_session()):
    return _json({**monitoring.status(), "tickets": tickets.counts()})


# ---------------------------------------------------------------- administration (données d'usage)
@dashboard_router.post("/api/data-management")
async def data_management_login(request: Request, response: Response):
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict) or data.get("action", "login") != "login":
        raise HTTPException(400, "Invalid action")
    auth.login(request, response, data.get("password"), os.getenv("DATA_MANAGEMENT_PASSWORD"),
               auth.SCOPE_DATA, settings.token_ttl)
    await asyncio.to_thread(analytics.log_action, get_real_ip(request), "data_management_login")
    return {"success": True, "message": "Login successful"}


@dashboard_router.get("/api/data-management")
def data_management_data(request: Request, action: str = "data", period: str = "7days"):
    if action != "data":
        raise HTTPException(400, "Invalid action")
    if not auth.sessions.valid(request.cookies.get(auth.COOKIE_NAMES[auth.SCOPE_DATA]), auth.SCOPE_DATA):
        logger.warning("Data management : accès refusé depuis %s", get_real_ip(request))
        raise HTTPException(401, "Not authenticated")
    access = analytics.access_logs(period)
    return _json({"period": period, "access_logs": access["access_logs"], "access_stats": access["access_stats"],
                  "alerts_stats": tickets.alerts_stats(period), "config_changes": analytics.config_changes(period),
                  "generated_at": now_local().isoformat()})
