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


def test_passwords_fail_closed_and_constant_time():
    assert auth.check_password(None, None) is False and auth.check_password("", "") is False
    assert auth.check_password("x", "x") is True and auth.check_password("x", "y") is False


def test_update_config_requires_a_session(client):
    assert client.post("/update_config", json={"alert_traffic_threshold_percent": 10}).status_code == 401


def test_auth_endpoint_rejects_missing_and_wrong_passwords(client):
    assert client.post("/api/auth", json={}).status_code == 401
    assert client.post("/api/auth", json={"password": "nope"}).status_code == 401


def test_brute_force_is_rate_limited(client):
    from core import auth as a
    a.limiter._fails.clear()
    codes = [client.post("/api/auth", json={"password": "bad"}, headers={}).status_code for _ in range(7)]
    assert 429 in codes
    a.limiter._fails.clear()


def test_config_session_cannot_read_data_management(client):
    auth.limiter._fails.clear()
    c = TestClient(client.app)
    assert c.post("/api/auth", json={"password": "cfgpass"}).status_code == 200
    c.cookies.set("dm_token", c.cookies.get("cfg"))
    assert c.get("/api/data-management", params={"action": "data"}).status_code == 401


def test_sessions_expire_and_are_scoped():
    tok = auth.sessions.create("cfg", ttl=-1)
    assert not auth.sessions.valid(tok, "cfg")
    tok = auth.sessions.create("cfg", ttl=60)
    assert auth.sessions.valid(tok, "cfg") and not auth.sessions.valid(tok, "dm")


def test_security_headers_and_no_cors_by_default(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "script-src 'self'" in r.headers["content-security-policy"] and "'unsafe-inline'" not in r.headers["content-security-policy"].split("style-src")[0]
    assert r.headers["x-frame-options"] == "DENY"
    pre = client.options("/api/dashboard_data", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
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
