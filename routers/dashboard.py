"""ROUTES TABLEAU DE BORD — pages, API de données, SSE, accusés de réception, liens, santé.

Règle : aucun accès base de données dans la boucle d'événements. Les endpoints qui touchent MySQL sont des ``def``
(exécutés dans le pool de threads, borné à 20) ou passent par ``asyncio.to_thread``.
"""
import asyncio
import gzip
import zlib
from urllib.parse import quote

import orjson
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from fastapi.responses import RedirectResponse, StreamingResponse

from core import analytics, auth, constants, db, governance, history, monitoring, tickets
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


def _app_page(request: Request, template: str, requires: str, title_key: str, title_default: str):
    """Coquille HTML d'une page réservée : aucun secret dedans, l'accès aux DONNÉES est contrôlé par les API (401/403)."""
    return request.app.state.templates.TemplateResponse(
        request, template, {"requires": requires, "title_key": title_key, "title_default": title_default})


@dashboard_router.get("/supervision")
async def supervision_page(request: Request):
    return _app_page(request, "supervision.html", "supervision", "page.supervision", "Supervision")


@dashboard_router.get("/admin")
async def admin_page(request: Request):
    return _app_page(request, "admin.html", "admin", "page.admin", "Administration")


@dashboard_router.get("/api/maintenance/active")
async def api_active_maintenance():
    """Fenêtres de maintenance en cours (public : le visiteur doit comprendre pourquoi une base ne remonte plus d'alerte)."""
    windows = await asyncio.to_thread(governance.list_windows, True)
    return {"windows": [{"db_key": w["db_key"], "ends_at": w["ends_at"], "reason": (w["reason"] or "")[:80]} for w in windows]}


@dashboard_router.get("/data-management")
async def data_management_page():
    """Ancienne page d'administration des données : remplacée par la page /admin (comptes nominatifs)."""
    return RedirectResponse("/admin", status_code=307)


# ---------------------------------------------------------------- données
def _etag_matches(header: str | None, etag: str) -> bool:
    """``If-None-Match`` peut lister plusieurs ETag ; la comparaison faible ignore le préfixe W/."""
    if not header:
        return False
    wanted = etag.removeprefix("W/")
    return header.strip() == "*" or any(part.strip().removeprefix("W/") == wanted for part in header.split(","))


@dashboard_router.get("/api/dashboard_data")
async def api_dashboard_data(request: Request, response: Response, status: str | None = Query(None, pattern="^(open|closed)$"),
                             type: str | None = Query(None, max_length=40), period: str | None = Query(None, pattern="^(today|week|month)$")):
    """Tableau de bord pré-calculé (JSON sérialisé UNE fois par cycle) + alertes filtrées, avec ETag : 304 si rien n'a changé.

    Jamais de calcul lourd dans la requête : 503 tant que le 1er cycle n'est pas fini.
    """
    analytics.track_user(request, response.headers)                 # tampon mémoire : aucune E/S
    with constants.DASHBOARD_CACHE_LOCK:
        cached_json, version = constants.DASHBOARD_JSON, constants.DASHBOARD_VERSION
    if not cached_json:
        raise HTTPException(503, "Données en cours de préparation", headers={"Retry-After": "10"})
    filters = {key: value for key, value in (("status", status), ("type", type), ("period", period)) if value}
    alerts_json = orjson.dumps(await asyncio.to_thread(tickets.dashboard_alerts, filters), default=str)
    etag = f'W/"{version}-{zlib.crc32(alerts_json):08x}"'
    headers = {**dict(response.headers), "ETag": etag, "Cache-Control": "no-cache"}
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    use_gzip = "gzip" in request.headers.get("accept-encoding", "").lower()
    body = await asyncio.to_thread(_response_body, etag, use_gzip, cached_json, alerts_json)
    if use_gzip:
        headers.update({"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})        # déjà compressé : le middleware ne le refait pas
    return Response(body, media_type="application/json", headers=headers)


_BODY_CACHE: dict[tuple[str, bool], bytes] = {}
_BODY_CACHE_MAX = 32


def _response_body(etag: str, use_gzip: bool, cached_json: bytes, alerts_json: bytes) -> bytes:
    """Réponse complète, mémorisée par (ETag, gzip) : les spectateurs aux mêmes filtres partagent UNE réponse, compressée une seule fois.

    L'ETag contient la version du cycle et l'empreinte des alertes : une entrée périmée n'est donc jamais resservie.
    Un seul membre gzip : les clients courants (httpx, etc.) ne décodent pas les flux à plusieurs membres.
    """
    key = (etag, use_gzip)
    body = _BODY_CACHE.get(key)
    if body is None:
        raw = cached_json + b',"alertes":' + alerts_json + b"}"
        body = gzip.compress(raw, compresslevel=5, mtime=0) if use_gzip else raw
        if len(_BODY_CACHE) >= _BODY_CACHE_MAX:
            _BODY_CACHE.clear()
        _BODY_CACHE[key] = body
    return body


def format_sse(event: dict) -> str:
    """Évènement SSE SANS nom (``data: {...}``) : seul ce format atteint ``EventSource.onmessage`` côté navigateur."""
    return f"data: {orjson.dumps(event).decode()}\n\n"


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
                    yield format_sse(ev)
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


@dashboard_router.get("/api/presence")
async def api_presence():
    """Spectateurs connectés maintenant et pic du jour (public : ni nom ni adresse)."""
    return {"viewers": monitoring.viewer_count(), "peak_today": await asyncio.to_thread(analytics.peak_today)}


@dashboard_router.get("/api/sparklines")
async def api_sparklines():
    """Mini-courbes 24 h de toutes les bases + bande « attendu » (calcul mis en cache 60 s : N spectateurs = 1 calcul)."""
    return await asyncio.to_thread(history.sparklines)


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
