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

from . import constants, rules, ticket_log
from .config import fmt, now_local, parse, period_to_cutoff
from .db import fetch_all, fetch_one, local_conn, local_tx
from .logger import get_logger

logger = get_logger(__name__)

JSON_COLS = ("last_values", "pertes_warnings")
DT_COLS = ("timestamp", "closure_timestamp", "closure_pending_timestamp", "orange_next_retry_at", "last_note_at")
INSERT_COLS = ("id", "type", "site", "database_name", "timestamp", "value", "status", "agent", "severity",
               "closure_timestamp", "closure_pending_timestamp", "closure_reason", "autin_id", "external_api_ticket_id",
               "ttr_seconds", "category", "pertes", "last_values", "description", "reason", "pertes_warnings",
               "orange_sync_state", "orange_attempts", "orange_next_retry_at", "orange_last_error",
               "validation_state", "last_note_value", "last_note_at")
_UPDATABLE = set(INSERT_COLS) - {"id"}
INSERT_DEFAULTS = {"validation_state": "auto"}      # colonnes NOT NULL sans valeur fournie par l'appelant


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
    for numeric in ("value", "last_note_value"):
        if isinstance(t.get(numeric), Decimal):
            t[numeric] = float(t[numeric])
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
              ticket_type: str | None = None, db_key: str | None = None, include_deleted: bool = False) -> tuple[list[dict], int]:
    """Page de tickets (les plus récents d'abord) + total. Les ouverts sont toujours listés avant les fermés.

    Les tickets supprimés (suppression logique) sont masqués sauf demande explicite.
    """
    where, p = [], {"limit": max(1, min(limit, 5000)), "offset": max(0, offset)}
    if not include_deleted and status != "deleted":
        where.append("status <> 'deleted'")
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
        r = fetch_one(c, "SELECT COUNT(*) AS total, COALESCE(SUM(status = 'open'), 0) AS open_n FROM tickets_tb WHERE status <> 'deleted'")
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
    where, p = ["t.status <> 'deleted'"], {"lim": ALERTS_LIMIT}
    if filters.get("status") in ("open", "closed"):
        where.append("t.status = :status")
        p["status"] = filters["status"]
    types = [t for t in str(filters.get("type") or "").split(",") if t in ("traffic", "pertes", "db_down")]
    if types:
        where.append("t.type IN :types")
        p["types"] = tuple(types)
    period = filters.get("period")
    today = now_local().replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    start = {"today": today, "week": today - timedelta(days=today.weekday()), "month": today.replace(day=1)}.get(period)
    if start:
        where.append("t.timestamp >= :start")
        p["start"] = fmt(start)
    sql = ("SELECT t.id, t.type, t.database_name, t.timestamp, t.status, t.reason, t.value, t.last_values, "
           "t.external_api_ticket_id, t.autin_id, t.validation_state, (a.ticket_id IS NOT NULL) AS acked, a.acked_by "
           "FROM tickets_tb t LEFT JOIN alert_acks a ON a.ticket_id = t.id WHERE " + " AND ".join(where) +
           " ORDER BY t.timestamp DESC LIMIT :lim")
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
                "external_id": a.get("external_api_ticket_id"), "autin_id": a.get("autin_id"),
                "validation_state": a.get("validation_state"), "acked": bool(a.get("acked")), "acked_by": a.get("acked_by")}
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
    log: list[dict] = field(default_factory=list)                # lignes de ticket_events, écrites avec les tickets

    def add_event(self, ticket_id: int, kind: str, message: str, *, to_orange: bool = False, at: str | None = None) -> None:
        self.log.append(ticket_log.make_event(ticket_id, kind, message, to_orange=to_orange, at=at))

    def update(self, ticket: dict, fields: dict) -> None:
        if fields:
            self.updates.setdefault(ticket["id"], {}).update(fields)
            self.versions.setdefault(ticket["id"], int(ticket.get("version") or 0))
            ticket.update(fields)                       # vue en mémoire cohérente pour la suite du cycle


def persist(cs: ChangeSet) -> list[int]:
    """Applique le ChangeSet dans UNE transaction. Retourne les ID en conflit de version (réévalués au cycle suivant)."""
    conflicts: list[int] = []
    if not cs.inserts and not cs.updates and not cs.log:
        return conflicts
    cols = ", ".join(f"`{c}`" for c in INSERT_COLS)
    vals = ", ".join(f":{c}" for c in INSERT_COLS)
    with local_tx() as c:
        if cs.inserts:
            rows = [_prepare({k: (t.get(k) if t.get(k) is not None else INSERT_DEFAULTS.get(k)) for k in INSERT_COLS}) for t in cs.inserts]
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
        ticket_log.insert_events(c, cs.log)                     # même transaction : chronologie toujours cohérente avec les tickets
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
    # (la validation humaine des créations se lit dans cfg["ticket_validation_required"])

    @property
    def site(self) -> str:
        return constants.DB_CONFIGS.get(self.db_key, {}).get("site", "Unknown")


def _new_ticket(ctx: DbContext, ttype: str, values: list, reason: str, category: int,
                id_factory: Callable[[], int], *, description: str | None = None,
                timestamp: str | None = None) -> dict:
    value, ts = rules.original_value(ttype, values, category, ctx.cfg)
    ts = timestamp or ts or ctx.now
    desc = description or rules.build_description(ttype, ctx.site, reason, value, ts, ctx.connection_failed, ctx.failure_reason)
    validation_required = bool(ctx.cfg.get("ticket_validation_required"))
    if validation_required:
        orange_state = "held"                       # rien n'est envoyé à Orange tant qu'un superviseur n'a pas validé
    else:
        orange_state = "pending" if ctx.orange_enabled else "skipped"
    return {"id": id_factory(), "type": ttype, "site": ctx.site, "database_name": ctx.db_key, "timestamp": ts,
            "value": value, "status": "open", "agent": "DIAGNET", "severity": "critical", "description": desc,
            "reason": reason, "autin_id": None, "pertes": "No", "external_api_ticket_id": None,
            "closure_timestamp": None, "closure_pending_timestamp": None, "closure_reason": None, "ttr_seconds": None,
            "last_values": values, "category": category, "pertes_warnings": [], "version": 0,
            "orange_sync_state": orange_state, "orange_attempts": 0, "orange_next_retry_at": None,
            "orange_last_error": None, "validation_state": "pending" if validation_required else "auto",
            "last_note_value": None, "last_note_at": None}


def _close_fields(ticket: dict, fields: dict) -> dict:
    """Si la fermeture est décidée, programme la fermeture côté Orange (outbox) selon l'état de synchronisation."""
    if fields.get("status") == "closed":
        state = ticket.get("orange_sync_state")
        if state == "synced" and ticket.get("external_api_ticket_id"):
            fields["orange_sync_state"], fields["orange_attempts"], fields["orange_next_retry_at"] = "close_pending", 0, None
        elif state in ("pending", "failed", "held"):
            fields["orange_sync_state"] = "closed"        # jamais créé chez Orange : rien à restaurer
    return fields


def _log_creation(cs: ChangeSet, ticket: dict, now: str) -> None:
    cs.add_event(ticket["id"], "created", ticket.get("reason") or str(ticket.get("description", ""))[:200], at=now)
    if ticket.get("validation_state") == "pending":
        cs.add_event(ticket["id"], "validation_requested", "Création en attente de validation par un superviseur", at=now)


def _update_and_log(cs: ChangeSet, ticket: dict, fields: dict, now: str) -> None:
    """Applique ``fields`` au ticket et journalise ce qui compte (pas la simple actualisation des valeurs récentes)."""
    if not fields:
        return
    before_category = ticket.get("category")
    before_pending = ticket.get("closure_pending_timestamp")
    before_warnings = len(ticket.get("pertes_warnings") or [])
    cs.update(ticket, fields)
    if fields.get("status") == "closed":
        cs.add_event(ticket["id"], "closed", fields.get("closure_reason") or "Ticket fermé", at=now)
    elif fields.get("closure_pending_timestamp") and not before_pending:
        cs.add_event(ticket["id"], "closure_pending", "Critère de fermeture rempli : fermeture si la situation persiste", at=now)
    elif fields.get("category") not in (None, before_category):
        # (le motif change à chaque cycle car il cite les valeurs courantes : seul un changement de catégorie est un fait marquant)
        cs.add_event(ticket["id"], "update", f"Catégorie {before_category} → {fields['category']} : {fields.get('reason') or ''}", at=now)
    if len(fields.get("pertes_warnings") or []) > before_warnings:
        cs.add_event(ticket["id"], "pertes_warning", fields["pertes_warnings"][-1]["message"], at=now)


def _maybe_note(cs: ChangeSet, ticket: dict, current: float | None, cfg: dict, now: str) -> None:
    """Prépare une note pour Orange quand la valeur a assez changé ET que la dernière note est assez ancienne."""
    if current is None or ticket.get("status") != "open":
        return
    if ticket.get("orange_sync_state") not in ("pending", "synced", "held"):
        return                                           # ticket jamais envoyé / abandonné : aucune note à transmettre
    reference = ticket.get("last_note_value")
    if reference is None:
        reference = ticket.get("value")
    reference = float(reference or 0.0)
    if abs(rules.percent_variation(reference, current)) < cfg.get("orange_note_min_change_percent", 10.0):
        return
    since = ticket.get("last_note_at") or ticket.get("timestamp")
    minimum_gap = timedelta(minutes=cfg.get("orange_note_min_interval_minutes", 30))
    if since and parse(now) - parse(since) < minimum_gap:
        return
    cs.add_event(ticket["id"], "orange_note", ticket_log.note_message(ticket["type"], current, reference), to_orange=True, at=now)
    cs.update(ticket, {"last_note_value": round(float(current), 3), "last_note_at": now})


def _last_value(values: list) -> float | None:
    return values[-1]["value"] if values else None


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
            _log_creation(cs, new_traffic, ctx.now)
        elif a.kind == "create_pertes":
            t = _new_ticket(ctx, "pertes", ctx.loss_values, ctx.pertes.reason, ctx.pertes.category, id_factory)
            cs.inserts.append(t)
            cs.events.append(("create_pertes", t["id"], ctx.db_key))
            _log_creation(cs, t, ctx.now)
        elif a.kind in ("update_traffic", "update_pertes"):
            t = traffic_t if a.kind == "update_traffic" else pertes_t
            if t is None:
                continue
            if a.kind == "update_traffic":
                fields = rules.evaluate_update(t, ctx.traffic_values, ctx.traffic.reason, ctx.traffic.category, ctx.cfg)
                current = _last_value(ctx.traffic_values)
            else:
                fields = rules.evaluate_update(t, ctx.loss_values, ctx.pertes.reason, ctx.pertes.category, ctx.cfg)
                current = _last_value(ctx.loss_values)
            _update_and_log(cs, t, fields, ctx.now)
            if a.attach_pertes:
                _update_and_log(cs, t, rules.attach_pertes(t, loss_last, ctx.now), ctx.now)
            _maybe_note(cs, t, current, ctx.cfg, ctx.now)
            cs.events.append((a.kind, t["id"], ctx.db_key))
        elif a.kind == "try_close":
            t = traffic_t if a.ticket_type == "traffic" else pertes_t
            if t is None:
                continue
            values = ctx.traffic_values if a.ticket_type == "traffic" else ctx.loss_values
            fields = rules.evaluate_closure(t, values, ctx.now, ctx.cfg, ctx.mode7d)
            fields = _close_fields(t, fields)
            _update_and_log(cs, t, fields, ctx.now)
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
            _log_creation(cs, t, now)
        else:
            _update_and_log(cs, open_ticket, {"value": minutes, "reason": reason, "category": 1, "last_values": values,
                                              "description": desc, "closure_pending_timestamp": None}, now)
            _maybe_note(cs, open_ticket, minutes, cfg, now)
    elif state == "ok" and open_ticket is not None:
        fields = rules.evaluate_closure(open_ticket, values, now, cfg, None)
        fields = _close_fields(open_ticket, fields)
        _update_and_log(cs, open_ticket, fields, now)
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


def orange_queue_counts() -> dict:
    """Nombre de tickets par état de synchronisation Orange (page « santé » de l'admin)."""
    with local_conn() as c:
        rows = fetch_all(c, "SELECT orange_sync_state AS state, COUNT(*) AS n FROM tickets_tb GROUP BY orange_sync_state")
    return {r["state"]: int(r["n"]) for r in rows}


def open_with_orange_id() -> list[dict]:
    with local_conn() as c:
        return [_row_to_ticket(r) for r in fetch_all(
            c, "SELECT * FROM tickets_tb WHERE status = 'open' AND orange_sync_state = 'synced' "
               "AND external_api_ticket_id IS NOT NULL AND external_api_ticket_id <> ''")]


# ============================================================================
# Décisions humaines (superviseur) : validation, rejet, suppression, commentaire, retour d'expérience
# ============================================================================
def get_ticket(ticket_id: int) -> dict | None:
    with local_conn() as c:
        row = fetch_one(c, "SELECT * FROM tickets_tb WHERE id = :id", id=ticket_id)
    return _row_to_ticket(row) if row else None


def pending_validation() -> list[dict]:
    """Créations en attente de validation (tickets encore ouverts)."""
    with local_conn() as c:
        rows = fetch_all(c, "SELECT * FROM tickets_tb WHERE validation_state = 'pending' AND status = 'open' ORDER BY timestamp")
    return [_row_to_ticket(r) for r in rows]


def _decide(ticket_id: int, condition: str, build_fields: Callable[[dict], dict], kind: str, message: str, actor: str) -> dict | None:
    """Modification conditionnelle d'un ticket + évènement de chronologie, dans UNE transaction.

    ``condition`` (SQL fixe, jamais issu de l'utilisateur) garantit qu'une décision ne s'applique qu'une fois :
    si un autre superviseur est passé avant, la ligne n'est plus sélectionnée et on retourne None.
    """
    with local_tx() as c:
        row = fetch_one(c, f"SELECT * FROM tickets_tb WHERE id = :id AND {condition} FOR UPDATE", id=ticket_id)
        if row is None:
            return None
        fields = {k: v for k, v in _prepare(build_fields(row)).items() if k in _UPDATABLE}
        sets = ", ".join(f"`{k}` = :{k}" for k in fields)
        c.execute(text(f"UPDATE tickets_tb SET {sets}, version = version + 1 WHERE id = :_id"), {**fields, "_id": ticket_id})
        ticket_log.insert_events(c, [ticket_log.make_event(ticket_id, kind, message, actor)])
    invalidate_alert_cache()
    return _row_to_ticket(row)


def approve(ticket_id: int, actor: str, orange_enabled: bool) -> dict | None:
    """Valide la création : le ticket peut partir chez Orange (ou reste local si Orange n'est pas configuré)."""
    return _decide(ticket_id, "validation_state = 'pending' AND status = 'open'",
                   lambda row: {"validation_state": "approved", "orange_sync_state": "pending" if orange_enabled else "skipped"},
                   "approved", f"Création validée par {actor}", actor)


def reject(ticket_id: int, actor: str, reason: str) -> dict | None:
    """Refuse la création : le ticket disparaît (jamais envoyé à Orange)."""
    now = fmt(now_local().replace(tzinfo=None))
    return _decide(ticket_id, "validation_state = 'pending' AND status = 'open'",
                   lambda row: {"validation_state": "rejected", "status": "deleted", "orange_sync_state": "closed",
                                "closure_timestamp": now, "closure_reason": f"Rejeté par {actor} : {reason}"[:1000]},
                   "rejected", f"Création rejetée par {actor} : {reason}", actor)


def delete(ticket_id: int, actor: str, reason: str) -> dict | None:
    """Suppression logique. Si le ticket existe chez Orange, sa restauration est programmée (outbox ``close_pending``)."""
    now = fmt(now_local().replace(tzinfo=None))

    def fields_for(row: dict) -> dict:
        synced = row["orange_sync_state"] == "synced" and row["external_api_ticket_id"]
        return {"status": "deleted", "closure_timestamp": now, "closure_reason": f"Supprimé par {actor} : {reason}"[:1000],
                "orange_sync_state": "close_pending" if synced else "closed", "orange_attempts": 0, "orange_next_retry_at": None}

    return _decide(ticket_id, "status <> 'deleted'", fields_for, "deleted", f"Ticket supprimé par {actor} : {reason}", actor)


def add_comment(ticket_id: int, actor: str, message: str, send_to_orange: bool) -> bool:
    """Commentaire de supervision dans la chronologie ; transmis à Orange (note) si demandé."""
    if get_ticket(ticket_id) is None:
        return False
    orange_text = f"{actor}: {message}" if send_to_orange else message
    ticket_log.log_event(ticket_id, "comment", orange_text, actor, to_orange=send_to_orange)
    return True


def set_feedback(ticket_id: int, label: str, actor: str) -> bool:
    """Retour d'expérience « real » / « false » (alimente l'apprentissage de la qualité d'alerte)."""
    if label not in ("real", "false") or get_ticket(ticket_id) is None:
        return False
    with local_tx() as c:
        c.execute(text("REPLACE INTO ticket_feedback (ticket_id, label, given_by, given_at) VALUES (:t, :l, :a, :d)"),
                  {"t": ticket_id, "l": label, "a": actor[:64], "d": fmt(now_local().replace(tzinfo=None))})
        ticket_log.insert_events(c, [ticket_log.make_event(
            ticket_id, "feedback", "Alerte jugée " + ("réelle" if label == "real" else "fausse (faux positif)") + f" par {actor}", actor)])
    return True


# ============================================================================
# Accusés de réception (partagés entre tous les navigateurs)
# ============================================================================
def ack_alerts(ticket_ids: list[int], actor: str) -> int:
    """Acquitte des alertes (ouvert aux visiteurs). Retourne le nombre de nouveaux accusés."""
    now = fmt(now_local().replace(tzinfo=None))
    new = 0
    with local_tx() as c:
        for tid in ticket_ids[:200]:
            res = c.execute(text("INSERT IGNORE INTO alert_acks (ticket_id, acked_at, acked_by) "
                                 "SELECT id, :n, :a FROM tickets_tb WHERE id = :id"), {"n": now, "a": actor[:64], "id": int(tid)})
            if res.rowcount:
                new += 1
                ticket_log.insert_events(c, [ticket_log.make_event(int(tid), "acked", f"Alerte acquittée par {actor}", actor)])
    invalidate_alert_cache()
    return new
