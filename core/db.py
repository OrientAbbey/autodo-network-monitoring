"""COUCHE BASE DE DONNÉES — SQLAlchemy 2.x (Core, sans ORM) + PyMySQL.

Choix et justification : voir README § « Décisions techniques ».

* base LOCALE (tickets, visites, journaux)  : un moteur, pool 8+8, transactions explicites
  (``with local_tx() as c`` : COMMIT à la sortie, ROLLBACK sur exception — il n'existe plus de ``commit()`` à oublier).
* bases DIAGNET (≈ 20, LECTURE SEULE)       : un moteur léger par (hôte, port, base), session ``READ ONLY`` imposée,
  délai d'exécution borné côté serveur, mode AUTOCOMMIT (aucune transaction qui traîne), pre-ping + recyclage
  (les pare-feux coupent les connexions inactives entre deux cycles de 5 min).
* schéma : migrations versionnées minimalistes (table ``schema_version``), idempotentes, sans Alembic.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL, Connection, Engine

from .config import fmt, now_local, settings
from .logger import get_logger

logger = get_logger(__name__)

_local_engine: Engine | None = None
_diag_engines: dict[tuple, Engine] = {}
_lock = threading.Lock()


# ----------------------------------------------------------------------------
# Moteurs
# ----------------------------------------------------------------------------
def local_engine() -> Engine:
    global _local_engine
    with _lock:
        if _local_engine is None:
            url = URL.create("mysql+pymysql", username=settings.local_db_user, password=settings.local_db_pass,
                             host=settings.local_db_host, port=settings.local_db_port,
                             database=settings.local_db_name, query={"charset": "utf8mb4"})
            _local_engine = create_engine(
                url, pool_size=settings.local_pool_size, max_overflow=settings.local_pool_overflow,
                pool_timeout=10, pool_pre_ping=True, pool_recycle=1800,
                connect_args={"connect_timeout": 5, "read_timeout": 30, "write_timeout": 30},
            )
        return _local_engine


def _enforce_read_only(dbapi_conn, _record):
    """Garde-fou : la session DIAGNET ne PEUT PAS écrire, même si le compte en avait le droit."""
    cur = dbapi_conn.cursor()
    try:
        cur.execute("SET SESSION TRANSACTION READ ONLY")
        maria = "mariadb" in str(getattr(dbapi_conn, "server_version", "")).lower()
        try:
            if maria:
                cur.execute(f"SET SESSION max_statement_time = {int(settings.diag_statement_timeout)}")
            else:
                cur.execute(f"SET SESSION max_execution_time = {int(settings.diag_statement_timeout) * 1000}")
        except Exception:   # variable absente selon la version/les droits : le read_timeout client reste actif
            logger.debug("Délai d'exécution serveur non appliqué")
    finally:
        cur.close()


def diag_engine(host: str, db_name: str, port: int | None = None) -> Engine:
    """Moteur DIAGNET (lecture seule), partagé par toutes les clés pointant vers la même base."""
    port = port or settings.diag_port
    key = (host, port, db_name)
    with _lock:
        eng = _diag_engines.get(key)
        if eng is None:
            url = URL.create("mysql+pymysql", username=settings.diag_user, password=settings.diag_pass,
                             host=host, port=port, database=db_name, query={"charset": "utf8mb4"})
            eng = create_engine(
                url, pool_size=1, max_overflow=1, pool_timeout=settings.collect_db_timeout,
                pool_pre_ping=True, pool_recycle=240, isolation_level="AUTOCOMMIT",
                connect_args={"connect_timeout": settings.diag_connect_timeout,
                              "read_timeout": settings.diag_read_timeout, "write_timeout": 5},
            )
            event.listen(eng, "connect", _enforce_read_only)
            _diag_engines[key] = eng
        return eng


def dispose_all() -> None:
    global _local_engine
    with _lock:
        for e in _diag_engines.values():
            e.dispose()
        _diag_engines.clear()
        if _local_engine is not None:
            _local_engine.dispose()
            _local_engine = None


# ----------------------------------------------------------------------------
# Accès base locale
# ----------------------------------------------------------------------------
@contextmanager
def local_tx() -> Iterator[Connection]:
    """Transaction d'écriture : COMMIT à la sortie normale, ROLLBACK sur exception."""
    with local_engine().begin() as conn:
        yield conn


@contextmanager
def local_conn() -> Iterator[Connection]:
    """Connexion de lecture (rien n'est validé ; la connexion est rendue au pool)."""
    with local_engine().connect() as conn:
        yield conn


def fetch_all(conn: Connection, sql: str, **params) -> list[dict]:
    return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]


def fetch_one(conn: Connection, sql: str, **params) -> dict | None:
    r = conn.execute(text(sql), params).mappings().first()
    return dict(r) if r else None


def ping_local() -> bool:
    try:
        with local_conn() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


# ----------------------------------------------------------------------------
# Migrations (sans Alembic) — chaque étape est idempotente
# ----------------------------------------------------------------------------
def _col_exists(c: Connection, table: str, col: str) -> bool:
    return bool(c.execute(text(
        "SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"),
        {"t": table, "c": col}).first())


def _idx_exists(c: Connection, table: str, idx: str) -> bool:
    return bool(c.execute(text(
        "SELECT 1 FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :i"),
        {"t": table, "i": idx}).first())


def _add_col(c: Connection, table: str, col: str, ddl: str) -> None:
    if not _col_exists(c, table, col):
        c.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))


def _add_idx(c: Connection, table: str, idx: str, ddl: str, unique: bool = False) -> None:
    if not _idx_exists(c, table, idx):
        try:
            c.execute(text(f"CREATE {'UNIQUE ' if unique else ''}INDEX {idx} ON {table} {ddl}"))
        except Exception as e:   # ex. doublons historiques : on n'échoue pas au démarrage
            logger.warning("Index %s non créé (%s)", idx, str(e)[:120])


def _m1_baseline(c: Connection) -> None:
    c.execute(text("""CREATE TABLE IF NOT EXISTS tickets_tb (
        id BIGINT NOT NULL PRIMARY KEY, type VARCHAR(20) NOT NULL, site VARCHAR(50) NOT NULL,
        database_name VARCHAR(50) NOT NULL, timestamp DATETIME NOT NULL, value DECIMAL(10,3) NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'open', agent VARCHAR(50) NOT NULL DEFAULT 'DIAGNET',
        severity VARCHAR(20) NOT NULL DEFAULT 'critical', closure_timestamp DATETIME DEFAULT NULL,
        closure_pending_timestamp DATETIME DEFAULT NULL, closure_reason TEXT DEFAULT NULL,
        autin_id VARCHAR(50) DEFAULT NULL, external_api_ticket_id VARCHAR(100) DEFAULT NULL,
        ttr_seconds INT DEFAULT NULL, category TINYINT DEFAULT NULL, pertes CHAR(3) NOT NULL DEFAULT 'No',
        last_values JSON DEFAULT NULL, description TEXT NOT NULL, reason TEXT DEFAULT NULL,
        pertes_warnings JSON DEFAULT NULL,
        INDEX idx_ticket_filter (database_name, type, status, category), INDEX idx_timestamp (timestamp)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""))
    c.execute(text("""CREATE TABLE IF NOT EXISTS daily_users (
        `date` VARCHAR(10) NOT NULL, client_id VARCHAR(255) NOT NULL, last_seen DATETIME NOT NULL,
        visits_count INT NOT NULL DEFAULT 0, last_ip VARCHAR(45), user_agent TEXT,
        PRIMARY KEY (`date`, client_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""))
    c.execute(text("""CREATE TABLE IF NOT EXISTS logs (
        id INT AUTO_INCREMENT PRIMARY KEY, ip_address VARCHAR(45) NOT NULL,
        access_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP, action VARCHAR(100) NOT NULL DEFAULT 'criteria_access',
        INDEX idx_ip_time (ip_address, access_time), INDEX idx_access_time (access_time), INDEX idx_action (action)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""))
    c.execute(text("CREATE TABLE IF NOT EXISTS ticket_counters (day DATE PRIMARY KEY, n INT NOT NULL) ENGINE=InnoDB"))
    c.execute(text("""CREATE TABLE IF NOT EXISTS notification_state (
        recipient VARCHAR(255) NOT NULL PRIMARY KEY, ticket_ids TEXT, sent_at DATETIME NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""))


def _m2_tickets_outbox(c: Connection) -> None:
    """Version (verrouillage optimiste) + « outbox » Orange (état, tentatives, prochaine échéance)."""
    _add_col(c, "tickets_tb", "version", "INT NOT NULL DEFAULT 0")
    _add_col(c, "tickets_tb", "orange_sync_state", "VARCHAR(16) NOT NULL DEFAULT 'pending'")
    _add_col(c, "tickets_tb", "orange_attempts", "INT NOT NULL DEFAULT 0")
    _add_col(c, "tickets_tb", "orange_next_retry_at", "DATETIME NULL")
    _add_col(c, "tickets_tb", "orange_last_error", "VARCHAR(255) NULL")
    # reprise de l'existant
    c.execute(text("UPDATE tickets_tb SET orange_sync_state = 'synced' WHERE orange_sync_state = 'pending' "
                   "AND external_api_ticket_id IS NOT NULL AND external_api_ticket_id <> '' AND status = 'open'"))
    c.execute(text("UPDATE tickets_tb SET orange_sync_state = 'closed' WHERE status = 'closed' "
                   "AND orange_sync_state IN ('pending','synced')"))
    _add_idx(c, "tickets_tb", "idx_status_ts", "(status, timestamp)")
    _add_idx(c, "tickets_tb", "idx_orange_sync", "(orange_sync_state, orange_next_retry_at)")
    _add_idx(c, "tickets_tb", "uq_external_ticket", "(external_api_ticket_id)", unique=True)


def _m3_logs_datetime(c: Connection) -> None:
    """logs.access_time : TIMESTAMP (converti selon le fuseau de session) → DATETIME locale, comme le reste du schéma."""
    row = c.execute(text("SELECT data_type FROM information_schema.columns WHERE table_schema = DATABASE() "
                         "AND table_name = 'logs' AND column_name = 'access_time'")).first()
    if row and str(row[0]).lower() == "timestamp":
        c.execute(text("ALTER TABLE logs MODIFY access_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"))


MIGRATIONS = [
    (1, "baseline", _m1_baseline),
    (2, "tickets: version + outbox Orange + index", _m2_tickets_outbox),
    (3, "logs.access_time en DATETIME", _m3_logs_datetime),
]


def ensure_schema() -> bool:
    """Applique les migrations manquantes. Retourne False si la base est inaccessible."""
    try:
        with local_tx() as c:
            c.execute(text("CREATE TABLE IF NOT EXISTS schema_version (version INT PRIMARY KEY, "
                           "name VARCHAR(100) NOT NULL, applied_at DATETIME NOT NULL) ENGINE=InnoDB"))
            applied = {r[0] for r in c.execute(text("SELECT version FROM schema_version"))}
        for version, name, fn in MIGRATIONS:
            if version in applied:
                continue
            with local_tx() as c:
                fn(c)
                c.execute(text("INSERT INTO schema_version (version, name, applied_at) VALUES (:v, :n, :t)"),
                          {"v": version, "n": name, "t": fmt(now_local().replace(tzinfo=None))})
            logger.info("Migration %s appliquée : %s", version, name)
        return True
    except Exception:
        logger.exception("Vérification/migration du schéma impossible")
        return False
