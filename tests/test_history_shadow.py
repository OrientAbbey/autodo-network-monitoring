"""Historique local (D2), mode ombre, rapport détecteurs ↔ tickets, simulation « et si ? »."""
import math
import random
import threading
from datetime import timedelta

import pytest
from sqlalchemy import text

from core import anomaly, collector, config, governance, history, shadow, simulation, tickets
from core.config import fmt, utc_naive_to_local_naive
from tests.test_ticket_workflow import FakeOrange, mk  # noqa: F401  (mk : fabrique de tickets)

STEP = timedelta(minutes=5)
UNIT = 37_500_000                                        # unités DIAGNET pour 1 Gb/s


def aligned_now():
    return config.round5(history.utc_now())


def raw_row(gbps: float, loss=0.0, sessions=1000, synack=100, rtt=5000, no_response=10) -> dict:
    return {"tcp_dw": gbps * UNIT, "udp_dw": 0.0, "icmp_dw": 0.0, "other_dw": 0.0, "#tcp_pkt_dw": 10_000.0, "#tcp_loss_dw": loss * 100,
            "#tcp_sessions": float(sessions), "#tcp_synack_core": float(synack), "Irt_tcp_core_td22": float(rtt),
            "#tcp_no_response_to_syn": float(no_response), "#tcp_block_wp_up": 0.0}


def profile(slot_index: int) -> float:
    hour = (slot_index % 288) / 12.0
    return 18 * (0.62 + 0.38 * math.sin((hour - 14) / 24 * 2 * math.pi))


def fill_history(db_key: str, days: int, *, end=None, noise=0.03, seed=1, modify=None) -> None:
    """Remplit ``metrics_5min`` avec ``days`` jours de trafic sinusoïdal ; ``modify(ts, gbps) -> gbps`` pour injecter des anomalies."""
    end = end or aligned_now()
    rnd = random.Random(seed)
    rows = []
    total = days * 288
    for i in range(total):
        ts = end - (total - 1 - i) * STEP
        gbps = profile(ts.hour * 12 + ts.minute // 5) * (1 + rnd.gauss(0, noise))
        gbps = modify(ts, gbps) if modify else gbps
        # (les vraies séries varient : des mesures strictement constantes ne permettraient à aucun modèle d'en déduire une anomalie)
        rows.append({"db_key": db_key, "ts": fmt(ts), "traffic_gbps": gbps, "loss_pct": max(0.0, rnd.gauss(1.0, 0.3)),
                     "latency_ms": max(1.0, rnd.gauss(50.0, 4.0)), "tcp_no_response_pct": max(0.0, rnd.gauss(1.0, 0.2)),
                     "tcp_block_wp_up_pct": 0.0, "dns_malformed_pct": 0.0})
    with history.local_tx() as conn:
        conn.execute(history._REPLACE_SQL, rows)


# ------------------------------------------------------------------ extraction et écriture
def test_metrics_follow_the_dashboard_formulas():
    metrics = history.metrics_from_row(raw_row(5.0, loss=2.0, sessions=2000, synack=200, rtt=9000, no_response=40))
    assert metrics["traffic_gbps"] == pytest.approx(5.0, abs=1e-3)
    assert metrics["loss_pct"] == pytest.approx(2.0) and metrics["latency_ms"] == pytest.approx(45.0)
    assert metrics["tcp_no_response_pct"] == pytest.approx(2.0)


def test_a_row_without_traffic_is_not_stored_but_zero_traffic_is(clean_state):
    assert history.metrics_from_row({"tcp_dw": None, "udp_dw": None, "icmp_dw": None, "other_dw": None}) is None
    assert history.metrics_from_row(None) is None
    assert history.store_rows("kribi", {"2026-01-01 00:00:00": raw_row(0.0)}) == 1                  # trafic nul = information capitale
    assert history.load_series("kribi", aligned_now() - timedelta(days=3650), aligned_now())[0]["traffic_gbps"] == 0.0


def test_storing_twice_is_idempotent(clean_state):
    rows = {"2026-01-01 00:00:00": raw_row(5.0), "2026-01-01 00:05:00": raw_row(6.0)}
    history.store_rows("kribi", rows)
    history.store_rows("kribi", rows)
    assert history.stats()["total_rows"] == 2


def test_record_snapshots_skips_unreachable_databases(clean_state):
    ok = collector.DbSnapshot("a", ok=True, whole={"2026-01-01 00:00:00": raw_row(5.0)})
    down = collector.DbSnapshot("b", ok=False, error="timeout", whole={"2026-01-01 00:00:00": raw_row(5.0)})
    assert history.record_snapshots({"a": ok, "b": down}) == 1
    assert set(history.stats()["bases"]) == {"a"}


def test_a_monitoring_cycle_feeds_the_history(clean_state, monkeypatch):
    from tests.test_ticket_workflow import crash_snapshot, run_cycle
    stats = run_cycle(monkeypatch, crash_snapshot())
    assert stats["historique"] > 0 and history.stats()["bases"]["kribi"]["rows"] == stats["historique"]


def test_purge_removes_only_rows_older_than_the_retention(clean_state):
    history.store_rows("kribi", {fmt(aligned_now() - timedelta(days=500)): raw_row(5.0), fmt(aligned_now() - timedelta(days=10)): raw_row(6.0)})
    assert history.purge(400) == 1 and history.stats()["total_rows"] == 1


def test_daily_stats_keep_complete_days_only(clean_state):
    fill_history("kribi", 4)
    days = history.daily_stats("kribi", 10)
    assert 2 <= len(days) <= 4 and all(d["peak"] >= d["mean"] for d in days)                       # aujourd'hui (partiel) exclu
    assert str(history.utc_now().date()) not in {str(d["day"]) for d in days}


# ------------------------------------------------------------------ import (backfill)
def test_backfill_reads_in_chunks_and_a_failing_base_does_not_stop_the_others(clean_state, monkeypatch):
    calls = []

    def fake_read(db_key, start, end):
        calls.append((db_key, start, end))
        if db_key == "panne":
            raise OSError("connexion refusée")
        return {start: raw_row(5.0)}

    monkeypatch.setattr(collector, "read_range", fake_read)
    result = history.backfill(12, ["ok", "panne", "ok2"])
    assert result["ok"]["error"] is None and result["ok"]["rows"] == 3                              # 12 jours = 3 lectures de 5 jours max
    assert "OSError" in result["panne"]["error"] and result["ok2"]["rows"] == 3
    assert len([c for c in calls if c[0] == "ok"]) == 3


def test_only_one_backfill_runs_at_a_time(clean_state, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(history, "backfill", lambda days, db_keys=None: gate.wait(5))
    assert history.start_backfill(3) is True
    assert history.start_backfill(3) is False and history.backfill_state.running
    gate.set()
    for _ in range(100):
        if not history.backfill_state.running:
            break
        threading.Event().wait(0.05)
    assert not history.backfill_state.running and history.backfill_state.finished_at


# ------------------------------------------------------------------ mini-courbes
def test_an_import_refreshes_the_sparklines_immediately(clean_state, monkeypatch):
    history.reset_sparkline_cache()
    assert history.sparklines()["bases"] == {}                                   # mis en cache : vide
    monkeypatch.setattr(collector, "read_range", lambda key, a, b: {fmt(aligned_now() - i * STEP): raw_row(5.0) for i in range(12)})
    history.backfill(1, ["kribi"])
    assert "kribi" in history.sparklines()["bases"]                              # pas d'attente de l'expiration des 60 s

def test_sparklines_give_96_points_and_an_expected_band(clean_state):
    fill_history("kribi", 9)
    history.reset_sparkline_cache()
    data = history.sparklines()["bases"]["kribi"]
    assert len(data["values"]) == 96 == len(data["expected"])
    assert sum(v is not None for v in data["values"]) > 90 and sum(e is not None for e in data["expected"]) > 90
    again = history.sparklines()
    assert again is history.sparklines()                                                             # mise en cache 60 s


# ------------------------------------------------------------------ mode ombre
def snapshot_at(db_key: str, at) -> collector.DbSnapshot:
    return collector.DbSnapshot(db_key, ok=True, whole={fmt(at): raw_row(1.0)})


def history_with_drop(db_key="kribi", factor=0.8):
    end = aligned_now()
    fill_history(db_key, 9, end=end, modify=lambda ts, v: v * factor if ts >= end - STEP else v)
    return end


def test_shadow_records_a_seasonal_verdict_once_and_never_touches_tickets(clean_state):
    end = history_with_drop()
    stats = shadow.run_shadow({"kribi": snapshot_at("kribi", end)})
    assert stats["seasonal"] == 1
    shadow.run_shadow({"kribi": snapshot_at("kribi", end)})                                          # même relevé : pas de doublon
    with clean_state.local_conn() as c:
        assert c.execute(text("SELECT COUNT(*) FROM detector_verdicts")).scalar() == 1
        assert c.execute(text("SELECT explanation FROM detector_verdicts")).scalar().startswith("−")
    assert tickets.load_open() == []


def test_a_normal_slot_records_nothing(clean_state):
    end = aligned_now()
    fill_history("kribi", 9, end=end)
    assert shadow.run_shadow({"kribi": snapshot_at("kribi", end)})["seasonal"] == 0


def test_a_disabled_detector_and_the_global_switch_stop_the_shadow(clean_state):
    end = history_with_drop()
    shadow.set_enabled("seasonal", False)
    assert shadow.run_shadow({"kribi": snapshot_at("kribi", end)})["seasonal"] == 0
    shadow.set_enabled("seasonal", True)
    governance.save_global_config("alice", "1.1.1.1", {"shadow_detectors": False})
    assert shadow.run_shadow({"kribi": snapshot_at("kribi", end)}) == {"seasonal": 0, "isolation": 0, "level_shift": 0, "quality": 0}


def test_a_crashing_detector_never_breaks_the_cycle(clean_state, monkeypatch):
    end = history_with_drop()
    monkeypatch.setattr(shadow, "_run_seasonal", lambda *a: (_ for _ in ()).throw(RuntimeError("boum")))
    assert shadow.run_shadow({"kribi": snapshot_at("kribi", end)})["seasonal"] == 0                  # journalisé, ignoré
    monkeypatch.setattr(shadow, "get_states", lambda: (_ for _ in ()).throw(RuntimeError("base HS")))
    assert shadow.run_shadow({"kribi": snapshot_at("kribi", end)})                                   # ne lève pas non plus


def test_unreachable_bases_are_skipped(clean_state):
    down = collector.DbSnapshot("kribi", ok=False)
    assert shadow.run_shadow({"kribi": down})["seasonal"] == 0


def test_level_shift_is_recorded_when_recent_and_ignored_when_old(clean_state, monkeypatch):
    today = history.utc_now().date()
    days = [(today - timedelta(days=30 - i), 15.0 if i < 28 else 11.0) for i in range(30)]
    monkeypatch.setattr(history, "daily_stats", lambda db_key, n: [{"day": str(d), "mean": m, "peak": m} for d, m in days])
    assert shadow._run_level_shifts(["kribi"]) == 1
    old = [(today - timedelta(days=60 - i), 15.0 if i < 20 else 11.0) for i in range(60)]
    monkeypatch.setattr(history, "daily_stats", lambda db_key, n: [{"day": str(d), "mean": m, "peak": m} for d, m in old])
    assert shadow._run_level_shifts(["autre"]) == 0                                                  # rupture détectée il y a > 3 jours


# ------------------------------------------------------------------ rapport ombre ↔ tickets
def add_verdicts(detector, db_key, start, count):
    for k in range(count):
        shadow.record_verdict(detector, db_key, start + k * STEP, f"épisode {start}", -5.0)


def add_ticket(tid, db_key, utc_instant, ttype="traffic"):
    tickets.persist(tickets.ChangeSet(inserts=[mk(tid, database_name=db_key, type=ttype, timestamp=fmt(utc_naive_to_local_naive(utc_instant)))]))


def test_report_matches_episodes_with_tickets_and_measures_the_lead(clean_state):
    t0 = aligned_now() - timedelta(hours=10)
    add_verdicts("seasonal", "kribi", t0, 4)                                  # épisode 1 (confirmé)
    add_ticket(1, "kribi", t0 + timedelta(minutes=20))                        # ticket 20 min APRÈS le début : avance de 20 min
    add_verdicts("seasonal", "kribi", t0 + timedelta(hours=3), 2)             # épisode 2 : sans ticket
    add_ticket(2, "autre", t0 + timedelta(hours=5))                           # ticket jamais vu par le détecteur
    report = shadow.shadow_report(2)["detectors"]["seasonal"]
    assert (report["episodes"], report["confirmed"], report["without_ticket"]) == (2, 1, 1)
    assert (report["tickets_total"], report["tickets_covered"], report["recall"]) == (2, 1, 0.5)
    assert report["median_lead_minutes"] == 20


def test_matching_window_is_30_minutes_before_to_60_after(clean_state):
    t0 = aligned_now() - timedelta(hours=6)
    add_verdicts("seasonal", "a", t0, 1)
    add_ticket(1, "a", t0 - timedelta(minutes=31))                            # trop tôt
    add_verdicts("seasonal", "b", t0, 1)
    add_ticket(2, "b", t0 + timedelta(minutes=59))                            # dans la fenêtre
    add_verdicts("seasonal", "c", t0, 1)
    add_ticket(3, "c", t0 + timedelta(minutes=61))                            # trop tard
    add_verdicts("seasonal", "d", t0, 1)
    add_ticket(4, "d", t0 - timedelta(minutes=29))                            # dans la fenêtre (le ticket précède l'alerte : avance négative)
    episodes = {e["db_key"]: e for e in shadow.shadow_report(2)["episodes"]}
    assert [episodes[k]["ticket_id"] for k in "abcd"] == [None, 2, None, 4]
    assert episodes["d"]["lead_minutes"] == -29


def test_close_verdicts_form_one_episode_and_far_ones_two(clean_state):
    t0 = aligned_now() - timedelta(hours=8)
    add_verdicts("seasonal", "kribi", t0, 3)
    add_verdicts("seasonal", "kribi", t0 + timedelta(minutes=25), 2)          # 10 min après la fin du 1er : même épisode ? (écart 15 max)
    add_verdicts("seasonal", "kribi", t0 + timedelta(hours=2), 1)
    counts = [e["count"] for e in sorted(shadow.shadow_report(2)["episodes"], key=lambda e: e["start"])]
    assert counts == [5, 1]


def test_rejected_and_deleted_tickets_do_not_count_as_real_incidents(clean_state):
    t0 = aligned_now() - timedelta(hours=6)
    add_ticket(1, "kribi", t0)
    tickets.delete(1, "alice", "test")
    assert shadow.shadow_report(2)["tickets_in_period"] == 0


# ------------------------------------------------------------------ simulation « et si ? »
# Baisse de 12 % pendant 1 h : sous le seuil de 17 % de « première baisse » → aucune alerte aujourd'hui ;
# avec traffic_consecutive_drops_min_percent = 10 elle en déclenche une. (Une baisse de 25 %, elle, est déjà détectée.)
CANDIDATE = {"traffic_consecutive_drops_min_percent": 10.0}


def flat_history(db_key, end, dip=True):
    rows = []
    for i in range(3 * 288):
        ts = end - (3 * 288 - 1 - i) * STEP
        v = 10.0 * (0.88 if dip and end - timedelta(hours=7) <= ts < end - timedelta(hours=6) else 1.0)
        rows.append({"db_key": db_key, "ts": fmt(ts), "traffic_gbps": v, "loss_pct": 0.5, "latency_ms": 40.0,
                     "tcp_no_response_pct": 1.0, "tcp_block_wp_up_pct": 0.0, "dns_malformed_pct": 0.0})
    with history.local_tx() as conn:
        conn.execute(history._REPLACE_SQL, rows)


def test_a_stricter_candidate_would_have_raised_one_more_alert(clean_state):
    flat_history("kribi", aligned_now())
    result = simulation.simulate(CANDIDATE, days=2)
    assert result["totals"]["current"]["total"] == 0 and result["totals"]["candidate"]["traffic"] == 1 and result["delta"] == 1
    assert result["per_base"] == [{"db_key": "kribi", "current": 0, "candidate": 1, "delta": 1}]
    assert result["new_alerts_total"] == 1 and result["new_alerts_without_ticket"] == 1
    assert result["new_alerts"][0]["db_key"] == "kribi" and result["new_alerts"][0]["followed_by_ticket"] is False


def test_the_simulation_says_whether_the_extra_alert_had_a_real_ticket(clean_state):
    end = aligned_now()
    flat_history("kribi", end)
    add_ticket(1, "kribi", end - timedelta(hours=7))
    result = simulation.simulate(CANDIDATE, days=2)
    assert result["new_alerts_without_ticket"] == 0 and result["new_alerts"][0]["followed_by_ticket"] is True


def test_the_simulation_saves_nothing_and_leaves_the_configuration_untouched(clean_state):
    flat_history("kribi", aligned_now())
    before = config.read_raw()
    simulation.simulate(CANDIDATE, {"kribi": {"alert_pertes_threshold": 9.0}}, days=2)
    assert config.read_raw() == before and governance.list_config_versions() == []


def test_identical_candidate_changes_nothing(clean_state):
    flat_history("kribi", aligned_now())
    assert simulation.simulate({}, days=2)["delta"] == 0


def test_a_per_database_override_is_simulated_for_that_database_only(clean_state):
    end = aligned_now()
    flat_history("kribi", end)
    flat_history("autre", end)
    result = simulation.simulate({}, {"kribi": CANDIDATE}, days=2)
    assert [(b["db_key"], b["delta"]) for b in result["per_base"]] == [("kribi", 1), ("autre", 0)]


def test_a_laxer_candidate_reports_removed_alerts(clean_state):
    flat_history("kribi", aligned_now())
    governance.save_global_config("alice", "1.1.1.1", CANDIDATE)
    result = simulation.simulate({"traffic_consecutive_drops_min_percent": 17.0}, days=2)
    assert result["delta"] == -1 and result["removed_alerts_total"] == 1


def test_invalid_candidates_are_refused_before_any_computation(clean_state):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        simulation.simulate({"alert_traffic_threshold_percent": 400.0})
    with pytest.raises(ValueError):
        simulation.simulate({}, {"kribi": {"alerts_enable_gi": 1}})


def test_the_period_is_bounded_and_aligned_on_five_minutes(clean_state):
    flat_history("kribi", aligned_now())
    result = simulation.simulate({}, days=999)
    assert result["days"] == simulation.MAX_DAYS
    assert int(result["to"][14:16]) % 5 == 0 and int(result["from"][14:16]) % 5 == 0


def test_a_database_with_alerts_disabled_is_not_simulated(clean_state, monkeypatch):
    flat_history("kribi", aligned_now())
    monkeypatch.setattr(simulation, "alerts_enabled", lambda db_key, cfg: False)
    result = simulation.simulate(CANDIDATE, days=2)
    assert result["bases_disabled"] == ["kribi"] and result["delta"] == 0 and result["bases_evaluated"] == 0


def test_anomaly_constants_are_what_the_report_assumes():
    assert anomaly.CONSECUTIVE_POINTS == 2 and shadow.MATCH_BEFORE == timedelta(minutes=30) and shadow.MATCH_AFTER == timedelta(minutes=60)
