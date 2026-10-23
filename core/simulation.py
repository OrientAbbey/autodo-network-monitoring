"""SIMULATION « ET SI ? » (A4) — rejoue une configuration CANDIDATE sur l'historique local avant de l'enregistrer.

On applique, relevé après relevé, EXACTEMENT les règles de production (``rules.detect_traffic`` / ``rules.detect_pertes``,
seuils par base inclus) à la configuration actuelle puis à la candidate, et l'on compare le nombre d'ÉPISODES d'alerte.

Un épisode = une alerte continue ; il ne se termine que lorsque l'alerte a disparu depuis au moins
``alert_persistence_duration_minutes`` (reflet de la fenêtre de persistance avant fermeture d'un ticket).

Limites assumées : seuls le trafic et les pertes sont rejoués (pas la fraîcheur des bases « db_down »), et les relevés
manquants de l'historique donnent « données manquantes » comme en production. Résultat indicatif, jamais une décision.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text

from . import config, history, rules
from .config import ConfigParams, fmt, utc_naive_to_local_naive
from .db import local_conn
from .logger import get_logger
from .monitoring import alerts_enabled
from .shadow import MATCH_AFTER, MATCH_BEFORE
from .tickets import real_tickets_since

logger = get_logger(__name__)

MAX_DAYS = 30
EXAMPLES_LIMIT = 20
STEP = timedelta(minutes=history.STEP_MINUTES)


def _episodes(slots: list[datetime], alerts: list[bool], reasons: list[str], persistence: timedelta) -> list[dict]:
    """Épisodes d'une série d'alertes booléennes ; une pause plus courte que ``persistence`` ne coupe pas l'épisode."""
    episodes: list[dict] = []
    for slot, alert, reason in zip(slots, alerts, reasons, strict=True):
        if not alert:
            continue
        if episodes and slot - episodes[-1]["end"] <= persistence:
            episodes[-1]["end"] = slot
        else:
            episodes.append({"start": slot, "end": slot, "reason": reason})
    return episodes


def _replay_base(rows: list[dict], slots: list[datetime], base_cfg: dict) -> dict[str, list[dict]]:
    """Épisodes de trafic et de pertes d'UNE base pour UNE configuration."""
    traffic = {r["ts"]: r["traffic_gbps"] for r in rows}
    loss = {r["ts"]: r["loss_pct"] for r in rows}
    drops = base_cfg["traffic_consecutive_drops_count"]
    window = max(drops, base_cfg["pertes_consecutive_count"]) + 1            # production : une fenêtre de n + 1 relevés, du plus ancien au plus récent
    persistence = timedelta(minutes=base_cfg["alert_persistence_duration_minutes"])
    flags = {"traffic": ([], []), "pertes": ([], [])}
    for slot in slots:
        instants = [slot - k * STEP for k in range(window - 1, -1, -1)]
        traffic_values = [traffic.get(t) for t in instants][-(drops + 1):]
        loss_values = [loss.get(t) for t in instants]
        for kind, verdict in (("traffic", rules.detect_traffic(traffic_values, base_cfg)), ("pertes", rules.detect_pertes(loss_values, base_cfg))):
            flags[kind][0].append(verdict.alert)
            flags[kind][1].append(verdict.reason)
    return {kind: _episodes(slots, alerts, reasons, persistence) for kind, (alerts, reasons) in flags.items()}


def _overlap(episode: dict, others: list[dict]) -> bool:
    return any(o["start"] <= episode["end"] and episode["start"] <= o["end"] for o in others)


def _followed_by_ticket(db_key: str, kind: str, episode: dict, tickets: list[dict]) -> bool:
    return any(t["database_name"] == db_key and t["type"] == kind
               and episode["start"] - MATCH_BEFORE <= t["utc"] <= episode["start"] + MATCH_AFTER for t in tickets)


def _describe(db_key: str, kind: str, episode: dict, tickets: list[dict]) -> dict:
    return {"db_key": db_key, "type": kind, "start": fmt(utc_naive_to_local_naive(episode["start"])),
            "end": fmt(utc_naive_to_local_naive(episode["end"])), "reason": episode["reason"],
            "followed_by_ticket": _followed_by_ticket(db_key, kind, episode, tickets)}


def _base_keys() -> list[str]:
    with local_conn() as conn:
        return [r[0] for r in conn.execute(text("SELECT DISTINCT db_key FROM metrics_5min ORDER BY db_key"))]


def simulate(updates: dict | None = None, overrides: dict | None = None, days: int = 7) -> dict:
    """Compare la configuration actuelle à la candidate (``updates`` = champs globaux modifiés, ``overrides`` = nouveaux seuils par base).

    Lève ``pydantic.ValidationError`` / ``ValueError`` si la candidate est invalide (rien n'est calculé).
    """
    days = max(1, min(int(days), MAX_DAYS))
    current_cfg = config.load_config()
    current_overrides = config.load_overrides()
    candidate_cfg = ConfigParams(**{**current_cfg, **(updates or {})}).model_dump()
    candidate_overrides = current_overrides if overrides is None else config.validate_overrides(
        {"criteria": candidate_cfg, "alerts": {}, "overrides": overrides})

    end = config.round5(history.utc_now())                       # relevés alignés sur 5 min, comme les checkpoints de production
    start = end - timedelta(days=days)
    warmup = timedelta(minutes=5 * (max(current_cfg["traffic_consecutive_drops_count"], candidate_cfg["traffic_consecutive_drops_count"],
                                        max((o.get("traffic_consecutive_drops_count", 0) for o in candidate_overrides.values()), default=0)) + 2))
    slots = [start + i * STEP for i in range(int((end - start) / STEP) + 1)]
    tickets = real_tickets_since(start - timedelta(hours=2))

    per_base, new_alerts, removed_alerts, evaluated, disabled = [], [], [], 0, []
    totals = {"current": {"traffic": 0, "pertes": 0}, "candidate": {"traffic": 0, "pertes": 0}}
    for db_key in _base_keys():
        rows = history.load_series(db_key, start - warmup, end)
        if not rows:
            continue
        outcome = {}
        for label, cfg, overrides_map in (("current", current_cfg, current_overrides), ("candidate", candidate_cfg, candidate_overrides)):
            if not alerts_enabled(db_key, cfg):
                outcome[label] = {"traffic": [], "pertes": []}
                continue
            outcome[label] = _replay_base(rows, slots, config.config_for(db_key, cfg, overrides_map))
        if not alerts_enabled(db_key, current_cfg) and not alerts_enabled(db_key, candidate_cfg):
            disabled.append(db_key)
            continue
        evaluated += 1
        counts = {label: sum(len(eps) for eps in kinds.values()) for label, kinds in outcome.items()}
        for label in totals:
            for kind in ("traffic", "pertes"):
                totals[label][kind] += len(outcome[label][kind])
        per_base.append({"db_key": db_key, "current": counts["current"], "candidate": counts["candidate"],
                         "delta": counts["candidate"] - counts["current"]})
        for kind in ("traffic", "pertes"):
            new_alerts += [_describe(db_key, kind, e, tickets) for e in outcome["candidate"][kind] if not _overlap(e, outcome["current"][kind])]
            removed_alerts += [_describe(db_key, kind, e, tickets) for e in outcome["current"][kind] if not _overlap(e, outcome["candidate"][kind])]

    for label in totals:
        totals[label]["total"] = totals[label]["traffic"] + totals[label]["pertes"]
    per_base.sort(key=lambda b: (-abs(b["delta"]), b["db_key"]))
    new_alerts.sort(key=lambda a: a["start"], reverse=True)
    removed_alerts.sort(key=lambda a: a["start"], reverse=True)
    return {"days": days, "from": fmt(utc_naive_to_local_naive(start)), "to": fmt(utc_naive_to_local_naive(end)),
            "bases_evaluated": evaluated, "bases_disabled": disabled, "totals": totals,
            "delta": totals["candidate"]["total"] - totals["current"]["total"], "per_base": per_base,
            "new_alerts": new_alerts[:EXAMPLES_LIMIT], "new_alerts_total": len(new_alerts),
            "new_alerts_without_ticket": sum(1 for a in new_alerts if not a["followed_by_ticket"]),
            "removed_alerts": removed_alerts[:EXAMPLES_LIMIT], "removed_alerts_total": len(removed_alerts),
            "removed_alerts_with_ticket": sum(1 for a in removed_alerts if a["followed_by_ticket"])}
