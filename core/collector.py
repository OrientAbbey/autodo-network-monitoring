"""COLLECTE DIAGNET — lecture seule, en parallèle (ThreadPoolExecutor).

Choix de concurrence : les ≈ 20 bases sont distantes et les requêtes bloquantes (PyMySQL) → un pool de threads
borné (``COLLECT_MAX_WORKERS``, 8 par défaut) est le bon outil ; asyncio n'apporterait rien sans pilote asynchrone
fiable et compliquerait le code. Une base lente ou en panne ne retarde plus les autres (délai global par base).

Charge sur les bases DIAGNET (sur lesquelles nous n'avons qu'un accès en lecture) :
* UNE connexion par base et par cycle (au lieu de 3 : `whole`, `apn`, `mode`) ;
* ``SHOW TABLES`` supprimé ; ``MAX(SAUV_td32)`` lit l'index de clé primaire ;
* le mode statistique sur 7 jours (scan de ≈ 2 000 lignes) est **mis en cache** (``MODE7D_CACHE_TTL_S``, 30 min) ;
* la requête APN (TDD) n'est exécutée que pour les bases « utilisateur » ;
* session READ ONLY + délai d'exécution serveur imposés par ``db.diag_engine``.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import bindparam, text

from . import constants
from .config import parse, settings
from .db import diag_engine
from .logger import get_logger

logger = get_logger(__name__)

NUM_COLS = ("tcp_dw", "udp_dw", "icmp_dw", "other_dw", "#tcp_loss_dw", "#tcp_pkt_dw", "#tcp_no_response_to_syn",
            "#tcp_block_wp_up", "#tcp_synack_core", "Irt_tcp_core_td22", "#tcp_sessions")
DNS_COLS = ("dns_malformed_ul", "dns_queries_woresp", "dns_queries")
EMPTY_ROW = {c: None for c in NUM_COLS + DNS_COLS}

_SUM = ", ".join(f"SUM(`{c}`) AS `{c}`" for c in NUM_COLS)
SQL_WHOLE = text("SELECT SAUV_td32, " + ", ".join(f"`{c}`" for c in NUM_COLS) +
                 " FROM whole_5min WHERE SAUV_td32 IN :ts").bindparams(bindparam("ts", expanding=True))
SQL_APN = text(f"SELECT SAUV_td32, {_SUM} FROM apn_5min WHERE SAUV_td32 IN :ts AND apn LIKE :apn GROUP BY SAUV_td32"
               ).bindparams(bindparam("ts", expanding=True))
SQL_DNS = text("SELECT SAUV_td32, SUM(qtype_MALFORMED) + SUM(tooshort_src) AS dns_malformed_ul, "
               "SUM(queries_woresp) AS dns_queries_woresp, SUM(queries) AS dns_queries FROM dns_5min "
               "WHERE SAUV_td32 IN :ts AND ip IN :ips AND supra='*' "
               "GROUP BY SAUV_td32").bindparams(bindparam("ts", expanding=True), bindparam("ips", expanding=True))
SQL_WHOLE_RANGE = text("SELECT SAUV_td32, " + ", ".join(f"`{c}`" for c in NUM_COLS) +
                       " FROM whole_5min WHERE SAUV_td32 BETWEEN :a AND :b ORDER BY SAUV_td32")
SQL_DNS_RANGE = text("SELECT SAUV_td32, SUM(qtype_MALFORMED) + SUM(tooshort_src) AS dns_malformed_ul, "
                     "SUM(queries_woresp) AS dns_queries_woresp, SUM(queries) AS dns_queries FROM dns_5min "
                     "WHERE SAUV_td32 BETWEEN :a AND :b AND ip IN :ips AND supra='*' "
                     "GROUP BY SAUV_td32").bindparams(bindparam("ips", expanding=True))
SQL_LAST = text("SELECT MAX(SAUV_td32) FROM whole_5min")
SQL_MODE = text("""
WITH computed AS (
  SELECT (COALESCE(tcp_dw,0)+COALESCE(udp_dw,0)+COALESCE(icmp_dw,0)+COALESCE(other_dw,0)) / :upg AS traffic_gbps
  FROM whole_5min WHERE SAUV_td32 BETWEEN :a AND :b),
bins AS (SELECT FLOOR(traffic_gbps / :bin) * :bin AS bin_start, COUNT(*) AS freq FROM computed GROUP BY bin_start),
ranked AS (SELECT bin_start, freq, LAG(freq) OVER (ORDER BY bin_start) AS freq_prev,
                  LEAD(freq) OVER (ORDER BY bin_start) AS freq_next FROM bins),
modal AS (SELECT bin_start, freq, COALESCE(freq_prev,0) AS freq_prev, COALESCE(freq_next,0) AS freq_next
          FROM ranked ORDER BY freq DESC, bin_start LIMIT 1)
SELECT CASE WHEN (2*freq - freq_prev - freq_next) = 0 THEN bin_start + :bin / 2
            ELSE bin_start + ((freq - freq_prev) / (2*freq - freq_prev - freq_next)) * :bin END AS mode_7days_gbps
FROM modal""")


@dataclass
class DbSnapshot:
    db_key: str
    ok: bool = False
    error: str = ""
    whole: dict = field(default_factory=dict)        # "YYYY-mm-dd HH:MM:SS" (UTC) -> ligne
    apn: dict = field(default_factory=dict)          # idem, TDD (bases « utilisateur »)
    last_update: datetime | None = None           # UTC naïf
    mode7d: float | None = None
    duration_s: float = 0.0


_mode_cache: dict[str, tuple[float, float | None]] = {}
_mode_lock = threading.Lock()


def _f(v):
    return float(v) if v is not None else None


def _rows(conn, stmt, ts: list, **extra) -> dict:
    out = {}
    for r in conn.execute(stmt, {"ts": ts, **extra}).mappings():
        out[str(r["SAUV_td32"])] = {k: _f(v) for k, v in r.items() if k != "SAUV_td32"}
    return out


def _collect_one(db_key: str, timestamps: list, latest_alert_ts: datetime, want_monitoring: bool) -> DbSnapshot:
    snap = DbSnapshot(db_key)
    t0 = time.monotonic()
    cfg = constants.DB_CONFIGS.get(db_key, {})
    host, db_name = cfg.get("host"), cfg.get("db_name")
    if not host or not db_name:
        snap.error = f"configuration incomplète (host={host}, db_name={db_name})"
        return snap
    try:
        with diag_engine(host, db_name, cfg.get("port")).connect() as conn:
            dns = _rows(conn, SQL_DNS, timestamps, ips=list(settings.dns_reference_ips))
            whole = _rows(conn, SQL_WHOLE, timestamps)
            for ts, row in whole.items():
                row.update({k: v for k, v in dns.get(ts, {}).items()})
            snap.whole = whole
            if db_key in constants.LABELS.get("utilisateur", {}):
                apn = _rows(conn, SQL_APN, timestamps, apn="%tdd%")
                snap.apn = apn
            if want_monitoring:
                snap.last_update = conn.execute(SQL_LAST).scalar()
                with _mode_lock:
                    cached = _mode_cache.get(db_key)
                if cached and time.monotonic() - cached[0] < settings.mode7d_cache_ttl:
                    snap.mode7d = cached[1]
                else:
                    r = conn.execute(SQL_MODE, {"a": latest_alert_ts - timedelta(days=7), "b": latest_alert_ts,
                                                 "upg": round(1 / constants.DEBIT_FACTOR), "bin": settings.mode_bin_gbps}).scalar()
                    snap.mode7d = float(r) if r is not None else None
                    with _mode_lock:
                        _mode_cache[db_key] = (time.monotonic(), snap.mode7d)
        snap.ok = True
    except Exception as e:                                      # base injoignable, table absente, délai dépassé…
        snap.error = f"{type(e).__name__}: {str(e)[:200]}"
        logger.warning("Collecte %s en échec : %s", db_key, snap.error)
    snap.duration_s = time.monotonic() - t0
    return snap


def read_range(db_key: str, start_utc: str, end_utc: str) -> dict[str, dict]:
    """Lecture SEULE d'une plage de relevés (UTC, bornes incluses) : ``{"YYYY-mm-dd HH:MM:SS": ligne}``.

    Sert à l'import d'historique (``history.backfill``). Même session READ ONLY que la collecte ; lève l'exception
    de la base en cas d'échec (l'appelant décide : l'import continue avec les autres bases).
    """
    cfg = constants.DB_CONFIGS.get(db_key, {})
    host, db_name = cfg.get("host"), cfg.get("db_name")
    if not host or not db_name:
        raise ValueError(f"configuration incomplète pour {db_key}")
    with diag_engine(host, db_name, cfg.get("port")).connect() as conn:
        params = {"a": start_utc, "b": end_utc}
        dns = {str(r["SAUV_td32"]): {k: _f(v) for k, v in r.items() if k != "SAUV_td32"}
               for r in conn.execute(SQL_DNS_RANGE, {**params, "ips": list(settings.dns_reference_ips)}).mappings()}
        rows = {}
        for r in conn.execute(SQL_WHOLE_RANGE, params).mappings():
            ts = str(r["SAUV_td32"])
            rows[ts] = {k: _f(v) for k, v in r.items() if k != "SAUV_td32"}
            rows[ts].update(dns.get(ts, {}))
    return rows


def collect_all(timestamps: list, alert_timestamps: list, monitor_roaming: bool = False) -> dict[str, DbSnapshot]:
    """Collecte toutes les bases en parallèle. Ne lève jamais : une base en échec donne un ``DbSnapshot(ok=False)``."""
    keys = list(constants.DB_CONFIGS.keys())
    if not keys:
        return {}
    latest = max(parse(t) for t in alert_timestamps)
    workers = max(1, min(settings.collect_max_workers, len(keys)))
    results: dict[str, DbSnapshot] = {}
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="collect") as pool:
        futures = {k: pool.submit(_collect_one, k, timestamps, latest, monitor_roaming or "roaming" not in k) for k in keys}
        for k, fut in futures.items():
            try:
                results[k] = fut.result(timeout=settings.collect_db_timeout + 10)
            except FutureTimeout:
                results[k] = DbSnapshot(k, error="délai de collecte dépassé")
                logger.error("Collecte %s : délai dépassé", k)
            except Exception as e:                                 # sécurité : ne jamais casser le cycle
                results[k] = DbSnapshot(k, error=f"{type(e).__name__}: {e}")
    failed = [k for k, s in results.items() if not s.ok]
    logger.info("Collecte terminée en %.1fs : %d/%d bases OK%s", time.monotonic() - t0, len(results) - len(failed),
                len(results), f" (échecs : {', '.join(failed)})" if failed else "")
    return results


def reset_cache() -> None:
    with _mode_lock:
        _mode_cache.clear()
