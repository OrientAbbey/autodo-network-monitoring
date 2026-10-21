"""API d'authentification et de rôles : visiteur / supervision / admin, CSRF, sessions révocables, audit."""
import pytest
from fastapi.testclient import TestClient

from core import accounts, auth, governance

GOOD = "Abcdefghi1"


@pytest.fixture
def app_client(clean_state):
    import main
    return main.app


def make_client(app, username=None, password=GOOD, role="supervision", must_change=False):
    """Client HTTP ; connecté si ``username`` est fourni (le compte est créé au besoin)."""
    client = TestClient(app)
    if username:
        accounts.create_user(username, username, role, password, must_change_password=must_change)
        response = client.post("/api/login", json={"username": username, "password": password})
        assert response.status_code == 200, response.text
    return client


def test_visitor_has_no_user_and_can_read_the_dashboard_pages(app_client):
    client = TestClient(app_client)
    assert client.get("/api/me").json() == {"user": None}
    assert client.get("/").status_code == 200
    assert client.get("/api/config").status_code == 200                             # le visiteur voit les seuils


def test_login_sets_a_hardened_cookie_and_me_returns_the_identity(app_client):
    accounts.create_user("alice", "Alice", "supervision", GOOD, must_change_password=False)
    client = TestClient(app_client)
    response = client.post("/api/login", json={"username": "ALICE", "password": GOOD})
    assert response.status_code == 200 and response.json()["user"]["role"] == "supervision"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "autodo_session=" in cookie
    me = client.get("/api/me").json()["user"]
    assert me["username"] == "alice" and me["must_change_password"] is False
    client.post("/api/logout")
    assert client.get("/api/me").json() == {"user": None}


def test_wrong_credentials_give_one_generic_message_and_are_audited(app_client):
    accounts.create_user("alice", "Alice", "supervision", GOOD)
    client = TestClient(app_client)
    bodies = {client.post("/api/login", json={"username": n, "password": "Mauvais-Passe-9"}).text for n in ("alice", "inconnu")}
    assert len(bodies) == 1 and "invalides" in bodies.pop()
    assert [e["action"] for e in governance.list_audit(action="login_failed")] == ["login_failed", "login_failed"]


def test_ip_rate_limit_blocks_after_too_many_failures(app_client):
    client = TestClient(app_client)
    codes = [client.post("/api/login", json={"username": "x", "password": "y"}).status_code for _ in range(auth.limiter.limit + 2)]
    assert codes[0] == 401 and codes[-1] == 429


def test_protected_routes_answer_401_to_visitors_and_403_to_insufficient_roles(app_client):
    visitor = TestClient(app_client)
    for method, url in (("get", "/api/supervision/config-schema"), ("get", "/api/admin/users"), ("get", "/api/admin/audit"),
                        ("post", "/api/supervision/config")):
        assert getattr(visitor, method)(url).status_code == 401, url
    supervisor = make_client(app_client, "sup", role="supervision")
    assert supervisor.get("/api/supervision/config-schema").status_code == 200
    assert supervisor.get("/api/admin/users").status_code == 403                     # superviseur ≠ admin
    admin = make_client(app_client, "adm", role="admin")
    assert admin.get("/api/supervision/config-schema").status_code == 200            # un admin a aussi les droits de supervision
    assert admin.get("/api/admin/users").status_code == 200


def test_initial_password_must_be_changed_before_anything_else(app_client):
    client = make_client(app_client, "newbie", role="admin", must_change=True)
    assert client.get("/api/me").json()["user"]["must_change_password"] is True
    blocked = client.get("/api/admin/users")
    assert blocked.status_code == 403 and blocked.json()["detail"] == "password_change_required"
    assert client.post("/api/me/password", json={"current_password": GOOD, "new_password": "faible"}).status_code == 400
    assert client.post("/api/me/password", json={"current_password": GOOD, "new_password": "Nouveau-Passe-2"}).status_code == 200
    assert client.get("/api/admin/users").status_code == 200                          # débloqué sans se reconnecter


def test_origin_check_blocks_cross_site_writes_but_not_same_origin(app_client):
    client = make_client(app_client, "sup")
    evil = client.post("/api/supervision/config", json={"alert_pertes_threshold": 5}, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    same = client.post("/api/supervision/config", json={"alert_pertes_threshold": 5}, headers={"Origin": "http://testserver"})
    assert same.status_code == 200
    assert client.get("/api/me", headers={"Origin": "https://evil.example"}).status_code == 200      # lecture : non concernée


def test_session_token_is_stored_only_as_a_hash(app_client):
    client = make_client(app_client, "sup")
    token = client.cookies.get("autodo_session")
    assert token and token not in auth.sessions._by_digest                            # seul le SHA-256 est en mémoire
    assert auth._digest(token) in auth.sessions._by_digest


def test_admin_lists_and_revokes_sessions(app_client):
    admin = make_client(app_client, "adm", role="admin")
    victim = make_client(app_client, "bob", role="supervision")
    listing = admin.get("/api/admin/sessions").json()["sessions"]
    assert {s["username"] for s in listing} == {"adm", "bob"} and all("token" not in s for s in listing)
    target = next(s for s in listing if s["username"] == "bob")
    assert admin.delete(f"/api/admin/sessions/{target['session_id']}").status_code == 200
    assert victim.get("/api/me").json() == {"user": None}                             # session révoquée
    assert admin.delete("/api/admin/sessions/inexistante").status_code == 404


def test_admin_reset_password_closes_the_user_sessions_and_forces_change(app_client):
    admin = make_client(app_client, "adm", role="admin")
    victim = make_client(app_client, "bob", role="supervision")
    user_id = next(u["id"] for u in accounts.list_users() if u["username"] == "bob")
    response = admin.post(f"/api/admin/users/{user_id}/reset-password", json={"new_password": "Temporaire-Pass-3"})
    assert response.status_code == 200 and response.json()["sessions_closed"] == 1
    assert victim.get("/api/me").json() == {"user": None}
    again = TestClient(app_client).post("/api/login", json={"username": "bob", "password": "Temporaire-Pass-3"})
    assert again.json()["user"]["must_change_password"] is True


def test_demoting_a_user_applies_to_the_open_session_immediately(app_client):
    admin = make_client(app_client, "adm", role="admin")
    other = make_client(app_client, "other", role="admin")
    other_id = next(u["id"] for u in accounts.list_users() if u["username"] == "other")
    assert other.get("/api/admin/users").status_code == 200
    admin.patch(f"/api/admin/users/{other_id}", json={"role": "supervision"})
    assert other.get("/api/admin/users").status_code == 403
    admin.patch(f"/api/admin/users/{other_id}", json={"active": False})
    assert other.get("/api/me").json() == {"user": None}


def test_admin_cannot_remove_the_last_admin_through_the_api(app_client):
    admin = make_client(app_client, "adm", role="admin")
    admin_id = next(u["id"] for u in accounts.list_users() if u["username"] == "adm")
    assert admin.patch(f"/api/admin/users/{admin_id}", json={"active": False}).status_code == 400


def test_user_creation_enforces_the_password_policy_and_is_audited(app_client):
    admin = make_client(app_client, "adm", role="admin")
    weak = admin.post("/api/admin/users", json={"username": "carl", "role": "supervision", "password": "faible"})
    assert weak.status_code == 400
    ok = admin.post("/api/admin/users", json={"username": "carl", "display_name": "Carl", "role": "supervision", "password": GOOD})
    assert ok.status_code == 201 and "password_hash" not in ok.text
    assert [e["target"] for e in governance.list_audit(action="user_create")] == ["carl"]


def test_audit_csv_neutralises_spreadsheet_formulas(clean_state):
    governance.audit("=cmd|calc", "1.2.3.4", "login_failed", "+SUM(A1)", {"x": 1})
    csv_text = governance.audit_csv(governance.list_audit())
    assert "'=cmd|calc" in csv_text and "'+SUM(A1)" in csv_text


def test_old_shared_password_endpoints_are_gone(app_client):
    client = TestClient(app_client)
    assert client.post("/update_config", json={}).status_code in (404, 405)
    assert client.post("/api/auth", json={"password": "x"}).status_code in (404, 405)
    assert client.get("/api/data-management").status_code in (404, 405)
    assert client.get("/data-management", follow_redirects=False).headers["location"] == "/admin"
