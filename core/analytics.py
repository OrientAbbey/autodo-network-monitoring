"""STATISTIQUES D'USAGE ET JOURNAL D'ACTIONS (base locale) — écritures groupées, lectures en une requête.

* ``track_user``  : AUCUNE E/S — la visite est ajoutée à un tampon mémoire (``VisitBuffer``) vidé toutes les
                    10 s par UN upsert multi-lignes. N visiteurs qui rafraîchissent ⇒ 1 écriture / 10 s, pas N.
* entrées validées : identifiant client = UUID, adresse IP validée (voir ``auth.get_real_ip``), user-agent borné.
* ``purge``       : rétention (90 j par défaut) par lots, pour ne pas verrouiller les tables.
"""
from __future__ import annotations

import threading
import uuid
from datetime import timedelta

from sqlalchemy import text

from .auth import get_real_ip
from .config import fmt, now_local, parse, period_to_cutoff, settings
from .db import fetch_all, local_conn, local_tx
from .logger import get_logger

logger = get_logger(__name__)
SESSION_GAP_MIN = 60
MAX_BUFFER = 10_000


class VisitBuffer:
    def __init__(self):
        self._d: dict[tuple, dict] = {}
        self._lock = threading.Lock()

    def add(self, day: str, client_id: str, now: str, ip: str, ua: str) -> None:
        with self._lock:
            k = (day, client_id)
            v = self._d.get(k)
            if v is None:
                if len(self._d) >= MAX_BUFFER:       # identifiants forgés en masse : on plafonne la mémoire
                    return
                self._d[k] = {"first": now, "last": now, "extra": 0, "ip": ip, "ua": ua}
                return
            if (parse(now) - parse(v["last"])) >= timedelta(minutes=SESSION_GAP_MIN):
                v["extra"] += 1
            v.update(last=now, ip=ip, ua=ua)

    def drain(self) -> dict:
        with self._lock:
            d, self._d = self._d, {}
        return d


visits = VisitBuffer()


def track_user(request, response_headers) -> str:
    raw = request.headers.get("X-Client-Id", "")
    try:
        client_id = str(uuid.UUID(raw))
    except ValueError:
        client_id = str(uuid.uuid4())
        response_headers["X-Client-Id"] = client_id
    now = now_local().replace(tzinfo=None)
    visits.add(now.strftime("%Y-%m-%d"), client_id, fmt(now), get_real_ip(request), (request.headers.get("user-agent") or "")[:255])
    return client_id


def flush_visits() -> int:
    """Vide le tampon vers daily_users en UNE transaction (upsert multi-lignes)."""
    batch = visits.drain()
    if not batch:
        return 0
    rows = [{"d": d, "c": c, "first": v["first"], "last": v["last"], "extra": v["extra"], "ip": v["ip"], "ua": v["ua"]}
            for (d, c), v in batch.items()]
    try:
        with local_tx() as conn:
            conn.execute(text(
                "INSERT INTO daily_users (`date`, client_id, last_seen, visits_count, last_ip, user_agent) "
                "VALUES (:d, :c, :last, 1 + :extra, :ip, :ua) ON DUPLICATE KEY UPDATE "
                "visits_count = visits_count + IF(TIMESTAMPDIFF(MINUTE, last_seen, :first) >= :gap, 1, 0) + :extra, "
                "last_seen = :last, last_ip = :ip, user_agent = :ua"), [{**r, "gap": SESSION_GAP_MIN} for r in rows])
        return len(rows)
    except Exception:
        logger.exception("Écriture des visites impossible (%d entrées perdues)", len(rows))
        return 0


def record_viewer_peak(viewers_now: int) -> None:
    """Conserve le plus haut nombre de spectateurs simultanés de la journée (appelé chaque minute)."""
    if viewers_now <= 0:
        return
    now = now_local().replace(tzinfo=None)
    with local_tx() as c:
        c.execute(text("INSERT INTO usage_peaks (`day`, peak_viewers, peak_at) VALUES (:d, :n, :t) ON DUPLICATE KEY UPDATE "
                       "peak_at = IF(:n > peak_viewers, :t, peak_at), peak_viewers = GREATEST(peak_viewers, :n)"),
                  {"d": now.strftime("%Y-%m-%d"), "n": int(viewers_now), "t": fmt(now)})


def viewer_peaks(period: str = "7days") -> list[dict]:
    cutoff = period_to_cutoff(period).strftime("%Y-%m-%d")
    with local_conn() as c:
        rows = fetch_all(c, "SELECT `day`, peak_viewers, peak_at FROM usage_peaks WHERE `day` >= :c ORDER BY `day` DESC", c=cutoff)
    return [{"day": str(r["day"]), "peak_viewers": int(r["peak_viewers"]), "peak_at": fmt(r["peak_at"])} for r in rows]


def log_action(ip: str, action: str) -> bool:
    try:
        with local_tx() as c:
            c.execute(text("INSERT INTO logs (ip_address, access_time, action) VALUES (:ip, :t, :a)"),
                      {"ip": ip[:45], "t": fmt(now_local().replace(tzinfo=None)), "a": action[:100]})
        return True
    except Exception:
        logger.exception("Journalisation de l'action impossible")
        return False


def access_logs(period: str = "7days") -> dict:
    """Journaux d'accès + statistiques, en UNE requête (le reste est dérivé en Python)."""
    cutoff = period_to_cutoff(period).strftime("%Y-%m-%d")
    with local_conn() as c:
        rows = fetch_all(c, """SELECT `date`, last_ip AS ip_address, SUM(visits_count) AS access_count,
                MAX(last_seen) AS last_access, COUNT(DISTINCT client_id) AS unique_users
                FROM daily_users WHERE `date` >= :c GROUP BY `date`, last_ip ORDER BY `date` DESC, last_ip ASC""", c=cutoff)
    logs = [{"date": str(r["date"]), "ip_address": r["ip_address"], "access_count": int(r["access_count"] or 0),
             "last_access": r["last_access"], "unique_users": int(r["unique_users"] or 0)} for r in rows]
    per_day: dict[str, set] = {}
    for r in rows:
        if r["ip_address"]:
            per_day.setdefault(str(r["date"]), set()).add(r["ip_address"])
    daily = {d: len(s) for d, s in per_day.items()}
    today = now_local().strftime("%Y-%m-%d")
    return {"access_logs": logs, "access_stats": {
        "daily_data": daily, "total_unique_ips": len(set().union(*per_day.values())) if per_day else 0,
        "avg_daily_unique": round(sum(daily.values()) / len(daily)) if daily else 0,
        "today_unique_count": daily.get(today, 0)}}


def config_changes(period: str = "7days") -> list:
    cutoff = fmt(period_to_cutoff(period))
    with local_conn() as c:
        rows = fetch_all(c, "SELECT access_time AS timestamp, action AS parameter, ip_address AS modified_by "
                            "FROM logs WHERE access_time >= :c ORDER BY access_time DESC LIMIT 1000", c=cutoff)
    return rows


def purge(retention_days: int | None = None, batch: int = 5000) -> dict:
    """Supprime les données plus vieilles que la rétention, par lots."""
    days = retention_days or settings.retention_days
    limit_day = (now_local().replace(tzinfo=None) - timedelta(days=days)).strftime("%Y-%m-%d")
    limit_ts = limit_day + " 00:00:00"
    out = {}
    for table, where, p in (("daily_users", "`date` < :v", limit_day), ("logs", "access_time < :v", limit_ts)):
        total = 0
        while True:
            with local_tx() as c:
                n = c.execute(text(f"DELETE FROM {table} WHERE {where} LIMIT {batch}"), {"v": p}).rowcount
            total += n
            if n < batch:
                break
        out[table] = total
    if any(out.values()):
        logger.info("Purge de rétention (%s j) : %s", days, out)
    return out
