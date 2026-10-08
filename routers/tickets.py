"""ROUTES TICKETS — page, API paginée, diagnostic protégé."""
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
import orjson

from core import auth, constants, db, monitoring, tickets
from core.config import now_local
from core.logger import get_logger

logger = get_logger(__name__)
tickets_router = APIRouter()
PAGE_LIMIT = 1000        # tickets envoyés à la page (les ouverts d'abord) ; le reste via /tickets/api?offset=


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
                status: str | None = Query(None, pattern="^(open|closed)$"),
                type: str | None = Query(None, pattern="^(traffic|pertes|db_down)$"),
                database: str | None = Query(None, max_length=50)):
    rows, total = tickets.load_page(limit, offset, status, type, database)
    return _json({"tickets": rows, "total": total, "limit": limit, "offset": offset})


@tickets_router.get("/status")
def get_system_status(_=auth.require_any_session()):
    sched = constants.scheduler
    jobs = [{"id": j.id, "next_run": j.next_run_time.strftime("%Y-%m-%d %H:%M:%S") if j.next_run_time else None}
            for j in (sched.get_jobs() if sched else [])]
    return _json({"status": "ok", "timestamp": now_local().isoformat(), "database": db.ping_local(),
                  "tickets": tickets.counts(), "jobs": jobs, "monitoring": monitoring.status()})


@tickets_router.get("/test/orange-api")
def test_orange_api(_=auth.require_session(auth.SCOPE_CONFIG)):
    """Diagnostic (session de configuration requise) : n'expose ni identifiants ni URL complètes."""
    try:
        return _json(monitoring.get_orange().test_connection())
    except Exception:
        logger.exception("Test API Orange")
        raise HTTPException(500, "Test API Orange en échec") from None
