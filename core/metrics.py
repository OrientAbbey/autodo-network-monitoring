"""MÉTRIQUES DU TABLEAU DE BORD — calcul pur Python (aucune E/S, pas de DataFrame).

Volume traité par cycle : ≤ 20 bases × 3 intervalles × 2 relevés ≈ 120 lignes → des dictionnaires suffisent ;
polars (import ≈ 100 ms, ≈ 30 Mo) n'apporterait aucun gain à cette échelle et a été retiré du projet.
"""
from __future__ import annotations

from datetime import datetime

from . import constants
from .config import load_config
from .logger import get_logger

logger = get_logger(__name__)


def _go(kind: str, db_key: str, c_param: str | None = None) -> str:
    """Lien vers le portail ExNet via /go/ (le JS remplace __END_TIMESTAMP__ avant l'ouverture)."""
    return f"/go/{kind}/{db_key}?end_date=__END_TIMESTAMP__" + (f"&c={c_param}" if c_param else "")


_PROTOCOLS = ("tcp_dw", "udp_dw", "icmp_dw", "other_dw")
_EMPTY_METRICS = {
    "total": None, "pertes": None, "tcp_no_response": None, "tcp_block_wp_up": None, "latence": None,
    "dns_malformed": None, "dns_no_response": None, "tcp_loss_dw_raw": 0, "tcp_pkt_dw_raw": 0, "tcp_no_response_raw": 0,
    "tcp_block_wp_up_raw": 0, "tcp_sessions_raw": 0, "dns_malformed_raw": 0, "dns_queries_raw": 0, "dns_queries_woresp_raw": 0,
}


def _num(rec: dict, key: str, default: float = 0.0) -> float:
    v = rec.get(key)
    return float(v) if v is not None else default


def _is_empty(rec) -> bool:
    return isinstance(rec, dict) and all(v is None or (isinstance(v, str) and v.strip() == "") for v in rec.values())


def _compute_metrics(rec: dict) -> dict:
    """Métriques dérivées d'UN relevé 5 min (None = relevé vide)."""
    if _is_empty(rec):
        return dict(_EMPTY_METRICS)
    sessions = _num(rec, "#tcp_sessions")
    synack = _num(rec, "#tcp_synack_core")
    queries = _num(rec, "dns_queries")
    malformed, woresp = _num(rec, "dns_malformed_ul"), _num(rec, "dns_queries_woresp")
    return {
        "total": sum(_num(rec, p) for p in _PROTOCOLS) * constants.DEBIT_FACTOR,
        "pertes": 100 * _num(rec, "#tcp_loss_dw") / max(1, _num(rec, "#tcp_pkt_dw", 1.0)),
        "tcp_no_response": (_num(rec, "#tcp_no_response_to_syn") / max(1, sessions)) * 100,
        "tcp_block_wp_up": (_num(rec, "#tcp_block_wp_up") / max(1, sessions)) * 100,
        "latence": (_num(rec, "Irt_tcp_core_td22") / synack) if synack > 0 else 0,
        "dns_malformed": (malformed / max(1, queries)) * 100 if queries > 0 else 0,
        "dns_no_response": (woresp / max(1, queries)) * 100 if queries > 0 else 0,
        "tcp_loss_dw_raw": _num(rec, "#tcp_loss_dw"), "tcp_pkt_dw_raw": _num(rec, "#tcp_pkt_dw"),
        "tcp_no_response_raw": _num(rec, "#tcp_no_response_to_syn"), "tcp_block_wp_up_raw": _num(rec, "#tcp_block_wp_up"),
        "tcp_sessions_raw": sessions, "dns_malformed_raw": malformed, "dns_queries_raw": queries, "dns_queries_woresp_raw": woresp,
    }


def _nz(v):
    return 0 if v is None else v


def _block(kind: str, ms: dict, me: dict, key: str, extra: dict | None = None, dns: bool = False) -> dict:
    """Bloc {start, end, taux, <extras>, trend_icon, trend_color} pour une métrique (table de description, pas de copier-coller)."""
    s, e = ms[key], me[key]
    icon, color = _get_trend_icon_and_color(kind, _nz(e), _nz(s))
    taux = ((s or 0) - (e or 0)) if dns else (_nz(s) - _nz(e))
    return {"start": s, "end": e, "taux": taux, **(extra or {}), "trend_icon": icon, "trend_color": color}


def _entry(ms: dict, me: dict, db_key: str) -> dict:
    pair = lambda k: {"start": ms[k], "end": me[k]}           # noqa: E731
    return {
        "total": _block("traffic", ms, me, "total"),
        "pertes": _block("pertes", ms, me, "pertes", {
            "tcp_loss_dw_start": ms["tcp_loss_dw_raw"], "tcp_pkt_dw_start": ms["tcp_pkt_dw_raw"],
            "tcp_loss_dw_end": me["tcp_loss_dw_raw"], "tcp_pkt_dw_end": me["tcp_pkt_dw_raw"]}),
        "tcp_metrics": {
            "tcp_no_response": _block("tcp_no_response", ms, me, "tcp_no_response", {
                "tcp_no_response_start": ms["tcp_no_response_raw"], "tcp_no_response_end": me["tcp_no_response_raw"]}),
            "tcp_block_wp_up": _block("tcp_block_wp_up", ms, me, "tcp_block_wp_up", {
                "tcp_block_wp_up_start": ms["tcp_block_wp_up_raw"], "tcp_block_wp_up_end": me["tcp_block_wp_up_raw"]}),
            "latence": _block("latence", ms, me, "latence"),
            "tcp_sessions": pair("tcp_sessions_raw"),
        },
        "dns_metrics": {
            "dns_malformed": _block("dns_malformed", ms, me, "dns_malformed", {
                "dns_malformed_start": ms["dns_malformed_raw"], "dns_malformed_end": me["dns_malformed_raw"],
                "queries_start": ms["dns_queries_raw"], "queries_end": me["dns_queries_raw"]}, dns=True),
            "dns_no_response": _block("dns_no_response", ms, me, "dns_no_response", {
                "dns_no_response_start": ms["dns_queries_woresp_raw"], "dns_no_response_end": me["dns_queries_woresp_raw"],
                "queries_start": ms["dns_queries_raw"], "queries_end": me["dns_queries_raw"]}, dns=True),
        },
        "link": {"total": _go("traffic", db_key), "pertes": _go("loss", db_key), "tcp_service_access": _go("tcp_service_access", db_key),
                 "tcp_tts": _go("tcp_tts", db_key), "dns": _go("dns", db_key)},
    }


_NA = {"start": "N/A", "end": "N/A", "taux": "N/A", "trend_icon": "↔", "trend_color": "neutral"}


def _tdd_entry(start_rec, end_rec, db_key: str) -> dict:
    """Colonne TDD (bases « utilisateur ») : métriques si les deux relevés existent, sinon « N/A »."""
    if not (start_rec and end_rec):
        return {"total": dict(_NA), "pertes": dict(_NA), "link": {"total": None, "pertes": None}}
    ms, me = _compute_metrics(start_rec), _compute_metrics(end_rec)
    return {"total": _block("traffic", ms, me, "total"),
            "pertes": _block("pertes", ms, me, "pertes", {"pkt_start": ms["tcp_pkt_dw_raw"], "pkt_end": me["tcp_pkt_dw_raw"]}),
            "link": {"total": _go("traffic", db_key, "SGi"), "pertes": _go("loss", db_key, "SGi")}}


def _process_data_for_interval(data_dict: dict, interval: str, checkpoints: dict, recap: dict, tdd_recap: dict,
                               roaming_recap: dict, inaccessible_dbs: list, tdd_data: dict) -> None:
    """Remplit ``recap`` / ``roaming_recap`` / ``tdd_recap`` pour un intervalle (début → fin)."""
    start_str, end_str = checkpoints[interval]["start_str"], checkpoints[interval]["end_str"]
    for db_key, db_data in data_dict.items():
        start_rec, end_rec = db_data.get(start_str), db_data.get(end_str)
        if start_rec is None or end_rec is None:
            if db_key not in inaccessible_dbs:
                inaccessible_dbs.append(db_key)
            logger.warning("Données manquantes pour %s : %s -> %s", db_key, start_rec, end_rec)
            continue
        entry = _entry(_compute_metrics(start_rec), _compute_metrics(end_rec), db_key)
        (roaming_recap if "roaming" in db_key else recap)[interval][db_key] = entry
        if db_key in constants.LABELS["utilisateur"]:
            t = tdd_data.get(db_key, {})
            tdd_recap[interval][db_key] = _tdd_entry(t.get(start_str), t.get(end_str), db_key)


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
            totals["tcp_no_response_start"] += tcp_no_response_info.get("tcp_no_response_start", 0) or 0
            totals["tcp_no_response_end"] += tcp_no_response_info.get("tcp_no_response_end", 0) or 0

        # --- TCP block wp up ---
        tcp_block_wp_up_info = tcp_metrics.get("tcp_block_wp_up", {})
        if isinstance(tcp_block_wp_up_info.get("start"), (int, float)):
            totals["tcp_block_wp_up_start"] += tcp_block_wp_up_info.get("tcp_block_wp_up_start", 0) or 0
            totals["tcp_block_wp_up_end"] += tcp_block_wp_up_info.get("tcp_block_wp_up_end", 0) or 0

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
        and isinstance(data_dict[db_key].get("tcp_metrics", {}).get("latence", {}).get("start"), (int, float))
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


def build_dashboard(all_data_dashboard: dict, tdd_data: dict, checkpoints: dict, current_checkpoint: datetime) -> dict:
    """Assemble le JSON du tableau de bord (même structure que l'ancienne ``precalculate_dashboard_data``)."""
    recap = {interval: {} for interval in checkpoints}
    tdd_recap = {interval: {} for interval in checkpoints}
    roaming_recap = {interval: {} for interval in checkpoints}
    inaccessible_dbs: list = []
    for interval in checkpoints:
        _process_data_for_interval(all_data_dashboard, interval, checkpoints, recap, tdd_recap, roaming_recap,
                                   inaccessible_dbs, tdd_data)
    labels = constants.LABELS
    totals_international = {i: _calculate_totals_optimized(recap[i], labels["international"]) for i in checkpoints}
    totals_gi = {i: _calculate_totals_optimized(recap[i], labels["utilisateur"]) for i in checkpoints}
    totals_tdd = {i: _calculate_totals_optimized(tdd_recap[i], labels["utilisateur"]) for i in checkpoints}
    totals_roaming = {i: _calculate_totals_optimized(roaming_recap[i], labels["roaming"]) for i in checkpoints}
    combined = {**labels["international"], **labels["utilisateur"]}
    totals_international_gi = {i: _calculate_totals_optimized(recap[i], combined) for i in checkpoints}

    def trend(totals, *keys):
        return {k: _get_totals_with_trend(totals, k) for k in keys}

    meta = constants.FRONTEND_METADATA
    return {
        "current_time": current_checkpoint.strftime("%Y-%m-%d %H:%M"),
        "recap": recap, "tdd_recap": tdd_recap, "roaming_recap": roaming_recap,
        "totals_international": trend(totals_international, "traffic", "pertes"),
        "totals_international_gi": trend(totals_international_gi, "traffic", "pertes", "tcp_no_response",
                                         "tcp_block_wp_up", "latence", "dns_malformed", "dns_no_response"),
        "totals_gi": trend(totals_gi, "traffic", "pertes"),
        "totals_tdd": trend(totals_tdd, "traffic", "pertes"),
        "totals_roaming": trend(totals_roaming, "traffic", "pertes"),
        "names": meta["international_names"], "gi_names": meta["gi_names"],
        "roaming_cities": meta["roaming_cities"], "roaming_types": meta["roaming_types"],
        "last_updated": current_checkpoint.isoformat(),
    }
