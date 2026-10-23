"""MODE OMBRE — exécute les détecteurs « futurs » à chaque cycle SANS JAMAIS créer ni modifier un ticket.

Les verdicts positifs sont enregistrés (``detector_verdicts``, uniques par détecteur + base + relevé) puis comparés aux vrais
tickets dans ``shadow_report`` : combien d'épisodes d'alerte auraient eu un ticket, combien de minutes d'avance, combien de
tickets auraient été manqués. C'est la preuve chiffrée nécessaire avant d'envisager de faire confiance à un détecteur.

Règle d'or : ``run_shadow`` ne lève JAMAIS d'exception (toute erreur est journalisée puis ignorée) — un détecteur expérimental
ne doit pas pouvoir perturber la supervision.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text

from . import anomaly, anomaly_ml, history, ticket_log
from .config import fmt, load_config, now_local, parse, settings
from .db import fetch_all, local_conn, local_tx
from .tickets import real_tickets_since
from .logger import get_logger

logger = get_logger(__name__)

EPISODE_GAP = timedelta(minutes=15)       # deux verdicts séparés de moins de 15 min appartiennent au même épisode
MATCH_BEFORE = timedelta(minutes=30)      # un ticket « correspond » à un épisode s'il est créé de −30 min …
MATCH_AFTER = timedelta(minutes=60)       # … à +60 min autour du début de l'épisode
LEVEL_SHIFT_LOOKBACK_DAYS = 60
LEVEL_SHIFT_RECENT_DAYS = 3               # on n'enregistre que les ruptures détectées dans les 3 derniers jours


@dataclass(frozen=True)
class DetectorInfo:
    key: str
    code: str                 # référence de la feuille de route (D1…)
    title: str
    description: str
    needs_training: bool
    shadow: bool              # exécuté à chaque cycle ? (D3 est une analyse à la demande)


DETECTORS: dict[str, DetectorInfo] = {d.key: d for d in (
    DetectorInfo("seasonal", "D1", "Référence saisonnière",
                 "Trafic anormalement bas pour ce créneau horaire (médiane/MAD des 7 jours précédents).", False, True),
    DetectorInfo("isolation", "D4", "Isolation Forest (multivarié)",
                 "Combinaison inhabituelle de métriques avec trafic sous 90 % de l'attendu (un modèle par base).", True, True),
    DetectorInfo("level_shift", "D5", "Ruptures de niveau (CUSUM)",
                 "Changement durable de la moyenne quotidienne du trafic.", False, True),
    DetectorInfo("quality", "D7", "Qualité d'alerte",
                 "Probabilité qu'un ticket soit un vrai incident, apprise sur les retours des superviseurs.", True, True),
    DetectorInfo("forecast", "D3", "Prévision et capacité",
                 "Courbe attendue sur 24 h et jours avant saturation d'un lien (analyse à la demande, sans mode ombre).", False, False),
)}
TICKET_TYPES_COMPARED = {"seasonal": ("traffic",), "isolation": ("traffic", "pertes")}     # tickets auxquels on compare chaque détecteur


# ============================================================================
# État des détecteurs (table detector_settings)
# ============================================================================
def get_states() -> dict[str, dict]:
    """``{détecteur: {enabled, trained_at, metrics}}`` — activé par défaut tant que rien n'a été réglé."""
    with local_conn() as conn:
        rows = {r["detector"]: r for r in fetch_all(conn, "SELECT * FROM detector_settings")}
    states = {}
    for key in DETECTORS:
        row = rows.get(key)
        states[key] = {"enabled": bool(row["enabled"]) if row else True,
                       "trained_at": fmt(row["trained_at"]) if row and row["trained_at"] else None,
                       "metrics": json.loads(row["metrics_json"]) if row and row["metrics_json"] else None}
    return states


def set_enabled(key: str, enabled: bool) -> None:
    if key not in DETECTORS:
        raise KeyError(key)
    sql = ("INSERT INTO detector_settings (detector, enabled) VALUES (:k, :e) "
           "ON CONFLICT(detector) DO UPDATE SET enabled = excluded.enabled" if settings.db_backend == "sqlite" else
           "INSERT INTO detector_settings (detector, enabled) VALUES (:k, :e) ON DUPLICATE KEY UPDATE enabled = :e")
    with local_tx() as conn:
        conn.execute(text(sql), {"k": key, "e": int(enabled)})


def record_training(key: str, metrics: dict) -> None:
    sql = ("INSERT INTO detector_settings (detector, enabled, trained_at, metrics_json) VALUES (:k, 1, :t, :m) "
           "ON CONFLICT(detector) DO UPDATE SET trained_at = excluded.trained_at, metrics_json = excluded.metrics_json"
           if settings.db_backend == "sqlite" else
           "INSERT INTO detector_settings (detector, enabled, trained_at, metrics_json) VALUES (:k, 1, :t, :m) "
           "ON DUPLICATE KEY UPDATE trained_at = :t, metrics_json = :m")
    with local_tx() as conn:
        conn.execute(text(sql),
                     {"k": key, "t": fmt(now_local().replace(tzinfo=None)), "m": json.dumps(metrics, ensure_ascii=False)})


def record_verdict(detector: str, db_key: str, timestamp: datetime, explanation: str, score: float | None) -> bool:
    """Enregistre un verdict positif (une seule fois par détecteur + base + relevé). Retourne True si nouveau."""
    prefix = "INSERT OR IGNORE" if settings.db_backend == "sqlite" else "INSERT IGNORE"
    with local_tx() as conn:
        result = conn.execute(text(f"{prefix} INTO detector_verdicts (detector, db_key, ts, score, explanation, created_at) "
                                   "VALUES (:d, :k, :t, :s, :e, :c)"),
                              {"d": detector, "k": db_key, "t": fmt(timestamp), "s": score, "e": explanation[:1000],
                               "c": fmt(now_local().replace(tzinfo=None))})
        return result.rowcount == 1


def verdict_counts(hours: int = 24) -> dict[str, int]:
    """Nombre de verdicts positifs par détecteur sur les dernières heures."""
    with local_conn() as conn:
        rows = fetch_all(conn, "SELECT detector, COUNT(*) AS n FROM detector_verdicts WHERE ts >= :s GROUP BY detector",
                         s=fmt(history.utc_now() - timedelta(hours=hours)))
    return {r["detector"]: int(r["n"]) for r in rows}


# ============================================================================
# Exécution à chaque cycle
# ============================================================================
_last_level_shift_day: str | None = None


def _latest_slot(snapshot) -> datetime | None:
    """Dernier relevé de la base qui contient du trafic mesuré."""
    stamps = [ts for ts, row in snapshot.whole.items() if history.metrics_from_row(row)]
    return parse(max(stamps)) if stamps else None


def _seasonal_inputs(db_key: str, at: datetime) -> dict[datetime, float]:
    """Trafic aux relevés ``at`` et ``at - 5 min`` et à leurs créneaux des 7 jours précédents."""
    slots = [at - offset * anomaly.STEP - timedelta(days=day) for offset in range(anomaly.CONSECUTIVE_POINTS)
             for day in range(anomaly.SEASONAL_DAYS + 1)]
    return history.traffic_by_slot(list(history.values_at(db_key, slots).values()))


def _run_seasonal(db_key: str, at: datetime) -> bool:
    finding = anomaly.seasonal_finding(_seasonal_inputs(db_key, at), at)
    return finding.alert and record_verdict("seasonal", db_key, at, finding.explanation, finding.score)


def _run_isolation(db_key: str, at: datetime) -> bool:
    finding = anomaly_ml.isolation_finding(db_key, at)
    return bool(finding and finding.alert and record_verdict("isolation", db_key, at, finding.explanation, finding.score))


def _run_level_shifts(db_keys: list[str]) -> int:
    """Une fois par jour : recherche les ruptures de niveau récentes (moyennes quotidiennes des 60 derniers jours)."""
    found = 0
    recent_limit = (history.utc_now() - timedelta(days=LEVEL_SHIFT_RECENT_DAYS)).strftime("%Y-%m-%d")
    for db_key in db_keys:
        daily = history.daily_stats(db_key, LEVEL_SHIFT_LOOKBACK_DAYS)
        for shift in anomaly.level_shifts([(d["day"], d["mean"]) for d in daily]):
            if shift["day"] >= recent_limit:
                day = parse(shift["day"])
                found += record_verdict("level_shift", db_key, day, anomaly.shift_explanation(shift), shift["change_percent"])
    return found


def _score_new_tickets(created_tickets: list[dict]) -> int:
    scored = 0
    for ticket in created_tickets:
        score = anomaly_ml.quality_score(ticket)
        if score is not None:
            ticket_log.log_event(ticket["id"], "quality_score",
                                 f"Score de qualité (mode ombre) : {score:.2f} — probabilité d'un vrai incident ; aucune décision n'en dépend")
            scored += 1
    return scored


def run_shadow(snapshots: dict, created_tickets: list[dict] | tuple = ()) -> dict:
    """Exécute les détecteurs activés sur le cycle qui vient de se terminer. Retourne des compteurs ; ne lève jamais."""
    global _last_level_shift_day
    stats = {"seasonal": 0, "isolation": 0, "level_shift": 0, "quality": 0}
    try:
        if not load_config().get("shadow_detectors", True):
            return stats
        states = get_states()
        for db_key, snapshot in snapshots.items():
            if not snapshot.ok:
                continue
            at = _latest_slot(snapshot)
            if at is None:
                continue
            for key, runner in (("seasonal", _run_seasonal), ("isolation", _run_isolation)):
                if states[key]["enabled"]:
                    try:
                        stats[key] += int(runner(db_key, at))
                    except Exception:
                        logger.exception("Mode ombre : détecteur %s en échec sur %s (ignoré)", key, db_key)
        today = now_local().strftime("%Y-%m-%d")
        if states["level_shift"]["enabled"] and _last_level_shift_day != today:
            try:
                stats["level_shift"] = _run_level_shifts([k for k, s in snapshots.items() if s.ok])
                _last_level_shift_day = today
            except Exception:
                logger.exception("Mode ombre : détecteur level_shift en échec (ignoré)")
        if states["quality"]["enabled"] and created_tickets:
            try:
                stats["quality"] = _score_new_tickets(list(created_tickets))
            except Exception:
                logger.exception("Mode ombre : score de qualité en échec (ignoré)")
    except Exception:
        logger.exception("Mode ombre : erreur inattendue (ignorée)")
    return stats


# ============================================================================
# Rapport : verdicts de l'ombre comparés aux vrais tickets
# ============================================================================
def _episodes(verdicts: list[dict]) -> list[dict]:
    """Regroupe les verdicts consécutifs (écart ≤ 15 min) d'un même détecteur et d'une même base en épisodes."""
    episodes: list[dict] = []
    for verdict in verdicts:                                  # déjà triés par (détecteur, base, instant)
        last = episodes[-1] if episodes else None
        if (last and last["detector"] == verdict["detector"] and last["db_key"] == verdict["db_key"]
                and verdict["ts"] - last["end"] <= EPISODE_GAP):
            last["end"], last["count"] = verdict["ts"], last["count"] + 1
        else:
            episodes.append({"detector": verdict["detector"], "db_key": verdict["db_key"], "start": verdict["ts"],
                             "end": verdict["ts"], "count": 1, "explanation": verdict["explanation"]})
    return episodes


def _match(episode: dict, tickets: list[dict]) -> dict | None:
    """Ticket réel de la même base le plus proche dans la fenêtre −30 / +60 min autour du début de l'épisode."""
    candidates = [t for t in tickets if t["database_name"] == episode["db_key"] and t["type"] in TICKET_TYPES_COMPARED.get(episode["detector"], ())
                  and episode["start"] - MATCH_BEFORE <= t["utc"] <= episode["start"] + MATCH_AFTER]
    return min(candidates, key=lambda t: abs((t["utc"] - episode["start"]).total_seconds()), default=None)


def shadow_report(days: int = 7) -> dict:
    """Comparaison détecteurs ↔ tickets sur ``days`` jours.

    Par détecteur : épisodes, épisodes ayant un ticket réel (« confirmés »), épisodes sans ticket (fausse alerte potentielle OU
    incident non ticketé), tickets réels couverts (rappel) et avance médiane (minutes ; positif = le détecteur a vu l'incident AVANT le ticket).
    """
    since = history.utc_now() - timedelta(days=days)
    with local_conn() as conn:
        verdicts = fetch_all(conn, "SELECT detector, db_key, ts, score, explanation FROM detector_verdicts WHERE ts >= :s "
                                   "ORDER BY detector, db_key, ts", s=fmt(since))
    tickets = real_tickets_since(since)
    episodes = _episodes(verdicts)
    report: dict[str, dict] = {}
    listing = []
    for key, info in DETECTORS.items():
        if not info.shadow or key == "quality":
            continue
        mine = [e for e in episodes if e["detector"] == key]
        comparable = key in TICKET_TYPES_COMPARED
        leads, covered = [], set()
        for episode in mine:
            ticket = _match(episode, tickets) if comparable else None
            episode["ticket_id"] = ticket["id"] if ticket else None
            if ticket:
                covered.add(ticket["id"])
                episode["lead_minutes"] = round((ticket["utc"] - episode["start"]).total_seconds() / 60)
                leads.append(episode["lead_minutes"])
        relevant = [t for t in tickets if t["type"] in TICKET_TYPES_COMPARED.get(key, ())]
        matched = sum(1 for e in mine if e["ticket_id"])
        report[key] = {
            "title": info.title, "code": info.code, "episodes": len(mine), "comparable": comparable,
            "confirmed": matched if comparable else None, "without_ticket": len(mine) - matched if comparable else None,
            "tickets_total": len(relevant) if comparable else None,
            "tickets_covered": len(covered) if comparable else None,
            "recall": round(len(covered) / len(relevant), 2) if comparable and relevant else None,
            "median_lead_minutes": statistics.median(leads) if leads else None}
        listing.extend(mine)
    listing.sort(key=lambda e: e["start"], reverse=True)
    for episode in listing:
        episode["start"], episode["end"] = fmt(episode["start"]), fmt(episode["end"])
    return {"days": days, "detectors": report, "episodes": listing[:200], "tickets_in_period": len(tickets)}
