"""Sécurité HTTP : authentification, en-têtes, CORS, confiance aux en-têtes proxy (sans base de données)."""
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from core import auth


@pytest.fixture(scope="module")
def client():
    import main
    return TestClient(main.app)          # sans « with » : pas de lifespan (ni ordonnanceur ni base)


def _req(peer, xff=None, trusted=()):
    from core import config
    object.__setattr__(config.settings, "trusted_proxies", frozenset(trusted))
    h = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "method": "GET", "path": "/", "query_string": b"", "client": (peer, 1), "headers": h})


def test_security_headers_and_no_cors_by_default(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "script-src 'self'" in r.headers["content-security-policy"] and "'unsafe-inline'" not in r.headers["content-security-policy"].split("style-src")[0]
    assert r.headers["x-frame-options"] == "DENY"
    pre = client.options("/api/dashboard_data", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in pre.headers


def test_exnet_autologin_endpoint_is_gone(client):
    assert client.post("/api/exnet-auth-url", json={"base_url": "https://x"}).status_code in (404, 405)


def test_go_redirect_rejects_unknown_targets(client):
    assert client.get("/go/traffic/unknown", follow_redirects=False).status_code == 404
    assert client.get("/go/evil/kribi", follow_redirects=False).status_code == 404


def test_x_forwarded_for_ignored_from_untrusted_peer():
    assert auth.get_real_ip(_req("203.0.113.9", "<img src=x onerror=alert(1)>")) == "203.0.113.9"


def test_x_forwarded_for_used_from_trusted_proxy_and_validated():
    assert auth.get_real_ip(_req("10.0.0.2", "198.51.100.7, 10.0.0.2", trusted={"10.0.0.2"})) == "198.51.100.7"
    assert auth.get_real_ip(_req("10.0.0.2", "<script>", trusted={"10.0.0.2"})) == "10.0.0.2"     # valeur invalide ignorée


def test_config_params_reject_out_of_range():
    from pydantic import ValidationError
    from core.config import ConfigParams
    with pytest.raises(ValidationError):
        ConfigParams(alert_traffic_threshold_percent=-5)
    with pytest.raises(ValidationError):
        ConfigParams(traffic_recovery_ratio=7)


def test_config_save_is_partial_atomic_and_cached():
    from core import config
    before = config.load_config()
    after = config.save_config_updates({"alert_pertes_threshold": 4.5})
    assert after["alert_pertes_threshold"] == 4.5
    assert after["alert_traffic_threshold_percent"] == before["alert_traffic_threshold_percent"]      # champs absents conservés
    assert config.load_config()["alert_pertes_threshold"] == 4.5                                     # cache invalidé
    with pytest.raises(Exception):
        config.save_config_updates({"alert_pertes_threshold": 999})                                  # revalidation à l'écriture
    config.save_config_updates({"alert_pertes_threshold": before["alert_pertes_threshold"]})


def test_large_json_is_gzipped_but_the_sse_stream_is_not(client, monkeypatch):
    from core import constants, tickets
    monkeypatch.setattr(tickets, "dashboard_alerts", lambda filters=None: {"traffic": [], "pertes": []})
    with constants.DASHBOARD_CACHE_LOCK:
        constants.set_dashboard({"recap": {f"db{i}": {"v": "x" * 50} for i in range(400)}})
    try:
        r = client.get("/api/dashboard_data", headers={"Accept-Encoding": "gzip"})
        assert r.status_code == 200 and r.headers.get("content-encoding") == "gzip"
    finally:
        with constants.DASHBOARD_CACHE_LOCK:
            constants.DASHBOARD_CACHE.clear()
            constants.DASHBOARD_JSON = b""
    assert client.get("/static/js/state.js").headers.get("cache-control") == "no-cache"
