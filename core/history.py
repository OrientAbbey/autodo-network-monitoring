"""HISTORIQUE LOCAL DES MÉTRIQUES (D2) — table ``metrics_5min``, une ligne par base et par relevé de 5 minutes (UTC).

Alimentée de deux façons :
* à CHAQUE cycle (``record_snapshots``) : les relevés déjà lus par la collecte sont copiés, aucune requête DIAGNET de plus ;
* à la demande (``backfill``) : import des N derniers jours depuis DIAGNET (``collector.read_range``, lecture seule).

Sert de base aux détecteurs « futurs » (référence saisonnière, prévision, ruptures, ML), à la simulation « et si ? »
et aux mini-courbes du tableau de bord. Rétention : ``HISTORY_RETENTION_DAYS`` (400 j par défaut).

⚠ Écriture par ``REPLACE INTO`` (idempotent) : avec SQLAlchemy 2.x, ``ON DUPLICATE KEY UPDATE`` en insertion groupée
n'accepte pas qu'un même paramètre serve deux fois.
"""
from __future__ import annotations

import statistics
import threading
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import bindparam, text

from . import collector, constants
from .config import fmt, settings
from .db import fetch_all, local_conn, local_tx
from .logger import get_logger

logger = get_logger(__name__)

STEP_MINUTES = 5
POINTS_PER_DAY = 288
TRAFFIC_COLUMNS = ("tcp_dw", "udp_dw", "icmp_dw", "other_dw")
METRIC_COLUMNS = ("traffic_gbps", "loss_pct", "latency_ms", "tcp_no_response_pct", "tcp_block_wp_up_pct", "dns_malformed_pct")
BACKFILL_CHUNK_DAYS = 5          # taille d'une lecture DIAGNET : 5 jours = 1 440 lignes par requête


def utc_now() -> datetime:
    """Maintenant, en UTC naïf (convention des bases DIAGNET et de ``metrics_5min``)."""
    return datetime.now(UTC).replace(tzinfo=None)


# ============================================================================
# Extraction : ligne brute DIAGNET → métriques
# ============================================================================
def _number(value) -> float:
    return float(value) if value is not None else 0.0


def metrics_from_row(row: dict | None) -> dict | None:
    """Métriques d'un relevé (mêmes formules que le tableau de bord). None si le relevé ne contient aucun trafic mesuré."""
    if not row or all(row.get(column) is None for column in TRAFFIC_COLUMNS):
        return None
    total = sum(_number(row.get(column)) for column in TRAFFIC_COLUMNS)
    packets = _number(row.get("#tcp_pkt_dw"))
    sessions = _number(row.get("#tcp_sessions"))
    synack = _number(row.get("#tcp_synack_core"))
    queries = _number(row.get("dns_queries"))
    return {
        "traffic_gbps": round(total * constants.DEBIT_FACTOR, 4),
        "loss_pct": round(100 * _number(row.get("#tcp_loss_dw")) / (packets if packets > 0 else 1), 4),
        "latency_ms": round(_number(row.get("Irt_tcp_core_td22")) / synack, 4) if synack > 0 else 0.0,
        "tcp_no_response_pct": round(100 * _number(row.get("#tcp_no_response_to_syn")) / max(1.0, sessions), 4),
        "tcp_block_wp_up_pct": round(100 * _number(row.get("#tcp_block_wp_up")) / max(1.0, sessions), 4),
        "dns_malformed_pct": round(100 * _number(row.get("dns_malformed_ul")) / queries, 4) if queries > 0 else 0.0,
    }


# ============================================================================
# Écriture
# ============================================================================
_REPLACE_SQL = text("REPLACE INTO metrics_5min (db_key, ts, traffic_gbps, loss_pct, latency_ms, tcp_no_response_pct, "
                    "tcp_block_wp_up_pct, dns_malformed_pct) VALUES (:db_key, :ts, :traffic_gbps, :loss_pct, :latency_ms, "
                    ":tcp_no_response_pct, :tcp_block_wp_up_pct, :dns_malformed_pct)")


def store_rows(db_key: str, raw_rows: dict[str, dict]) -> int:
    """Enregistre ``{ts UTC: ligne brute}`` pour une base. Retourne le nombre de relevés écrits."""
    batch = []
    for timestamp, row in raw_rows.items():
        metrics = metrics_from_row(row)
        if metrics:
            batch.append({"db_key": db_key, "ts": timestamp, **metrics})
    if batch:
        with local_tx() as conn:
            conn.execute(_REPLACE_SQL, batch)
    return len(batch)


def record_snapshots(snapshots: dict) -> int:
    """Copie dans l'historique les relevés lus par le cycle (bases joignables uniquement). Ne lève jamais."""
    written = 0
    for db_key, snapshot in snapshots.items():
        if not snapshot.ok:
            continue
        try:
            written += store_rows(db_key, snapshot.whole)
        except Exception:
            logger.exception("Historique : écriture impossible pour %s", db_key)
    return written


# ============================================================================
# Import (backfill) depuis DIAGNET
# ============================================================================
class BackfillState:
    """État de l'import en cours (lu par la page Admin). Un seul import à la fois."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.days = 0
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.progress: dict[str, dict] = {}

    def public(self) -> dict:
        return {"running": self.running, "days": self.days, "started_at": self.started_at,
                "finished_at": self.finished_at, "progress": dict(self.progress)}


backfill_state = BackfillState()


def backfill(days: int, db_keys: list[str] | None = None) -> dict[str, dict]:
    """Importe les ``days`` derniers jours de chaque base (lecture seule). Une base en échec n'arrête pas les autres."""
    end = utc_now()
    start = end - timedelta(days=days)
    result: dict[str, dict] = {}
    for db_key in db_keys or list(constants.DB_CONFIGS):
        written, error = 0, None
        try:
            cursor = start
            while cursor < end:
                chunk_end = min(cursor + timedelta(days=BACKFILL_CHUNK_DAYS), end)
                written += store_rows(db_key, collector.read_range(db_key, fmt(cursor), fmt(chunk_end)))
                cursor = chunk_end
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:160]}"
            logger.warning("Import d'historique %s en échec : %s", db_key, error)
        result[db_key] = {"rows": written, "error": error}
        backfill_state.progress[db_key] = result[db_key]
    reset_sparkline_cache()          # les mini-courbes reflètent tout de suite l'historique importé (sinon : jusqu'à 60 s de retard)
    return result


def start_backfill(days: int) -> bool:
    """Lance l'import en arrière-plan. Retourne False si un import est déjà en cours."""
    state = backfill_state
    with state.lock:
        if state.running:
            return False
        state.running, state.days, state.progress = True, days, {}
        state.started_at, state.finished_at = fmt(utc_now()), None

    def work():
        try:
            backfill(days)
        finally:
            state.finished_at = fmt(utc_now())
            state.running = False

    threading.Thread(target=work, name="history-backfill", daemon=True).start()
    return True


# ============================================================================
# Lecture
# ============================================================================
def load_series(db_key: str, start: datetime, end: datetime) -> list[dict]:
    """Relevés d'une base entre ``start`` et ``end`` (UTC naïfs), du plus ancien au plus récent."""
    with local_conn() as conn:
        rows = fetch_all(conn, "SELECT * FROM metrics_5min WHERE db_key = :k AND ts BETWEEN :a AND :b ORDER BY ts",
                         k=db_key, a=fmt(start), b=fmt(end))
    return rows


def values_at(db_key: str, timestamps: list[datetime]) -> dict[datetime, dict]:
    """Relevés exacts aux instants demandés (ceux qui manquent sont absents du résultat)."""
    if not timestamps:
        return {}
    statement = text("SELECT * FROM metrics_5min WHERE db_key = :k AND ts IN :ts").bindparams(bindparam("ts", expanding=True))
    with local_conn() as conn:
        rows = conn.execute(statement, {"k": db_key, "ts": [fmt(t) for t in timestamps]}).mappings().all()
    return {row["ts"]: dict(row) for row in rows}


def traffic_by_slot(rows: list[dict]) -> dict[datetime, float]:
    """``{ts: trafic Gb/s}`` sans les valeurs absentes."""
    return {row["ts"]: row["traffic_gbps"] for row in rows if row["traffic_gbps"] is not None}


def traffic_grid(db_key: str, days: int) -> tuple[datetime, list[float | None]] | None:
    """Trafic des ``days`` derniers jours sur une grille régulière de 5 min (None = relevé absent), ou None si la base n'a pas d'historique.

    Retourne ``(instant du dernier relevé, valeurs du plus ancien au plus récent)`` — entrée directe de ``anomaly.holt_winters_forecast``.
    """
    with local_conn() as conn:
        latest = conn.execute(text("SELECT MAX(ts) FROM metrics_5min WHERE db_key = :k"), {"k": db_key}).scalar()
    if latest is None:
        return None
    count = days * POINTS_PER_DAY
    start = latest - timedelta(minutes=STEP_MINUTES * (count - 1))
    by_slot = traffic_by_slot(load_series(db_key, start, latest))
    return latest, [by_slot.get(start + timedelta(minutes=STEP_MINUTES * i)) for i in range(count)]


MIN_POINTS_PER_FULL_DAY = 200        # un jour est « complet » s'il compte au moins 200 relevés sur 288


def daily_stats(db_key: str, days: int) -> list[dict]:
    """Moyenne et pic de trafic par jour UTC (jours COMPLETS uniquement, aujourd'hui exclu), du plus ancien au plus récent."""
    since = utc_now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days)
    today = utc_now().strftime("%Y-%m-%d")
    with local_conn() as conn:
        rows = fetch_all(conn, "SELECT DATE(ts) AS day, AVG(traffic_gbps) AS mean_gbps, MAX(traffic_gbps) AS peak_gbps, COUNT(traffic_gbps) AS n "
                               "FROM metrics_5min WHERE db_key = :k AND ts >= :s GROUP BY DATE(ts) ORDER BY day", k=db_key, s=fmt(since))
    return [{"day": r["day"], "mean": float(r["mean_gbps"]), "peak": float(r["peak_gbps"])}
            for r in rows if int(r["n"]) >= MIN_POINTS_PER_FULL_DAY and str(r["day"]) != today]


def stats() -> dict:
    """Volume de l'historique par base (page Admin) : lignes, premier et dernier relevé."""
    with local_conn() as conn:
        rows = fetch_all(conn, "SELECT db_key, COUNT(*) AS n, MIN(ts) AS first_ts, MAX(ts) AS last_ts FROM metrics_5min GROUP BY db_key")
    bases = {r["db_key"]: {"rows": int(r["n"]), "first": fmt(r["first_ts"]), "last": fmt(r["last_ts"])} for r in rows}
    return {"bases": bases, "total_rows": sum(b["rows"] for b in bases.values()),
            "retention_days": settings.history_retention_days, "backfill": backfill_state.public()}


def purge(retention_days: int | None = None, batch: int = 20_000) -> int:
    """Supprime les relevés plus vieux que la rétention, par lots (ne verrouille pas la table longtemps)."""
    limit = fmt(utc_now() - timedelta(days=retention_days or settings.history_retention_days))
    total = 0
    while True:
        with local_tx() as conn:
            deleted = conn.execute(text(f"DELETE FROM metrics_5min WHERE ts < :limit LIMIT {int(batch)}"), {"limit": limit}).rowcount
        total += deleted
        if deleted < batch:
            break
    if total:
        logger.info("Historique : %d relevés purgés (rétention %s j)", total, retention_days or settings.history_retention_days)
    return total


# ============================================================================
# Mini-courbes 24 h (U3) : valeurs réelles + bande « attendu » (même créneau, 7 jours précédents)
# ============================================================================
SPARK_BUCKET_MINUTES = 15
SPARK_POINTS = 96                    # 24 h / 15 min
SPARK_CACHE_SECONDS = 60
_spark_cache: dict = {"at": 0.0, "data": None}
_spark_lock = threading.Lock()


def _bucket_means(rows: list[tuple]) -> dict[int, float]:
    """Moyenne du trafic par tranche de 15 minutes (clé = tranche depuis l'époque Unix)."""
    sums: dict[int, list[float]] = {}
    for timestamp, value in rows:
        bucket = int(timestamp.replace(tzinfo=UTC).timestamp() // (SPARK_BUCKET_MINUTES * 60))
        sums.setdefault(bucket, []).append(value)
    return {bucket: sum(values) / len(values) for bucket, values in sums.items()}


def _build_sparklines(now: datetime) -> dict:
    since = now - timedelta(days=8)
    with local_conn() as conn:
        rows = conn.execute(text("SELECT db_key, ts, traffic_gbps FROM metrics_5min WHERE ts >= :s AND traffic_gbps IS NOT NULL "
                                 "ORDER BY db_key, ts"), {"s": fmt(since)}).all()
    per_base: dict[str, list[tuple]] = {}
    for db_key, timestamp, value in rows:
        per_base.setdefault(db_key, []).append((timestamp, value))
    last_bucket = int(now.replace(tzinfo=UTC).timestamp() // (SPARK_BUCKET_MINUTES * 60))
    buckets = range(last_bucket - SPARK_POINTS + 1, last_bucket + 1)
    bases = {}
    for db_key, points in per_base.items():
        means = _bucket_means(points)
        values = [means.get(b) for b in buckets]
        expected = []
        for b in buckets:
            same_slot = [means[b - day * SPARK_POINTS] for day in range(1, 8) if (b - day * SPARK_POINTS) in means]
            expected.append(round(statistics.median(same_slot), 3) if len(same_slot) >= 3 else None)
        bases[db_key] = {"values": [None if v is None else round(v, 3) for v in values], "expected": expected}
    return {"step_minutes": SPARK_BUCKET_MINUTES, "generated_at": fmt(now), "bases": bases}


def sparklines() -> dict:
    """Mini-courbes de toutes les bases, calculées au plus une fois par minute (N spectateurs = 1 calcul)."""
    with _spark_lock:
        if _spark_cache["data"] is not None and time.monotonic() - _spark_cache["at"] < SPARK_CACHE_SECONDS:
            return _spark_cache["data"]
        data = _build_sparklines(utc_now())
        _spark_cache.update(at=time.monotonic(), data=data)
        return data


def reset_sparkline_cache() -> None:
    with _spark_lock:
        _spark_cache.update(at=0.0, data=None)

