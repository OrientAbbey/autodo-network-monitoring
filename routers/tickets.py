"""ROUTES TICKETS — page, API paginée, chronologie, décisions de supervision (valider, rejeter, supprimer, commenter)."""
import asyncio

import orjson
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from core import auth, constants, db, governance, monitoring, ticket_log, tickets
from core.auth import get_real_ip
from core.config import load_config, now_local
from core.logger import get_logger

logger = get_logger(__name__)
tickets_router = APIRouter()
PAGE_LIMIT = 1000        # tickets envoyés à la page (les ouverts d'abord) ; le reste via /tickets/api?offset=
supervision = auth.require_role("supervision")


def _json(data, status_code: int = 200) -> Response:
    return Response(orjson.dumps(data, default=str), status_code=status_code, media_type="application/json")


@tickets_router.get("/")
def tickets_view(request: Request):
    rows, total = tickets.load_page(limit=PAGE_LIMIT)
    sites = sorted({t["site"] for t in rows if t.get("site")})
    dbs = sorted({t["database_name"] for t in rows if t.get("database_name")})
    filter_options = {
        "types": [{"value": "traffic", "label": "Traffic"}, {"value": "pertes", "label": "Pertes"},
                  {"value": "db_down", "label": "DB_DOWN"}],
        "sites": [{"value": s, "label": s} for s in sites], "databases": [{"value": d, "label": d} for d in dbs],
        "statuses": [{"value": "open", "label": "Open"}, {"value": "closed", "label": "Closed"}],
        "time_periods": [{"value": "all", "label": "All Time"}, {"value": "today", "label": "Today"},
                         {"value": "yesterday", "label": "Yesterday"}, {"value": "week", "label": "Last 7 Days"},
                         {"value": "month", "label": "Last 30 Days"}, {"value": "current_month", "label": "Current Month"}]}
    return request.app.state.templates.TemplateResponse(
        request, "tickets.html", {"tickets": rows, "total": total, "filter_options": filter_options})


@tickets_router.get("/api")
def get_tickets(limit: int = Query(PAGE_LIMIT, ge=1, le=5000), offset: int = Query(0, ge=0),
                status: str | None = Query(None, pattern="^(open|closed|deleted)$"),
                type: str | None = Query(None, pattern="^(traffic|pertes|db_down)$"),
                database: str | None = Query(None, max_length=50)):
    rows, total = tickets.load_page(limit, offset, status, type, database)
    return _json({"tickets": rows, "total": total, "limit": limit, "offset": offset})


@tickets_router.get("/api/{ticket_id}/timeline")
def ticket_timeline(ticket_id: int):
    """Chronologie d'un ticket : ouverte à tous (même le visiteur peut comprendre l'historique d'une alerte)."""
    if tickets.get_ticket(ticket_id) is None:
        raise HTTPException(404, "Ticket introuvable")
    return _json({"ticket_id": ticket_id, "events": ticket_log.timeline(ticket_id)})


# ---------------------------------------------------------------- décisions de supervision
class ReasonBody(BaseModel):
    reason: str = Field("", max_length=500)


class CommentBody(BaseModel):
    message: str = Field(min_length=1, max_length=1500)
    send_to_orange: bool = False


class FeedbackBody(BaseModel):
    label: str = Field(pattern="^(real|false)$")


async def _announce(request: Request) -> None:
    await monitoring.sse_publish({"type": "tickets_changed", "at": now_local().isoformat(timespec="seconds")}, request.app)


@tickets_router.post("/api/{ticket_id}/approve")
async def approve_ticket(ticket_id: int, request: Request, session=supervision):
    ticket = await asyncio.to_thread(tickets.approve, ticket_id, session.username, monitoring.get_orange().configured)
    if ticket is None:
        raise HTTPException(409, "Ce ticket n'est pas (ou plus) en attente de validation")
    governance.audit(session.username, get_real_ip(request), "ticket_approve", str(ticket_id), {"base": ticket["database_name"]})
    await _announce(request)
    return _json({"ok": True})


async def _remove_ticket(action: str, ticket_id: int, body: ReasonBody, request: Request, session) -> Response:
    """Rejet ou suppression : même effet de bord — la base concernée est mise en silence (évite que l'alerte revienne aussitôt)."""
    function = tickets.reject if action == "reject" else tickets.delete
    ticket = await asyncio.to_thread(function, ticket_id, session.username, body.reason.strip() or "sans motif")
    if ticket is None:
        raise HTTPException(409, "Action impossible sur ce ticket (déjà traité ou inexistant)")
    minutes = int(load_config().get("rejection_silence_minutes", 0))
    window = await asyncio.to_thread(governance.silence_after_rejection, ticket["database_name"], session.username, minutes)
    governance.audit(session.username, get_real_ip(request), f"ticket_{action}", str(ticket_id),
                     {"base": ticket["database_name"], "reason": body.reason, "silence_minutes": minutes if window else 0})
    await _announce(request)
    return _json({"ok": True, "silence_minutes": minutes if window else 0})


@tickets_router.post("/api/{ticket_id}/reject")
async def reject_ticket(ticket_id: int, request: Request, body: ReasonBody = ReasonBody(), session=supervision):
    return await _remove_ticket("reject", ticket_id, body, request, session)


@tickets_router.post("/api/{ticket_id}/delete")
async def delete_ticket(ticket_id: int, request: Request, body: ReasonBody = ReasonBody(), session=supervision):
    return await _remove_ticket("delete", ticket_id, body, request, session)


@tickets_router.post("/api/{ticket_id}/comment")
async def comment_ticket(ticket_id: int, body: CommentBody, request: Request, session=supervision):
    if not await asyncio.to_thread(tickets.add_comment, ticket_id, session.username, body.message.strip(), body.send_to_orange):
        raise HTTPException(404, "Ticket introuvable")
    governance.audit(session.username, get_real_ip(request), "ticket_comment", str(ticket_id), {"send_to_orange": body.send_to_orange})
    return _json({"ok": True})


@tickets_router.post("/api/{ticket_id}/feedback")
async def feedback_ticket(ticket_id: int, body: FeedbackBody, request: Request, session=supervision):
    if not await asyncio.to_thread(tickets.set_feedback, ticket_id, body.label, session.username):
        raise HTTPException(404, "Ticket introuvable")
    governance.audit(session.username, get_real_ip(request), "ticket_feedback", str(ticket_id), {"label": body.label})
    return _json({"ok": True})


# ---------------------------------------------------------------- exploitation
@tickets_router.get("/status")
def get_system_status(_s=supervision):
    sched = constants.scheduler
    jobs = [{"id": j.id, "next_run": j.next_run_time.strftime("%Y-%m-%d %H:%M:%S") if j.next_run_time else None}
            for j in (sched.get_jobs() if sched else [])]
    return _json({"status": "ok", "timestamp": now_local().isoformat(), "database": db.ping_local(),
                  "tickets": tickets.counts(), "jobs": jobs, "monitoring": monitoring.status()})


@tickets_router.get("/test/orange-api")
def test_orange_api(_s=auth.require_role("admin")):
    """Diagnostic (admin) : n'expose ni identifiants ni URL complètes."""
    try:
        return _json(monitoring.get_orange().test_connection())
    except Exception:
        logger.exception("Test API Orange")
        raise HTTPException(500, "Test API Orange en échec") from None
