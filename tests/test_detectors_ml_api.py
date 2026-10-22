"""D4 Isolation Forest, D7 qualité d'alerte (apprentissage) et API de la phase B (historique, détecteurs, analyses, simulation)."""
import shutil
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from core import accounts, anomaly_ml, config, governance, history, shadow, tickets, ticket_log
from core.config import fmt, settings
from tests.test_history_shadow import STEP, add_verdicts, aligned_now, fill_history, flat_history, snapshot_at
from tests.test_ticket_workflow import mk

GOOD = "Abcdefghi1"
needs_ml = pytest.mark.skipif(not anomaly_ml.ml_available(), reason="scikit-learn non installé")


@pytest.fixture(autouse=True)
def clean_models():
    yield
    shutil.rmtree(settings.models_dir, ignore_errors=True)
    anomaly_ml._model_cache.clear()


# ------------------------------------------------------------------ D4 : Isolation Forest
def perturb_last_slots(db_key, end, **columns):
    """Modifie les deux derniers relevés d'une base (trafic ×facteur, autres métriques fixées)."""
    factor = columns.pop("traffic_factor", 1.0)
    with history.local_tx() as c:
        for k in range(2):
            c.execute(text("UPDATE metrics_5min SET traffic_gbps = traffic_gbps * :f, loss_pct = :l, latency_ms = :m, "
                           "tcp_no_response_pct = :n WHERE db_key = :k AND ts = :t"),
                      {"f": factor, "l": columns.get("loss", 1.0), "m": columns.get("latency", 50.0),
                       "n": columns.get("no_response", 1.0), "k": db_key, "t": fmt(end - k * STEP)})


@needs_ml
def test_isolation_forest_trains_and_flags_an_unusual_combination(clean_state):
    end = aligned_now()
    fill_history("kribi", 14, end=end)
    metrics = anomaly_ml.train_isolation("kribi")
    assert metrics["rows"] >= anomaly_ml.ISOLATION_MIN_ROWS and anomaly_ml.has_model("iforest_kribi")
    assert anomaly_ml.isolation_finding("kribi", end).alert is False                              # relevé habituel
    perturb_last_slots("kribi", end, traffic_factor=0.4, loss=25.0, latency=400.0, no_response=20.0)
    finding = anomaly_ml.isolation_finding("kribi", end)
    assert finding.alert and finding.explanation.startswith("Combinaison inhabituelle") and "pertes 25,00 %" in finding.explanation and "% de l'attendu" in finding.explanation


@needs_ml
def test_an_unusual_increase_is_not_reported_as_a_drop(clean_state):
    end = aligned_now()
    fill_history("kribi", 14, end=end)
    anomaly_ml.train_isolation("kribi")
    perturb_last_slots("kribi", end, traffic_factor=1.8, loss=25.0, latency=400.0, no_response=20.0)
    assert anomaly_ml.isolation_finding("kribi", end).alert is False


@needs_ml
def test_isolation_needs_enough_history_and_a_trained_model(clean_state):
    fill_history("kribi", 2)
    with pytest.raises(anomaly_ml.NotEnoughData, match="importez"):
        anomaly_ml.train_isolation("kribi")
    assert anomaly_ml.isolation_finding("kribi", aligned_now()) is None                           # pas de modèle : pas de verdict


def test_model_names_cannot_escape_the_models_directory():
    with pytest.raises(ValueError):
        anomaly_ml._model_path("../../etc/passwd")
    assert anomaly_ml._model_path("iforest_kribi").parent == settings.models_dir


def test_without_scikit_learn_training_is_refused_cleanly(monkeypatch):
    monkeypatch.setattr(anomaly_ml, "ml_available", lambda: False)
    with pytest.raises(anomaly_ml.MlUnavailable):
        anomaly_ml.train_isolation("kribi")
    with pytest.raises(anomaly_ml.MlUnavailable):
        anomaly_ml.train_quality()
    assert anomaly_ml.isolation_finding("kribi", aligned_now()) is None and anomaly_ml.quality_score({}) is None


@needs_ml
def test_the_shadow_runs_the_trained_isolation_forest(clean_state):
    end = aligned_now()
    fill_history("kribi", 14, end=end)
    anomaly_ml.train_isolation("kribi")
    perturb_last_slots("kribi", end, traffic_factor=0.4, loss=25.0, latency=400.0, no_response=20.0)
    stats = shadow.run_shadow({"kribi": snapshot_at("kribi", end)})
    assert stats["isolation"] == 1 and stats["seasonal"] == 1 and tickets.load_open() == []


# ------------------------------------------------------------------ D7 : qualité d'alerte
def labeled_corpus(count: int, only: str | None = None) -> None:
    """``count`` tickets étiquetés répartis sur autant de jours : « réel » = jour ouvré à valeur élevée, sinon « faux positif »."""
    start = aligned_now() - timedelta(days=count + 5)
    inserts = []
    for i in range(count):
        real = i % 3 != 0
        ts = start + timedelta(days=i, hours=9 if real else 3)
        inserts.append(mk(1000 + i, timestamp=fmt(ts), value=14.0 if real else 2.0, category=2 if real else 3,
                          database_name=f"base{i % 4}", type="traffic" if real else "pertes"))
    tickets.persist(tickets.ChangeSet(inserts=inserts))
    for i in range(count):
        tickets.set_feedback(1000 + i, only or ("real" if i % 3 != 0 else "false"), "sup")


@needs_ml
def test_quality_model_trains_with_a_temporal_validation_and_scores_tickets(clean_state):
    labeled_corpus(150)
    metrics = anomaly_ml.train_quality()
    assert (metrics["labeled"], metrics["train"], metrics["test"]) == (150, 120, 30) and metrics["validation"] == "temporelle 80/20"
    assert {"accuracy", "precision_real", "recall_real", "precision_false", "recall_false", "baseline_accuracy"} <= set(metrics)
    likely_real = anomaly_ml.quality_score(mk(1, timestamp=fmt(aligned_now()), value=14.0, category=2))
    likely_false = anomaly_ml.quality_score(mk(2, timestamp=fmt(aligned_now().replace(hour=3)), value=2.0, category=3, type="pertes"))
    assert 0.0 <= likely_false < likely_real <= 1.0


def test_quality_features_never_use_information_known_only_afterwards():
    assert not any("ttr" in name or "clos" in name for name in anomaly_ml.QUALITY_FEATURES)
    base = mk(1, timestamp="2026-03-02 09:00:00", value=14.0, category=2)
    later = {**base, "ttr_seconds": 99999, "closure_reason": "faux", "status": "closed", "pertes": "Yes"}
    assert anomaly_ml.ticket_features(base, 0) == anomaly_ml.ticket_features(later, 0)


@needs_ml
def test_quality_training_refuses_too_few_or_one_sided_labels(clean_state):
    labeled_corpus(20)
    with pytest.raises(anomaly_ml.NotEnoughData, match="au moins"):
        anomaly_ml.train_quality()
    with clean_state.local_tx() as c:
        for table in ("ticket_feedback", "ticket_events", "tickets_tb"):
            c.execute(text(f"DELETE FROM {table}"))
    labeled_corpus(120, only="real")
    with pytest.raises(anomaly_ml.NotEnoughData, match="« réel » ET"):
        anomaly_ml.train_quality()


@needs_ml
def test_quality_score_is_logged_in_shadow_without_any_decision(clean_state):
    labeled_corpus(150)
    anomaly_ml.train_quality()
    created = mk(1, timestamp=fmt(aligned_now()), value=14.0, category=2)
    tickets.persist(tickets.ChangeSet(inserts=[created]))
    assert shadow.run_shadow({}, [created])["quality"] == 1
    event = [e for e in ticket_log.timeline(1) if e["kind"] == "quality_score"][0]
    assert "aucune décision" in event["message"] and tickets.get_ticket(1)["status"] == "open"


def test_no_quality_event_without_a_model(clean_state):
    created = mk(1, timestamp=fmt(aligned_now()))
    tickets.persist(tickets.ChangeSet(inserts=[created]))
    assert shadow.run_shadow({}, [created])["quality"] == 0


# ------------------------------------------------------------------ API
@pytest.fixture
def app(clean_state):
    import main
    return main.app


def client_for(app, username, role):
    accounts.create_user(username, username, role, GOOD, must_change_password=False)
    client = TestClient(app)
    assert client.post("/api/login", json={"username": username, "password": GOOD}).status_code == 200
    return client


@pytest.fixture
def admin(app):
    return client_for(app, "adm", "admin")


def test_phase_b_routes_are_protected_by_role(app):
    visitor, supervisor = TestClient(app), client_for(app, "sup", "supervision")
    admin_urls = ["/api/admin/history", "/api/admin/detectors", "/api/admin/detectors/report",
                  "/api/admin/analysis/forecast?db_key=x", "/api/admin/analysis/capacity?db_key=x&capacity_gbps=10",
                  "/api/admin/analysis/level-shifts?db_key=x"]
    for url in admin_urls:
        assert visitor.get(url).status_code == 401 and supervisor.get(url).status_code == 403, url
    assert visitor.post("/api/supervision/simulation", json={}).status_code == 401
    assert supervisor.post("/api/supervision/simulation", json={}).status_code == 200            # la simulation sert aux superviseurs
    assert visitor.get("/api/sparklines").status_code == 200                                      # mini-courbes : publiques


def test_history_endpoints_and_the_single_import_rule(admin, monkeypatch):
    fill_history("kribi", 2)
    stats = admin.get("/api/admin/history").json()
    assert stats["bases"]["kribi"]["rows"] == 576 and stats["retention_days"] == settings.history_retention_days
    monkeypatch.setattr(history, "start_backfill", lambda days: True)
    assert admin.post("/api/admin/history/backfill", json={"days": 30}).status_code == 202
    monkeypatch.setattr(history, "start_backfill", lambda days: False)
    assert admin.post("/api/admin/history/backfill", json={"days": 30}).status_code == 409
    assert admin.post("/api/admin/history/backfill", json={"days": 500}).status_code == 422
    assert governance.list_audit(action="history_backfill")[0]["details"] == {"days": 30}


def test_detector_listing_toggle_and_audit(admin):
    listing = admin.get("/api/admin/detectors").json()
    assert [d["code"] for d in listing["detectors"]] == ["D1", "D4", "D5", "D7", "D3"] and listing["shadow_enabled"] is True
    assert all(d["enabled"] for d in listing["detectors"])
    assert admin.post("/api/admin/detectors/seasonal", json={"enabled": False}).json() == {"key": "seasonal", "enabled": False}
    assert next(d for d in admin.get("/api/admin/detectors").json()["detectors"] if d["key"] == "seasonal")["enabled"] is False
    assert admin.post("/api/admin/detectors/inconnu", json={"enabled": True}).status_code == 404
    assert governance.list_audit(action="detector_toggle")[0]["target"] == "seasonal"


def test_training_endpoint_statuses(admin, monkeypatch):
    assert admin.post("/api/admin/detectors/seasonal/train").status_code == 400                   # D1 n'a pas d'entraînement
    assert admin.post("/api/admin/detectors/quality/train").status_code in (422, 501)
    monkeypatch.setattr(anomaly_ml, "ml_available", lambda: False)
    unavailable = admin.post("/api/admin/detectors/isolation/train")
    assert unavailable.status_code == 501 and "scikit-learn" in unavailable.json()["detail"]
    assert next(d for d in admin.get("/api/admin/detectors").json()["detectors"] if d["key"] == "quality")["available"] is False


@needs_ml
def test_training_through_the_api_records_state_and_audit(admin):
    assert admin.post("/api/admin/detectors/isolation/train").status_code == 422                  # aucun historique
    assert admin.post("/api/admin/detectors/quality/train").status_code == 422                    # aucune étiquette
    fill_history("kribi", 14)
    fill_history("court", 2, seed=2)
    result = admin.post("/api/admin/detectors/isolation/train").json()
    assert result["bases"] == 1 and [s["db_key"] for s in result["skipped"]] == ["court"]
    detector = next(d for d in admin.get("/api/admin/detectors").json()["detectors"] if d["key"] == "isolation")
    assert detector["models"] == ["kribi"] and detector["trained_at"] and detector["metrics"]["bases"] == 1
    assert governance.list_audit(action="detector_train")[0]["target"] == "isolation"
    assert admin.post("/api/admin/detectors/isolation/train", json={"db_key": "../etc"}).status_code == 422


def test_shadow_report_endpoint(admin):
    t0 = aligned_now() - timedelta(hours=5)
    add_verdicts("seasonal", "kribi", t0, 3)
    report = admin.get("/api/admin/detectors/report?days=2").json()
    assert report["detectors"]["seasonal"]["episodes"] == 1 and report["episodes"][0]["db_key"] == "kribi"
    assert admin.get("/api/admin/detectors/report?days=999").status_code == 422


def test_forecast_endpoint(admin):
    assert admin.get("/api/admin/analysis/forecast?db_key=inconnue").status_code == 404
    fill_history("court", 1)
    assert admin.get("/api/admin/analysis/forecast?db_key=court&days=2").status_code == 422        # moins de 2 jours de données utiles... 
    fill_history("kribi", 5)
    body = admin.get("/api/admin/analysis/forecast?db_key=kribi&days=4").json()
    assert len(body["expected"]) == len(body["lower"]) == len(body["upper"]) == 288 and body["step_minutes"] == 5
    assert body["forecast_start"] > body["last_point"]
    assert admin.get("/api/admin/analysis/forecast?db_key=../x").status_code == 422


def test_capacity_and_level_shift_endpoints(admin):
    fill_history("kribi", 12)
    capacity = admin.get("/api/admin/analysis/capacity?db_key=kribi&capacity_gbps=25").json()
    assert capacity["status"] in ("stable", "growing") and capacity["capacity_gbps"] == 25
    assert admin.get("/api/admin/analysis/capacity?db_key=kribi&capacity_gbps=0").status_code == 422
    assert admin.get("/api/admin/analysis/capacity?db_key=vide&capacity_gbps=25").json()["status"] == "insufficient_data"
    shifts = admin.get("/api/admin/analysis/level-shifts?db_key=kribi&days=60").json()
    assert shifts["shifts"] == [] and shifts["days_of_data"] >= 8


def test_simulation_endpoint(app):
    supervisor = client_for(app, "sup", "supervision")
    flat_history("kribi", aligned_now())
    body = {"config": {"traffic_consecutive_drops_min_percent": 10.0}, "days": 2}
    result = supervisor.post("/api/supervision/simulation", json=body).json()
    assert result["delta"] == 1 and result["new_alerts_without_ticket"] == 1
    assert supervisor.post("/api/supervision/simulation", json={"config": {"inconnu": 1}}).status_code == 422
    assert supervisor.post("/api/supervision/simulation", json={"config": {"alert_traffic_threshold_percent": 400}}).status_code == 422
    assert supervisor.post("/api/supervision/simulation", json={"days": 90}).status_code == 422
    assert governance.list_config_versions() == [] and config.read_raw()["overrides"] == {}        # rien n'a été enregistré
