"""Intégration avec une vraie base MariaDB/MySQL (ignoré si indisponible) : transactions, IDs, verrouillage optimiste,
outbox Orange, tampon de visites, collecte DIAGNET en lecture seule."""
import threading
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from core import analytics, constants, orange, rules, tickets
from core.config import ConfigParams, fmt, now_local

CFG = ConfigParams().model_dump()
NOW = fmt(now_local().replace(tzinfo=None))


@pytest.fixture(autouse=True)
def clean(test_db):
    with test_db.local_tx() as c:
        for t in ("tickets_tb", "ticket_counters", "daily_users", "logs", "notification_state"):
            c.execute(text(f"DELETE FROM {t}"))
    tickets.invalidate_alert_cache()
    yield


def mk(tid, **kw):
    t = {"id": tid, "type": "traffic", "site": "Douala", "database_name": "kribi", "timestamp": NOW, "value": 10.0,
         "status": "open", "agent": "DIAGNET", "severity": "critical", "description": "d", "reason": "r", "autin_id": None,
         "pertes": "No", "external_api_ticket_id": None, "closure_timestamp": None, "closure_pending_timestamp": None,
         "closure_reason": None, "ttr_seconds": None, "last_values": [{"timestamp": NOW, "value": 10.0}], "category": 2,
         "pertes_warnings": [], "version": 0, "orange_sync_state": "pending", "orange_attempts": 0,
         "orange_next_retry_at": None, "orange_last_error": None}
    t.update(kw)
    return t


def test_schema_migrations_are_idempotent(test_db):
    assert test_db.ensure_schema() and test_db.ensure_schema()
    with test_db.local_conn() as c:
        cols = {r[0] for r in c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='tickets_tb'"))}
        n = c.execute(text("SELECT COUNT(*) FROM schema_version")).scalar()
    assert {"version", "orange_sync_state", "orange_attempts", "orange_next_retry_at"} <= cols and n == len(test_db.MIGRATIONS)


def test_writes_are_committed_and_visible_from_a_new_connection():           # régression C-01
    cs = tickets.ChangeSet(inserts=[mk(1)])
    tickets.persist(cs)
    assert [t["id"] for t in tickets.load_open()] == [1]


def test_exception_inside_local_tx_rolls_back(test_db):
    with pytest.raises(RuntimeError):
        with test_db.local_tx() as c:
            c.execute(text("INSERT INTO logs (ip_address, action) VALUES ('1.1.1.1', 'x')"))
            raise RuntimeError("boom")
    with test_db.local_conn() as c:
        assert c.execute(text("SELECT COUNT(*) FROM logs")).scalar() == 0


def test_next_id_is_atomic_under_concurrency_and_well_formed():
    ids, lock = [], threading.Lock()
    def worker():
        for _ in range(8):
            i = tickets.next_id()
            with lock:
                ids.append(i)
    ts = [threading.Thread(target=worker) for _ in range(12)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(ids) == len(set(ids)) == 96
    assert all(len(str(i)) == 11 for i in ids)


def test_next_id_does_not_reuse_ids_of_tickets_with_old_timestamps():      # régression M-10
    base = int(now_local().strftime("%Y%m%d")) * 1000
    tickets.persist(tickets.ChangeSet(inserts=[mk(base + 1, timestamp=fmt(datetime.now() - timedelta(days=3)))]))
    assert tickets.next_id() == base + 2


def test_next_id_switches_to_four_digits_after_999():
    base = int(now_local().strftime("%Y%m%d"))
    with __import__("core.db", fromlist=["x"]).local_tx() as c:
        c.execute(text("INSERT INTO ticket_counters (day, n) VALUES (:d, 999)"), {"d": now_local().date()})
    assert tickets.next_id() == int(f"{base}1000")


def test_optimistic_locking_detects_stale_writers():                        # régression M-03
    tickets.persist(tickets.ChangeSet(inserts=[mk(7)]))
    a, b = tickets.load_open()[0], tickets.load_open()[0]
    cs1 = tickets.ChangeSet(); cs1.update(a, {"reason": "A"})
    cs2 = tickets.ChangeSet(); cs2.update(b, {"reason": "B"})
    assert tickets.persist(cs1) == []
    assert tickets.persist(cs2) == [7]                                       # copie périmée : refusée, rien d'écrasé
    t = tickets.load_open()[0]
    assert t["reason"] == "A" and t["version"] == 1


def test_insert_is_strict_never_overwrites(test_db):
    tickets.persist(tickets.ChangeSet(inserts=[mk(9, description="premier")]))
    with pytest.raises(Exception):
        tickets.persist(tickets.ChangeSet(inserts=[mk(9, description="doublon")]))
    assert tickets.load_open()[0]["description"] == "premier"


def test_full_ticket_lifecycle_create_update_close_with_persistence_window():
    seq = iter(range(100, 200))
    ids = lambda: next(seq)
    def cycle(now, traffic_vals, verdict):
        ctx = tickets.DbContext("kribi", now, traffic_vals, [], verdict, rules.Verdict(False), None, CFG)
        open_t = {t["type"]: t for t in tickets.load_open() if t["database_name"] == "kribi"}
        tt = open_t.get("traffic")
        cs = tickets.ChangeSet()
        tickets.apply_actions(ctx, rules.plan_actions(verdict.alert, False, tt is not None, False), {"traffic": tt}, cs, ids)
        tickets.persist(cs)
        return cs
    drop = [{"timestamp": "2026-01-01 00:00:00", "value": 10.0}, {"timestamp": "2026-01-01 00:05:00", "value": 5.0}]
    cs = cycle("2026-01-01 00:05:00", [{"timestamp": "d", "value": 10.0}] + drop, rules.Verdict(True, "Diminution critique instantanée", 50, 2))
    assert len(cs.inserts) == 1 and tickets.load_open()[0]["orange_sync_state"] == "pending"
    cs = cycle("2026-01-01 00:10:00", drop, rules.Verdict(True, "Diminution critique instantanée", 50, 2))     # même incident : pas de doublon
    assert not cs.inserts and len(tickets.load_open()) == 1
    ok = [{"timestamp": "2026-01-01 00:15:00", "value": 9.8}]
    cycle("2026-01-01 00:15:00", ok, rules.Verdict(False))                                    # critère OK : fermeture armée
    t = tickets.load_open()[0]; assert t["closure_pending_timestamp"]
    cycle("2026-01-01 00:20:00", ok, rules.Verdict(False))                                    # fenêtre non écoulée
    assert len(tickets.load_open()) == 1
    cycle("2026-01-01 00:45:00", ok, rules.Verdict(False))                                    # 25 min écoulées : fermé
    assert tickets.load_open() == []
    closed = tickets.load_page(status="closed")[0][0]
    assert closed["status"] == "closed" and closed["ttr_seconds"] > 0 and closed["orange_sync_state"] == "closed"


# ------------------------------------------------------------------ outbox Orange
class FakeOrange:
    configured = True
    class breaker: state = "closed"
    def __init__(self, results): self.results, self.calls = list(results), []
    def create_ticket(self, t): self.calls.append(("create", t["id"])); return self.results.pop(0)
    def restore(self, oid): self.calls.append(("restore", oid)); return self.results.pop(0)
    def find_by_external_id(self, i): return None
    def get_status(self, oid): self.calls.append(("status", oid)); return self.results.pop(0)


def test_outbox_creates_pending_tickets_and_marks_them_synced():
    tickets.persist(tickets.ChangeSet(inserts=[mk(1), mk(2)]))
    fake = FakeOrange([orange.Result(True, orange_id="TT1"), orange.Result(True, orange_id="TT2")])
    stats = orange.sync_outbox(fake, now=NOW)
    assert stats["created"] == 2
    got = {t["id"]: t for t in tickets.load_open()}
    assert got[1]["external_api_ticket_id"] == "TT1" and got[1]["orange_sync_state"] == "synced"
    assert orange.sync_outbox(FakeOrange([]), now=NOW)["created"] == 0      # rien à refaire


def test_outbox_retry_schedules_backoff_then_gives_up():
    tickets.persist(tickets.ChangeSet(inserts=[mk(1)]))
    orange.sync_outbox(FakeOrange([orange.Result(False, retryable=True, error="HTTP 503")]), now=NOW)
    t = tickets.load_open()[0]
    assert t["orange_attempts"] == 1 and t["orange_next_retry_at"] > NOW and t["orange_sync_state"] == "pending"
    again = orange.sync_outbox(FakeOrange([]), now=NOW)                      # échéance non atteinte : aucun appel
    assert again == {"created": 0, "restored": 0, "retry": 0, "failed": 0}
    failed = []
    tickets.update_fields(1, {"orange_attempts": 7, "orange_next_retry_at": None})
    orange.sync_outbox(FakeOrange([orange.Result(False, retryable=True, error="HTTP 503")]), on_failed=lambda t, e: failed.append(t["id"]), now=NOW)
    assert tickets.load_open()[0]["orange_sync_state"] == "failed" and failed == [1]


def test_outbox_non_retryable_error_fails_immediately():
    tickets.persist(tickets.ChangeSet(inserts=[mk(1)]))
    orange.sync_outbox(FakeOrange([orange.Result(False, retryable=False, error="HTTP 400")]), now=NOW)
    assert tickets.load_open()[0]["orange_sync_state"] == "failed"


def test_closing_a_ticket_never_sent_to_orange_skips_the_restore():
    t = mk(1)
    fields = tickets._close_fields(t, {"status": "closed"})
    assert fields["orange_sync_state"] == "closed"
    t2 = mk(2, orange_sync_state="synced", external_api_ticket_id="TT2")
    assert tickets._close_fields(t2, {"status": "closed"})["orange_sync_state"] == "close_pending"


def test_close_pending_triggers_restore_once():
    tickets.persist(tickets.ChangeSet(inserts=[mk(1, orange_sync_state="close_pending", external_api_ticket_id="TT1", status="closed")]))
    fake = FakeOrange([orange.Result(True)])
    assert orange.sync_outbox(fake, now=NOW)["restored"] == 1 and fake.calls == [("restore", "TT1")]
    assert orange.sync_outbox(FakeOrange([]), now=NOW)["restored"] == 0


def test_claim_lease_prevents_double_processing():
    tickets.persist(tickets.ChangeSet(inserts=[mk(1)]))
    first = tickets.claim_for_orange(("pending",), 10, NOW)
    second = tickets.claim_for_orange(("pending",), 10, NOW)
    assert len(first) == 1 and second == []


def test_reconcile_only_closes_on_terminal_status_whitelist():
    tickets.persist(tickets.ChangeSet(inserts=[mk(1, orange_sync_state="synced", external_api_ticket_id="A"),
                                               mk(2, orange_sync_state="synced", external_api_ticket_id="B"),
                                               mk(3, orange_sync_state="synced", external_api_ticket_id="C")]))
    def st(code): return orange.Result(True, data={"current": code, "entries": []})
    fake = FakeOrange([st("InProgress"), st(None), st("Restored")])
    stats = orange.reconcile(fake, now=NOW)
    assert stats["closed"] == 1 and stats["unknown"] == 1
    assert sorted(t["id"] for t in tickets.load_open()) == [1, 2]            # statut absent/inconnu : on ne ferme PAS


# ------------------------------------------------------------------ lectures
def test_dashboard_alerts_filters_and_cache():
    tickets.persist(tickets.ChangeSet(inserts=[mk(1), mk(2, type="pertes", status="closed")]))
    constants.LOCATION_MAPPING["kribi"] = "[Sortie Internationale] KRIBI"
    a = tickets.dashboard_alerts({})
    assert len(a["traffic"]) == 1 and len(a["pertes"]) == 1
    assert tickets.dashboard_alerts({"status": "open"})["pertes"] == []
    assert tickets.dashboard_alerts({"type": "traffic,pertes", "period": "week"})["traffic"]
    assert tickets.dashboard_alerts({}) is a                                  # servi depuis le cache TTL


def test_load_page_paginates_open_first():
    tickets.persist(tickets.ChangeSet(inserts=[mk(i, status="closed" if i % 2 else "open") for i in range(1, 11)]))
    rows, total = tickets.load_page(limit=4, offset=0)
    assert total == 10 and len(rows) == 4 and all(r["status"] == "open" for r in rows)
    assert tickets.load_page(limit=4, offset=8)[0][-1]["status"] == "closed"
    assert tickets.counts() == {"total": 10, "open": 5}


# ------------------------------------------------------------------ visites (écritures groupées)
def test_visit_buffer_flushes_many_hits_in_one_upsert_and_counts_sessions(test_db):
    for i in range(50):
        analytics.visits.add("2026-01-01", "client-1", f"2026-01-01 10:{i % 60:02d}:00", "1.2.3.4", "ua")
    analytics.visits.add("2026-01-01", "client-1", "2026-01-01 12:30:00", "1.2.3.4", "ua")     # nouvelle session (> 60 min)
    assert analytics.flush_visits() == 1
    with test_db.local_conn() as c:
        row = c.execute(text("SELECT visits_count, last_ip FROM daily_users WHERE client_id='client-1'")).first()
    assert row[0] == 2 and row[1] == "1.2.3.4"
    analytics.visits.add("2026-01-01", "client-1", "2026-01-01 12:40:00", "1.2.3.4", "ua")
    analytics.flush_visits()
    with test_db.local_conn() as c:
        assert c.execute(text("SELECT visits_count FROM daily_users")).scalar() == 2         # même session : inchangé


def test_visit_buffer_is_capped():
    analytics.visits.drain()
    for i in range(analytics.MAX_BUFFER + 50):
        analytics.visits.add("d", f"c{i}", "2026-01-01 10:00:00", "ip", "ua")
    assert len(analytics.visits.drain()) == analytics.MAX_BUFFER


def test_purge_removes_only_expired_rows(test_db):
    old = (now_local().replace(tzinfo=None) - timedelta(days=200)).strftime("%Y-%m-%d")
    new = now_local().strftime("%Y-%m-%d")
    with test_db.local_tx() as c:
        for d in (old, new):
            c.execute(text("INSERT INTO daily_users (`date`, client_id, last_seen, visits_count) VALUES (:d, 'x', NOW(), 1)"), {"d": d})
    assert analytics.purge(90)["daily_users"] == 1
    with test_db.local_conn() as c:
        assert c.execute(text("SELECT COUNT(*) FROM daily_users")).scalar() == 1


def test_access_logs_stats_from_a_single_query(test_db):
    with test_db.local_tx() as c:
        for cid, ip in (("a", "1.1.1.1"), ("b", "1.1.1.1"), ("c", "2.2.2.2")):
            c.execute(text("INSERT INTO daily_users (`date`, client_id, last_seen, visits_count, last_ip) VALUES (:d, :c, NOW(), 2, :ip)"),
                      {"d": now_local().strftime("%Y-%m-%d"), "c": cid, "ip": ip})
    r = analytics.access_logs("today")
    assert r["access_stats"]["total_unique_ips"] == 2 and r["access_stats"]["today_unique_count"] == 2
    assert sum(x["access_count"] for x in r["access_logs"]) == 6


def test_notification_state_is_stored_in_database(test_db):
    from core import notify
    assert notify._already_notified("A@x.test") == set()
    notify._remember("A@x.test", [1, 2])
    assert notify._already_notified("a@x.test") == {"1", "2"}
    notify._remember("a@x.test", [3])
    assert notify._already_notified("a@x.test") == {"3"}


# ------------------------------------------------------------------ DIAGNET : lecture seule, parallèle, isolation des pannes
@pytest.fixture()
def fake_diagnet(test_db):
    from sqlalchemy import text as t
    from core import collector
    with test_db.local_tx() as c:
        for ddl in ("CREATE TABLE IF NOT EXISTS whole_5min (SAUV_td32 DATETIME PRIMARY KEY, tcp_dw BIGINT, udp_dw BIGINT, icmp_dw BIGINT, other_dw BIGINT, `#tcp_loss_dw` BIGINT, `#tcp_pkt_dw` BIGINT, `#tcp_no_response_to_syn` BIGINT, `#tcp_block_wp_up` BIGINT, `#tcp_synack_core` BIGINT, Irt_tcp_core_td22 BIGINT, `#tcp_sessions` BIGINT)",
                    "CREATE TABLE IF NOT EXISTS apn_5min (SAUV_td32 DATETIME, apn VARCHAR(64), tcp_dw BIGINT, udp_dw BIGINT, icmp_dw BIGINT, other_dw BIGINT, `#tcp_loss_dw` BIGINT, `#tcp_pkt_dw` BIGINT, `#tcp_no_response_to_syn` BIGINT, `#tcp_block_wp_up` BIGINT, `#tcp_synack_core` BIGINT, Irt_tcp_core_td22 BIGINT, `#tcp_sessions` BIGINT)",
                    "CREATE TABLE IF NOT EXISTS dns_5min (SAUV_td32 DATETIME, ip VARCHAR(64), supra VARCHAR(8), qtype_MALFORMED INT, tooshort_src INT, queries_woresp INT, queries INT)",
                    "DELETE FROM whole_5min"):
            c.execute(t(ddl))
        base = datetime(2026, 1, 1, 10, 0)
        for i in range(12):
            c.execute(t("INSERT INTO whole_5min (SAUV_td32, tcp_dw, udp_dw, icmp_dw, other_dw, `#tcp_loss_dw`, `#tcp_pkt_dw`) VALUES (:d, 3750000000, 0, 0, 0, 90, 1000)"),
                      {"d": base + timedelta(minutes=5 * i)})
    collector.reset_cache()
    from seed_demo_data import admin_conn      # compte DIAGNET avec droits d'ÉCRITURE : le garde-fou READ ONLY doit les neutraliser
    adm = admin_conn(); cur = adm.cursor()
    for host in ("127.0.0.1", "localhost"):
        cur.execute(f"CREATE USER IF NOT EXISTS 'diag'@'{host}' IDENTIFIED BY 'diagpass'")
        cur.execute(f"GRANT ALL ON autodo_test.* TO 'diag'@'{host}'")
    adm.commit(); adm.close()
    saved = (dict(constants.DB_CONFIGS), constants.LABELS)
    h, p = test_db.local_engine().url.host, test_db.local_engine().url.port
    constants.DB_CONFIGS = {"ok1": {"host": h, "port": p, "db_name": "autodo_test", "site": "S"},
                            "ok2": {"host": h, "port": p, "db_name": "autodo_test", "site": "S"},
                            "dead": {"host": "127.0.0.1", "port": 1, "db_name": "nope", "site": "S"}}
    constants.LABELS = {"international": {"ok1": "A", "ok2": "B", "dead": "C"}, "utilisateur": {}, "roaming": {}}
    yield base
    constants.DB_CONFIGS, constants.LABELS = saved


def test_collector_isolates_a_dead_source_and_reads_in_parallel(fake_diagnet, test_db):
    from core import collector
    ts = [fmt(fake_diagnet + timedelta(minutes=5 * i)) for i in range(12)]
    snaps = collector.collect_all(ts, ts[-6:])
    assert snaps["ok1"].ok and snaps["ok2"].ok and not snaps["dead"].ok and snaps["dead"].error
    assert len(snaps["ok1"].whole) == 12 and snaps["ok1"].whole[ts[0]]["#tcp_loss_dw"] == 90.0
    assert snaps["ok1"].last_update == fake_diagnet + timedelta(minutes=55)
    assert snaps["ok1"].mode7d is not None and "ok1" in collector._mode_cache


def test_mode_is_cached_between_cycles(fake_diagnet):
    from core import collector
    ts = [fmt(fake_diagnet)]
    a = collector.collect_all(ts, ts)["ok1"]
    first_stamp = collector._mode_cache["ok1"][0]
    b = collector.collect_all(ts, ts)["ok1"]
    assert collector._mode_cache["ok1"][0] == first_stamp and a.mode7d == b.mode7d


def test_diagnet_session_is_read_only_even_if_the_account_can_write(fake_diagnet, test_db):
    from sqlalchemy.exc import DBAPIError
    from core import db
    h, p = test_db.local_engine().url.host, test_db.local_engine().url.port
    with db.diag_engine(h, "autodo_test", p).connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM whole_5min")).scalar() == 12
        with pytest.raises(DBAPIError):
            conn.execute(text("DELETE FROM whole_5min"))
        with pytest.raises(DBAPIError):
            conn.execute(text("INSERT INTO whole_5min (SAUV_td32) VALUES ('2030-01-01 00:00:00')"))


def test_series_builder_and_detection_see_the_loss_counters(fake_diagnet):
    """Régression C-02 : les compteurs de pertes lus en base alimentent bien la détection."""
    from core import collector, monitoring
    ts = [fmt(fake_diagnet + timedelta(minutes=5 * i)) for i in range(12)]
    snap = collector.collect_all(ts, ts[-6:])["ok1"]
    stamps, traffic, loss = monitoring.build_series(snap, ts[-6:])
    assert loss[-1] == pytest.approx(9.0) and traffic[-1] == pytest.approx(100.0)
    assert rules.detect_pertes(loss, CFG).alert
