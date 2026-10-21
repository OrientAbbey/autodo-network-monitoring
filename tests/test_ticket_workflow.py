"""Workflow des tickets : validation humaine, rejet/suppression + silence, chronologie, notes Orange, retours, accusés partagés."""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from core import accounts, collector, config, governance, monitoring, orange, rules, ticket_log, tickets
from core.config import ConfigParams, fmt, now_local, round5

GOOD = "Abcdefghi1"
NOW = fmt(now_local().replace(tzinfo=None))


def local_now() -> datetime:
    return now_local().replace(tzinfo=None)


def mk(tid, **kw):
    ticket = {"id": tid, "type": "traffic", "site": "Douala", "database_name": "kribi", "timestamp": NOW, "value": 10.0,
              "status": "open", "agent": "DIAGNET", "severity": "critical", "description": "d", "reason": "r", "autin_id": None,
              "pertes": "No", "external_api_ticket_id": None, "closure_timestamp": None, "closure_pending_timestamp": None,
              "closure_reason": None, "ttr_seconds": None, "last_values": [{"timestamp": NOW, "value": 10.0}], "category": 2,
              "pertes_warnings": [], "version": 0, "orange_sync_state": "pending", "orange_attempts": 0,
              "orange_next_retry_at": None, "orange_last_error": None}
    ticket.update(kw)
    return ticket


class FakeOrange:
    """Faux client Orange : enregistre les appels, répond selon un scénario."""
    configured = True

    class breaker:
        state = "closed"

    def __init__(self, results=()):
        self.results, self.calls = list(results), []

    def create_ticket(self, ticket):
        self.calls.append(("create", ticket["id"]))
        return self.results.pop(0)

    def restore(self, orange_id):
        self.calls.append(("restore", orange_id))
        return self.results.pop(0)

    def add_note(self, orange_id, message, author="AUTODO", when=None):
        self.calls.append(("note", orange_id, message))
        return self.results.pop(0)

    def find_by_external_id(self, autodo_id):
        return None


def crash_snapshot(db_key="kribi"):
    cfg = config.load_config()
    _, alert_ts, _ = monitoring.build_windows(round5(local_now()), cfg)
    values = [10.0] + [10.0] * (len(alert_ts) - 2) + [5.0]
    whole = {ts: {"tcp_dw": v * 37_500_000, "udp_dw": 0.0, "icmp_dw": 0.0, "other_dw": 0.0, "#tcp_pkt_dw": 0.0, "#tcp_loss_dw": 0.0}
             for ts, v in zip(alert_ts, values, strict=True)}
    return collector.DbSnapshot(db_key=db_key, ok=True, whole=whole, last_update=datetime.now(UTC).replace(tzinfo=None))


def run_cycle(monkeypatch, *snaps):
    monkeypatch.setattr(monitoring.notify, "notify_new_tickets", lambda *a, **k: None)
    cfg = config.load_config()
    checkpoint = round5(local_now())
    _, alert_ts, _ = monitoring.build_windows(checkpoint, cfg)
    return monitoring._process_monitoring({s.db_key: s for s in snaps}, alert_ts, fmt(checkpoint), cfg, config.load_overrides())


def kinds(ticket_id):
    return [e["kind"] for e in ticket_log.timeline(ticket_id)]


# ------------------------------------------------------------------ chronologie du cycle
def test_creation_is_logged_in_the_timeline_in_the_same_transaction(clean_state, monkeypatch):
    run_cycle(monkeypatch, crash_snapshot())
    ticket = tickets.load_open()[0]
    assert kinds(ticket["id"]) == ["created"] and ticket["validation_state"] == "auto"
    assert ticket["orange_sync_state"] == "pending"


def test_lifecycle_events_closure_armed_then_closed(clean_state):
    ids = iter(range(100, 200))
    drop = [{"timestamp": "2026-01-01 00:00:00", "value": 10.0}, {"timestamp": "2026-01-01 00:05:00", "value": 5.0}]
    ok = [{"timestamp": "2026-01-01 00:15:00", "value": 9.8}]
    cfg = ConfigParams().model_dump()

    def cycle(now, values, verdict):
        ctx = tickets.DbContext("kribi", now, values, [], verdict, rules.Verdict(False), None, cfg)
        existing = {t["type"]: t for t in tickets.load_open() if t["database_name"] == "kribi"}
        cs = tickets.ChangeSet()
        tickets.apply_actions(ctx, rules.plan_actions(verdict.alert, False, "traffic" in existing, False), {"traffic": existing.get("traffic")}, cs, lambda: next(ids))
        tickets.persist(cs)

    cycle("2026-01-01 00:05:00", [{"timestamp": "d", "value": 10.0}] + drop, rules.Verdict(True, "Diminution critique", 50, 2))
    cycle("2026-01-01 00:10:00", drop, rules.Verdict(True, "Diminution critique", 50, 2))     # actualisation : AUCUN évènement
    cycle("2026-01-01 00:15:00", ok, rules.Verdict(False))
    cycle("2026-01-01 00:45:00", ok, rules.Verdict(False))
    assert kinds(100) == ["created", "closure_pending", "closed"]


# ------------------------------------------------------------------ validation humaine
def test_validation_required_holds_the_ticket_back_from_orange(clean_state, monkeypatch):
    governance.save_global_config("alice", "1.1.1.1", {"ticket_validation_required": True})
    run_cycle(monkeypatch, crash_snapshot())
    ticket = tickets.load_open()[0]
    assert ticket["validation_state"] == "pending" and ticket["orange_sync_state"] == "held"
    assert kinds(ticket["id"]) == ["created", "validation_requested"]
    fake = FakeOrange()
    assert orange.sync_outbox(fake, now=NOW)["created"] == 0 and fake.calls == []          # rien ne part avant validation


def test_approval_releases_the_ticket_to_the_outbox_exactly_once(clean_state, monkeypatch):
    governance.save_global_config("alice", "1.1.1.1", {"ticket_validation_required": True})
    run_cycle(monkeypatch, crash_snapshot())
    ticket_id = tickets.load_open()[0]["id"]
    assert tickets.approve(ticket_id, "alice", orange_enabled=True)["id"] == ticket_id
    assert tickets.approve(ticket_id, "bob", orange_enabled=True) is None                 # déjà traité : pas de double validation
    assert orange.sync_outbox(FakeOrange([orange.Result(True, orange_id="TT1")]), now=NOW)["created"] == 1
    assert tickets.get_ticket(ticket_id)["orange_sync_state"] == "synced" and "approved" in kinds(ticket_id)


def test_approval_without_orange_keeps_the_ticket_local(clean_state, monkeypatch):
    governance.save_global_config("alice", "1.1.1.1", {"ticket_validation_required": True})
    run_cycle(monkeypatch, crash_snapshot())
    ticket_id = tickets.load_open()[0]["id"]
    tickets.approve(ticket_id, "alice", orange_enabled=False)
    assert tickets.get_ticket(ticket_id)["orange_sync_state"] == "skipped"


def test_rejection_hides_the_ticket_and_it_never_reaches_orange(clean_state, monkeypatch):
    governance.save_global_config("alice", "1.1.1.1", {"ticket_validation_required": True})
    run_cycle(monkeypatch, crash_snapshot())
    ticket_id = tickets.load_open()[0]["id"]
    assert tickets.reject(ticket_id, "alice", "fausse alerte")["id"] == ticket_id
    ticket = tickets.get_ticket(ticket_id)
    assert ticket["status"] == "deleted" and ticket["validation_state"] == "rejected" and ticket["orange_sync_state"] == "closed"
    assert tickets.load_open() == [] and tickets.load_page()[0] == [] and tickets.counts()["total"] == 0
    assert tickets.load_page(include_deleted=True)[1] == 1
    assert tickets.reject(ticket_id, "bob", "encore") is None                              # plus en attente
    assert orange.sync_outbox(FakeOrange(), now=NOW)["created"] == 0


def test_a_pending_ticket_closed_by_the_rules_is_not_left_held(clean_state):
    ticket = mk(1, orange_sync_state="held", validation_state="pending")
    assert tickets._close_fields(ticket, {"status": "closed"})["orange_sync_state"] == "closed"


# ------------------------------------------------------------------ API : droits et silence après rejet
@pytest.fixture
def supervisor(clean_state):
    import main
    accounts.create_user("sup", "Sup", "supervision", GOOD, must_change_password=False)
    client = TestClient(main.app)
    client.post("/api/login", json={"username": "sup", "password": GOOD})
    return client


def test_decision_endpoints_require_the_supervision_role(clean_state):
    import main
    visitor = TestClient(main.app)
    tickets.persist(tickets.ChangeSet(inserts=[mk(1)]))
    for action in ("approve", "reject", "delete", "comment", "feedback"):
        assert visitor.post(f"/tickets/api/1/{action}", json={"message": "x", "label": "real"}).status_code == 401, action
    assert visitor.get("/tickets/api/1/timeline").status_code == 200                      # la chronologie reste publique
    assert visitor.get("/tickets/api/999/timeline").status_code == 404


def test_rejecting_through_the_api_silences_the_database_and_blocks_the_next_cycle(supervisor, monkeypatch):
    governance.save_global_config("alice", "1.1.1.1", {"ticket_validation_required": True})
    run_cycle(monkeypatch, crash_snapshot())
    ticket_id = tickets.load_open()[0]["id"]
    response = supervisor.post(f"/tickets/api/{ticket_id}/reject", json={"reason": "test réseau interne"})
    assert response.status_code == 200 and response.json()["silence_minutes"] == 120
    windows = governance.list_windows(active_only=True)
    assert [(w["db_key"], w["auto_silence"]) for w in windows] == [("kribi", True)]
    stats = run_cycle(monkeypatch, crash_snapshot())                                       # l'anomalie persiste, mais la base est en silence
    assert stats["tickets_crees"] == 0 and stats["bases_en_silence"] == 1
    assert governance.list_audit(action="ticket_reject")[0]["details"]["reason"] == "test réseau interne"
    assert supervisor.post(f"/tickets/api/{ticket_id}/reject", json={}).status_code == 409


def test_silence_duration_follows_the_setting_and_zero_disables_it(supervisor):
    governance.save_global_config("alice", "1.1.1.1", {"rejection_silence_minutes": 0})
    tickets.persist(tickets.ChangeSet(inserts=[mk(1)]))
    assert supervisor.post("/tickets/api/1/delete", json={"reason": "doublon"}).json()["silence_minutes"] == 0
    assert governance.list_windows() == []


def test_approve_through_the_api(supervisor, monkeypatch):
    governance.save_global_config("alice", "1.1.1.1", {"ticket_validation_required": True})
    run_cycle(monkeypatch, crash_snapshot())
    ticket_id = tickets.load_open()[0]["id"]
    pending = supervisor.get("/api/supervision/tickets-to-validate").json()["tickets"]
    assert [t["id"] for t in pending] == [ticket_id]
    assert supervisor.post(f"/tickets/api/{ticket_id}/approve").status_code == 200
    assert supervisor.post(f"/tickets/api/{ticket_id}/approve").status_code == 409
    assert supervisor.get("/api/supervision/tickets-to-validate").json()["tickets"] == []


# ------------------------------------------------------------------ suppression logique
def test_deleting_a_synced_ticket_schedules_its_restoration_at_orange(clean_state):
    tickets.persist(tickets.ChangeSet(inserts=[mk(1, orange_sync_state="synced", external_api_ticket_id="TT1")]))
    assert tickets.delete(1, "alice", "doublon")["id"] == 1
    ticket = tickets.get_ticket(1)
    assert ticket["status"] == "deleted" and ticket["orange_sync_state"] == "close_pending"
    fake = FakeOrange([orange.Result(True)])
    assert orange.sync_outbox(fake, now=NOW)["restored"] == 1 and fake.calls == [("restore", "TT1")]
    assert tickets.delete(1, "alice", "encore") is None                                    # déjà supprimé
    assert kinds(1) == ["deleted", "orange_restored"]


def test_deleting_a_ticket_never_sent_needs_no_restoration(clean_state):
    tickets.persist(tickets.ChangeSet(inserts=[mk(1)]))
    tickets.delete(1, "alice", "test")
    assert tickets.get_ticket(1)["orange_sync_state"] == "closed"


def test_deleted_tickets_disappear_from_dashboard_alerts(clean_state):
    tickets.persist(tickets.ChangeSet(inserts=[mk(1), mk(2, type="pertes", database_name="autre")]))
    tickets.delete(1, "alice", "test")
    alerts = tickets.dashboard_alerts({})
    assert [a["id"] for a in alerts["traffic"]] == [] and [a["id"] for a in alerts["pertes"]] == [2]


# ------------------------------------------------------------------ notes vers Orange
def note_for(ticket, current, minutes_after_last, **cfg_overrides):
    """Applique ``_maybe_note`` à ``ticket`` et retourne le ChangeSet."""
    cfg = {**ConfigParams().model_dump(), **cfg_overrides}
    now = fmt(local_now())
    ticket["last_note_at"] = ticket.get("last_note_at") or fmt(local_now() - timedelta(minutes=minutes_after_last))
    cs = tickets.ChangeSet()
    tickets._maybe_note(cs, ticket, current, cfg, now)
    return cs


def test_note_text_matches_the_expected_english_format():
    assert ticket_log.note_message("traffic", 7.2, 18.0) == "AUTODO update: current value 7.20 Gb/s (previous 18.00 Gb/s, -60%)"
    assert ticket_log.note_message("pertes", 5.0, 3.0).endswith("(previous 3.00 %, +67%)")


def test_a_small_change_sends_no_note():
    assert note_for(mk(1, value=10.0), 9.5, 60).log == []                                  # −5 % < 10 %


def test_a_big_change_too_soon_after_the_last_note_sends_nothing():
    assert note_for(mk(1, value=10.0), 5.0, 10).log == []                                  # dernière note il y a 10 min < 30


def test_a_big_change_after_the_interval_creates_a_note_for_orange():
    ticket = mk(1, value=18.0)
    cs = note_for(ticket, 7.2, 45)
    assert [(e["kind"], e["to_orange"]) for e in cs.log] == [("orange_note", 1)]
    assert cs.log[0]["message"] == "AUTODO update: current value 7.20 Gb/s (previous 18.00 Gb/s, -60%)"
    assert ticket["last_note_value"] == 7.2 and cs.updates[1]["last_note_value"] == 7.2


def test_the_reference_moves_after_each_note_so_the_same_value_is_not_repeated():
    ticket = mk(1, value=18.0)
    note_for(ticket, 7.2, 45)
    ticket["last_note_at"] = fmt(local_now() - timedelta(minutes=45))
    assert note_for(ticket, 7.0, 45).log == []                                             # 7.2 → 7.0 : −3 %, pas de nouvelle note


@pytest.mark.parametrize("state", ["skipped", "failed", "closed"])
def test_no_note_for_tickets_that_will_never_reach_orange(state):
    assert note_for(mk(1, value=18.0, orange_sync_state=state), 5.0, 90).log == []


def test_note_thresholds_come_from_the_configuration():
    assert note_for(mk(1, value=10.0), 8.0, 60, orange_note_min_change_percent=30.0).log == []
    assert len(note_for(mk(1, value=10.0), 8.0, 10, orange_note_min_interval_minutes=5).log) == 1


def test_a_zero_reference_still_notifies_when_the_value_comes_back():
    assert len(note_for(mk(1, value=0.0, type="traffic"), 6.0, 60).log) == 1


def test_notes_are_persisted_with_the_cycle_and_sent_only_once_the_ticket_exists_at_orange(clean_state):
    ticket = mk(1, value=18.0, orange_sync_state="pending")
    cs = note_for(ticket, 7.2, 45)
    tickets.persist(tickets.ChangeSet(inserts=[mk(1, value=18.0)], log=cs.log))
    fake = FakeOrange([orange.Result(True)])
    assert orange.sync_notes(fake)["waiting"] == 1 and fake.calls == []                    # ticket pas encore créé chez Orange
    tickets.update_fields(1, {"orange_sync_state": "synced", "external_api_ticket_id": "TT1"})
    assert orange.sync_notes(fake)["sent"] == 1
    assert fake.calls == [("note", "TT1", "AUTODO update: current value 7.20 Gb/s (previous 18.00 Gb/s, -60%)")]
    assert orange.sync_notes(FakeOrange([]))["sent"] == 0                                  # jamais renvoyée
    assert ticket_log.timeline(1)[0]["orange_sent"] is True


def test_a_permanent_note_error_is_dropped_and_a_temporary_one_retried(clean_state):
    tickets.persist(tickets.ChangeSet(inserts=[mk(1, orange_sync_state="synced", external_api_ticket_id="TT1")]))
    ticket_log.log_event(1, "orange_note", "note A", to_orange=True)
    assert orange.sync_notes(FakeOrange([orange.Result(False, retryable=True, error="HTTP 503")]))["retry"] == 1
    assert len(ticket_log.unsent_orange_notes()) == 1                                      # sera réessayée
    assert orange.sync_notes(FakeOrange([orange.Result(False, retryable=False, error="HTTP 400")]))["dropped"] == 1
    assert ticket_log.unsent_orange_notes() == []                                          # pas de boucle infinie


def test_notes_of_closed_or_deleted_tickets_are_dropped_not_sent(clean_state):
    tickets.persist(tickets.ChangeSet(inserts=[mk(1, orange_sync_state="synced", external_api_ticket_id="TT1")]))
    ticket_log.log_event(1, "comment", "message", "alice", to_orange=True)
    tickets.delete(1, "alice", "test")
    fake = FakeOrange()
    assert orange.sync_notes(fake)["dropped"] == 1 and fake.calls == []


# ------------------------------------------------------------------ commentaires, retours, accusés
def test_comment_is_in_the_timeline_and_goes_to_orange_only_when_asked(supervisor):
    tickets.persist(tickets.ChangeSet(inserts=[mk(1, orange_sync_state="synced", external_api_ticket_id="TT1")]))
    assert supervisor.post("/tickets/api/1/comment", json={"message": "intervention en cours", "send_to_orange": False}).status_code == 200
    assert supervisor.post("/tickets/api/1/comment", json={"message": "équipe sur place", "send_to_orange": True}).status_code == 200
    events = ticket_log.timeline(1)
    assert [(e["actor"], e["to_orange"]) for e in events] == [("sup", False), ("sup", True)]
    fake = FakeOrange([orange.Result(True)])
    assert orange.sync_notes(fake)["sent"] == 1 and fake.calls[0][2] == "sup: équipe sur place"
    assert supervisor.post("/tickets/api/999/comment", json={"message": "x"}).status_code == 404
    assert supervisor.post("/tickets/api/1/comment", json={"message": ""}).status_code == 422


def test_feedback_is_stored_once_per_ticket_and_validated(supervisor, clean_state):
    from sqlalchemy import text
    tickets.persist(tickets.ChangeSet(inserts=[mk(1)]))
    assert supervisor.post("/tickets/api/1/feedback", json={"label": "false"}).status_code == 200
    assert supervisor.post("/tickets/api/1/feedback", json={"label": "real"}).status_code == 200     # un retour peut être corrigé
    assert supervisor.post("/tickets/api/1/feedback", json={"label": "peut-être"}).status_code == 422
    with clean_state.local_conn() as c:
        assert c.execute(text("SELECT label FROM ticket_feedback WHERE ticket_id = 1")).scalar() == "real"
    assert kinds(1) == ["feedback", "feedback"]


def test_a_visitor_can_acknowledge_and_everybody_sees_it(clean_state):
    import main
    tickets.persist(tickets.ChangeSet(inserts=[mk(1), mk(2, database_name="autre")]))
    assert [a["acked"] for a in tickets.dashboard_alerts({})["traffic"]] == [False, False]
    visitor = TestClient(main.app)
    assert visitor.post("/api/ack", json={"ticket_ids": [1, 999]}).json() == {"acked": 1}          # 999 n'existe pas : ignoré
    by_id = {a["id"]: a for a in tickets.dashboard_alerts({})["traffic"]}                          # (autre navigateur, autre requête)
    assert by_id[1]["acked"] is True and by_id[1]["acked_by"] == "visiteur" and by_id[2]["acked"] is False
    assert visitor.post("/api/ack", json={"ticket_ids": [1]}).json() == {"acked": 0}               # déjà acquitté
    assert kinds(1) == ["acked"]


def test_ack_by_a_logged_in_user_records_the_account_name(supervisor):
    tickets.persist(tickets.ChangeSet(inserts=[mk(1)]))
    supervisor.post("/api/ack", json={"ticket_ids": [1]})
    assert tickets.dashboard_alerts({})["traffic"][0]["acked_by"] == "sup"


def test_ack_payload_is_bounded(clean_state):
    import main
    assert TestClient(main.app).post("/api/ack", json={"ticket_ids": list(range(500))}).status_code == 422
