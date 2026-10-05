"""
DATA COLLECTOR - Fetches counter data from multiple databases
Handles synchronous connections only
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Dict

import polars as pl
from mysql.connector import Error, connection, pooling

from . import constants
from .logger import get_logger

logger = get_logger(__name__)

# Database credentials (monitoring)
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_PORT = int(os.getenv("DB_PORT", "3306"))

# Synchronous connection pools for monitoring
_sync_pools: Dict[str, pooling.MySQLConnectionPool] = {}
# Local DB pool (used for tickets/logs)
_local_pool: pooling.MySQLConnectionPool | None = None

__all__ = [
    "DataCollector",
    "get_local_db_connection",
    "round_to_nearest_five",
    "get_db_datetime",
    "calculate_vectorized_metrics"
]


def round_to_nearest_five(dt: datetime) -> datetime:
    return dt.replace(minute=dt.minute - (dt.minute % 5), second=0, microsecond=0)


def get_db_datetime(user_dt: datetime) -> datetime:
    """Convert user datetime to database datetime (UTC, rounded)."""
    naive = user_dt.tzinfo is None
    user_dt = user_dt.replace(tzinfo=constants.CAMEROON_TZ) if naive else user_dt
    user_dt = round_to_nearest_five(user_dt.astimezone(timezone.utc))
    return user_dt.replace(tzinfo=None) if naive else user_dt


def _create_pool(pool_name: str, config: dict) -> pooling.MySQLConnectionPool:
    """
    Helper: Create MySQL connection pool from config dict.
    Centralizes pool creation logic to avoid duplication.
    """
    return pooling.MySQLConnectionPool(
        pool_name=pool_name,
        host=config["host"],
        user=config.get("user"),
        password=config.get("password"),
        database=config.get("db") or config.get("db_name"),
        port=config.get("port", 3306),
        pool_size=6,
        connection_timeout=60,
        autocommit=False,
        pool_reset_session=True,
        use_pure=True,
    )


def _create_local_pool() -> pooling.MySQLConnectionPool:
    """Create synchronous pool for the local database (tickets/logs)."""
    return _create_pool("local_pool", constants.LOGS_DB_CONFIG)


def get_local_db_connection()-> pooling.PooledMySQLConnection | connection.MySQLConnection:
    """Return connection from local DB pool (used for tickets and logs)."""
    global _local_pool
    if _local_pool is None:
        _local_pool = _create_local_pool()
    try:
        return _local_pool.get_connection()
    except Error as e:
        logger.critical(f"Error getting local DB connection: {e}")
        raise RuntimeError(f"Error getting local DB connection: {e}")


class DataCollector:
    """Centralized data collection from multiple databases"""
    
    @staticmethod
    def _create_sync_pool(db_key: str) -> pooling.MySQLConnectionPool:
        """Create synchronous database connection pool"""
        if db_key not in constants.DB_CONFIGS:
            raise KeyError(f"Unknown database key: {db_key}")
        
        config = constants.DB_CONFIGS[db_key]
        return _create_pool(
            pool_name=f"{db_key}_pool",
            config={
                "host": config["host"],
                "user": DB_USER,
                "password": DB_PASS,
                "db_name": config["db_name"],
                "port": DB_PORT,
            }
        )
    
    @staticmethod
    def get_sync_connection(db_key: str)-> (pooling.PooledMySQLConnection | connection.MySQLConnection):
        """Get synchronous connection from pool"""
        if db_key not in _sync_pools:
            _sync_pools[db_key] = DataCollector._create_sync_pool(db_key)
        try:
            return _sync_pools[db_key].get_connection()
        except Error as e:
            logger.critical(f"Error getting connection for {db_key}: {e}")
            raise RuntimeError(f"Error getting connection for {db_key}: {e}")
    
    @staticmethod
    def load_all_databases_data_sync(alert_timestamps: list[str], exclude_roaming: bool = False) -> tuple[dict[str, pl.DataFrame], dict[str, datetime], dict[str, float]]:
        """
        Load data from all monitoring databases synchronously (for background task)
        Args:
            alert_timestamps: List of timestamps to fetch
            exclude_roaming: If True, exclude roaming databases from the fetch
        Returns: (all_data, all_last_update, all_mode_7days_gbps)
        """
        all_data, all_last_update, all_mode_7days_gbps = {}, {}, {}
        
        default_result = {
            "tcp_dw": 0, "udp_dw": 0, "icmp_dw": 0, "other_dw": 0,
            "tcp_loss_dw": 0, "tcp_pkt_dw": 1, "connection_failed": True,
            "failure_reason": None
        }
        
        for db_key, config in constants.DB_CONFIGS.items():
            # Exclude roaming databases if requested
            if exclude_roaming and "roaming" in db_key:
                continue
            host, db_name = config.get('host'), config.get('db_name')
            if not host or not db_name:
                logger.warning(f"❌ SKIPPING {db_key}: Missing host ({host}) or db_name ({db_name})")
                continue
            
            try:
                with DataCollector.get_sync_connection(db_key) as connection:
                    with connection.cursor(dictionary=True) as cursor:
                        cursor.execute("SHOW TABLES LIKE 'whole_5min'")
                        if not cursor.fetchone():
                            all_data[db_key] = pl.DataFrame([
                                {**default_result, "SAUV_td32": str(ts)} for ts in alert_timestamps
                            ])
                            all_last_update[db_key] = None
                            all_mode_7days_gbps[db_key] = None
                            continue
                        
                        # Recuperer la date la plus recente:
                        cursor.execute("""SELECT SAUV_td32 as last_update FROM whole_5min ORDER BY SAUV_td32 DESC LIMIT 1""")
                        r = cursor.fetchone()
                        last_update = r["last_update"] if r else None
                        
                        # Calculer le mode du traffic des 7 derniers jours
                        latest_alert_ts = max(datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") for ts in alert_timestamps)
                        query = f"""
                            WITH computed AS (
                                SELECT
                                    (COALESCE(tcp_dw,0) + COALESCE(udp_dw,0) + COALESCE(icmp_dw,0) + COALESCE(other_dw,0))
                                    / 37500000.0 AS traffic_gbps
                                FROM whole_5min
                                WHERE `SAUV_td32` BETWEEN %s AND %s
                            ),
                            bins AS (
                                SELECT
                                    FLOOR(traffic_gbps / 2.5) * 2.5 AS bin_start,
                                    COUNT(*) AS freq
                                FROM computed
                                GROUP BY bin_start
                            ),
                            ranked AS (
                                SELECT
                                    bin_start,
                                    freq,
                                    LAG(freq) OVER (ORDER BY bin_start) AS freq_prev,
                                    LEAD(freq) OVER (ORDER BY bin_start) AS freq_next
                                FROM bins
                            ),
                            modal AS (
                                SELECT
                                    bin_start,
                                    freq,
                                    COALESCE(freq_prev, 0) AS freq_prev,
                                    COALESCE(freq_next, 0) AS freq_next
                                FROM ranked
                                ORDER BY freq DESC, bin_start
                                LIMIT 1
                            )
                            SELECT
                                CASE
                                    WHEN (2*freq - freq_prev - freq_next) = 0
                                        THEN bin_start + 1.25     -- midpoint fallback
                                    ELSE bin_start
                                        + ((freq - freq_prev) / (2*freq - freq_prev - freq_next)) * 2.5
                                END AS mode_7days_gbps
                            FROM modal;
                        """

                        # latest_alert_ts doit être un datetime.datetime
                        start_ts = latest_alert_ts - timedelta(days=7)
                        end_ts = latest_alert_ts

                        # Exécution sécurisée
                        cursor.execute(query, (start_ts, end_ts))

                        r = cursor.fetchone()
                        mode_7days_gbps = float(r["mode_7days_gbps"]) if r else None
                        
                        # Recuperer les autres données
                        placeholders = ','.join(['%s'] * len(alert_timestamps))
                        cursor.execute(f"""
                            SELECT tcp_dw, udp_dw, icmp_dw, other_dw, `#tcp_loss_dw`, `#tcp_pkt_dw`, SAUV_td32
                            FROM whole_5min
                            WHERE SAUV_td32 IN ({placeholders})
                        """, alert_timestamps)
                        rows = cursor.fetchall()
                        
                        # DataFrame complet avec défauts (SAUV_td32 en string)
                        df_full = pl.DataFrame([{**default_result, "SAUV_td32": str(ts)} for ts in alert_timestamps])

                        if rows:
                            # Récupérer noms de colonnes depuis cursor (robuste si rows est list[tuple])
                            colnames = [c[0] for c in cursor.description]
                            df_existing = pl.DataFrame(rows, schema=colnames, orient="row")

                            # Renommer colonnes SQL si besoin et préparer colonnes additionnelles
                            df_existing = df_existing.rename({"#tcp_loss_dw": "tcp_loss_dw", "#tcp_pkt_dw": "tcp_pkt_dw"})

                            # Normaliser SAUV_td32 en chaîne pour le join et ajouter flags
                            df_existing = df_existing.with_columns([
                                pl.col("SAUV_td32").dt.strftime("%Y-%m-%d %H:%M:%S").alias("SAUV_td32"),
                                pl.lit(False).alias("connection_failed"),
                                pl.lit(None).alias("failure_reason"),
                            ])

                            # join (les colonnes dupliquées auront suffixe _right)
                            df_full = df_existing.join(df_full, on="SAUV_td32", how="right")

                            # colonnes de données à coalescer (strings)
                            data_cols = [
                                "tcp_dw", "udp_dw", "icmp_dw", "other_dw",
                                "tcp_loss_dw", "tcp_pkt_dw",
                                "connection_failed", "failure_reason", "SAUV_td32"
                            ]

                            df_full = df_full.select(data_cols)

                        all_data[db_key] = df_full
                        all_last_update[db_key] = last_update
                        all_mode_7days_gbps[db_key] = mode_7days_gbps
            
            except Exception as e:
                logger.error(f"🚨 DATABASE CONNECTION FAILED for {db_name}: {e}")
                all_data[db_key] = pl.DataFrame([{**default_result, "failure_reason": f"Connection error: {e}", "SAUV_td32": str(ts)}
                                            for ts in alert_timestamps])
                all_last_update[db_key] = None
                all_mode_7days_gbps[db_key] = None

        return all_data, all_last_update, all_mode_7days_gbps
    
    @staticmethod
    def fetch_data_batch(db_key: str, timestamps: tuple, table_type: str = "whole") -> dict:
        """Fetch batch data from single database synchronously"""
        try:
            connection = DataCollector.get_sync_connection(db_key)
            if not connection:
                return {ts: None for ts in timestamps}

            with connection:
                with connection.cursor(dictionary=True) as cursor:
                    placeholders = ",".join(["%s"] * len(timestamps))

                    if table_type == "apn":
                        sql = f"""
                            SELECT SAUV_td32,
                                   SUM(tcp_dw) AS tcp_dw, SUM(udp_dw) AS udp_dw,
                                   SUM(icmp_dw) AS icmp_dw, SUM(other_dw) AS other_dw,
                                   SUM(`#tcp_loss_dw`) AS `#tcp_loss_dw`, SUM(`#tcp_pkt_dw`) AS `#tcp_pkt_dw`,
                                   SUM(`#tcp_no_response_to_syn`) AS `#tcp_no_response_to_syn`,
                                   SUM(`#tcp_block_wp_up`) AS `#tcp_block_wp_up`,
                                   SUM(`#tcp_synack_core`) AS `#tcp_synack_core`,
                                   SUM(Irt_tcp_core_td22) AS Irt_tcp_core_td22,
                                   SUM(`#tcp_sessions`) AS `#tcp_sessions`
                            FROM apn_5min WHERE SAUV_td32 IN ({placeholders}) AND apn LIKE %s
                            GROUP BY SAUV_td32
                        """
                        cursor.execute(sql, (*timestamps, "%tdd%"))
                    else:
                        sql = f"""
                            SELECT SAUV_td32, tcp_dw, udp_dw, icmp_dw, other_dw,
                                   `#tcp_loss_dw`, `#tcp_pkt_dw`,
                                   `#tcp_no_response_to_syn`, `#tcp_block_wp_up`,
                                   `#tcp_synack_core`, Irt_tcp_core_td22,
                                   `#tcp_sessions`
                            FROM whole_5min WHERE SAUV_td32 IN ({placeholders})
                        """
                        cursor.execute(sql, timestamps)

                    # Fetch results for whichever query we executed above
                    results = cursor.fetchall()

                    # Additionally attempt to fetch DNS aggregates (if present) from local dns_5min table
                    dns_dict = {}
                    try:
                        # Google (ip='8.8.8.0/24' OR ip='8.8.4.0/24' OR ip='2001:4860:4860::/128') or All (ip='*')
                        dns_sql = f"""
                            SELECT SAUV_td32,
                                   SUM(qtype_MALFORMED) + SUM(tooshort_src) AS dns_malformed_ul,
                                   SUM(queries_woresp) AS dns_queries_woresp,
                                   SUM(queries) AS dns_queries
                            FROM dns_5min
                            WHERE SAUV_td32 IN ({placeholders}) AND (ip='8.8.8.0/24' OR ip='8.8.4.0/24' OR ip='2001:4860:4860::/128') AND supra='*'
                            GROUP BY SAUV_td32
                        """
                        cursor.execute(dns_sql, timestamps)
                        dns_results = cursor.fetchall()
                        dns_dict = {str(r["SAUV_td32"]): r for r in dns_results}
                    except Exception:
                        dns_dict = {}

            data_dict = {str(row["SAUV_td32"]): row for row in results}
            # Merge DNS rows where available
            for k, v in list(data_dict.items()):
                dns_row = dns_dict.get(k)
                if dns_row:
                    data_dict[k] = {**v, **dns_row}

            default_data = {
                "tcp_dw": None,
                "udp_dw": None,
                "icmp_dw": None,
                "other_dw": None,
                "#tcp_loss_dw": None,
                "#tcp_pkt_dw": None,
                "#tcp_no_response_to_syn": None,
                "#tcp_block_wp_up": None,
                "#tcp_synack_core": None,
                "Irt_tcp_core_td22": None,
                "#tcp_sessions": None,
                # DNS fields
                "dns_malformed_ul": None,
                "dns_queries_woresp": None,
                "dns_queries": None,
            }
            return {str(ts): data_dict.get(str(ts), default_data) for ts in timestamps}
        
        except Exception as e:
            logger.error(f"Error fetching data for {db_key}: {e}")
            return {ts: None for ts in timestamps}
    
    @staticmethod
    def fetch_all_data_concurrent(timestamps: tuple, table_type: str = "whole", exclude_roaming: bool = False) -> dict:
        """Fetch data from all databases synchronously
        Args:
            timestamps: Tuple of timestamps to fetch
            table_type: "whole" or "apn"
            exclude_roaming: If True, exclude roaming databases from the fetch
        """
        db_keys = [db_key for db_key in constants.DB_CONFIGS.keys() if not (exclude_roaming and "roaming" in db_key)]

        results = [
            (db_key, DataCollector.fetch_data_batch(db_key, timestamps, table_type))
            for db_key in db_keys
        ]

        all_data = {}
        for db_key, result in results:
            if isinstance(result, Exception):
                logger.error(f"Error getting data for {db_key}: {result}")
                all_data[db_key] = {ts: None for ts in timestamps}
            else:
                all_data[db_key] = result

        return all_data

    @staticmethod
    def fetch_unified_data_for_processing(timestamps: tuple, alert_timestamps: list[str], config: Dict, checkpoints: dict, current_checkpoint: datetime) -> dict:
        """
        Fetch all data in a single pass and organize it for parallel processing.

        Args:
            timestamps: All timestamps needed (for dashboard)
            alert_timestamps: Timestamps specifically for monitoring/alerts
            config: Configuration dictionary
            checkpoints: Pre-calculated checkpoints with start_str, end_str, etc.
            current_checkpoint: Current checkpoint datetime

        Returns:
            dict with keys:
            - 'dashboard_data': dict containing all_data_dashboard, tdd_data, checkpoints, etc.
            - 'tickets_data': dict containing all_data_monitoring, all_last_update, all_mode_7days_gbps, etc.
        """
        logger.info(f"🔄 Unified data collection: fetching {len(timestamps)} timestamps from all databases")

        default_result = {
            "tcp_dw": 0, "udp_dw": 0, "icmp_dw": 0, "other_dw": 0,
            "tcp_loss_dw": 0, "tcp_pkt_dw": 1, "connection_failed": True,
            "failure_reason": None
        }

        # Get all databases (no exclusion for unified fetch)
        all_db_keys = list(constants.DB_CONFIGS.keys())

        # Fetch data for whole tables (dashboard + monitoring)
        whole_results = [
            (db_key, DataCollector.fetch_data_batch(db_key, timestamps, "whole"))
            for db_key in all_db_keys
        ]

        # Fetch data for apn tables (TDD data)
        apn_results = [
            (db_key, DataCollector.fetch_data_batch(db_key, timestamps, "apn"))
            for db_key in all_db_keys
        ]

        # Process whole data results
        all_data_dashboard = {}
        for db_key, result in whole_results:
            if isinstance(result, Exception):
                logger.error(f"Error getting whole data for {db_key}: {result}")
                all_data_dashboard[db_key] = {ts: None for ts in timestamps}
            else:
                all_data_dashboard[db_key] = result

        # Process apn data results (TDD data)
        tdd_data = {}
        for db_key, result in apn_results:
            if isinstance(result, Exception):
                logger.error(f"Error getting apn data for {db_key}: {result}")
                tdd_data[db_key] = {ts: None for ts in timestamps}
            else:
                tdd_data[db_key] = result

        # Extract monitoring data from dashboard data (exclude roaming databases)
        all_data_monitoring = {}
        all_last_update = {}
        all_mode_7days_gbps = {}

        for db_key, db_config in constants.DB_CONFIGS.items():
            # Skip roaming databases for monitoring
            if "roaming" in db_key:
                continue

            host, db_name = db_config.get('host'), db_config.get('db_name')
            if not host or not db_name:
                logger.warning(f"❌ SKIPPING {db_key}: Missing host ({host}) or db_name ({db_name})")
                continue

            try:
                # Get monitoring data from the unified fetch
                monitoring_data = {ts: all_data_dashboard[db_key].get(ts) for ts in alert_timestamps}

                # Convert to DataFrame format for compatibility
                df_full = pl.DataFrame([{**default_result, "SAUV_td32": str(ts)} for ts in alert_timestamps])

                if monitoring_data:
                    rows = []
                    for ts in alert_timestamps:
                        row_data = monitoring_data.get(ts)
                        if row_data:
                            # Ensure SAUV_td32 is a string (convert from datetime if needed)
                            if 'SAUV_td32' in row_data:
                                # Convert to string if it's a datetime object
                                sauv_value = row_data['SAUV_td32']
                                if isinstance(sauv_value, datetime):
                                    row_data = {**row_data, 'SAUV_td32': sauv_value.strftime("%Y-%m-%d %H:%M:%S")}
                                elif not isinstance(sauv_value, str):
                                    row_data = {**row_data, 'SAUV_td32': str(sauv_value)}
                            else:
                                row_data = {**row_data, 'SAUV_td32': ts}
                            rows.append(row_data)

                    if rows:
                        df_existing = pl.DataFrame(rows)
                        # Ensure SAUV_td32 is string type in DataFrame before join
                        df_existing = df_existing.with_columns(
                            pl.col("SAUV_td32").cast(pl.Utf8).alias("SAUV_td32"),
                            pl.lit(False).alias("connection_failed"),
                            pl.lit(None).alias("failure_reason"),
                        )
                        # Ensure df_full also has SAUV_td32 as string
                        df_full = df_full.with_columns(
                            pl.col("SAUV_td32").cast(pl.Utf8).alias("SAUV_td32")
                        )

                        # Join with default data
                        df_full = df_existing.join(df_full, on="SAUV_td32", how="right")

                        # Select final columns
                        data_cols = [
                            "tcp_dw", "udp_dw", "icmp_dw", "other_dw",
                            "tcp_loss_dw", "tcp_pkt_dw",
                            "connection_failed", "failure_reason", "SAUV_td32"
                        ]
                        df_full = df_full.select(data_cols)

                all_data_monitoring[db_key] = df_full

                # Calculate last_update and mode_7days_gbps (similar to original function)
                connection = DataCollector.get_sync_connection(db_key)
                with connection:
                    with connection.cursor(dictionary=True) as cursor:
                        # Get last update
                        cursor.execute("SHOW TABLES LIKE 'whole_5min'")
                        if cursor.fetchone():
                            cursor.execute("SELECT SAUV_td32 as last_update FROM whole_5min ORDER BY SAUV_td32 DESC LIMIT 1")
                            r = cursor.fetchone()
                            last_update = r["last_update"] if r else None

                            # Calculate mode 7 days gbps
                            latest_alert_ts = max(datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") for ts in alert_timestamps)
                            query = f"""
                                WITH computed AS (
                                    SELECT
                                        (COALESCE(tcp_dw,0) + COALESCE(udp_dw,0) + COALESCE(icmp_dw,0) + COALESCE(other_dw,0))
                                        / 37500000.0 AS traffic_gbps
                                    FROM whole_5min
                                    WHERE `SAUV_td32` BETWEEN %s AND %s
                                ),
                                bins AS (
                                    SELECT
                                        FLOOR(traffic_gbps / 2.5) * 2.5 AS bin_start,
                                        COUNT(*) AS freq
                                    FROM computed
                                    GROUP BY bin_start
                                ),
                                ranked AS (
                                    SELECT
                                        bin_start,
                                        freq,
                                        LAG(freq) OVER (ORDER BY bin_start) AS freq_prev,
                                        LEAD(freq) OVER (ORDER BY bin_start) AS freq_next
                                    FROM bins
                                ),
                                modal AS (
                                    SELECT
                                        bin_start,
                                        freq,
                                        COALESCE(freq_prev, 0) AS freq_prev,
                                        COALESCE(freq_next, 0) AS freq_next
                                    FROM ranked
                                    ORDER BY freq DESC, bin_start
                                    LIMIT 1
                                )
                                SELECT
                                    CASE
                                        WHEN (2*freq - freq_prev - freq_next) = 0
                                            THEN bin_start + 1.25     -- midpoint fallback
                                        ELSE bin_start
                                            + ((freq - freq_prev) / (2*freq - freq_prev - freq_next)) * 2.5
                                    END AS mode_7days_gbps
                                FROM modal;
                            """

                            start_ts = latest_alert_ts - timedelta(days=7)
                            end_ts = latest_alert_ts
                            cursor.execute(query, (start_ts, end_ts))
                            r = cursor.fetchone()
                            mode_7days_gbps = float(r["mode_7days_gbps"]) if r else None
                        else:
                            last_update = None
                            mode_7days_gbps = None

                all_last_update[db_key] = last_update
                all_mode_7days_gbps[db_key] = mode_7days_gbps

            except Exception as e:
                logger.error(f"🚨 DATABASE CONNECTION FAILED for {db_name}: {e}")
                all_data_monitoring[db_key] = pl.DataFrame([{**default_result, "failure_reason": f"Connection error: {e}", "SAUV_td32": str(ts)}
                                                for ts in alert_timestamps])
                all_last_update[db_key] = None
                all_mode_7days_gbps[db_key] = None

        # Organize data for parallel processing
        unified_data = {
            "dashboard_data": {
                "all_data_dashboard": all_data_dashboard,
                "tdd_data": tdd_data,
                "checkpoints": checkpoints,
                "timestamps": timestamps,
                "current_checkpoint": current_checkpoint,
            },
            "tickets_data": {
                "all_data_monitoring": all_data_monitoring,
                "all_last_update": all_last_update,
                "all_mode_7days_gbps": all_mode_7days_gbps,
                "alert_timestamps": alert_timestamps,
                "current_checkpoint": current_checkpoint,
                "config": config,
            }
        }

        return unified_data


# ============================================================================
# DATA PROCESSING METHODS (formerly DataProcessor class)
# ============================================================================

def calculate_vectorized_metrics(all_data: dict[str, pl.DataFrame], alert_timestamps: list[str]) -> dict[str, pl.DataFrame]:
    """
    Calcule les métriques vectorisées pour chaque DB et tous les timestamps fournis.
    
    Renvoie:
        { db_key: DataFrame métriques pour tous les timestamps, ... }
    """
    if not alert_timestamps:
        return {}

    metrics = {}

    for db_key, df in all_data.items():
        # Filtrer et trier selon les timestamps
        df = df.filter(pl.col("SAUV_td32").is_in(alert_timestamps)).sort("SAUV_td32")

        # Calcul vectorisé des métriques principales
        df_metrics = df.with_columns([
            # Traffic total converti en Gbps directement
            ((pl.col("tcp_dw") + pl.col("udp_dw") + pl.col("icmp_dw") + pl.col("other_dw")) * constants.DEBIT_FACTOR).round(2).alias("traffic_gbps"),

            # Loss percentage, gestion division par zéro
            (100 * pl.col("tcp_loss_dw") / pl.when(pl.col("tcp_pkt_dw") > 0).then(pl.col("tcp_pkt_dw")).otherwise(1)).round(3).alias("loss_percentage"),

            # Nettoyage des colonnes
            pl.col("connection_failed").cast(pl.Boolean),
            pl.col("failure_reason").fill_null("").alias("failure_reason")
        ]).select([
            "SAUV_td32", "traffic_gbps", "loss_percentage", "connection_failed", "failure_reason",
            "tcp_dw", "udp_dw", "icmp_dw", "other_dw"
        ])

        metrics[db_key] = df_metrics

    return metrics

