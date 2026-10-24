"""Tests E2E (Playwright + HTTP) de l'application AUTODO avec la source de données simulée : pipeline de tickets, Orange, e-mail,
sécurité (comptes nominatifs, rôles, CORS, CSP, XSS), accessibilité, mobile, diffusion temps réel.
Chaque test exprime le comportement ATTENDU. Les écrans de l'interface (supervision, admin, préférences…) sont dans test_e2e_ui.py."""
import time
import pytest
import requests
from conftest import ADMIN_USER, BASE, NEW_PASSWORD, SHOTS, XSS_IP, api_login, clear_acks, db, log_text, wait_cycles

EXPECTED = {  # base -> (type, catégorie)
    "douala_camtel": ("traffic", 1),    # trafic nul
    "kribi": ("traffic", 2),            # chute instantanée
    "oci_sat3": ("traffic", 3),         # baisses consécutives
    "garoua_camtel": ("db_down", 1),    # base figée depuis 90 min
    "wacs_ic": ("pertes", 1),           # pertes TCP 8 %
}


def orange():
    return requests.get("http://127.0.0.1:9001/_debug/tickets", timeout=5).json()


# ---------------------------------------------------------------- pages & UI
@pytest.mark.parametrize("path", ["/", "/tickets/", "/supervision", "/admin"])
def test_pages_return_200(stack, path):
    assert requests.get(BASE + path, timeout=10).status_code == 200


def test_old_data_management_page_redirects_to_admin(stack):
    r = requests.get(BASE + "/data-management", allow_redirects=False, timeout=5)
    assert r.status_code in (301, 302, 307, 308) and r.headers["location"] == "/admin"


def test_dashboard_renders_simulated_data(stack, page):
    page.goto(BASE + "/")
    page.wait_for_selector("#table-wrapper-international table", timeout=20000)
    txt = page.inner_text("#table-wrapper-international")
    for name in ("KRIBI", "OCI-SAT3", "WACS-IC"):
        assert name.lower() in txt.lower()
    page.screenshot(path=str(SHOTS / "01_dashboard.png"), full_page=True)


def test_sse_hello_event(stack, page):
    page.goto(BASE + "/")
    got = page.evaluate("""() => new Promise((res, rej) => {
        const es = new EventSource('/events'); const t = setTimeout(() => rej('timeout'), 8000);
        es.addEventListener('hello', e => { clearTimeout(t); es.close(); res(JSON.parse(e.data).type); }); })""")
    assert got == "hello"


def test_dashboard_alert_list_shows_tickets_from_db(stack, page):
    page.goto(BASE + "/")
    page.wait_for_function(
        "() => (document.getElementById('alertes-list-international')?.textContent||'').includes('Alerte TRAFFIC')",
        timeout=20000)


def test_tickets_page_lists_cards(stack, page):
    page.goto(BASE + "/tickets/")
    page.wait_for_selector(".ticket-card", timeout=15000)
    assert page.locator(".ticket-card").count() >= 4
    page.screenshot(path=str(SHOTS / "02_tickets.png"), full_page=True)


# ---------------------------------------------------------------- moteur de tickets
def test_tickets_are_persisted_with_unique_ids(stack):
    rows = db("SELECT id, database_name, type, category, status FROM tickets_tb")
    assert len(rows) >= len(EXPECTED), f"{len(rows)} ligne(s) en base (attendu >= {len(EXPECTED)})"
    assert len({r["id"] for r in rows}) == len(rows)


def test_one_ticket_per_scenario_with_expected_type(stack):
    rows = {r["database_name"]: r for r in db("SELECT database_name, type, category FROM tickets_tb")}
    for key, (typ, cat) in EXPECTED.items():
        assert key in rows, f"aucun ticket pour {key} ({typ})"
        assert (rows[key]["type"], rows[key]["category"]) == (typ, cat), key


def test_orange_tickets_match_local_tickets_and_description_is_clean(stack):
    o = orange()["tickets"]
    local = db("SELECT id, external_api_ticket_id FROM tickets_tb")
    assert len(o) == len(EXPECTED), f"{len(o)} ticket(s) Orange pour {len(EXPECTED)} incidents"
    assert all(r["external_api_ticket_id"] for r in local)
    for t in o:
        d = t["payload"]["description"]
        assert d.count("AUTODO Network Monitoring Alert") == 1, "description répétée"


def test_notification_email_sent(stack):
    import glob
    assert glob.glob("/tmp/autodo_demo_mail/*.eml"), "aucun e-mail reçu par le puits SMTP"


def test_second_cycle_is_stable_no_duplicates(stack):
    assert wait_cycles(stack["c0"] + 1, timeout=240), "2e cycle de monitoring non observé"
    time.sleep(3)
    errs = [ln for ln in log_text().splitlines() if "Cycle de supervision en échec" in ln or "Traceback" in ln]
    assert not errs, errs[:3]
    assert len(db("SELECT id FROM tickets_tb")) == stack["local"], "doublons en base après le 2e cycle"
    assert len(orange()["tickets"]) == stack["orange"], "tickets Orange dupliqués au 2e cycle"


# ---------------------------------------------------------------- sécurité
def test_reserved_endpoints_require_authentication_and_old_ones_are_gone(stack):
    assert requests.post(BASE + "/api/supervision/config", json={"alert_traffic_threshold_percent": 77.0}, timeout=5).status_code == 401
    for gone in ("/update_config", "/api/auth", "/api/data-management"):
        assert requests.post(BASE + gone, json={}, timeout=5).status_code in (404, 405), gone       # plus de mot de passe partagé
    assert requests.get(BASE + "/api/config", timeout=5).json()["alert_traffic_threshold_percent"] != 77.0


def test_exnet_credentials_endpoint_removed_and_hosts_not_published(stack):
    r = requests.post(BASE + "/api/exnet-auth-url", json={"base_url": "https://evil.example"}, timeout=5)
    assert r.status_code in (404, 405) and "passwd" not in r.text, "l'auto-login ExNet (mot de passe partagé) doit avoir disparu"
    data = requests.get(BASE + "/api/dashboard_data", timeout=10).text
    assert "127.0.0.1" not in data and "/go/traffic/" in data, "le nom d'hôte interne ne doit plus figurer dans le JSON"


def test_roles_are_separated_on_the_api(stack, admin_api, supervisor):
    supervision_urls = ["/api/supervision/config-schema", "/api/supervision/maintenance", "/api/supervision/tickets-to-validate"]
    admin_urls = ["/api/admin/users", "/api/admin/sessions", "/api/admin/audit", "/api/admin/usage", "/api/admin/health", "/api/admin/detectors"]
    for url in supervision_urls + admin_urls + ["/api/status", "/tickets/status"]:
        assert requests.get(BASE + url, timeout=10).status_code == 401, f"{url} accessible au visiteur"
    for url in supervision_urls:
        assert supervisor.get(url).status_code == 200, url
    for url in admin_urls:
        assert supervisor.get(url).status_code == 403, f"{url} accessible à un simple superviseur"
        assert admin_api.get(url).status_code == 200, url
    assert supervisor.post("/api/admin/simulation", {}).status_code == 403            # la simulation « et si ? » est réservée à l'admin


def test_wrong_credentials_give_one_message_and_five_failures_lock_the_account(stack, admin_api):
    admin_api.post("/api/admin/users", {"username": "locktest", "display_name": "L", "role": "supervision", "password": "Lock-Passw0rd1"})
    bodies = set()
    for name, password in (("locktest", "faux-mot-de-passe-1A"), ("compte-inexistant", "faux-mot-de-passe-1A")):
        r = requests.post(BASE + "/api/login", json={"username": name, "password": password}, timeout=10)
        assert r.status_code == 401
        bodies.add(r.text)
    assert len(bodies) == 1                                                            # « inconnu » et « mauvais mot de passe » indiscernables
    for _ in range(4):
        requests.post(BASE + "/api/login", json={"username": "locktest", "password": "faux-mot-de-passe-1A"}, timeout=10)
    right = requests.post(BASE + "/api/login", json={"username": "locktest", "password": "Lock-Passw0rd1"}, timeout=10)
    assert right.status_code == 401 and right.text in bodies                           # verrouillé : même le bon mot de passe est refusé
    locked = [u for u in admin_api.get("/api/admin/users").json()["users"] if u["username"] == "locktest"][0]
    assert locked["locked_until"] is not None


def test_cross_site_write_is_refused(stack, supervisor):
    r = supervisor.s.post(BASE + "/api/supervision/config", json={"alert_pertes_threshold": 3.0}, headers={"Origin": "https://evil.example"}, timeout=10)
    assert r.status_code == 403


def test_cors_does_not_reflect_arbitrary_origin_with_credentials(stack):
    r = requests.options(BASE + "/api/dashboard_data", timeout=5,
                         headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert not (r.headers.get("access-control-allow-origin") == "https://evil.example"
                and r.headers.get("access-control-allow-credentials") == "true")


def test_stored_xss_in_access_log_table(stack, admin_api, page):
    # un visiteur malveillant empoisonne l'IP journalisée via X-Forwarded-For
    requests.get(BASE + "/api/dashboard_data", headers={"X-Forwarded-For": XSS_IP}, timeout=10)
    time.sleep(11)                                              # le tampon de visites est vidé toutes les 10 s
    api_login(page, ADMIN_USER, NEW_PASSWORD)
    page.goto(BASE + "/admin")
    page.click("#tab-usage")
    page.wait_for_selector("#panel-usage table", timeout=15000)
    page.wait_for_timeout(1000)
    assert page.evaluate("() => window.__xss") is None, "XSS stocké exécuté dans la page d'administration"
    assert page.locator("#panel-usage img").count() == 0


@pytest.mark.parametrize("path", ["/", "/tickets/", "/supervision", "/admin"])
def test_no_csp_violation_or_js_error(stack, page, path):
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + path)
    page.wait_for_timeout(2500)
    bad = [e for e in errors if "Content Security Policy" in e or "Refused to" in e or "is not defined" in e]
    assert not bad, bad[:3]


def test_security_headers_present(stack):
    h = requests.get(BASE + "/", timeout=5).headers
    assert "script-src 'self'" in h.get("content-security-policy", "")
    assert h.get("x-frame-options") == "DENY" and h.get("x-content-type-options") == "nosniff"


def test_go_redirect_hides_internal_host_until_click(stack):
    r = requests.get(BASE + "/go/traffic/kribi?end_date=1700000000", allow_redirects=False, timeout=5)
    assert r.status_code == 302 and "1700000000" in r.headers["location"]
    assert requests.get(BASE + "/go/traffic/unknown", allow_redirects=False, timeout=5).status_code == 404


# ====================================================================== accessibilité & clavier
def _open_dashboard(page):
    page.goto(BASE + "/")
    page.wait_for_selector("#table-wrapper-international table", timeout=20000)


def test_alert_popup_is_an_accessible_modal_with_focus_management_and_escape(stack, page):
    clear_acks()                                                                       # la pop-up n'apparaît que pour des alertes non acquittées
    _open_dashboard(page)
    page.wait_for_selector("#alert-popup", state="visible", timeout=8000)
    assert page.get_attribute("#alert-popup", "role") == "alertdialog"
    assert page.get_attribute("#alert-popup", "aria-modal") == "true"
    assert page.evaluate("() => document.activeElement.closest('#alert-popup') !== null"), "le focus doit être dans la pop-up"
    page.keyboard.press("Escape")
    page.wait_for_selector("#alert-popup", state="hidden", timeout=3000)


def test_icon_buttons_have_accessible_names_and_toggle_state(stack, page):
    _open_dashboard(page)
    for sel in ("#themeToggleBtn", "#filterBtn", "#configLink", "#prefsBtn"):
        assert page.get_attribute(sel, "aria-label"), f"{sel} sans libellé accessible"
    before = page.get_attribute("#autoUpdateBtn", "aria-pressed")
    page.evaluate("() => { const o = document.getElementById('alert-popup'); if (o) o.style.display = 'none'; }")
    page.click("#autoUpdateBtn")
    assert page.get_attribute("#autoUpdateBtn", "aria-pressed") != before


def test_keyboard_only_reaches_the_main_controls(stack, page):
    _open_dashboard(page)
    page.keyboard.press("Escape")
    seen = set()
    for _ in range(14):
        page.keyboard.press("Tab")
        seen.add(page.evaluate("() => document.activeElement && document.activeElement.id"))
    assert {"autoUpdateBtn", "themeToggleBtn", "prefsBtn", "configLink"} <= seen


# ====================================================================== mobile & second navigateur
@pytest.mark.parametrize("path", ["/", "/tickets/"])
def test_mobile_viewport_renders_without_errors(stack, browser_ctx, path):
    ctx = browser_ctx.new_context(viewport={"width": 393, "height": 851}, device_scale_factor=2.75, is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.goto(BASE + path)
    pg.wait_for_timeout(3000)
    assert not [e for e in errors if "Content Security Policy" in e or "is not defined" in e], errors[:3]
    assert pg.is_visible("header, .header, #tickets-container, .ticket-card, .controls") or pg.locator("h1,h2").count() > 0
    pg.screenshot(path=str(SHOTS / f"06_mobile{path.strip('/').replace('/', '_') or 'dashboard'}.png"))
    width = pg.evaluate("() => window.innerWidth")
    assert pg.evaluate("() => document.documentElement.scrollWidth") <= width + 1, "défilement horizontal de la page"
    over = pg.evaluate("""(w) => [...document.querySelectorAll('.controls > *, #alert-popup .alert-popup-content')]
        .filter(e => e.offsetParent !== null && e.getBoundingClientRect().right > w + 1).map(e => e.id || e.className)""", width)
    assert not over, f"éléments qui dépassent de l'écran mobile : {over}"
    ctx.close()


def test_runs_in_google_chrome_stable_too(pw):
    try:
        b = pw.chromium.launch(channel="chrome")
    except Exception as e:
        pytest.skip(f"Google Chrome stable indisponible : {str(e)[:80]}")
    pg = b.new_page()
    errors = []
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.goto(BASE + "/")
    pg.wait_for_selector("#table-wrapper-international table", timeout=20000)
    assert not [e for e in errors if "Content Security Policy" in e]
    print("navigateur :", b.version)
    b.close()


# ====================================================================== plusieurs utilisateurs : synchronisation SSE
def test_config_change_is_pushed_to_every_connected_viewer(stack, browser_ctx, supervisor):
    """Un superviseur modifie un paramètre : TOUS les spectateurs connectés reçoivent l'évènement SSE (sans nom : `onmessage`)."""
    viewers = []
    for _ in range(5):
        ctx = browser_ctx.new_context()
        pg = ctx.new_page()
        pg.goto(BASE + "/")
        pg.evaluate("""() => { window.__events = []; const es = new EventSource('/events');
            es.onmessage = e => window.__events.push(JSON.parse(e.data).type); window.__es = es; }""")
        viewers.append((ctx, pg))
    time.sleep(1.5)
    current = requests.get(BASE + "/api/config", timeout=5).json()["alert_pertes_threshold"]
    try:
        assert supervisor.post("/api/supervision/config", {"alert_pertes_threshold": current + 0.5}).status_code == 200
        for _, pg in viewers:
            pg.wait_for_function("() => window.__events.includes('config_updated')", timeout=8000)
    finally:
        supervisor.post("/api/supervision/config", {"alert_pertes_threshold": current})
        for ctx, _ in viewers:
            ctx.close()


def test_viewer_cannot_use_reserved_features(stack, page):
    _open_dashboard(page)
    page.keyboard.press("Escape")
    assert page.get_attribute("#configLink", "href") == "/supervision"
    page.goto(BASE + "/supervision")
    page.wait_for_selector(".gate")
    assert not page.locator("#params-form").count()
    for url in ("/api/supervision/config-schema", "/api/admin/users", "/api/status"):
        assert requests.get(BASE + url, timeout=5).status_code == 401, url


@pytest.mark.skipif(not __import__("os").environ.get("E2E_SLOW"), reason="attend un vrai cycle (≤ 5 min) : E2E_SLOW=1")
def test_real_cycle_publishes_data_version_to_connected_browsers(stack, browser_ctx):
    """Le cycle tourne dans un thread APScheduler : sa publication SSE doit atteindre le navigateur ET déclencher un rechargement."""
    ctx = browser_ctx.new_context()
    pg = ctx.new_page()
    pg.goto(BASE + "/")
    pg.wait_for_selector("tr[data-db]")
    pg.evaluate("""() => { window.__versions = []; const es = new EventSource('/events');
        es.onmessage = e => { const m = JSON.parse(e.data); if (m.type === 'data_version') window.__versions.push(m.version); }; }""")
    seen = []
    pg.on("request", lambda r: seen.append(r.url) if "/api/dashboard_data" in r.url else None)
    before = len(seen)
    deadline = time.time() + 330
    while time.time() < deadline and not pg.evaluate("() => window.__versions.length"):
        time.sleep(2)
    assert pg.evaluate("() => window.__versions.length") >= 1, "aucun data_version reçu après 5 min"
    time.sleep(5)                                                                      # gigue de 0–3 s puis rechargement
    assert len(seen) > before, "le navigateur n'a pas rechargé les données après data_version"
    ctx.close()

