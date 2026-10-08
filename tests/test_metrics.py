"""Tableau de bord : agrégats, robustesse aux valeurs NULL, liens sans nom d'hôte interne (Python pur, sans base)."""
from datetime import datetime

import pytest

from core import constants, metrics


def _db(sessions, no_response):
    pct = no_response / sessions * 100
    return {"total": {"start": 1.0, "end": 1.0}, "pertes": {"start": 0, "end": 0},
            "tcp_metrics": {"tcp_no_response": {"start": pct, "end": pct, "tcp_no_response_start": no_response, "tcp_no_response_end": no_response},
                            "tcp_block_wp_up": {"start": 0, "end": 0}, "latence": {"start": 1, "end": 1},
                            "tcp_sessions": {"start": sessions, "end": sessions}}, "dns_metrics": {}}


def test_aggregate_tcp_no_response_uses_raw_counts():                       # régression M-06
    totals = metrics._calculate_totals_optimized({"a": _db(1000, 100), "b": _db(1000, 100)}, {"a": "A", "b": "B"})
    assert float(totals["tcp_no_response_start"]) == pytest.approx(10.0)    # 200 / 2 000 sessions


def test_null_column_does_not_break_the_dashboard_calculation(monkeypatch):    # régression M-07
    rec = {"tcp_dw": 1, "udp_dw": None, "icmp_dw": 0, "other_dw": 0, "#tcp_loss_dw": 0, "#tcp_pkt_dw": 1,
           "#tcp_no_response_to_syn": 0, "#tcp_block_wp_up": 0, "#tcp_synack_core": 0, "Irt_tcp_core_td22": 0, "#tcp_sessions": 1}
    monkeypatch.setattr(constants, "LABELS", {"international": {}, "utilisateur": {}, "roaming": {}})
    metrics._process_data_for_interval({"db": {"s": rec, "e": rec}}, "5min", {"5min": {"start_str": "s", "end_str": "e"}},
                                       {"5min": {}}, {"5min": {}}, {"5min": {}}, [], {})


def test_dashboard_links_are_relative_go_urls_without_internal_hosts():     # M-12
    link = metrics._go("traffic", "kribi")
    assert link == "/go/traffic/kribi?end_date=__END_TIMESTAMP__"
    assert "SGi" in metrics._go("loss", "douala_gi", "SGi")


def test_build_dashboard_smoke_with_missing_database(monkeypatch):
    labels = {"international": {"kribi": "KRIBI"}, "utilisateur": {}, "roaming": {}}
    monkeypatch.setattr(constants, "LABELS", labels)
    monkeypatch.setattr(constants, "DB_CONFIGS", {"kribi": {"host": "10.9.9.9", "db_name": "d", "site": "Douala"}})
    monkeypatch.setattr(constants, "LOCATION_MAPPING", {"kribi": "KRIBI"})
    cps = {"5min": {"start_str": "a", "end_str": "b", "start": datetime(2026, 1, 1), "end": datetime(2026, 1, 1)}}
    data = metrics.build_dashboard({"kribi": {"a": None, "b": None}}, {}, cps, datetime(2026, 1, 1))
    assert "recap" in data and "10.9.9.9" not in str(data)
