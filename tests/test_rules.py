"""Règles métier pures : détection, fermeture, décision de tickets (aucune base, aucun réseau)."""
import itertools

import pytest

from core import rules
from core.config import ConfigParams

CFG = ConfigParams().model_dump()


# ------------------------------------------------------------------ détection trafic
def test_zero_traffic_is_category_1():
    v = rules.detect_traffic([5, 5, 5, 5, 0, 0], CFG)
    assert v.alert and v.category == 1


def test_default_requires_two_zero_samples_a_single_zero_waits_for_confirmation():
    assert CFG["zero_traffic_min_samples"] == 2
    v = rules.detect_traffic([5, 5, 5, 5, 5, 0], CFG)
    assert not v.alert and "non confirmé" in v.reason
    assert rules.detect_traffic([5, 5, 5, 5, 5, 0], {**CFG, "zero_traffic_min_samples": 1}).alert


def test_zero_traffic_min_samples_is_configurable():
    cfg = {**CFG, "zero_traffic_min_samples": 3}
    assert not rules.detect_traffic([5, 5, 5, 5, 0, 0], cfg).alert
    assert rules.detect_traffic([5, 5, 5, 0, 0, 0], cfg).alert


def test_instant_drop_is_category_2():
    v = rules.detect_traffic([10, 10, 10, 10, 10, 5], CFG)
    assert v.alert and v.category == 2 and v.metric == pytest.approx(50)


def test_small_drop_is_not_an_alert():
    assert not rules.detect_traffic([10, 10, 10, 10, 10, 9], CFG).alert


def test_consecutive_drops_is_category_3():
    v = rules.detect_traffic([10, 7.5, 5.6, 4.2, 3.1, 2.4], CFG)
    assert v.alert and v.category == 3


def test_missing_data_never_alerts_and_never_raises():
    v = rules.detect_traffic([10, 10, None, 10, 10, 0], CFG)
    assert not v.alert and "manquantes" in v.reason


def test_percent_variation_zero_previous():
    assert rules.percent_variation(0, 0) == 0 and rules.percent_variation(0, 5) == 100


# ------------------------------------------------------------------ pertes & fraîcheur
def test_pertes_requires_consecutive_samples():
    assert rules.detect_pertes([0.5, 8, 8], CFG).alert
    assert not rules.detect_pertes([0.5, 0.5, 8], CFG).alert
    assert not rules.detect_pertes([8, None], CFG).alert


@pytest.mark.parametrize("minutes,expected", [(10, "ok"), (70, "ok"), (71, "down")])
def test_db_freshness_threshold(minutes, expected):
    from datetime import datetime, timedelta
    now = datetime(2026, 1, 1, 12, 0)
    assert rules.db_freshness(now - timedelta(minutes=minutes), now, CFG)[0] == expected


def test_db_freshness_unknown_when_no_data():
    from datetime import datetime
    assert rules.db_freshness(None, datetime(2026, 1, 1), CFG)[0] == "unknown"


# ------------------------------------------------------------------ valeur d'origine & fermeture
def _vals(*v):
    return [{"timestamp": f"2026-01-01 00:{i * 5:02d}:00", "value": x} for i, x in enumerate(v)]


def test_original_value_never_raises_on_short_series():
    assert rules.original_value("traffic", _vals(4.0), 3, CFG)[0] == 4.0
    assert rules.original_value("traffic", [], 1, CFG) == (0.0, "")


def test_closure_requires_persistence_window():
    ticket = {"type": "traffic", "category": 2, "value": 10.0, "timestamp": "2026-01-01 00:00:00", "closure_pending_timestamp": None}
    recovered = _vals(9.5, 9.6, 9.7)
    f1 = rules.evaluate_closure(ticket, recovered, "2026-01-01 01:00:00", CFG, None)
    assert "closure_pending_timestamp" in f1 and "status" not in f1                  # armement
    ticket["closure_pending_timestamp"] = f1["closure_pending_timestamp"]
    assert rules.evaluate_closure(ticket, recovered, "2026-01-01 01:10:00", CFG, None) == {}      # trop tôt
    f3 = rules.evaluate_closure(ticket, recovered, "2026-01-01 01:30:00", CFG, None)
    assert f3["status"] == "closed" and f3["ttr_seconds"] == 5400


def test_relapse_cancels_pending_closure():
    ticket = {"type": "traffic", "category": 2, "value": 10.0, "timestamp": "2026-01-01 00:00:00",
              "closure_pending_timestamp": "2026-01-01 01:25:00"}
    assert rules.evaluate_closure(ticket, _vals(3.0), "2026-01-01 01:10:00", CFG, None) == {"closure_pending_timestamp": None}


def test_db_down_hysteresis_uses_separate_recovery_threshold():
    t = {"type": "db_down", "category": 1, "value": 120.0, "timestamp": "2026-01-01 00:00:00", "closure_pending_timestamp": None}
    assert rules.evaluate_closure(t, _vals(60.0), "2026-01-01 02:00:00", CFG, None) == {}      # 60 > seuil de levée (35)
    assert "closure_pending_timestamp" in rules.evaluate_closure(t, _vals(20.0), "2026-01-01 02:00:00", CFG, None)


def test_attach_pertes_is_throttled_to_one_per_30_minutes():
    t = {"database_name": "x", "pertes_warnings": []}
    first = rules.attach_pertes(t, 5.0, "2026-01-01 10:00:00")
    t.update(first)
    assert first["pertes"] == "Yes" and rules.attach_pertes(t, 5.0, "2026-01-01 10:10:00") == {}
    assert rules.attach_pertes(t, 5.0, "2026-01-01 10:40:00")["pertes_warnings"][-1]["timestamp"] == "2026-01-01 10:40:00"


# ------------------------------------------------------------------ décision : les 16 combinaisons
# (T, P, ticket_trafic, ticket_pertes) -> actions attendues. Table établie en prouvant l'équivalence avec l'ancien
# process_ticket_logic (16 branches) sur chacune des 16 combinaisons.
EXPECTED = {
    (1, 1, 0, 0): {("create_traffic", "", True)},
    (1, 1, 1, 0): {("update_traffic", "", True)},
    (1, 1, 0, 1): {("create_traffic", "", True), ("update_pertes", "", False)},
    (1, 1, 1, 1): {("update_traffic", "", False), ("update_pertes", "", False)},
    (1, 0, 0, 0): {("create_traffic", "", False)},
    (1, 0, 1, 0): {("update_traffic", "", False)},
    (1, 0, 0, 1): {("create_traffic", "", False), ("try_close", "pertes", False)},
    (1, 0, 1, 1): {("update_traffic", "", False), ("try_close", "pertes", False)},
    (0, 1, 0, 0): {("create_pertes", "", False)},
    (0, 1, 1, 0): {("update_traffic", "", True)},
    (0, 1, 0, 1): {("update_pertes", "", False)},
    (0, 1, 1, 1): {("try_close", "traffic", False), ("update_pertes", "", False)},
    (0, 0, 0, 0): set(),
    (0, 0, 1, 0): {("try_close", "traffic", False)},
    (0, 0, 0, 1): {("try_close", "pertes", False)},
    (0, 0, 1, 1): {("try_close", "traffic", False), ("try_close", "pertes", False)},
}


@pytest.mark.parametrize("combo", list(itertools.product([0, 1], repeat=4)))
def test_plan_actions_matches_the_16_historical_cases(combo):
    got = {(a.kind, a.ticket_type, a.attach_pertes) for a in rules.plan_actions(*map(bool, combo))}
    assert got == EXPECTED[combo]


def test_descriptions_are_informative():
    assert "Chute instantanée" in rules.build_description("traffic", "Douala", "Diminution critique instantanée 10 → 5", 10, "t")
    assert "DATABASE CONNECTION FAILURE" in rules.build_description("traffic", "S", "r", 0, "t", True, "boom")


def test_ttr_human_format():
    assert rules.ttr_human(5400) == "1h 30m" and rules.ttr_human(59) == "0m" and rules.ttr_human(None) == ""
