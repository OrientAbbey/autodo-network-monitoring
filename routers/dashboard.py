"""ROUTES TABLEAU DE BORD — pages, API de données, SSE, accusés de réception, liens, santé.

Règle : aucun accès base de données dans la boucle d'événements. Les endpoints qui touchent MySQL sont des ``def``
(exécutés dans le pool de threads, borné à 20) ou passent par ``asyncio.to_thread``.
"""
import asyncio
from urllib.parse import quote

import orjson
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from fastapi.responses import RedirectResponse, StreamingResponse

from core import analytics, auth, constants, db, monitoring, tickets
from core.config import load_config, now_local, settings
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
async def data_management_page():
    """Ancienne page d'administration des données : remplacée par la page /admin (comptes nominatifs)."""
    return RedirectResponse("/admin", status_code=307)


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


class AckBody(BaseModel):
    ticket_ids: list[int] = Field(max_length=200)


@dashboard_router.post("/api/ack")
async def acknowledge_alerts(body: AckBody, request: Request):
    """Accusé de réception PARTAGÉ : ouvert aux visiteurs (auteur « visiteur » sans compte). La pop-up se ferme chez tous."""
    session = auth.current_session(request)
    created = await asyncio.to_thread(tickets.ack_alerts, body.ticket_ids, auth.actor_name(session))
    if created:
        await monitoring.sse_publish({"type": "ack", "at": now_local().isoformat(timespec="seconds")}, request.app)
    return {"acked": created}


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
def api_status(_s=auth.require_role("supervision")):
    return _json({**monitoring.status(), "tickets": tickets.counts()})
