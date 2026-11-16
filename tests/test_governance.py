"""Gouvernance : versions de configuration, seuils par base, maintenance/silences — et leur effet sur le cycle de supervision."""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from core import accounts, collector, config, governance, monitoring, tickets
from core.config import fmt, now_local, round5

GOOD = "Abcdefghi1"

def local_now() -> datetime:
    return now_local().replace(tzinfo=None)


# ------------------------------------------------------------------ versions de configuration
def test_a_change_creates_a_version_with_a_readable_diff_and_an_audit_line(clean_state):
    result = governance.save_global_config("alice", "1.1.1.1", {"alert_pertes_threshold": 4.5})
    assert result["version"] and result["changes"] == [{"parameter": "alert_pertes_threshold", "before": 3.0, "after": 4.5}]
    entry = governance.list_audit(action="config_update")[0]
    assert entry["actor"] == "alice" and entry["details"]["changes"][0]["after"] == 4.5
    assert governance.list_config_versions()[0]["actor"] == "alice"


def test_saving_the_same_values_creates_no_version(clean_state):
    governance.save_global_config("alice", "1.1.1.1", {"alert_pertes_threshold": 4.5})
    before = len(governance.list_config_versions())
    result = governance.save_global_config("alice", "1.1.1.1", {"alert_pertes_threshold": 4.5})
    assert result == {"changes": [], "version": None} and len(governance.list_config_versions()) == before


def test_invalid_values_write_nothing(clean_state):
    with pytest.raises(ValidationError):
        governance.save_global_config("alice", "1.1.1.1", {"alert_pertes_threshold": 999})
    assert config.load_config()["alert_pertes_threshold"] == 3.0 and governance.list_config_versions() == []


def test_restore_creates_a_new_version_and_never_rewrites_history(clean_state):
    governance.ensure_initial_version()
    v1 = governance.list_config_versions()[0]["id"]
    governance.save_global_config("alice", "1.1.1.1", {"alert_pertes_threshold": 6.0})
    result = governance.restore_config_version("bob", "2.2.2.2", v1)
    assert config.load_config()["alert_pertes_threshold"] == 3.0
    versions = governance.list_config_versions()
    assert len(versions) == 3 and versions[0]["id"] == result["version"] and "Restauration" in versions[0]["comment"]
    detail = governance.get_config_version(versions[1]["id"])                           # l'ancienne version 6.0 est intacte
    assert detail["config"]["criteria"]["alert_pertes_threshold"] == 6.0
    assert detail["changes_vs_current"] == [{"parameter": "alert_pertes_threshold", "before": 3.0, "after": 6.0}]


def test_restoring_an_unknown_version_fails_cleanly(clean_state):
    with pytest.raises(LookupError):
        governance.restore_config_version("bob", "2.2.2.2", 424242)


def test_an_old_config_file_without_overrides_still_loads(clean_state):
    import json
    legacy = {"criteria": {"alert_pertes_threshold": 4.0}, "alerts": {"alerts_enable_gi": True}}
    config.CONFIG_FILE.write_text(json.dumps(legacy), encoding="utf-8")
    config._cfg_cache["mtime"] = None
    assert config.load_config()["alert_pertes_threshold"] == 4.0 and config.load_overrides() == {}
    assert config.read_raw()["overrides"] == {}


# ------------------------------------------------------------------ seuils par base
def test_overrides_are_merged_per_database_only(clean_state):
    governance.save_overrides("alice", "1.1.1.1", {"kribi": {"alert_traffic_threshold_percent": 60.0}})
    assert config.config_for("kribi")["alert_traffic_threshold_percent"] == 60.0
    assert config.config_for("autre")["alert_traffic_threshold_percent"] == 40.0
    changes = governance.list_audit(action="config_overrides")[0]["details"]["changes"]
    assert changes[0]["parameter"] == "overrides.kribi.alert_traffic_threshold_percent"


@pytest.mark.parametrize("bad", [{"alerts_enable_gi": True}, {"inconnu": 1}, {"alert_pertes_threshold": 500}])
def test_invalid_overrides_are_refused(clean_state, bad):
    with pytest.raises((ValidationError, ValueError)):
        governance.save_overrides("alice", "1.1.1.1", {"kribi": bad})
    assert config.load_overrides() == {}


def test_overrides_survive_a_global_change(clean_state):
    governance.save_overrides("alice", "1.1.1.1", {"kribi": {"alert_pertes_threshold": 7.0}})
    governance.save_global_config("alice", "1.1.1.1", {"alert_traffic_threshold_percent": 35.0})
    assert config.config_for("kribi")["alert_pertes_threshold"] == 7.0


# ------------------------------------------------------------------ maintenance et silences
def test_windows_cover_a_database_or_all_of_them(clean_state):
    now = local_now()
    governance.add_window("kribi", now - timedelta(minutes=5), now + timedelta(minutes=30), "travaux", "alice")
    governance.add_window("autre", now + timedelta(hours=2), now + timedelta(hours=3), "plus tard", "alice")
    silenced = governance.silenced_databases()
    assert governance.is_silenced("kribi", silenced) and not governance.is_silenced("autre", silenced)
    governance.add_window(governance.ALL_DATABASES, now - timedelta(minutes=1), now + timedelta(minutes=5), "tout", "alice")
    assert governance.is_silenced("n_importe_quelle_base", governance.silenced_databases())


def test_window_end_must_follow_its_start(clean_state):
    with pytest.raises(ValueError):
        governance.add_window("kribi", local_now(), local_now() - timedelta(minutes=1), "x", "alice")


def test_expired_windows_are_not_listed_nor_applied(clean_state):
    now = local_now()
    governance.add_window("kribi", now - timedelta(hours=3), now - timedelta(hours=2), "fini", "alice")
    assert governance.list_windows() == [] and governance.silenced_databases() == set()


# ------------------------------------------------------------------ effet sur le cycle de supervision
def snapshot(db_key: str, series_gbps: list[float]) -> collector.DbSnapshot:
    """Relevé simulé : ``series_gbps`` = du plus ancien au plus récent ; la valeur de la veille (J-1) est ajoutée en tête (10 Gb/s)."""
    cfg = config.load_config()
    checkpoint = round5(local_now())
    _, alert_ts, _ = monitoring.build_windows(checkpoint, {**cfg, "traffic_consecutive_drops_count": monitoring.widest_drop_window(cfg, config.load_overrides())})
    values = [10.0] + ([10.0] * (len(alert_ts) - 1 - len(series_gbps))) + series_gbps
    whole = {ts: {"tcp_dw": v * 37_500_000, "udp_dw": 0.0, "icmp_dw": 0.0, "other_dw": 0.0, "#tcp_pkt_dw": 0.0, "#tcp_loss_dw": 0.0}
             for ts, v in zip(alert_ts, values, strict=True)}
    return collector.DbSnapshot(db_key=db_key, ok=True, whole=whole, last_update=datetime.now(UTC).replace(tzinfo=None), mode7d=None)


def run_cycle_on(snaps: dict, monkeypatch) -> dict:
    monkeypatch.setattr(monitoring.notify, "notify_new_tickets", lambda *a, **k: None)
    cfg = config.load_config()
    checkpoint = round5(local_now())
    _, alert_ts, _ = monitoring.build_windows(checkpoint, {**cfg, "traffic_consecutive_drops_count": monitoring.widest_drop_window(cfg, config.load_overrides())})
    return monitoring._process_monitoring(snaps, alert_ts, fmt(checkpoint), cfg, config.load_overrides())


def test_a_50_percent_drop_creates_a_ticket_with_default_thresholds(clean_state, monkeypatch):
    stats = run_cycle_on({"kribi": snapshot("kribi", [5.0])}, monkeypatch)
    assert stats["tickets_crees"] == 1 and [t["database_name"] for t in tickets.load_open()] == ["kribi"]


def test_a_per_database_threshold_changes_the_decision_for_that_database_only(clean_state, monkeypatch):
    governance.save_overrides("alice", "1.1.1.1", {"kribi": {"alert_traffic_threshold_percent": 60.0,
                                                           "traffic_consecutive_drops_min_percent": 90.0}})
    stats = run_cycle_on({"kribi": snapshot("kribi", [5.0]), "autre": snapshot("autre", [5.0])}, monkeypatch)
    assert [t["database_name"] for t in tickets.load_open()] == ["autre"] and stats["tickets_crees"] == 1


def test_maintenance_prevents_ticket_creation_and_is_reported(clean_state, monkeypatch):
    now = local_now()
    governance.add_window("kribi", now - timedelta(minutes=1), now + timedelta(minutes=30), "travaux", "alice")
    stats = run_cycle_on({"kribi": snapshot("kribi", [5.0]), "autre": snapshot("autre", [5.0])}, monkeypatch)
    assert [t["database_name"] for t in tickets.load_open()] == ["autre"] and stats["bases_en_silence"] == 1


def test_a_global_window_silences_every_database(clean_state, monkeypatch):
    now = local_now()
    governance.add_window(governance.ALL_DATABASES, now - timedelta(minutes=1), now + timedelta(minutes=30), "coupure", "alice")
    stats = run_cycle_on({"kribi": snapshot("kribi", [5.0]), "autre": snapshot("autre", [5.0])}, monkeypatch)
    assert tickets.load_open() == [] and stats["bases_en_silence"] == 2


def test_the_collection_window_covers_the_widest_override(clean_state):
    cfg = config.load_config()
    assert monitoring.widest_drop_window(cfg, {}) == cfg["traffic_consecutive_drops_count"]
    assert monitoring.widest_drop_window(cfg, {"kribi": {"traffic_consecutive_drops_count": 9}}) == 9


# ------------------------------------------------------------------ API de supervision
@pytest.fixture
def supervisor(clean_state):
    import main
    accounts.create_user("sup", "Sup", "supervision", GOOD, must_change_password=False)
    client = TestClient(main.app)
    assert client.post("/api/login", json={"username": "sup", "password": GOOD}).status_code == 200
    return client


def test_config_schema_describes_fields_bounds_and_overridable_ones(supervisor):
    schema = supervisor.get("/api/supervision/config-schema").json()
    fields = {f["name"]: f for f in schema["fields"]}
    assert fields["alert_traffic_threshold_percent"]["le"] == 100 and fields["alert_traffic_threshold_percent"]["overridable"]
    assert fields["ticket_validation_required"]["type"] == "bool" and not fields["ticket_validation_required"]["overridable"]
    assert "alert_pertes_threshold" in schema["overridable"]


def test_config_update_api_reports_changes_and_rejects_out_of_range_values(supervisor):
    ok = supervisor.post("/api/supervision/config", json={"alert_pertes_threshold": 5.5}).json()
    assert ok["changes"][0]["after"] == 5.5 and ok["version"]
    assert supervisor.post("/api/supervision/config", json={"alert_pertes_threshold": 500}).status_code == 422
    assert supervisor.get("/api/config").json()["alert_pertes_threshold"] == 5.5


def test_overrides_api_rejects_unknown_databases_and_parameters(supervisor):
    assert supervisor.put("/api/supervision/overrides", json={"overrides": {"base_inconnue": {"alert_pertes_threshold": 5}}}).status_code == 422
    from core import constants
    constants.DB_CONFIGS["kribi"] = {"site": "Kribi"}
    try:
        assert supervisor.put("/api/supervision/overrides", json={"overrides": {"kribi": {"alerts_enable_gi": 1}}}).status_code == 422
        good = supervisor.put("/api/supervision/overrides", json={"overrides": {"kribi": {"alert_pertes_threshold": 5}}})
        assert good.status_code == 200 and good.json()["overrides"] == {"kribi": {"alert_pertes_threshold": 5}}
    finally:
        constants.DB_CONFIGS.pop("kribi", None)


def test_version_history_and_restore_through_the_api(supervisor):
    supervisor.post("/api/supervision/config", json={"alert_pertes_threshold": 5.5})
    versions = supervisor.get("/api/supervision/config-versions").json()["versions"]
    detail = supervisor.get(f"/api/supervision/config-versions/{versions[0]['id']}").json()
    assert detail["changes_vs_current"] == []
    assert supervisor.post("/api/supervision/config-versions/999999/restore").status_code == 404


def test_maintenance_api_add_list_delete_with_audit(supervisor):
    from core import constants
    constants.DB_CONFIGS["kribi"] = {"site": "Kribi"}
    try:
        end = fmt(local_now() + timedelta(hours=1))
        assert supervisor.post("/api/supervision/maintenance", json={"db_key": "nope", "ends_at": end}).status_code == 422
        assert supervisor.post("/api/supervision/maintenance", json={"db_key": "kribi", "ends_at": fmt(local_now() - timedelta(hours=1))}).status_code == 422
        created = supervisor.post("/api/supervision/maintenance", json={"db_key": "kribi", "ends_at": end, "reason": "câblage"}).json()
        windows = supervisor.get("/api/supervision/maintenance").json()["windows"]
        assert [w["id"] for w in windows] == [created["id"]] and windows[0]["reason"] == "câblage"
        assert supervisor.delete(f"/api/supervision/maintenance/{created['id']}").status_code == 200
        assert supervisor.delete(f"/api/supervision/maintenance/{created['id']}").status_code == 404
        assert {e["action"] for e in governance.list_audit()} >= {"maintenance_add", "maintenance_delete"}
    finally:
        constants.DB_CONFIGS.pop("kribi", None)
