"""
UTILS MODULE - Shared utility functions for the unified application
"""

import json
import uuid
from contextlib import suppress
from datetime import datetime, timedelta

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict

from . import constants
from .database_manager import get_local_db_connection
from .logger import get_logger

logger = get_logger(__name__)

# ============================================================================
# CENTRALIZED EXPORTS - Avoid re-importing common functions across modules
# ============================================================================
# These functions are frequently used across ticket_manager.py and background_task.py
# Import them once here to avoid duplicate imports everywhere
__all__ = [
    'load_config',
    'calculate_ttr',
    'ttr_to_human_readable',
    'get_next_ticket_id',
    'ConfigParams',
]

# ============================================================================
# PYDANTIC MODELS
# ============================================================================
class ConfigParams(BaseModel):
    alert_traffic_threshold_percent: float = 40.0
    alert_pertes_threshold: float = 3.0
    pertes_consecutive_count: int = 2
    traffic_up_threshold_percent: float = 10.0
    traffic_consecutive_drops_count: int = 5
    traffic_consecutive_drops_min_percent: float = 17.0
    alert_persistence_duration_minutes: int = 25
    day_over_day_drop_percent: float = 50.0
    traffic_recovery_ratio: float = 0.9
    mode7days_recovery_ratio: float = 0.85
    db_recovery_threshold_minutes: int = 70
    # TCP metrics thresholds
    alert_tcp_no_response_threshold: float = 10.0
    alert_tcp_block_wp_up_threshold: float = 10.0
    alert_latence_threshold_ms: float = 250.0
    alerts_enable_international: bool = True
    alerts_enable_gi: bool = True
    alerts_enable_international_gi: bool = False
    alerts_enable_roaming: bool = False
    alerts_enable_tdd: bool = False

    model_config = ConfigDict(extra="allow")


# ============================================================================
# CONFIGURATION MANAGEMENT
# ============================================================================
def load_config() -> dict:
    """Load unified configuration from config.json"""
    from .utils import ConfigParams

    # Valeurs par défaut via Pydantic
    config = ConfigParams().model_dump()
    config_path = constants.CONFIG_FILE
    db_connections = {}

    try:
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # conserve la logique existante : fusionner criteria/alerts/database_connections
                config.update(data.get("criteria", {}))
                config.update(data.get("alerts", {}))
                db_connections = data.get("database_connections", {})
    except Exception as e:
        logger.error(f"Error loading config: {e}")

    config["database_connections"] = db_connections
    return config

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================
def get_cameroon_time() -> datetime:
    """Get current time in Cameroon timezone"""
    return datetime.now(constants.CAMEROON_TZ)


def calculate_ttr(creation_timestamp: str, closure_timestamp: str) -> int | None:
    """
    Calculate Time To Repair (TTR) between creation and closure timestamps
    """
    def parse_timestamp(ts):
        return datetime.fromisoformat(ts.replace("Z", "+00:00")) if isinstance(ts, str) else ts

    try:
        # Parse timestamps
        start_time = parse_timestamp(creation_timestamp)
        end_time = parse_timestamp(closure_timestamp)
        total_seconds = int((end_time - start_time).total_seconds())
        return total_seconds
    except Exception:
        return None


def ttr_to_human_readable(ttr_seconds: int | None) -> str | None:
    """
    Convert TTR seconds to a human-readable format
    """
    if ttr_seconds is None:
        return None
    if ttr_seconds == 0:
        return "Instantaneous"

    abs_seconds = abs(ttr_seconds)
    days, remainder = divmod(abs_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds:
        parts.append(f"{seconds}s")

    return ("-" if ttr_seconds < 0 else "") + " ".join(parts)


# Helper: convert period string to cutoff datetime/string (DRY)
def _period_to_cutoff(period: str, tz=constants.CAMEROON_TZ):
    now = datetime.now(tz)
    if period == "today":
        cutoff = datetime(now.year, now.month, now.day, tzinfo=tz)
    else:
        days_map = {"7days": 7, "1month": 30, "3months": 90}
        cutoff = now - timedelta(days=days_map.get(period, 7))
    return cutoff


def get_real_ip(request: Request):
    # Le plus fiable : X-Forwarded-For (HAProxy)
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    # Nginx/Traefik
    xri = request.headers.get("x-real-ip")
    if xri:
        return xri
    # Par défaut (rarement utile)
    return request.client.host if request.client else None


def get_next_ticket_id() -> int:
    """
    Génère le prochain ID de ticket en regardant uniquement la base de données.
    Format : YYYYMMDDNNN (NNN = 001..999)
    Utilise une recherche par intervalle [today 00:00:00, tomorrow 00:00:00) pour tirer parti de l'index timestamp.
    """
    # Get today's date prefix
    today = get_cameroon_time().date()
    today_start = datetime.combine(today, datetime.min.time(), tzinfo=constants.CAMEROON_TZ)
    tomorrow_start = today_start + timedelta(days=1)

    today_prefix = int(today.strftime("%Y%m%d"))

    try:
        with get_local_db_connection() as conn:
            with conn.cursor(dictionary=True) as cur:
                # Récupère l'id max pour aujourd'hui en utilisant l'index sur timestamp
                cur.execute(
                    "SELECT MAX(id) as max_id FROM tickets_tb WHERE timestamp >= %s AND timestamp < %s",
                    (today_start, tomorrow_start)
                )
                row = cur.fetchone()
        max_id = row["max_id"] if row else None
    except Exception as e:
        logger.warning(f"Impossible de vérifier les IDs en base : {e}")
        # En cas d'erreur DB, on peut choisir de retourner le premier ID du jour
        return int(f"{today_prefix}001")

    if not max_id:
        return int(f"{today_prefix}001")

    # extraire les 3 derniers chiffres (NNN) et incrémenter
    last_number = int(str(max_id)[-3:])
    next_number = last_number + 1

    # Ensure the number is padded with zeros
    next_id = int(f"{today_prefix}{next_number:03d}")
    logger.info(f"🔢 Generated new ticket ID: {next_id} (db max: {max_id})")
    return next_id


def log_criteria_access(request, action: str) -> bool:
    """Log criteria-related actions with IP address to database"""
    try:
        # Get client IP address for logging
        ip_address = get_real_ip(request)

        # Create database connection
        conn = get_local_db_connection()
        if not conn:
            return False

        with conn:
            with conn.cursor() as cursor:
                # Insert log entry
                query = """
                INSERT INTO logs (ip_address, access_time, action)
                VALUES (%s, %s, %s)
                """
                current_time = get_cameroon_time().strftime("%Y-%m-%d %H:%M:%S")

                cursor.execute(query, (ip_address, current_time, action))

                conn.commit()

        logger.info(f"Logged criteria access: IP={ip_address}, Action={action}")
        return True

    except Exception as e:
        logger.error(f"Failed to log criteria access: {e}")
        return False


def create_logs_table():
    """Create logs table for criteria access tracking"""
    try:
        with get_local_db_connection() as connection:
            with connection.cursor() as cursor:
                # Create logs table if it doesn't exist
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS logs (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        ip_address VARCHAR(45) NOT NULL,
                        access_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        action VARCHAR(100) NOT NULL DEFAULT 'criteria_access',
                        INDEX idx_ip_time (ip_address, access_time),
                        INDEX idx_access_time (access_time),
                        INDEX idx_action (action)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
        logger.debug("✅ logs table created/verified successfully")
    except Exception as e:
        logger.error(f"❌ Error creating logs table: {str(e)}")


def verify_database_setup():
    """Verify database connection and table structure with enhanced error handling."""
    try:
        logger.debug("Verifying database setup...")
        # Import DB host/port/name locally to avoid module-level circular imports
        logger.debug(f"Attempting to connect to {constants.LOCAL_DB_HOST}:{constants.LOCAL_DB_PORT}")
        with get_local_db_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:
                # Check if database exists
                cursor.execute(
                    "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = %s",
                    (constants.LOCAL_DB_NAME,)
                )
                if not cursor.fetchone():
                    logger.error(f"Error: Database '{constants.LOCAL_DB_NAME}' does not exist!")
                    return False

                # Check if tickets_tb table exists
                cursor.execute("SHOW TABLES LIKE 'tickets_tb'")
                if not cursor.fetchone():
                    logger.debug("Creating tickets_tb table...")
                    create_table_sql = """
                    CREATE TABLE IF NOT EXISTS tickets_tb (
                        id BIGINT(20) NOT NULL PRIMARY KEY,
                        type VARCHAR(20) NOT NULL,
                        site VARCHAR(50) NOT NULL,
                        database_name VARCHAR(50) NOT NULL,
                        timestamp DATETIME NOT NULL,
                        value DECIMAL(10,3) NOT NULL,
                        status VARCHAR(20) NOT NULL DEFAULT 'open',
                        agent VARCHAR(50) NOT NULL DEFAULT 'DIAGNET',
                        severity VARCHAR(20) NOT NULL DEFAULT 'critical',
                        closure_timestamp DATETIME DEFAULT NULL,
                        closure_pending_timestamp DATETIME DEFAULT NULL,
                        closure_reason TEXT DEFAULT NULL,
                        autin_id VARCHAR(50) DEFAULT NULL,
                        external_api_ticket_id VARCHAR(100) DEFAULT NULL,
                        ttr_seconds INT DEFAULT NULL,
                        category TINYINT DEFAULT NULL,
                        pertes CHAR(3) NOT NULL DEFAULT 'No',
                        last_values JSON DEFAULT NULL,
                        description TEXT NOT NULL,
                        reason TEXT DEFAULT NULL,
                        pertes_warnings JSON DEFAULT NULL,
                        INDEX idx_ticket_filter (database_name, type, status, category),
                        INDEX idx_timestamp (timestamp)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                    """
                    cursor.execute(create_table_sql)
                    logger.debug("Tickets table created successfully")

                logger.debug("Database setup verification successful!")
                return True
    except Exception:
        logger.exception("Unexpected error during database verification")
        return False


# ============================================================================
# DATABASE FUNCTIONS
# ============================================================================

def init_daily_users_table():
    """Initialize daily_users table for tracking"""
    CREATE_DAILY_USERS_TABLE = """
    CREATE TABLE IF NOT EXISTS daily_users (
        `date` VARCHAR(10) NOT NULL,
        client_id VARCHAR(255) NOT NULL,
        last_seen DATETIME NOT NULL,
        visits_count INT NOT NULL DEFAULT 0,
        last_ip VARCHAR(45),
        user_agent TEXT,
        PRIMARY KEY (`date`, client_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    try:
        conn = get_local_db_connection()
        if not conn:
            logger.error("Impossible d'obtenir une connexion pour initialiser daily_users")
            return
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(CREATE_DAILY_USERS_TABLE)
                conn.commit()
    except Exception as e:
        logger.warning(f"Error initializing daily_users table: {e}")


# ============================================================================
# USER TRACKING & ANALYTICS FUNCTIONS
# ============================================================================
def track_user(request: Request, response_headers, session_threshold_minutes=60):
    """Track user in daily_users table via MySQL (mysql-connector-python)"""
    client_id = request.headers.get("X-Client-Id")
    if not client_id:
        client_id = str(uuid.uuid4())
        response_headers["X-Client-Id"] = client_id  # renvoyé dans la réponse

    # Infos utilisateur
    ip = get_real_ip(request)
    # logger.debug(f"Tracking {request.client} with headers {request.headers}")

    user_agent = request.headers.get("user-agent", None)
    now = get_cameroon_time()
    today = now.strftime("%Y-%m-%d")
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    conn = get_local_db_connection()
    if not conn:
        return

    with conn:
        try:
            conn.start_transaction()
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    "SELECT last_seen, visits_count FROM daily_users WHERE `date` = %s AND client_id = %s",
                    (today, client_id),
                )
                row = cur.fetchone()

                if row is None:
                    # Première visite de la journée pour ce client_id
                    cur.execute(
                        "INSERT INTO daily_users (`date`, client_id, last_seen, visits_count, last_ip, user_agent) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (today, client_id, now_str, 1, ip, user_agent),
                    )
                else:
                    last_seen_str = row.get("last_seen")
                    visits_count = row.get("visits_count", 0)
                    # Parser last_seen
                    try:
                        if isinstance(last_seen_str, datetime):
                            last_seen_dt = last_seen_str
                        else:
                            last_seen_dt = datetime.strptime(last_seen_str, "%Y-%m-%d %H:%M:%S")
                        # 🔑 IMPORTANT : rendre last_seen_dt timezone-aware
                        if last_seen_dt.tzinfo is None:
                            last_seen_dt = last_seen_dt.replace(tzinfo=constants.CAMEROON_TZ)
                    except Exception as e:
                        logger.warning(f"Failed to parse last_seen '{last_seen_str}' for client_id {client_id}: {e}")
                        # Si parse échoue, considérer une visite ancienne
                        last_seen_dt = now - timedelta(minutes=(session_threshold_minutes or 0) + 1)

                    # Déterminer si nouvelle "visite"
                    increment = session_threshold_minutes is None or (
                        now - last_seen_dt >= timedelta(minutes=session_threshold_minutes)
                    )
                    if increment:
                        visits_count += 1

                    cur.execute(
                        "UPDATE daily_users "
                        "SET last_seen = %s, visits_count = %s, last_ip = %s, user_agent = %s "
                        "WHERE `date` = %s AND client_id = %s",
                        (now_str, visits_count, ip, user_agent, today, client_id),
                    )
                logger.debug(f"Tracked user {client_id} for date {today} (visits_count={row.get('visits_count', 0) if row else 1})")
            conn.commit()
        except Exception as e:
            logger.error(f"Error tracking user in daily_users: {e}")
            with suppress(Exception):
                conn.rollback()


def get_access_logs_from_db(period: str = "7days") -> dict:
    """Get access logs from database"""
    now = get_cameroon_time()
    cutoff = _period_to_cutoff(period)
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    conn = get_local_db_connection()
    if not conn:
        return {
            "access_logs": [],
            "access_stats": {
                "daily_data": {},
                "total_unique_ips": 0,
                "avg_daily_unique": 0,
                "today_unique_count": 0,
            },
        }

    with conn:
        with conn.cursor(dictionary=True) as cur:
            # access_logs
            cur.execute(
                """
                SELECT `date`, last_ip AS ip_address, SUM(visits_count) AS access_count,
                       MAX(last_seen) AS last_access, COUNT(DISTINCT client_id) AS unique_users
                FROM daily_users
                WHERE `date` >= %s
                GROUP BY `date`, last_ip
                ORDER BY `date` DESC, last_ip ASC
                """,
                (cutoff_str,),
            )
            access_rows = cur.fetchall()

            # daily_data (unique IPs per day)
            cur.execute(
                """
                SELECT `date`, COUNT(DISTINCT last_ip) AS daily_unique_ips
                FROM daily_users
                WHERE `date` >= %s
                GROUP BY `date`
                ORDER BY `date` DESC
                """,
                (cutoff_str,),
            )
            daily_rows = cur.fetchall()

            # totals & avg
            cur.execute(
                """
                SELECT
                    (SELECT COUNT(DISTINCT last_ip) FROM daily_users WHERE `date` >= %s) AS total_unique_ips,
                    COUNT(*) AS num_days,
                    ROUND(AVG(daily_unique_ips)) AS avg_daily_unique
                FROM (
                    SELECT `date`, COUNT(DISTINCT last_ip) AS daily_unique_ips
                    FROM daily_users
                    WHERE `date` >= %s
                    GROUP BY `date`
                ) AS t
                """,
                (cutoff_str, cutoff_str),
            )
            totals_row = cur.fetchone()

            # today unique count
            today_str = now.strftime("%Y-%m-%d")
            cur.execute(
                "SELECT COUNT(DISTINCT last_ip) AS today_unique_count FROM daily_users WHERE `date` = %s",
                (today_str,),
            )
            today_row = cur.fetchone()

    # Formatter les résultats
    access_logs = [
        {
            "date": (
                row["date"]
                if isinstance(row["date"], str)
                else row["date"].isoformat().split("T", 1)[0]
            ),
            "ip_address": row["ip_address"],
            "access_count": int(row["access_count"] or 0),
            "last_access": row["last_access"],
            "unique_users": int(row.get("unique_users") or 0),
        }
        for row in access_rows
    ]

    daily_data = {row["date"]: int(row["daily_unique_ips"] or 0) for row in daily_rows}

    total_unique = int((totals_row or {}).get("total_unique_ips") or 0)
    avg_daily = int((totals_row or {}).get("avg_daily_unique") or 0)
    today_unique = int((today_row or {}).get("today_unique_count") or 0)

    return {
        "access_logs": access_logs,
        "access_stats": {
            "daily_data": daily_data,
            "total_unique_ips": total_unique,
            "avg_daily_unique": avg_daily,
            "today_unique_count": today_unique,
        },
    }


def get_alerts_stats_from_db(period="7days"):
    """Get alert statistics from tickets_tb aggregated by day"""
    try:
        conn = get_local_db_connection()
        if not conn:
            return {}

        cutoff = _period_to_cutoff(period)
        cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

        sql = """
            SELECT DATE(timestamp) as day,
                   SUM(CASE WHEN type = 'traffic' THEN 1 ELSE 0 END) as traffic,
                   SUM(CASE WHEN type = 'pertes' THEN 1 ELSE 0 END) as pertes,
                   SUM(CASE WHEN type = 'db_down' THEN 1 ELSE 0 END) as db_down,
                   COUNT(*) as total
            FROM tickets_tb
            WHERE timestamp >= %s
            GROUP BY DATE(timestamp)
            ORDER BY day DESC
        """

        with conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(sql, (cutoff_str,))
                rows = cur.fetchall()

        # Formater les résultats par jour
        result = {
            (
                row["day"].isoformat()
                if hasattr(row["day"], "isoformat")
                else str(row["day"])
            ): {
                "traffic": int(row["traffic"] or 0),
                "pertes": int(row["pertes"] or 0),
                "db_down": int(row["db_down"] or 0),
                "total": int(row["total"] or 0),
            }
            for row in rows
        }

        return result
    except Exception as e:
        logger.error(f"DB error get_alerts_stats: {e}")
        return {}


def get_config_changes_from_db(period: str = "7days") -> list:
    """Get configuration changes from logs"""
    try:
        conn = get_local_db_connection()
        if not conn:
            return []

        cutoff = _period_to_cutoff(period)
        cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

        with conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    """
                    SELECT access_time as timestamp,
                           action as parameter,
                           ip_address as modified_by
                    FROM logs
                    WHERE access_time >= %s
                    ORDER BY access_time DESC
                    """,
                    (cutoff_str,),
                )
                rows = cur.fetchall()

        return [
            {
                "timestamp": row.get("timestamp"),
                "parameter": row.get("parameter"),
                "modified_by": row.get("modified_by"),
            }
            for row in rows
        ]
    except Exception as e:
        logger.error(f"DB error get_config_changes: {e}")
        return []


# ============================================================================
# SSE SUPPORT
# ============================================================================
async def _sse_publish(event: dict, app: FastAPI):
    """
    Publie un événement à tous les abonnés SSE de manière sûre.
    - Si une queue est pleine, on droppe le plus ancien élément (policy best-effort).
    - On protège l'accès à la collection d'abonnés par un lock.
    """
    try:
        subscribers = getattr(app.state, "sse_subscribers", set())
        if not subscribers:
            return
        async with app.state.sse_subscribers_lock:
            # Itérer sur une copie pour éviter RuntimeError si modification concurrente
            for q in list(subscribers):
                try:
                    # Si queue full, drop oldest (best-effort) pour faire de la place
                    if q.full():
                        with suppress(Exception):
                            _ = q.get_nowait()
                    q.put_nowait(event)
                except Exception as e:
                    # Si la queue est corrompue ou fermée, la retirer proprement
                    logger.debug(f"Removing dead SSE queue due to error: {e}")
                    subscribers.discard(q)
    except Exception as e:
        logger.exception(f"Erreur publication SSE: {e}")
