"""MÉTRIQUES DU TABLEAU DE BORD — calcul pur Python (aucune E/S, pas de DataFrame).

Volume traité par cycle : ≤ 20 bases × 3 intervalles × 2 relevés ≈ 120 lignes → des dictionnaires suffisent ;
polars (import ≈ 100 ms, ≈ 30 Mo) n'apporterait aucun gain à cette échelle et a été retiré du projet.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from . import constants
from .config import load_config
from .logger import get_logger

logger = get_logger(__name__)


def _go(kind: str, db_key: str, c_param: Optional[str] = None) -> str:
    """Lien vers le portail ExNet via /go/ (le JS remplace __END_TIMESTAMP__ avant l'ouverture)."""
    return f"/go/{kind}/{db_key}?end_date=__END_TIMESTAMP__" + (f"&c={c_param}" if c_param else "")


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
            if db_key not in inaccessible_dbs:
                inaccessible_dbs.append(db_key)
            logger.warning(
                f"Données manquantes pour {db_key} : {start_record} -> {end_record}"
            )
            continue

        # helper pour calculer les métriques d'un enregistrement (réduit répétitions)
        def _num(rec, key, default=0.0):
            v = rec.get(key)
            return float(v) if v is not None else default

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
            total = sum(_num(rec, p) for p in protocols) * constants.DEBIT_FACTOR
            pertes_val = 100 * _num(rec, "#tcp_loss_dw") / max(
                1, _num(rec, "#tcp_pkt_dw", 1.0)
            )

            tcp_sessions = _num(rec, "#tcp_sessions")
            tcp_no_response = (
                _num(rec, "#tcp_no_response_to_syn") / max(1, tcp_sessions)
            ) * 100
            tcp_block_wp_up = (
                _num(rec, "#tcp_block_wp_up") / max(1, tcp_sessions)
            ) * 100

            latence = (
                (
                    _num(rec, "Irt_tcp_core_td22")
                    / _num(rec, "#tcp_synack_core")
                )
                if _num(rec, "#tcp_synack_core") > 0
                else 0
            )

            # DNS metrics
            dns_malformed_raw = _num(rec, "dns_malformed_ul")
            dns_queries_raw = _num(rec, "dns_queries")
            dns_queries_woresp_raw = _num(rec, "dns_queries_woresp")

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
                "tcp_loss_dw_raw": _num(rec, "#tcp_loss_dw"),
                "tcp_pkt_dw_raw": _num(rec, "#tcp_pkt_dw"),
                "tcp_no_response_raw": _num(rec, "#tcp_no_response_to_syn"),
                "tcp_block_wp_up_raw": _num(rec, "#tcp_block_wp_up"),
                "tcp_sessions_raw": _num(rec, "#tcp_sessions"),
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
        # Liens relatifs /go/... : le nom d'hôte interne n'est plus publié dans le JSON (redirection côté serveur)
        link_t, link_l = _go("traffic", db_key), _go("loss", db_key)
        link_tcp_service_access, link_tcp_tts, link_dns = _go("tcp_service_access", db_key), _go("tcp_tts", db_key), _go("dns", db_key)

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
                tdd_t_link = _go("traffic", db_key, "SGi")
                tdd_l_link = _go("loss", db_key, "SGi")
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
