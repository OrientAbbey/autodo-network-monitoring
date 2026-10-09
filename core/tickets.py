"""TICKETS — dépôt SQL (tickets_tb), construction des tickets et application des décisions de ``rules``.

Principes d'optimisation écriture/lecture (base locale) :
* **écritures ciblées** : ``UPDATE … SET <champs modifiés>, version = version + 1 WHERE id = :id AND version = :v``
  (verrouillage optimiste : une copie périmée ne peut plus écraser un ticket modifié entre-temps) ;
* **une transaction par cycle** pour l'ensemble des créations/mises à jour (un seul COMMIT) ;
* **une seule lecture** des tickets ouverts par cycle (index ``(status, timestamp)``), pas de N+1 ;
* lecture des alertes du tableau de bord avec **plage de dates sargable**, ``LIMIT`` et petit cache TTL ;
* ID de ticket **atomique** (table compteur) — plus de collision ni d'écrasement silencieux.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from collections.abc import Callable

from sqlalchemy import bindparam, text

from . import constants, rules
from .config import fmt, now_local, parse, period_to_cutoff
from .db import fetch_all, fetch_one, local_conn, local_tx
from .logger import get_logger

logger = get_logger(__name__)

JSON_COLS = ("last_values", "pertes_warnings")
DT_COLS = ("timestamp", "closure_timestamp", "closure_pending_timestamp", "orange_next_retry_at")
INSERT_COLS = ("id", "type", "site", "database_name", "timestamp", "value", "status", "agent", "severity",
               "closure_timestamp", "closure_pending_timestamp", "closure_reason", "autin_id", "external_api_ticket_id",
               "ttr_seconds", "category", "pertes", "last_values", "description", "reason", "pertes_warnings",
               "orange_sync_state", "orange_attempts", "orange_next_retry_at", "orange_last_error")
_UPDATABLE = set(INSERT_COLS) - {"id"}


# ============================================================================
# Conversions ligne SQL <-> dict
# ============================================================================
def _row_to_ticket(row: dict) -> dict:
    t = dict(row)
    for c in DT_COLS:
        if isinstance(t.get(c), datetime):
            t[c] = fmt(t[c])
    for c in JSON_COLS:
        v = t.get(c)
        if isinstance(v, (str, bytes)):
            try:
                t[c] = json.loads(v)
            except Exception:
                t[c] = []
        elif v is None:
            t[c] = []
    if isinstance(t.get("value"), Decimal):
        t["value"] = float(t["value"])
    t["ttr_human"] = t["human_readable"] = rules.ttr_human(t.get("ttr_seconds"))
    return t


def _prepare(fields: dict) -> dict:
    out = {}
    for k, v in fields.items():
        if k in JSON_COLS:
            v = json.dumps(v if v is not None else [], default=str)
        elif k == "value" and v is not None:
            v = round(float(v), 3)
        out[k] = v
    return out


# ============================================================================
# Lectures
# ============================================================================
def load_open() -> list[dict]:
    """Tickets ouverts — UNE requête par cycle."""
    with local_conn() as c:
        return [_row_to_ticket(r) for r in fetch_all(c, "SELECT * FROM tickets_tb WHERE status = 'open'")]


def load_page(limit: int = 1000, offset: int = 0, status: str | None = None,
              ticket_type: str | None = None, db_key: str | None = None) -> tuple[list[dict], int]:
    """Page de tickets (les plus récents d'abord) + total. Les ouverts sont toujours listés avant les fermés."""
    where, p = [], {"limit": max(1, min(limit, 5000)), "offset": max(0, offset)}
    for col, val in (("status", status), ("type", ticket_type), ("database_name", db_key)):
        if val:
            where.append(f"{col} = :{col}")
            p[col] = val
    w = (" WHERE " + " AND ".join(where)) if where else ""
    with local_conn() as c:
        total = fetch_one(c, f"SELECT COUNT(*) AS n FROM tickets_tb{w}", **{k: v for k, v in p.items() if k in ("status", "type", "database_name")})["n"]
        rows = fetch_all(c, f"SELECT * FROM tickets_tb{w} ORDER BY (status = 'open') DESC, timestamp DESC LIMIT :limit OFFSET :offset", **p)
    return [_row_to_ticket(r) for r in rows], int(total)


def counts() -> dict:
    with local_conn() as c:
        r = fetch_one(c, "SELECT COUNT(*) AS total, COALESCE(SUM(status = 'open'), 0) AS open_n FROM tickets_tb")
    return {"total": int(r["total"]), "open": int(r["open_n"])}


_alerts_cache: dict = {}
_alerts_lock = threading.Lock()
ALERTS_TTL = 3.0      # s : N navigateurs qui interrogent en même temps = 1 requête SQL
ALERTS_LIMIT = 500


def invalidate_alert_cache() -> None:
    with _alerts_lock:
        _alerts_cache.clear()


def dashboard_alerts(filters: dict | None = None) -> dict:
    """Alertes du tableau de bord (format historique : {"traffic": [...], "pertes": [...]})."""
    filters = filters or {}
    key = json.dumps(filters, sort_keys=True, default=str)
    now = time.monotonic()
    with _alerts_lock:
        hit = _alerts_cache.get(key)
        if hit and now - hit[0] < ALERTS_TTL:
            return hit[1]
    where, p = [], {"lim": ALERTS_LIMIT}
    if filters.get("status") in ("open", "closed"):
        where.append("status = :status")
        p["status"] = filters["status"]
    types = [t for t in str(filters.get("type") or "").split(",") if t in ("traffic", "pertes", "db_down")]
    if types:
        where.append("type IN :types")
        p["types"] = tuple(types)
    period = filters.get("period")
    today = now_local().replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    start = {"today": today, "week": today - timedelta(days=today.weekday()), "month": today.replace(day=1)}.get(period)
    if start:
        where.append("timestamp >= :start")
        p["start"] = fmt(start)
    sql = "SELECT id, type, database_name, timestamp, status, reason, value, last_values, external_api_ticket_id, autin_id " \
          "FROM tickets_tb" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY timestamp DESC LIMIT :lim"
    stmt = text(sql)
    if "types" in p:
        stmt = stmt.bindparams(bindparam("types", expanding=True))
    with local_conn() as c:
        rows = [dict(r) for r in c.execute(stmt, p).mappings().all()]
    result = {"traffic": [], "pertes": []}
    for a in rows:
        try:
            vals = [v["value"] for v in json.loads(a["last_values"] or "[]")]
            vals = vals[1:] if len(vals) > 1 else vals
        except Exception:
            vals = [float(a["value"])] if a.get("value") is not None else []
        loc = constants.LOCATION_MAPPING.get(a["database_name"], a["database_name"])
        item = {"id": a["id"], "type": a["type"].upper(), "location": loc, "title": f"Alerte {a['type'].upper()} - {loc}",
                "message": a["reason"], "valeurs": vals, "status": a["status"],
                "date": a["timestamp"].strftime("%Y-%m-%d %H:%M"), "creation_time": a["timestamp"].strftime("%Y-%m-%d %H:%M"),
                "external_id": a.get("external_api_ticket_id"), "autin_id": a.get("autin_id")}
        result["pertes" if a["type"] == "pertes" else "traffic"].append(item)
    with _alerts_lock:
        _alerts_cache[key] = (now, result)
    return result


def alerts_stats(period: str = "7days") -> dict:
    cutoff = fmt(period_to_cutoff(period))
    with local_conn() as c:
        rows = fetch_all(c, """SELECT DATE(timestamp) AS day,
                SUM(type = 'traffic') AS traffic, SUM(type = 'pertes') AS pertes, SUM(type = 'db_down') AS db_down, COUNT(*) AS total
                FROM tickets_tb WHERE timestamp >= :c GROUP BY DATE(timestamp) ORDER BY day DESC""", c=cutoff)
    return {str(r["day"]): {k: int(r[k] or 0) for k in ("traffic", "pertes", "db_down", "total")} for r in rows}


# ============================================================================
# Identifiant atomique
# ============================================================================
def next_id() -> int:
    """YYYYMMDDNNN, attribué atomiquement ; suffixe à 4 chiffres au-delà de 999 tickets/jour."""
    day = now_local().date()
    prefix = int(day.strftime("%Y%m%d"))
    with local_tx() as c:
        seed = int(c.execute(text("SELECT COALESCE(MAX(id), :base) FROM tickets_tb WHERE id BETWEEN :lo AND :hi"),
                             {"base": prefix * 1000, "lo": prefix * 1000, "hi": prefix * 1000 + 999}).scalar()) - prefix * 1000
        c.execute(text("INSERT INTO ticket_counters (day, n) VALUES (:d, LAST_INSERT_ID(:n)) "
                       "ON DUPLICATE KEY UPDATE n = LAST_INSERT_ID(n + 1)"), {"d": day, "n": seed + 1})
        n = int(c.execute(text("SELECT LAST_INSERT_ID()")).scalar())
    return prefix * 1000 + n if n < 1000 else int(f"{prefix}{n:04d}")


# ============================================================================
# Écritures
# ============================================================================
@dataclass
class ChangeSet:
    inserts: list[dict] = field(default_factory=list)
    updates: dict[int, dict] = field(default_factory=dict)       # id -> champs modifiés
    versions: dict[int, int] = field(default_factory=dict)       # id -> version lue
    events: list[tuple] = field(default_factory=list)            # (kind, ticket_id, db_key) pour journal/notifications

    def update(self, ticket: dict, fields: dict) -> None:
        if fields:
            self.updates.setdefault(ticket["id"], {}).update(fields)
            self.versions.setdefault(ticket["id"], int(ticket.get("version") or 0))
            ticket.update(fields)                       # vue en mémoire cohérente pour la suite du cycle


def persist(cs: ChangeSet) -> list[int]:
    """Applique le ChangeSet dans UNE transaction. Retourne les ID en conflit de version (réévalués au cycle suivant)."""
    conflicts: list[int] = []
    if not cs.inserts and not cs.updates:
        return conflicts
    cols = ", ".join(f"`{c}`" for c in INSERT_COLS)
    vals = ", ".join(f":{c}" for c in INSERT_COLS)
    with local_tx() as c:
        if cs.inserts:
            rows = [_prepare({k: t.get(k) for k in INSERT_COLS}) for t in cs.inserts]
            c.execute(text(f"INSERT INTO tickets_tb ({cols}) VALUES ({vals})"), rows)      # strict : jamais d'écrasement
        for tid, fields in cs.updates.items():
            f = {k: v for k, v in _prepare(fields).items() if k in _UPDATABLE}
            if not f:
                continue
            sets = ", ".join(f"`{k}` = :{k}" for k in f)
            res = c.execute(text(f"UPDATE tickets_tb SET {sets}, version = version + 1 WHERE id = :_id AND version = :_v"),
                            {**f, "_id": tid, "_v": cs.versions.get(tid, 0)})
            if res.rowcount == 0:
                conflicts.append(tid)
    if conflicts:
        logger.warning("Conflit de version sur les tickets %s (modifiés en parallèle) — réévaluation au prochain cycle", conflicts)
    invalidate_alert_cache()
    return conflicts


def update_fields(ticket_id: int, fields: dict, *, expected_state: str | None = None) -> bool:
    """UPDATE ciblé hors cycle de détection (sync Orange). Conditionnel sur l'état Orange si demandé."""
    f = {k: v for k, v in _prepare(fields).items() if k in _UPDATABLE}
    if not f:
        return False
    sets = ", ".join(f"`{k}` = :{k}" for k in f)
    cond = " AND orange_sync_state = :_exp" if expected_state else ""
    with local_tx() as c:
        r = c.execute(text(f"UPDATE tickets_tb SET {sets}, version = version + 1 WHERE id = :_id{cond}"),
                      {**f, "_id": ticket_id, **({"_exp": expected_state} if expected_state else {})})
        return r.rowcount == 1


# ============================================================================
# Construction / application des décisions
# ============================================================================
@dataclass
class DbContext:
    """Tout ce qu'il faut pour décider des tickets d'UNE base, pour UN cycle."""
    db_key: str
    now: str                                        # heure locale du checkpoint
    traffic_values: list                            # [{"timestamp","value"}] (J-1 en tête)
    loss_values: list
    traffic: rules.Verdict
    pertes: rules.Verdict
    mode7d: float | None
    cfg: dict
    connection_failed: bool = False
    failure_reason: str = ""
    orange_enabled: bool = True

    @property
    def site(self) -> str:
        return constants.DB_CONFIGS.get(self.db_key, {}).get("site", "Unknown")


def _new_ticket(ctx: DbContext, ttype: str, values: list, reason: str, category: int,
                id_factory: Callable[[], int], *, description: str | None = None,
                timestamp: str | None = None) -> dict:
    value, ts = rules.original_value(ttype, values, category, ctx.cfg)
    ts = timestamp or ts or ctx.now
    desc = description or rules.build_description(ttype, ctx.site, reason, value, ts, ctx.connection_failed, ctx.failure_reason)
    return {"id": id_factory(), "type": ttype, "site": ctx.site, "database_name": ctx.db_key, "timestamp": ts,
            "value": value, "status": "open", "agent": "DIAGNET", "severity": "critical", "description": desc,
            "reason": reason, "autin_id": None, "pertes": "No", "external_api_ticket_id": None,
            "closure_timestamp": None, "closure_pending_timestamp": None, "closure_reason": None, "ttr_seconds": None,
            "last_values": values, "category": category, "pertes_warnings": [], "version": 0,
            "orange_sync_state": "pending" if ctx.orange_enabled else "skipped", "orange_attempts": 0,
            "orange_next_retry_at": None, "orange_last_error": None}


def _close_fields(ticket: dict, fields: dict) -> dict:
    """Si la fermeture est décidée, programme la fermeture côté Orange (outbox) selon l'état de synchronisation."""
    if fields.get("status") == "closed":
        state = ticket.get("orange_sync_state")
        if state == "synced" and ticket.get("external_api_ticket_id"):
            fields["orange_sync_state"], fields["orange_attempts"], fields["orange_next_retry_at"] = "close_pending", 0, None
        elif state in ("pending", "failed"):
            fields["orange_sync_state"] = "closed"        # jamais créé chez Orange : rien à restaurer
    return fields


def apply_actions(ctx: DbContext, actions: list[rules.Action], tickets_by_type: dict, cs: ChangeSet,
                  id_factory: Callable[[], int] = next_id) -> None:
    """Traduit les actions décidées par ``rules.plan_actions`` en changements (sans toucher à la base)."""
    traffic_t, pertes_t = tickets_by_type.get("traffic"), tickets_by_type.get("pertes")
    loss_last = ctx.loss_values[-1]["value"] if ctx.loss_values else 0.0
    new_traffic: dict | None = None
    for a in actions:
        if a.kind == "create_traffic":
            new_traffic = _new_ticket(ctx, "traffic", ctx.traffic_values, ctx.traffic.reason, ctx.traffic.category, id_factory)
            if a.attach_pertes:
                new_traffic.update(rules.attach_pertes(new_traffic, loss_last, ctx.now))
            cs.inserts.append(new_traffic)
            cs.events.append(("create_traffic", new_traffic["id"], ctx.db_key))
        elif a.kind == "create_pertes":
            t = _new_ticket(ctx, "pertes", ctx.loss_values, ctx.pertes.reason, ctx.pertes.category, id_factory)
            cs.inserts.append(t)
            cs.events.append(("create_pertes", t["id"], ctx.db_key))
        elif a.kind in ("update_traffic", "update_pertes"):
            t = traffic_t if a.kind == "update_traffic" else pertes_t
            if t is None:
                continue
            if a.kind == "update_traffic":
                fields = rules.evaluate_update(t, ctx.traffic_values, ctx.traffic.reason, ctx.traffic.category, ctx.cfg)
            else:
                fields = rules.evaluate_update(t, ctx.loss_values, ctx.pertes.reason, ctx.pertes.category, ctx.cfg)
            cs.update(t, fields)
            if a.attach_pertes:
                cs.update(t, rules.attach_pertes(t, loss_last, ctx.now))
            cs.events.append((a.kind, t["id"], ctx.db_key))
        elif a.kind == "try_close":
            t = traffic_t if a.ticket_type == "traffic" else pertes_t
            if t is None:
                continue
            values = ctx.traffic_values if a.ticket_type == "traffic" else ctx.loss_values
            fields = rules.evaluate_closure(t, values, ctx.now, ctx.cfg, ctx.mode7d)
            fields = _close_fields(t, fields)
            cs.update(t, fields)
            if fields.get("status") == "closed":
                cs.events.append((f"close_{a.ticket_type}", t["id"], ctx.db_key))


def apply_db_down(ctx_db_key: str, state: str, minutes: float, last_update_str: str | None, now: str, cfg: dict,
                  open_ticket: dict | None, cs: ChangeSet, orange_enabled: bool,
                  id_factory: Callable[[], int] = next_id) -> None:
    """Tickets « base de données figée » : création / rafraîchissement / fermeture avec hystérésis."""
    site = constants.DB_CONFIGS.get(ctx_db_key, {}).get("site", "Site inconnu")
    values = [{"timestamp": last_update_str or now, "value": minutes}]
    if state == "down":
        desc = rules.db_down_description(site, minutes, last_update_str or "?", now, cfg["db_down_threshold_minutes"])
        reason = rules.db_down_reason(ctx_db_key, minutes)
        if open_ticket is None:
            c = DbContext(ctx_db_key, now, values, [], rules.Verdict(False), rules.Verdict(False), None, cfg,
                          orange_enabled=orange_enabled)
            t = _new_ticket(c, "db_down", values, reason, 1, id_factory, description=desc, timestamp=last_update_str or now)
            cs.inserts.append(t)
            cs.events.append(("create_db_down", t["id"], ctx_db_key))
        else:
            cs.update(open_ticket, {"value": minutes, "reason": reason, "category": 1, "last_values": values,
                                    "description": desc, "closure_pending_timestamp": None})
    elif state == "ok" and open_ticket is not None:
        fields = rules.evaluate_closure(open_ticket, values, now, cfg, None)
        fields = _close_fields(open_ticket, fields)
        cs.update(open_ticket, fields)
        if fields.get("status") == "closed":
            cs.events.append(("close_db_down", open_ticket["id"], ctx_db_key))


# ============================================================================
# Outbox Orange (lecture/écriture)
# ============================================================================
def claim_for_orange(states: tuple, limit: int, now: str) -> list[dict]:
    """Réserve (bail de 5 min) les tickets à synchroniser dont l'échéance est atteinte. Idempotent, sans verrou long."""
    lease = fmt(parse(now).replace(microsecond=0) + timedelta(minutes=5))
    stmt = text("SELECT * FROM tickets_tb WHERE orange_sync_state IN :states AND "
                "(orange_next_retry_at IS NULL OR orange_next_retry_at <= :now) ORDER BY timestamp LIMIT :n"
                ).bindparams(bindparam("states", expanding=True))
    with local_conn() as c:
        rows = [dict(r) for r in c.execute(stmt, {"states": list(states), "now": now, "n": limit}).mappings()]
    claimed: list[dict] = []
    for r in rows:
        with local_tx() as c:
            res = c.execute(text("UPDATE tickets_tb SET orange_next_retry_at = :lease, version = version + 1 "
                                 "WHERE id = :id AND version = :v"), {"lease": lease, "id": r["id"], "v": r["version"]})
            if res.rowcount == 1:
                claimed.append(_row_to_ticket({**r, "version": r["version"] + 1}))
    return claimed


def open_with_orange_id() -> list[dict]:
    with local_conn() as c:
        return [_row_to_ticket(r) for r in fetch_all(
            c, "SELECT * FROM tickets_tb WHERE status = 'open' AND orange_sync_state = 'synced' "
               "AND external_api_ticket_id IS NOT NULL AND external_api_ticket_id <> ''")]
