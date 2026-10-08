#!/usr/bin/env python3
"""Faux API Orange CALYPSO (OAuth2 + troubleTicket) pour la démo. Aucun appel externe.

Lancement : uvicorn mock_orange_api:app --port 9001
Points de contrôle (hors API réelle) : GET /_debug/tickets  ·  POST /_debug/reset
Variable MOCK_ORANGE_FAIL=1 -> POST troubleTicket répond 503 (test des retries).
"""
import time
import itertools
import os
import threading
from datetime import datetime, timezone
from fastapi import FastAPI, Request, Response

app = FastAPI(title="Mock Orange API")
BASE = "/troubleticket_sandbox_b2b/v3"
_lock = threading.Lock()
_ids = itertools.count(1000)
TICKETS: dict[str, dict] = {}
CALLS: list[dict] = []
_window: dict[int, int] = {}


@app.post("/oauth/v3/token")
async def token(request: Request):
    CALLS.append({"op": "token", "auth": request.headers.get("authorization", "")[:6]})
    return {"access_token": "mock-token", "token_type": "Bearer", "expires_in": 3600}


@app.post(f"{BASE}/troubleTicket")
async def create(request: Request):
    body = await request.json()
    CALLS.append({"op": "create"})
    limit = int(os.getenv("MOCK_ORANGE_RATE_LIMIT", "0"))       # > 0 : au-delà de N créations/s -> HTTP 429 + Retry-After
    if limit:
        now = int(time.time())
        _window[now] = _window.get(now, 0) + 1
        if _window[now] > limit:
            return Response(status_code=429, headers={"Retry-After": "2"}, content='{"message":"rate limit"}',
                            media_type="application/json")
    if os.getenv("MOCK_ORANGE_FAIL") == "1":
        return Response(status_code=503, content='{"message":"unavailable"}', media_type="application/json")
    with _lock:
        tid = f"TT{next(_ids)}"
        TICKETS[tid] = {"id": tid, "payload": body, "status": "InProgress",
                        "created": datetime.now(timezone.utc).isoformat()}
    return Response(status_code=201, content=f'{{"id":"{tid}"}}', media_type="application/json")


@app.get(f"{BASE}/troubleTicket")
async def search(externalId: str = ""):
    found = [t for t in TICKETS.values() if str(t["payload"].get("description", "")).find(f"AUTODO ticket: {externalId}") >= 0]
    return {"items": [{"id": t["id"]} for t in found]}


@app.get(f"{BASE}/troubleTicket/{{tid}}")
async def get_one(tid: str):
    t = TICKETS.get(tid)
    if not t:
        return Response(status_code=404, content='{"message":"not found"}', media_type="application/json")
    inprog = t["status"] == "InProgress"
    entries = [{"code": "InProgress", "isCurrentStatus": inprog}]
    if not inprog:
        entries.append({"code": t["status"], "isCurrentStatus": True})
    return {"id": tid, "status": entries}


@app.put(f"{BASE}/troubleTicket/{{tid}}")
async def restore(tid: str, request: Request):
    CALLS.append({"op": "restore", "id": tid, "override": request.headers.get("x-http-method-override")})
    if tid in TICKETS:
        TICKETS[tid]["status"] = "Restored"
    return Response(status_code=204)


@app.post("/_debug/close/{tid}")
async def debug_close(tid: str):
    """Simule une clôture côté Orange (pour tester la réconciliation)."""
    if tid in TICKETS:
        TICKETS[tid]["status"] = "Closed"
    return {"ok": tid in TICKETS}


@app.get("/_debug/tickets")
async def debug_tickets():
    return {"tickets": list(TICKETS.values()), "calls": CALLS}


@app.post("/_debug/reset")
async def reset():
    TICKETS.clear(); CALLS.clear()
    return {"ok": True}
