"""
BACKGROUND TASK - Orchestrates data collection, processing, and alert management
Runs every 5 minutes to collect data, calculate metrics, detect alerts, and manage tickets
"""

import threading
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

import polars as pl

from . import constants
from .database_manager import (DataCollector, get_db_datetime,
                               round_to_nearest_five)
from .logger import get_logger
from .ticket_manager import (OrangeAPIClient, TicketManager, load_tickets,
                             save_tickets)
from .utils import calculate_ttr, load_config

logger = get_logger(__name__)


# Global unified data storage with lock protection
_unified_data: Dict = {}
_unified_data_lock = threading.Lock()

# Global state for background monitoring task execution
_monitor_databases_lock = threading.Lock()


# ============================================================================
# UNIFIED DATA COLLECTION AND PROCESSING TASK
# ============================================================================
def run_unified_data_collection_and_processing(config: Dict, orange_api_client: OrangeAPIClient = None):
    """
    Main entry point: Fetch all data once, store in global variable, then launch background processing threads.
    """
    global _unified_data

    if not _monitor_databases_lock.acquire(blocking=False):
        logger.warning("Background task already running, skipping")
        return

    try:
        # Use current time to get the latest data checkpoint
        current_checkpoint = round_to_nearest_five(datetime.now(constants.CAMEROON_TZ))
        datetime_str = current_checkpoint.strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"🚀 Starting unified data collection at {datetime_str}")

        # Calculate alert timestamps (for monitoring)
        alert_timestamps = []
        for i in range(config['traffic_consecutive_drops_count'] + 1):
            t = get_db_datetime(current_checkpoint - timedelta(minutes=5 * i))
            alert_timestamps.append(t.strftime("%Y-%m-%d %H:%M:%S"))
        day_ago = get_db_datetime(current_checkpoint - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        alert_timestamps.append(day_ago)
        alert_timestamps.sort()

        # Calculate dashboard checkpoints
        checkpoints = {
            "5min": {"end": current_checkpoint, "start": current_checkpoint - timedelta(minutes=5)},
            "1day": {"end": current_checkpoint, "start": current_checkpoint - timedelta(days=1)},
            "1week": {"end": current_checkpoint, "start": current_checkpoint - timedelta(weeks=1)},
        }

        # Prepare all timestamps needed
        all_timestamps = set()
        for interval in checkpoints:
            checkpoints[interval]["db_end"] = get_db_datetime(checkpoints[interval]["end"])
            checkpoints[interval]["db_start"] = get_db_datetime(checkpoints[interval]["start"])
            checkpoints[interval]["end_str"] = checkpoints[interval]["db_end"].strftime("%Y-%m-%d %H:%M:%S")
            checkpoints[interval]["start_str"] = checkpoints[interval]["db_start"].strftime("%Y-%m-%d %H:%M:%S")
            all_timestamps.update([checkpoints[interval]["end_str"], checkpoints[interval]["start_str"]])
        all_timestamps.update(alert_timestamps)

        # Step 1: Single unified data collection
        logger.info("📊 [MAIN] Fetching all data in single pass...")
        unified_data = DataCollector.fetch_unified_data_for_processing(
            tuple(all_timestamps), alert_timestamps, config, checkpoints, current_checkpoint
        )

        # Step 2: Store data in global variable with lock protection
        with _unified_data_lock:
            _unified_data = unified_data

        logger.info("✅ [MAIN] Data collection completed and stored globally, launching background processing threads...")

        # Step 3: Launch background processing threads (no waiting)

        dashboard_thread = threading.Thread(
            target=precalculate_dashboard_data,
            name="DashboardProcessor",
            daemon=True  # Daemon thread - will be terminated when main process exits
        )

        monitoring_thread = threading.Thread(
            target=process_monitoring_data,
            args=(orange_api_client,),
            name="MonitoringProcessor",
            daemon=True  # Daemon thread - will be terminated when main process exits
        )

        # Start both background threads
        dashboard_thread.start()
        monitoring_thread.start()

        logger.info("🎯 [MAIN] Background processing threads launched (non-blocking)")

    except Exception as e:
        logger.exception(f"❌ [MAIN] Error in unified data collection: {e}")
    finally:
        _monitor_databases_lock.release()


def detect_alerts_unified(metrics: dict[str, pl.DataFrame]) -> dict[str, dict]:
    """
    Prépare pour chaque DB :
    - traffic_yesterday = valeur la plus ancienne (index 0 après tri)
    - recent_traffic = toutes les autres valeurs (ordre ancien->récent)
    Même logique pour loss_percentage.
    Puis appelle les fonctions de détection avancée.
    """
    alerts: dict[str, dict] = {}

    for db_key, df in metrics.items():
        # sécurité : DataFrame attendu
        if df is None or df.height == 0:
            t_yesterday, t_recent = 0.0, []
            l_yesterday, l_recent = 0.0, []
        else:
            d = df.sort("SAUV_td32")  # ancien -> récent
            t_list = d["traffic_gbps"].to_list()
            l_list = d["loss_percentage"].to_list()
            t_yesterday, t_recent = (t_list[0], t_list[1:]) if t_list else (0.0, [])
            l_yesterday, l_recent = (l_list[0], l_list[1:]) if l_list else (0.0, [])

        # appels aux fonctions existantes (signatures attendues : key, old_value, recent_values)
        traffic_alert, traffic_reason, drop_percentage, traffic_cat = check_traffic_conditions_advanced(
            db_key, t_yesterday, t_recent
        )
        pertes_alert, pertes_reason, pertes_cat = check_pertes_conditions_advanced(
            db_key, l_yesterday, l_recent
        )

        alerts[db_key] = {
            'traffic_alert': traffic_alert,
            'pertes_alert': pertes_alert,
            'traffic_reason': traffic_reason,
            'pertes_reason': pertes_reason,
            'drop_percentage': drop_percentage,
            "traffic_cat": traffic_cat,
            "pertes_cat": pertes_cat
        }

    return alerts


def check_database_freshness(db_name: str, most_recent_timestamp, current_datetime_str) -> Tuple[bool, float]:
    """
    Check if database is more than configured threshold minutes behind current time
    Returns: (is_db_down, minutes_behind)
    """
    
    try:
        config = load_config()
        db_recovery_threshold_minutes = config.get("db_recovery_threshold_minutes", 70)
        # Get current time in Cameroon timezone
        if isinstance(current_datetime_str, str):
            current_time = datetime.strptime(current_datetime_str, "%Y-%m-%d %H:%M:%S")
        else:
            current_time = current_datetime_str
        
        # Parse the database timestamp
        if isinstance(most_recent_timestamp, str):
            db_time = datetime.strptime(most_recent_timestamp, "%Y-%m-%d %H:%M:%S")
        else:
            db_time = most_recent_timestamp
        
        # Ensure database time has timezone info
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=constants.CAMEROON_TZ)

        if db_time.tzinfo is None:
            db_time = db_time.replace(tzinfo=constants.CAMEROON_TZ)
        
        # Calculate time difference in minutes
        time_diff = current_time - db_time
        minutes_behind = time_diff.total_seconds() / 60
        
        # Check if database is more than configured threshold minutes behind
        is_db_down = minutes_behind > db_recovery_threshold_minutes
        
        if is_db_down:
            logger.debug(f"🕐 DB Freshness Check for {db_name}:\n"
                        f"   Current time: {current_datetime_str}\n"
                        f"   DB latest data: {db_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"   Minutes behind: {minutes_behind:.1f}\n"
                        f"   Status: {'🚨 DB DOWN' if is_db_down else '✅ DB OK'} (threshold: {db_recovery_threshold_minutes} min)")

        return is_db_down, minutes_behind

    except Exception as e:
        logger.error(f"Error checking database freshness for {db_name}: {e}")
        return True, 999  # Assume down on error


# ============================================================================#
# Orange API background helpers (run via scheduler)                           #
# ============================================================================#
def monitor_orange_ticket_status(orange_api_client: OrangeAPIClient):
    """
    Monitor open tickets to check if they've been manually closed/restored in Orange API.
    Runs via scheduler.
    """
    try:
        logger.debug(
            f"\n=== Orange Ticket Status Monitor ===\n"
            f"Checking open tickets against Orange API at {datetime.now()}"
        )

        tickets = load_tickets()
        open_tickets = [t for t in tickets if t.get("status") == "open"]

        if not open_tickets:
            logger.debug("No open tickets found to monitor")
            return

        # Filter tickets with valid Orange IDs
        tickets_with_orange_ids = []
        for ticket in open_tickets:
            orange_id = ticket.get('external_api_ticket_id')
            if orange_id and orange_id != 'Unknown' and orange_id != '':
                tickets_with_orange_ids.append(ticket)

        if not tickets_with_orange_ids:
            logger.debug("No open tickets with Orange IDs found")
            return

        logger.debug(f"Found {len(tickets_with_orange_ids)} open tickets with Orange IDs to check")

        tickets_to_close = []

        # Check each ticket's status in Orange API
        for ticket in tickets_with_orange_ids:
            orange_id = ticket['external_api_ticket_id']
            autodo_id = ticket['id']
            
            logger.debug(f"\nChecking AUTODO #{autodo_id} -> Orange #{orange_id}")
            
            # Get Orange ticket status
            status_result = orange_api_client.get_orange_ticket_status(orange_id)

            if status_result["success"]:
                if not status_result["is_inprogress"]:
                    logger.debug(f"⚠ Orange ticket {orange_id} is no longer InProgress - marking for closure")
                    tickets_to_close.append({"ticket": ticket, "orange_status": status_result})
            else:
                logger.error(
                    f"❌ Failed to check Orange ticket {orange_id}: {status_result.get('error', 'Unknown error')}"
                )

        if not tickets_to_close:
            logger.debug("✓ All open tickets are still InProgress in Orange API")
            return
        
        # Close tickets that are no longer InProgress in Orange
        logger.debug(f"\n=== Closing {len(tickets_to_close)} tickets that were manually resolved in Orange ===")
        for item in tickets_to_close:
            ticket = item["ticket"]
            try:
                logger.debug(f"\nClosing AUTODO ticket #{ticket['id']} (Orange #{ticket['external_api_ticket_id']})")
                # Create closure timestamp
                closure_timestamp = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')             
                
                # Update ticket status to closed
                ticket['status'] = 'closed'
                ticket['closure_timestamp'] = closure_timestamp
                
                # Calculate TTR
                creation_timestamp = ticket.get('timestamp', ticket.get('creation_timestamp', closure_timestamp))
                ttr_seconds = calculate_ttr(creation_timestamp, closure_timestamp)
                ticket['ttr_seconds'] = ttr_seconds
                        
                # Save to JSON
                if save_tickets([ticket]):
                    # Update database
                    logger.debug(f"✅ Successfully closed AUTODO ticket #{ticket['id']} (synced from Orange)")
                        
                else:
                    logger.warning(f"❌ Failed to find ticket #{ticket['id']} for closure")
            except Exception as e:
                logger.error(f"❌ Error closing ticket #{ticket['id']}: {str(e)}")

        logger.debug(f"=== Orange Ticket Status Monitor Complete ===\n")

    except Exception as e:
        logger.debug(f"❌ Error in Orange ticket status monitor: {str(e)}")


def retry_tickets_missing_orange_ids(orange_api_client: OrangeAPIClient):
    """
    Find tickets that are missing Orange ticket IDs and retry sending them to Orange API.
    """
    logger.debug("\n=== Checking for tickets missing Orange IDs ===")

    try:
        tickets = load_tickets()
        missing_orange_id_tickets = []
        # Find tickets that are missing external_api_ticket_id
        for ticket in tickets:
            if ticket.get('status') == 'open' and not ticket.get('external_api_ticket_id'):
                missing_orange_id_tickets.append(ticket)

        if not missing_orange_id_tickets:
            logger.debug("✓ All open tickets have Orange IDs")
            return

        logger.debug(f"Found {len(missing_orange_id_tickets)} tickets missing Orange IDs:")
        for ticket in missing_orange_id_tickets:
            logger.debug(f"  - Ticket ID: {ticket['id']}, Type: {ticket['type']}, Database: {ticket['database_name']}")

        # Try to send each missing ticket to Orange API
        for ticket in missing_orange_id_tickets:
            logger.debug(f"\nRetrying Orange API creation for ticket {ticket['id']}...")

            # Use the retry mechanism
            result = orange_api_client.create_trouble_ticket(ticket, retry_count=0, max_retries=3)

            if result.get("success"):
                orange_ticket_id = result.get("orange_ticket_id")
                logger.debug(f"✓ Successfully created Orange ticket {orange_ticket_id} for ticket {ticket['id']}")

                # Update the ticket in the JSON file
                ticket['external_api_ticket_id'] = orange_ticket_id

                # Update the database
                if save_tickets([ticket]):
                    logger.debug(f"✓ Updated database for ticket {ticket['id']}")
            else:
                if result.get("retry_scheduled"):
                    logger.debug(f"⏰ Retry scheduled for ticket {ticket['id']}")
                else:
                    logger.debug(
                        f"❌ Failed to create Orange ticket for {ticket['id']}: {result.get('message') or result.get('error')}"
                    )

        logger.debug("✓ Updated tickets file with new Orange IDs")

    except Exception as e:
        logger.error(f"Error checking tickets missing Orange IDs: {e}")


def check_traffic_conditions_advanced(db_name: str, traffic_yesterday: float, recent_traffic: List[float]):
    """Advanced traffic checking with merged zero-case + instant drop + consecutive drops + day-over-day check."""
    # Load criteria
    criteria = load_config()
    alert_traffic_threshold_percent = criteria['alert_traffic_threshold_percent']
    traffic_consecutive_drops_count = criteria['traffic_consecutive_drops_count']
    traffic_consecutive_drops_min_percent = criteria['traffic_consecutive_drops_min_percent']
    day_over_day_drop_percent = criteria['day_over_day_drop_percent']

    # recent_traffic est fourni oldest->newest; on l'utilise tel quel (dernier élément = now)
    vals = list(recent_traffic) if recent_traffic is not None else []

    def percent_variation(prev_val, curr_val):
        """Retourne la variation en % de la valeur courante par rapport à la précédente.
        (ex: -20% = baisse de 20%, +15% = hausse de 15%)"""
        if prev_val == 0:
            return 0.0 if curr_val == 0 else 100.0
        return ((curr_val - prev_val) / prev_val) * 100.0

    def is_traffic_critical(values):
        """Evaluate the 4 rules (merged zero-case + instant drop + consecutive drops + day-over-day)."""
        # --- 1) Trafic nul ---
        if len(values) >= 1 and values[-1] == 0:
            if len(values) >= 2 and values[-2] == 0:
                reason = "Perte de données persistante (trafic nul sur plusieurs intervalles)"
            else:
                reason = "Trafic tombé à zéro - Ticket critique immédiat"
            return True, reason, 0.0, 1

        # --- 2) Chute instantanée critique ---
        if len(values) >= 2:
            variation = percent_variation(values[-2], values[-1])
            if  variation < 0 and abs(variation) >= alert_traffic_threshold_percent:
                reason = f"Diminution critique instantanée {values[-2]:.1f} → {values[-1]:.1f} ({abs(variation):.1f}% ≥ seuil {alert_traffic_threshold_percent}%)"
                return True, reason, abs(variation), 2


        # --- 3) Diminutions consécutives (1er pas strict ≥ traffic_consecutive_drops_min_percent, suivants ≤ précédent) ---
        if len(values) >= traffic_consecutive_drops_count + 1:
            # fenêtre oldest -> newest
            analysis_values = values[-(traffic_consecutive_drops_count + 1):]
            n_steps = len(analysis_values) - 1  # = traffic_consecutive_drops_count

            # 1) vérifier que le premier pas est une baisse stricte
            first_is_strict_drop = analysis_values[1] < analysis_values[0]

            # 2) vérifier que les pas suivants (s'il y en a) sont soit baisses soit constantes (non-increasing)
            following_non_increasing = True
            if n_steps > 1:
                for i in range(1, n_steps):
                    if analysis_values[i+1] > analysis_values[i]:
                        following_non_increasing = False
                        break

            if first_is_strict_drop and following_non_increasing:
                # calculer variations individuelles (en %)
                individual_var = [percent_variation(analysis_values[i], analysis_values[i+1]) for i in range(n_steps)]
                first_drop_pct = abs(individual_var[0]) if individual_var else 0.0

                # première baisse doit être >= first_drop_min_percent
                if first_drop_pct >= traffic_consecutive_drops_min_percent:
                    drops_str = ' → '.join([f"{v:.1f}" for v in analysis_values])
                    drops_percent_str = ', '.join([f"{abs(d):.1f}%" for d in individual_var])
                    reason = (f"{traffic_consecutive_drops_count} diminutions consécutives détectées "
                            f"(première baisse: {first_drop_pct:.1f}% ≥ {traffic_consecutive_drops_min_percent:.1f}%) "
                            f"({drops_str} Gb/s) - Détail: [{drops_percent_str}]")
                    # On renvoie la première baisse (en %) comme métrique
                    return True, reason, first_drop_pct, 3

        # --- 4) Comparaison jour/jour ---
        # if traffic_yesterday is not None and len(values) >= 1:
        #     day_variation = percent_variation(traffic_yesterday, values[-1])
        #     if day_variation < 0 and abs(day_variation) >= day_over_day_drop_percent:
        #         reason = (f"Baisse journalière {abs(day_variation):.1f}% ≥ {day_over_day_drop_percent:.1f}% "
        #                 f"(hier-Auj: {traffic_yesterday:.1f} → {values[-1]:.1f} Gb/s)")
        #         return True, reason, abs(day_variation), 4

        # nothing triggered
        return False, "", None, 0

    # --- Main ---
    try:
        is_critical, reason, computed_drop, cat = is_traffic_critical(vals)
        if is_critical:
            overall_drop = abs(computed_drop or 0.0)
            logger.debug(f"[{db_name}] TRAFFIC TICKET WILL BE CREATED: {reason} (overall_drop={overall_drop:.1f}%)")
            return True, reason, overall_drop, cat
    except Exception as e:
        logger.warning(f"Error lors de la detection des alertes de traffic {db_name}: {e}")

    return False, "Traffic conditions normal", 0.0, 0


def check_pertes_conditions_advanced(db_name: str, pertes_yesterday: float, recent_pertes: List[float]):
    """Advanced pertes condition checking using Summary Table logic."""
    # Charger les critères
    criteria = load_config()
    alert_pertes_threshold = criteria['alert_pertes_threshold']
    pertes_consecutive_count = criteria.get('pertes_consecutive_count', 1)

    # recent_pertes attendu oldest->newest
    vals = list(recent_pertes) if recent_pertes is not None else []
    if not vals:
        return False, "", 0

    def is_pertes_critical(pertes_values: List[float]):
        """Vérifie si les pertes dépassent le seuil configuré."""
        try:
            # On prend les pertes_consecutive_count dernières valeurs (ou moins si pas assez)
            recent = pertes_values[-pertes_consecutive_count:] if len(pertes_values) >= pertes_consecutive_count else pertes_values
            critical_values = all([p >= alert_pertes_threshold for p in recent])

            if critical_values:
                max_loss = max(recent)
                return True, f"Pertes critiques détectées: {max_loss:.2f}% ≥ seuil {alert_pertes_threshold}%", 1

        except Exception as e:
            logger.warning(f"Error lors de la detection des alertes de traffic {db_name}: {e}")

        return False, "", 0

    return is_pertes_critical(vals)



def _process_data_for_interval(
    data_dict: dict[str, dict[str, dict]],
    interval: str,
    checkpoints: dict,
    recap: dict,
    tdd_recap: dict,
    roaming_recap: dict,
    inaccessible_dbs: list,
    tdd_data: dict,
):
    """Process data for a specific time interval"""
    protocols = ["tcp_dw", "udp_dw", "icmp_dw", "other_dw"]

    for db_key, db_data in data_dict.items():
        start_str = checkpoints[interval]["start_str"]
        end_str = checkpoints[interval]["end_str"]
        start_record = db_data.get(start_str)
        end_record = db_data.get(end_str)

        def record_is_empty(rec):
            if isinstance(rec, dict):
                return all(v is None or (isinstance(v, str) and v.strip() == "") for v in rec.values())
            return False

        if start_record is None or end_record is None:
            inaccessible_dbs.append(db_key)
            logger.warning(
                f"Données manquantes pour {db_key} : {start_record} -> {end_record}"
            )
            continue

        # helper pour calculer les métriques d'un enregistrement (réduit répétitions)
        def _compute_metrics(rec):
            if record_is_empty(rec):
                return {
                    "total": None,
                    "pertes": None,
                    "tcp_no_response": None,
                    "tcp_block_wp_up": None,
                    "latence": None,
                    "dns_malformed": None,
                    "dns_no_response": None,
                    "tcp_loss_dw_raw": 0,
                    "tcp_pkt_dw_raw": 0,
                    "tcp_no_response_raw": 0,
                    "tcp_block_wp_up_raw": 0,
                    "tcp_sessions_raw": 0,
                    "dns_malformed_raw": 0,
                    "dns_queries_raw": 0,
                    "dns_queries_woresp_raw": 0,
                }

            # Calculs métriques
            total = sum(float(rec.get(p, 0)) for p in protocols) * constants.DEBIT_FACTOR
            pertes_val = 100 * float(rec.get("#tcp_loss_dw", 0)) / max(
                1, float(rec.get("#tcp_pkt_dw", 1))
            )

            tcp_sessions = float(rec.get("#tcp_sessions", 0))
            tcp_no_response = (
                float(rec.get("#tcp_no_response_to_syn", 0)) / max(1, tcp_sessions)
            ) * 100
            tcp_block_wp_up = (
                float(rec.get("#tcp_block_wp_up", 0)) / max(1, tcp_sessions)
            ) * 100

            latence = (
                (
                    float(rec.get("Irt_tcp_core_td22", 0))
                    / float(rec.get("#tcp_synack_core", 0))
                )
                if float(rec.get("#tcp_synack_core", 0)) > 0
                else 0
            )

            # DNS metrics
            dns_malformed_raw = float(rec.get("dns_malformed_ul", 0) or 0)
            dns_queries_raw = float(rec.get("dns_queries", 0) or 0)
            dns_queries_woresp_raw = float(rec.get("dns_queries_woresp", 0) or 0)

            dns_malformed = (
                (dns_malformed_raw / max(1, dns_queries_raw)) * 100
                if dns_queries_raw > 0
                else 0
            )
            dns_no_response = (
                (dns_queries_woresp_raw / max(1, dns_queries_raw)) * 100
                if dns_queries_raw > 0
                else 0
            )

            return {
                "total": total,
                "pertes": pertes_val,
                "tcp_no_response": tcp_no_response,
                "tcp_block_wp_up": tcp_block_wp_up,
                "latence": latence,
                "dns_malformed": dns_malformed,
                "dns_no_response": dns_no_response,
                "tcp_loss_dw_raw": float(rec.get("#tcp_loss_dw", 0)),
                "tcp_pkt_dw_raw": float(rec.get("#tcp_pkt_dw", 0)),
                "tcp_no_response_raw": float(rec.get("#tcp_no_response_to_syn", 0)),
                "tcp_block_wp_up_raw": float(rec.get("#tcp_block_wp_up", 0)),
                "tcp_sessions_raw": float(rec.get("#tcp_sessions", 0)),
                "dns_malformed_raw": dns_malformed_raw,
                "dns_queries_raw": dns_queries_raw,
                "dns_queries_woresp_raw": dns_queries_woresp_raw,
            }

        metrics_start = _compute_metrics(start_record)
        metrics_end = _compute_metrics(end_record)

        # Calculs métriques (utilisation des résultats du helper)
        total_start = metrics_start["total"]
        total_end = metrics_end["total"]
        pertes_start = metrics_start["pertes"]
        pertes_end = metrics_end["pertes"]

        # Nouvelles métriques TCP
        tcp_no_response_start = metrics_start["tcp_no_response"]
        tcp_no_response_end = metrics_end["tcp_no_response"]
        tcp_block_wp_up_start = metrics_start["tcp_block_wp_up"]
        tcp_block_wp_up_end = metrics_end["tcp_block_wp_up"]
        latence_start = metrics_start["latence"]
        latence_end = metrics_end["latence"]

        # Ajout des compteurs de paquets bruts pour le calcul de la formule des pertes totales
        tcp_loss_dw_start = metrics_start["tcp_loss_dw_raw"]
        tcp_pkt_dw_start = metrics_start["tcp_pkt_dw_raw"]
        tcp_loss_dw_end = metrics_end["tcp_loss_dw_raw"]
        tcp_pkt_dw_end = metrics_end["tcp_pkt_dw_raw"]

        tcp_no_response_raw_start = metrics_start["tcp_no_response_raw"]
        tcp_no_response_raw_end = metrics_end["tcp_no_response_raw"]
        tcp_block_wp_up_raw_start = metrics_start["tcp_block_wp_up_raw"]
        tcp_block_wp_up_raw_end = metrics_end["tcp_block_wp_up_raw"]
        tcp_sessions_raw_start = metrics_start["tcp_sessions_raw"]
        tcp_sessions_raw_end = metrics_end["tcp_sessions_raw"]

        # taux_total = (
        #     ((total_start - total_end) / total_end * 100) if total_end != 0 else 0
        # )
        taux_total = (total_start if total_start is not None else 0) - (
            total_end if total_end is not None else 0
        )

        # taux_pertes = (
        #     ((pertes_start - pertes_end) / pertes_end * 100) if pertes_end != 0 else 0
        # )
        taux_pertes = (pertes_start if pertes_start is not None else 0) - (
            pertes_end if pertes_end is not None else 0
        )

        taux_tcp_no_response = (
            tcp_no_response_start if tcp_no_response_start is not None else 0
        ) - (tcp_no_response_end if tcp_no_response_end is not None else 0)

        taux_tcp_block_wp_up = (
            tcp_block_wp_up_start if tcp_block_wp_up_start is not None else 0
        ) - (tcp_block_wp_up_end if tcp_block_wp_up_end is not None else 0)

        taux_latence = (latence_start if latence_start is not None else 0) - (
            latence_end if latence_end is not None else 0
        )

        # Icones et couleurs
        total_start_for_trend = total_start if total_start is not None else 0
        total_end_for_trend = total_end if total_end is not None else 0
        pertes_start_for_trend = pertes_start if pertes_start is not None else 0
        pertes_end_for_trend = pertes_end if pertes_end is not None else 0

        icon_total, color_total = _get_trend_icon_and_color(
            "traffic", total_end_for_trend, total_start_for_trend
        )
        icon_pertes, color_pertes = _get_trend_icon_and_color(
            "pertes", pertes_end_for_trend, pertes_start_for_trend
        )

        # Tendances pour nouvelles métriques TCP
        tcp_no_response_start_for_trend = (
            tcp_no_response_start if tcp_no_response_start is not None else 0
        )
        tcp_no_response_end_for_trend = (
            tcp_no_response_end if tcp_no_response_end is not None else 0
        )
        tcp_block_wp_up_start_for_trend = (
            tcp_block_wp_up_start if tcp_block_wp_up_start is not None else 0
        )
        tcp_block_wp_up_end_for_trend = (
            tcp_block_wp_up_end if tcp_block_wp_up_end is not None else 0
        )
        latence_start_for_trend = latence_start if latence_start is not None else 0
        latence_end_for_trend = latence_end if latence_end is not None else 0

        icon_tcp_no_response, color_tcp_no_response = _get_trend_icon_and_color(
            "tcp_no_response",
            tcp_no_response_end_for_trend,
            tcp_no_response_start_for_trend,
        )
        icon_tcp_block_wp_up, color_tcp_block_wp_up = _get_trend_icon_and_color(
            "tcp_block_wp_up",
            tcp_block_wp_up_end_for_trend,
            tcp_block_wp_up_start_for_trend,
        )
        icon_latence, color_latence = _get_trend_icon_and_color(
            "latence", latence_end_for_trend, latence_start_for_trend
        )

        # DNS trends
        dns_malformed_start_for_trend = (
            metrics_start.get("dns_malformed") if metrics_start.get("dns_malformed") is not None else 0
        )
        dns_malformed_end_for_trend = (
            metrics_end.get("dns_malformed") if metrics_end.get("dns_malformed") is not None else 0
        )
        dns_no_response_start_for_trend = (
            metrics_start.get("dns_no_response") if metrics_start.get("dns_no_response") is not None else 0
        )
        dns_no_response_end_for_trend = (
            metrics_end.get("dns_no_response") if metrics_end.get("dns_no_response") is not None else 0
        )

        icon_dns_malformed, color_dns_malformed = _get_trend_icon_and_color(
            "dns_malformed", dns_malformed_end_for_trend, dns_malformed_start_for_trend
        )
        icon_dns_no_response, color_dns_no_response = _get_trend_icon_and_color(
            "dns_no_response", dns_no_response_end_for_trend, dns_no_response_start_for_trend
        )

        # --- Ajout du lien diagnet --- (Mapping db_key -> c_param)
        db_host = constants.DB_CONFIGS.get(db_key, {}).get("host", "#")
        c_param = constants.DB_KEY_TO_CPARAM.get(db_key, "#")

        link_t = constants.EXNET_TRAFFIC_LINK.format(db_host=db_host, c_param=c_param)
        link_l = constants.EXNET_LOSS_LINK.format(db_host=db_host, c_param=c_param)
        link_tcp_service_access = constants.EXNET_TCP_SERVICE_ACCESS_LINK.format(db_host=db_host, c_param=c_param)
        link_tcp_tts = constants.EXNET_TCP_TTS_LINK.format(db_host=db_host, c_param=c_param)
        link_dns = constants.EXNET_DNS_LINK.format(db_host=db_host, c_param=c_param)

        data_entry = {
            "total": {
                "start": total_start,
                "end": total_end,
                "taux": taux_total,
                "trend_icon": icon_total,
                "trend_color": color_total,
            },
            "pertes": {
                "start": pertes_start,
                "end": pertes_end,
                "tcp_loss_dw_start": tcp_loss_dw_start,
                "tcp_pkt_dw_start": tcp_pkt_dw_start,
                "tcp_loss_dw_end": tcp_loss_dw_end,
                "tcp_pkt_dw_end": tcp_pkt_dw_end,
                "taux": taux_pertes,
                "trend_icon": icon_pertes,
                "trend_color": color_pertes,
            },
            "tcp_metrics": {
                "tcp_no_response": {
                    "start": tcp_no_response_start,
                    "end": tcp_no_response_end,
                    "taux": taux_tcp_no_response,
                    "tcp_no_response_start": tcp_no_response_raw_start,
                    "tcp_no_response_end": tcp_no_response_raw_end,
                    "trend_icon": icon_tcp_no_response,
                    "trend_color": color_tcp_no_response,
                },
                "tcp_block_wp_up": {
                    "start": tcp_block_wp_up_start,
                    "end": tcp_block_wp_up_end,
                    "taux": taux_tcp_block_wp_up,
                    "tcp_block_wp_up_start": tcp_block_wp_up_raw_start,
                    "tcp_block_wp_up_end": tcp_block_wp_up_raw_end,
                    "trend_icon": icon_tcp_block_wp_up,
                    "trend_color": color_tcp_block_wp_up,
                },
                "latence": {
                    "start": latence_start,
                    "end": latence_end,
                    "taux": taux_latence,
                    "trend_icon": icon_latence,
                    "trend_color": color_latence,
                },
                "tcp_sessions": {"start": tcp_sessions_raw_start, "end": tcp_sessions_raw_end},
            },
            "dns_metrics": {
                "dns_malformed": {
                    "start": metrics_start.get("dns_malformed"),
                    "end": metrics_end.get("dns_malformed"),
                    "taux": (metrics_start.get("dns_malformed") or 0) - (metrics_end.get("dns_malformed") or 0),
                    "dns_malformed_start": metrics_start.get("dns_malformed_raw"),
                    "dns_malformed_end": metrics_end.get("dns_malformed_raw"),
                    "queries_start": metrics_start.get("dns_queries_raw"),
                    "queries_end": metrics_end.get("dns_queries_raw"),
                    "trend_icon": icon_dns_malformed,
                    "trend_color": color_dns_malformed,
                },
                "dns_no_response": {
                    "start": metrics_start.get("dns_no_response"),
                    "end": metrics_end.get("dns_no_response"),
                    "taux": (metrics_start.get("dns_no_response") or 0) - (metrics_end.get("dns_no_response") or 0),
                    "dns_no_response_start": metrics_start.get("dns_queries_woresp_raw"),
                    "dns_no_response_end": metrics_end.get("dns_queries_woresp_raw"),
                    "queries_start": metrics_start.get("dns_queries_raw"),
                    "queries_end": metrics_end.get("dns_queries_raw"),
                    "trend_icon": icon_dns_no_response,
                    "trend_color": color_dns_no_response,
                },
            },
            "link": {
                "total": link_t,
                "pertes": link_l,
                "tcp_service_access": link_tcp_service_access,
                "tcp_tts": link_tcp_tts,
                "dns": link_dns,
            },
        }

        # Assignation selon le type
        if "roaming" in db_key:
            roaming_recap[interval][db_key] = data_entry
        else:
            recap[interval][db_key] = data_entry

        # Traitement TDD pour les bases utilisateur
        if db_key in constants.LABELS["utilisateur"]:
            tdd_start = tdd_data.get(db_key, {}).get(start_str)
            tdd_end = tdd_data.get(db_key, {}).get(end_str)
            if tdd_start and tdd_end:
                # Calculs métriques
                tdd_total_start, tdd_total_end, tdd_pertes_start, tdd_pertes_end = (
                    None,
                    None,
                    None,
                    None,
                )
                tdd_taux_total, tdd_taux_pertes = None, None

                # Utilisation du helper pour TDD également
                tdd_metrics_start = _compute_metrics(tdd_start)
                tdd_metrics_end = _compute_metrics(tdd_end)

                tdd_total_start = tdd_metrics_start["total"]
                tdd_total_end = tdd_metrics_end["total"]
                tdd_pertes_start = tdd_metrics_start["pertes"]
                tdd_pertes_end = tdd_metrics_end["pertes"]

                # Ajout des compteurs de paquets bruts pour le calcul de la moyenne pondérée des pertes TDD
                tdd_pkt_count_start = tdd_metrics_start["tcp_pkt_dw_raw"]
                tdd_pkt_count_end = tdd_metrics_end["tcp_pkt_dw_raw"]

                # tdd_taux_total = (
                #     ((tdd_total_start - tdd_total_end) / tdd_total_end * 100)
                #     if tdd_total_end != 0
                #     else 0
                # )
                tdd_taux_total = (
                    tdd_total_start if tdd_total_start is not None else 0
                ) - (tdd_total_end if tdd_total_end is not None else 0)

                # tdd_taux_pertes = (
                #     ((tdd_pertes_start - tdd_pertes_end) / tdd_pertes_end * 100)
                #     if tdd_pertes_end != 0
                #     else 0
                # )
                tdd_taux_pertes = (
                    tdd_pertes_start if tdd_pertes_start is not None else 0
                ) - (tdd_pertes_end if tdd_pertes_end is not None else 0)

                # Icones et couleurs
                tdd_total_start_for_trend = (
                    tdd_total_start if tdd_total_start is not None else 0
                )
                tdd_total_end_for_trend = (
                    tdd_total_end if tdd_total_end is not None else 0
                )
                tdd_pertes_start_for_trend = (
                    tdd_pertes_start if tdd_pertes_start is not None else 0
                )
                tdd_pertes_end_for_trend = (
                    tdd_pertes_end if tdd_pertes_end is not None else 0
                )

                icon_tdd_total, color_tdd_total = _get_trend_icon_and_color(
                    "traffic", tdd_total_end_for_trend, tdd_total_start_for_trend
                )
                icon_tdd_pertes, color_tdd_pertes = _get_trend_icon_and_color(
                    "pertes", tdd_pertes_end_for_trend, tdd_pertes_start_for_trend
                )
                # Ajout du lien pour TDD
                tdd_t_link = constants.EXNET_TRAFFIC_LINK.format(db_host=db_host, c_param="SGi")
                tdd_l_link = constants.EXNET_LOSS_LINK.format(db_host=db_host, c_param="SGi")
                tdd_recap[interval][db_key] = {
                    "total": {
                        "start": tdd_total_start,
                        "end": tdd_total_end,
                        "taux": tdd_taux_total,
                        "trend_icon": icon_tdd_total,
                        "trend_color": color_tdd_total,
                    },
                    "pertes": {
                        "start": tdd_pertes_start,
                        "end": tdd_pertes_end,
                        "pkt_start": tdd_pkt_count_start,
                        "pkt_end": tdd_pkt_count_end,
                        "taux": tdd_taux_pertes,
                        "trend_icon": icon_tdd_pertes,
                        "trend_color": color_tdd_pertes,
                    },
                    "link": {"total": tdd_t_link, "pertes": tdd_l_link},
                }
            else:
                tdd_recap[interval][db_key] = {
                    "total": {
                        "start": "N/A",
                        "end": "N/A",
                        "taux": "N/A",
                        "trend_icon": "↔",
                        "trend_color": "neutral",
                    },
                    "pertes": {
                        "start": "N/A",
                        "end": "N/A",
                        "taux": "N/A",
                        "trend_icon": "↔",
                        "trend_color": "neutral",
                    },
                    "link": {"total": None, "pertes": None},
                }


def _calculate_totals_optimized(
    data_dict: dict[str, dict[str, dict]], label_dict: dict[str, dict]
) -> dict[str, str]:
    """Calcule les totaux pour toutes les bases de données de manière optimisée"""
    totals = {
        "traffic_start": 0,
        "traffic_end": 0,
        "taux_traffic": 0,
        "tcp_loss_dw_start": 0,
        "tcp_pkt_dw_start": 0,
        "tcp_loss_dw_end": 0,
        "tcp_pkt_dw_end": 0,
        "taux_pertes": 0,
        "tcp_no_response_start": 0,
        "tcp_no_response_end": 0,
        "tcp_block_wp_up_start": 0,
        "tcp_block_wp_up_end": 0,
        "tcp_sessions_start": 0,
        "tcp_sessions_end": 0,
        "taux_tcp_no_response": 0,
        "taux_tcp_block_wp_up": 0,
        "latence_start": 0,
        "latence_end": 0,
        "taux_latence": 0,
        # DNS aggregates (raw counts and computed percents)
        "dns_malformed_raw_start": 0,
        "dns_malformed_raw_end": 0,
        "dns_queries_start": 0,
        "dns_queries_end": 0,
        "dns_queries_woresp_start": 0,
        "dns_queries_woresp_end": 0,
        "dns_malformed_start": 0,
        "dns_malformed_end": 0,
        "dns_no_response_start": 0,
        "dns_no_response_end": 0,
        "taux_dns_malformed": 0,
        "taux_dns_no_response": 0,
    }

    for db_key in label_dict.keys():
        if db_key not in data_dict:
            continue

        info = data_dict[db_key]
        total_info = info.get("total", {})
        pertes_info = info.get("pertes", {})
        tcp_metrics = info.get("tcp_metrics", {})

        # --- Traffic ---
        if isinstance(total_info.get("start"), (int, float)):
            totals["traffic_start"] += total_info.get("start", 0) or 0
            totals["traffic_end"] += total_info.get("end", 0) or 0

        # --- Pertes raw ---
        if isinstance(pertes_info.get("start"), (int, float)):
            totals["tcp_loss_dw_start"] += pertes_info.get("tcp_loss_dw_start", 0) or 0
            totals["tcp_pkt_dw_start"] += pertes_info.get("tcp_pkt_dw_start", 0) or 0
            totals["tcp_loss_dw_end"] += pertes_info.get("tcp_loss_dw_end", 0) or 0
            totals["tcp_pkt_dw_end"] += pertes_info.get("tcp_pkt_dw_end", 0) or 0

        # --- TCP sessions ---
        tcp_sessions_info = tcp_metrics.get("tcp_sessions", {})
        totals["tcp_sessions_start"] += tcp_sessions_info.get("start", 0) or 0
        totals["tcp_sessions_end"] += tcp_sessions_info.get("end", 0) or 0

        # --- TCP no response ---
        tcp_no_response_info = tcp_metrics.get("tcp_no_response", {})
        if isinstance(tcp_no_response_info.get("start"), (int, float)):
            totals["tcp_no_response_start"] += tcp_no_response_info.get("start", 0) or 0
            totals["tcp_no_response_end"] += tcp_no_response_info.get("end", 0) or 0

        # --- TCP block wp up ---
        tcp_block_wp_up_info = tcp_metrics.get("tcp_block_wp_up", {})
        if isinstance(tcp_block_wp_up_info.get("start"), (int, float)):
            totals["tcp_block_wp_up_start"] += tcp_block_wp_up_info.get("start", 0) or 0
            totals["tcp_block_wp_up_end"] += tcp_block_wp_up_info.get("end", 0) or 0

        # --- Latence ---
        latence_info = tcp_metrics.get("latence", {})
        if isinstance(latence_info.get("start"), (int, float)):
            totals["latence_start"] += latence_info.get("start", 0) or 0
            totals["latence_end"] += latence_info.get("end", 0) or 0

        # --- DNS metrics (aggregate raw counts for accurate percentage calculation) ---
        dns_metrics = info.get("dns_metrics", {})
        dns_malformed_info = dns_metrics.get("dns_malformed", {})
        dns_no_response_info = dns_metrics.get("dns_no_response", {})

        # raw malformed counts
        totals["dns_malformed_raw_start"] += (
            dns_malformed_info.get("dns_malformed_start", 0) or 0
        )
        totals["dns_malformed_raw_end"] += (
            dns_malformed_info.get("dns_malformed_end", 0) or 0
        )

        # queries counts
        totals["dns_queries_start"] += dns_malformed_info.get("queries_start", 0) or 0
        totals["dns_queries_end"] += dns_malformed_info.get("queries_end", 0) or 0

        # queries wo response counts
        totals["dns_queries_woresp_start"] += (
            dns_no_response_info.get("dns_no_response_start", 0) or 0
        )
        totals["dns_queries_woresp_end"] += (
            dns_no_response_info.get("dns_no_response_end", 0) or 0
        )

    # --- Calculs finaux ---
    totals["pertes_start"] = (
        totals["tcp_loss_dw_start"] / totals["tcp_pkt_dw_start"] * 100
        if totals["tcp_pkt_dw_start"] > 0
        else 0
    )
    totals["pertes_end"] = (
        totals["tcp_loss_dw_end"] / totals["tcp_pkt_dw_end"] * 100
        if totals["tcp_pkt_dw_end"] > 0
        else 0
    )

    totals["taux_traffic"] = totals["traffic_end"] - totals["traffic_start"]
    totals["taux_pertes"] = totals["pertes_end"] - totals["pertes_start"]

    # DNS percent calculations (weighted by queries)
    totals["dns_malformed_start"] = (
        totals["dns_malformed_raw_start"] / totals["dns_queries_start"] * 100
        if totals["dns_queries_start"] > 0
        else 0
    )
    totals["dns_malformed_end"] = (
        totals["dns_malformed_raw_end"] / totals["dns_queries_end"] * 100
        if totals["dns_queries_end"] > 0
        else 0
    )

    totals["dns_no_response_start"] = (
        totals["dns_queries_woresp_start"] / totals["dns_queries_start"] * 100
        if totals["dns_queries_start"] > 0
        else 0
    )
    totals["dns_no_response_end"] = (
        totals["dns_queries_woresp_end"] / totals["dns_queries_end"] * 100
        if totals["dns_queries_end"] > 0
        else 0
    )

    totals["taux_dns_malformed"] = (
        totals["dns_malformed_end"] - totals["dns_malformed_start"]
    )
    totals["taux_dns_no_response"] = (
        totals["dns_no_response_end"] - totals["dns_no_response_start"]
    )

    if totals["tcp_sessions_start"] > 0:
        totals["tcp_no_response_start"] = (
            100 * totals["tcp_no_response_start"] / totals["tcp_sessions_start"]
        )
        totals["tcp_block_wp_up_start"] = (
            100 * totals["tcp_block_wp_up_start"] / totals["tcp_sessions_start"]
        )

    if totals["tcp_sessions_end"] > 0:
        totals["tcp_no_response_end"] = (
            100 * totals["tcp_no_response_end"] / totals["tcp_sessions_end"]
        )
        totals["tcp_block_wp_up_end"] = (
            100 * totals["tcp_block_wp_up_end"] / totals["tcp_sessions_end"]
        )

    totals["taux_tcp_no_response"] = (
        totals["tcp_no_response_end"] - totals["tcp_no_response_start"]
    )
    totals["taux_tcp_block_wp_up"] = (
        totals["tcp_block_wp_up_end"] - totals["tcp_block_wp_up_start"]
    )

    # --- Moyenne latence ---
    latence_count = sum(
        1
        for db_key in label_dict
        if db_key in data_dict
        and data_dict[db_key].get("tcp_metrics", {}).get("latence")
    )
    if latence_count > 0:
        totals["latence_start"] /= latence_count
        totals["latence_end"] /= latence_count
        totals["taux_latence"] = totals["latence_end"] - totals["latence_start"]

    return {k: f"{v:.2f}" for k, v in totals.items()}


def _get_trend_icon_and_color(type_, now, prev, prev_seq=None):
    config = load_config()
    icon = "↔"
    color = "neutral"

    # Petite fonction utilitaire
    def percent_change(curr, prev):
        if prev == 0:
            return 0
        return ((curr - prev) / prev) * 100

    # Variation principale
    diff_percent = percent_change(now, prev)

    # === TRAFFIC ===
    if type_ == "traffic":
        is_consecutive_drops = False

        if prev_seq and len(prev_seq) >= config["traffic_consecutive_drops_count"] - 1:
            # Construire la séquence complète : [prev] + prev_seq + [now]
            full_seq = [prev] + prev_seq + [now]

            # Calculer les variations consécutives
            percentage_drops = [
                percent_change(full_seq[i + 1], full_seq[i])
                for i in range(len(full_seq) - 1)
            ]
            
            # # Vérifier si toutes les dernières n baisses dépassent le seuil
            # is_consecutive_drops = all(
            #     (change < 0 and abs(change) >= config['traffic_consecutive_drops_min_percent'])
            #     for change in percentage_drops[-config['traffic_consecutive_drops_count']:]
            # )

            # 1) Vérifier qu'il s'agit bien de N baisses successives
            required = config["traffic_consecutive_drops_count"]
            recent_changes = percentage_drops[-required:]
            are_successive_drops = all(change < 0 for change in recent_changes)

            # 2) Appliquer le critère sur la baisse cumulée sur N pas
            #    Baisse cumulée = pourcentage entre la valeur de début et de fin de la fenêtre
            start_val = full_seq[-(required + 1)]
            end_val = full_seq[-1]
            cumulative_drop_percent = -percent_change(
                end_val, start_val
            )  # positif si baisse cumulée

            is_consecutive_drops = are_successive_drops and (
                cumulative_drop_percent
                >= config["traffic_consecutive_drops_min_percent"]
            )

        # Coloration
        if (
            diff_percent < 0
            and abs(diff_percent) >= config["alert_traffic_threshold_percent"]
        ):
            color = "down"
        elif is_consecutive_drops:
            color = "down"
        elif (
            diff_percent > 0 and diff_percent >= config.get("traffic_up_threshold_percent", 10)
        ):
            color = "up"
        else:
            color = "neutral"

        # Détermination de l'icône
        if diff_percent > 0:
            icon = "↑"
        elif diff_percent < 0:
            icon = "↓"

    # === PERTES ===
    elif type_ == "pertes":
        diff_abs = now - prev  # variation absolue

        # Icone (hausse/baisse absolue)
        if diff_abs > 0:
            icon = "↑"
        elif diff_abs < 0:
            icon = "↓"

        # Couleur
        if now > config["alert_pertes_threshold"]:
            color = "down"  # pertes au-dessus du seuil
        elif now <= config["alert_pertes_threshold"] and diff_abs < 0:
            color = "up"  # pertes en baisse et sous le seuil
        else:
            color = "neutral"

    # === TCP NO RESPONSE ===
    elif type_ == "tcp_no_response":
        diff_abs = now - prev  # variation absolue

        # Icone (hausse/baisse absolue)
        if diff_abs > 0:
            icon = "↑"
        elif diff_abs < 0:
            icon = "↓"

        # Couleur : rouge si valeur actuelle dépasse le seuil
        if now > config.get("alert_tcp_no_response_threshold", 100):
            color = "down"
        else:
            color = "neutral"

    # === TCP BLOCK UP ===
    elif type_ == "tcp_block_wp_up":
        diff_abs = now - prev  # variation absolue

        # Icone (hausse/baisse absolue)
        if diff_abs > 0:
            icon = "↑"
        elif diff_abs < 0:
            icon = "↓"

        # Couleur : rouge si valeur actuelle dépasse le seuil
        if now > config.get("alert_tcp_block_wp_up_threshold", 100):
            color = "down"
        else:
            color = "neutral"

    # === LATENCE ===
    elif type_ == "latence":
        diff_abs = now - prev  # variation absolue

        # Icone (hausse/baisse absolue)
        if diff_abs > 0:
            icon = "↑"
        elif diff_abs < 0:
            icon = "↓"

        # Couleur : rouge si valeur actuelle dépasse le seuil (en ms)
        if now > config.get("alert_latence_threshold_ms", 100):
            color = "down"
        else:
            color = "neutral"

    # === DNS MALFORMED ===
    elif type_ == "dns_malformed":
        diff_abs = now - prev
        if diff_abs > 0:
            icon = "↑"
        elif diff_abs < 0:
            icon = "↓"

        # Use the pertes threshold as a proxy for DNS malformed
        if now > config.get("alert_pertes_threshold", 3.0):
            color = "down"
        else:
            color = "neutral"

    # === DNS NO RESPONSE ===
    elif type_ == "dns_no_response":
        diff_abs = now - prev
        if diff_abs > 0:
            icon = "↑"
        elif diff_abs < 0:
            icon = "↓"

        # Use the tcp no response threshold
        if now > config.get("alert_tcp_no_response_threshold", 100):
            color = "down"
        else:
            color = "neutral"

    return icon, color


# ============================================================================
# BACKGROUND PROCESSING FUNCTIONS
# ============================================================================
def precalculate_dashboard_data():
    """
    Background function to process dashboard data using globally stored unified data.
    """
    global _unified_data

    try:
        logger.info("📈 [BACKGROUND] Starting dashboard data processing...")

        # Reload database configurations at the start of each background task cycle
        if constants.reload_database_configurations():
            logger.debug("✅ [BACKGROUND] Database configurations reloaded successfully")
        else:
            logger.warning("⚠️ [BACKGROUND] Failed to reload database configurations")

        # Extract data from global unified storage with lock
        with _unified_data_lock:
            dashboard_data = _unified_data.get("dashboard_data", {})
            if not dashboard_data:
                logger.warning("[BACKGROUND] No dashboard data available in global storage")
                return

            all_data_dashboard = dashboard_data["all_data_dashboard"]
            tdd_data = dashboard_data["tdd_data"]
            checkpoints = dashboard_data["checkpoints"]
            timestamps = dashboard_data["timestamps"]
            current_checkpoint = dashboard_data["current_checkpoint"]

        # Process dashboard data
        recap = {interval: {} for interval in checkpoints}
        tdd_recap = {interval: {} for interval in checkpoints}
        roaming_recap = {interval: {} for interval in checkpoints}
        inaccessible_dbs = []

        for interval in checkpoints:
            _process_data_for_interval(
                all_data_dashboard,
                interval,
                checkpoints,
                recap,
                tdd_recap,
                roaming_recap,
                inaccessible_dbs,
                tdd_data,
            )

        totals_international = {
            interval: _calculate_totals_optimized(
                recap[interval], constants.LABELS["international"]
            )
            for interval in checkpoints
        }
        totals_gi = {
            interval: _calculate_totals_optimized(recap[interval], constants.LABELS["utilisateur"])
            for interval in checkpoints
        }
        totals_tdd = {
            interval: _calculate_totals_optimized(
                tdd_recap[interval], constants.LABELS["utilisateur"]
            )
            for interval in checkpoints
        }
        totals_roaming = {
            interval: _calculate_totals_optimized(
                roaming_recap[interval], constants.LABELS["roaming"]
            )
            for interval in checkpoints
        }

        totals_international_trend = {
            "traffic": _get_totals_with_trend(totals_international, "traffic"),
            "pertes": _get_totals_with_trend(totals_international, "pertes"),
        }
        totals_gi_trend = {
            "traffic": _get_totals_with_trend(totals_gi, "traffic"),
            "pertes": _get_totals_with_trend(totals_gi, "pertes"),
        }
        totals_tdd_trend = {
            "traffic": _get_totals_with_trend(totals_tdd, "traffic"),
            "pertes": _get_totals_with_trend(totals_tdd, "pertes"),
        }
        totals_roaming_trend = {
            "traffic": _get_totals_with_trend(totals_roaming, "traffic"),
            "pertes": _get_totals_with_trend(totals_roaming, "pertes"),
        }

        # Combined totals for International + GI (used by the international+GI table)
        combined_labels = {**constants.LABELS["international"], **constants.LABELS["utilisateur"]}
        totals_international_gi = {
            interval: _calculate_totals_optimized(recap[interval], combined_labels)
            for interval in checkpoints
        }
        totals_international_gi_trend = {
            "traffic": _get_totals_with_trend(totals_international_gi, "traffic"),
            "pertes": _get_totals_with_trend(totals_international_gi, "pertes"),
            "tcp_no_response": _get_totals_with_trend(
                totals_international_gi, "tcp_no_response"
            ),
            "tcp_block_wp_up": _get_totals_with_trend(
                totals_international_gi, "tcp_block_wp_up"
            ),
            "latence": _get_totals_with_trend(totals_international_gi, "latence"),
            "dns_malformed": _get_totals_with_trend(
                totals_international_gi, "dns_malformed"
            ),
            "dns_no_response": _get_totals_with_trend(
                totals_international_gi, "dns_no_response"
            ),
        }

        # Metadata for frontend (from db_config.json via constants)
        international_names = constants.FRONTEND_METADATA["international_names"]
        gi_names = constants.FRONTEND_METADATA["gi_names"]
        roaming_cities = constants.FRONTEND_METADATA["roaming_cities"]
        roaming_types = constants.FRONTEND_METADATA["roaming_types"]

        # Prepare data for cache
        cached_data = {
            "current_time": current_checkpoint.strftime("%Y-%m-%d %H:%M"),
            "recap": recap,
            "tdd_recap": tdd_recap,
            "roaming_recap": roaming_recap,
            "totals_international": totals_international_trend,
            "totals_international_gi": totals_international_gi_trend,
            "totals_gi": totals_gi_trend,
            "totals_tdd": totals_tdd_trend,
            "totals_roaming": totals_roaming_trend,
            "names": international_names,
            "gi_names": gi_names,
            "roaming_cities": roaming_cities,
            "roaming_types": roaming_types,
            "last_updated": current_checkpoint.isoformat(),
        }

        # Update cache in thread-safe manner
        with constants.DASHBOARD_CACHE_LOCK:
            constants.DASHBOARD_CACHE.clear()
            constants.DASHBOARD_CACHE.update(cached_data)

        logger.info("✅ [BACKGROUND] Dashboard data processing completed successfully")
    except Exception as e:
        logger.exception(f"❌ [THREAD] Error in dashboard data processing: {e}")


def process_monitoring_data(orange_api_client: OrangeAPIClient = None):
    """
    Background function to process monitoring data (alerts and tickets) using globally stored unified data.
    """
    global _unified_data

    try:
        logger.info("🔍 [BACKGROUND] Starting monitoring data processing...")

        # Extract data from global unified storage with lock
        with _unified_data_lock:
            tickets_data = _unified_data.get("tickets_data", {})
            if not tickets_data:
                logger.warning("[BACKGROUND] No tickets data available in global storage")
                return

            all_data_monitoring = tickets_data["all_data_monitoring"]
            all_last_update = tickets_data["all_last_update"]
            all_mode_7days_gbps = tickets_data["all_mode_7days_gbps"]
            alert_timestamps = tickets_data["alert_timestamps"]
            current_checkpoint = tickets_data["current_checkpoint"]
            config = tickets_data["config"]

        datetime_str = current_checkpoint.strftime("%Y-%m-%d %H:%M:%S")

        # Step 2: Process data for monitoring (alerts and tickets)
        logger.info("🔢 [BACKGROUND] Calculating metrics...")
        from .database_manager import calculate_vectorized_metrics
        metrics = calculate_vectorized_metrics(all_data_monitoring, alert_timestamps)

        logger.info("🚨 [BACKGROUND] Detecting alerts...")
        alerts = detect_alerts_unified(metrics)

        # Step 3: Process tickets (create/update/close)
        logger.info("🎫 [BACKGROUND] Processing tickets...")
        ticket_manager = TicketManager(orange_api_client)
        tickets_before = load_tickets()  # Keep original tickets for comparison
        tickets = tickets_before.copy()  # Work on a copy
        results = {}

        for db_key in all_data_monitoring.keys():
            current_metrics = metrics[db_key]
            current_alerts = alerts[db_key]
            # logger.info(f"[{db_key}] Current Metrics: {current_metrics}\n Current Alerts: {current_alerts}")

            # Convert timestamp, add +1h, reformat
            current_metrics = current_metrics.with_columns(
                pl.col("SAUV_td32")
                .str.strptime(pl.Datetime, "%Y-%m-%d %H:%M:%S", strict=False)
                .dt.offset_by("1h")
                .dt.strftime("%Y-%m-%d %H:%M:%S")
            )

            # Select, rename and convert to dict
            util_metrics = (current_metrics
                            .select(["SAUV_td32", "traffic_gbps", "loss_percentage", "connection_failed", "failure_reason"])
                            .rename({"SAUV_td32": "timestamp"})
                            .to_dict(as_series=False))

            # Process traffic and pertes tickets
            tickets = ticket_manager.process_ticket_logic(
                db_key, current_alerts, util_metrics, datetime_str,
                tickets, all_mode_7days_gbps.get(db_key)
            )

            # Process DB down tickets
            if all_last_update[db_key]:
                db_timestamp = (all_last_update[db_key] + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            else:
                db_timestamp = None
            tickets = ticket_manager.process_db_down_logic(db_key, db_timestamp, datetime_str, tickets)

            results[db_key] = current_metrics.to_dicts()

        # Save all tickets
        if nb_save := save_tickets(tickets):
            logger.info(f"✅ [BACKGROUND] Saved {nb_save} tickets")

            # Send email notifications for new tickets
            try:
                # Identify new tickets by comparing with tickets_before
                existing_ticket_ids = {t.get('id') for t in tickets_before}
                new_tickets = [t for t in tickets if t.get('id') not in existing_ticket_ids and t.get('status') == 'open']

                if new_tickets:
                    logger.info(f"📧 [BACKGROUND] Sending email notifications for {len(new_tickets)} new ticket(s)")

                    # Send notification with all open tickets (including new ones)
                    open_tickets = [t for t in tickets if t.get('status') == 'open']

                    from .email_config import email_sender

                    # For multiple new tickets, use the first one as reference but include all open tickets
                    reference_ticket = new_tickets[0] if new_tickets else None

                    results = email_sender.send_new_ticket_notification(
                        new_ticket_data=reference_ticket,
                        all_open_tickets=open_tickets
                    )

                    if results['success_count'] > 0:
                        logger.info(f"📧 [BACKGROUND] Email notification sent to {results['success_count']} recipient(s)")
                    else:
                        logger.warning(f"📧 [BACKGROUND] Failed to send email notification")

                    if results['failed_recipients']:
                        logger.warning(f"📧 [BACKGROUND] Failed recipients: {results['failed_recipients']}")
                else:
                    logger.debug("📧 [BACKGROUND] No new tickets to notify about")

            except Exception as e:
                logger.error(f"📧 [BACKGROUND] Error sending email notifications: {e}")


        logger.info("✅ [BACKGROUND] Monitoring data processing completed successfully")
    except Exception as e:
        logger.exception(f"❌ [THREAD] Error in monitoring data processing: {e}")


def _get_totals_with_trend(totals_dict, key_type):
    result = {}
    intervals = list(totals_dict.keys())
    for idx, interval in enumerate(intervals):
        start = (
            float(totals_dict[interval][f"{key_type}_start"])
            if totals_dict[interval][f"{key_type}_start"] != ""
            else 0
        )
        end = (
            float(totals_dict[interval][f"{key_type}_end"])
            if totals_dict[interval][f"{key_type}_end"] != ""
            else 0
        )
        prev_seq = []
        if idx == 0:
            prev_seq = []
        elif idx == 1:
            prev_seq = [
                (
                    float(totals_dict[intervals[0]][f"{key_type}_start"])
                    if totals_dict[intervals[0]][f"{key_type}_start"] != ""
                    else 0
                )
            ]
        else:
            prev_seq = [
                (
                    float(totals_dict[intervals[idx - 1]][f"{key_type}_start"])
                    if totals_dict[intervals[idx - 1]][f"{key_type}_start"] != ""
                    else 0
                ),
                (
                    float(totals_dict[intervals[idx - 2]][f"{key_type}_start"])
                    if totals_dict[intervals[idx - 2]][f"{key_type}_start"] != ""
                    else 0
                ),
            ]

        # Déterminer le type pour _get_trend_icon_and_color
        trend_type = key_type
        if key_type in ["tcp_no_response", "tcp_block_wp_up", "latence"]:
            trend_type = key_type
        elif key_type == "traffic":
            trend_type = "traffic"
        elif key_type in ["dns_malformed", "dns_no_response"]:
            trend_type = key_type
        else:  # pertes
            trend_type = "pertes"

        icon, color = _get_trend_icon_and_color(trend_type, end, start, prev_seq)
        result[interval] = {
            f"{key_type}_start": start,
            f"{key_type}_end": end,
            "trend_icon": icon,
            "trend_color": color,
            f"taux_{key_type}": totals_dict[interval][f"taux_{key_type}"],
        }
    return result

