"""Tests E2E (Playwright + HTTP) de l'application AUTODO avec la source de données simulée.
Chaque test exprime le comportement ATTENDU ; sur le code d'origine plusieurs échouent (voir REVUE §8)."""
import time
import pytest
import requests
from conftest import (BASE, CFG_PASSWORD, DM_PASSWORD, SHOTS, XSS_IP, db, log_text, wait_cycles)

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
@pytest.mark.parametrize("path", ["/", "/tickets/", "/data-management"])
def test_pages_return_200(stack, path):
    assert requests.get(BASE + path, timeout=10).status_code == 200


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
def test_update_config_requires_authentication(stack):
    r = requests.post(BASE + "/update_config", json={"alert_traffic_threshold_percent": 77.0}, timeout=5)
    assert r.status_code in (401, 403), f"/update_config sans authentification -> {r.status_code}"


def test_exnet_credentials_endpoint_removed_and_hosts_not_published(stack):
    r = requests.post(BASE + "/api/exnet-auth-url", json={"base_url": "https://evil.example"}, timeout=5)
    assert r.status_code in (404, 405) and "passwd" not in r.text, "l'auto-login ExNet (mot de passe partagé) doit avoir disparu"
    data = requests.post(BASE + "/api/dashboard_data", json={}, timeout=10).text
    assert "127.0.0.1" not in data and "/go/traffic/" in data, "le nom d'hôte interne ne doit plus figurer dans le JSON"


def test_config_token_is_not_valid_for_data_management(stack):
    s = requests.Session()
    assert s.post(BASE + "/api/auth", json={"password": CFG_PASSWORD}, timeout=5).status_code == 200
    s.cookies.set("dm_token", s.cookies.get("cfg"))
    assert s.get(BASE + "/api/data-management", params={"action": "data"}, timeout=5).status_code == 401


def test_wrong_passwords_rejected(stack):
    assert requests.post(BASE + "/api/auth", json={"password": "nope"}, timeout=5).status_code == 401
    assert requests.post(BASE + "/api/auth", json={}, timeout=5).status_code == 401
    r = requests.post(BASE + "/api/data-management", json={"action": "login", "password": "nope"}, timeout=5)
    assert r.status_code == 401


def test_cors_does_not_reflect_arbitrary_origin_with_credentials(stack):
    r = requests.options(BASE + "/api/dashboard_data", timeout=5,
                         headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert not (r.headers.get("access-control-allow-origin") == "https://evil.example"
                and r.headers.get("access-control-allow-credentials") == "true")


def test_config_panel_ui_flow(stack, page):
    page.goto(BASE + "/")
    page.wait_for_selector("#openConfigBtn")
    try:   # pop-up modale « Nouvelle Alerte » : l'utilisateur l'acquitte avant d'ouvrir la configuration
        page.wait_for_selector("#alert-popup", state="visible", timeout=6000)
        page.screenshot(path=str(SHOTS / "05_alert_popup.png"))
        page.click("#alert-popup button")
    except Exception:
        pass
    page.click("#openConfigBtn")
    page.wait_for_selector("#password-dialog")
    page.fill("#config-password", "mauvais"); page.click("#password-submit")
    page.wait_for_selector("#password-dialog")            # toujours là : refusé
    page.fill("#config-password", CFG_PASSWORD); page.click("#password-submit")
    page.wait_for_selector("#config-drawer", state="visible")
    page.screenshot(path=str(SHOTS / "03_config_drawer.png"))
    page.fill("#alert_traffic_threshold_percent", "35")
    page.click("#config-form button[type=submit]")
    page.wait_for_function("() => document.body.innerText.includes('sauvegardée')", timeout=8000)
    assert requests.get(BASE + "/api/config", timeout=5).json()["alert_traffic_threshold_percent"] == 35.0


def test_stored_xss_in_access_log_table(stack, page):
    # un visiteur malveillant empoisonne l'IP journalisée via X-Forwarded-For
    requests.post(BASE + "/api/dashboard_data", json={}, headers={"X-Forwarded-For": XSS_IP}, timeout=10)
    page.goto(BASE + "/data-management")
    page.fill("#password", DM_PASSWORD); page.click("#loginBtn")
    page.wait_for_selector("#access-logs-table tr", timeout=15000)
    page.wait_for_timeout(1000)
    page.screenshot(path=str(SHOTS / "04_data_management.png"), full_page=True)
    assert page.evaluate("() => window.__xss") is None, "XSS stocké exécuté dans la page d'administration"
    assert page.locator("#access-logs-table img").count() == 0


@pytest.mark.parametrize("path", ["/", "/tickets/", "/data-management"])
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
    _open_dashboard(page)
    page.wait_for_selector("#alert-popup", state="visible", timeout=8000)
    assert page.get_attribute("#alert-popup", "role") == "alertdialog"
    assert page.get_attribute("#alert-popup", "aria-modal") == "true"
    assert page.evaluate("() => document.activeElement.closest('#alert-popup') !== null"), "le focus doit être dans la pop-up"
    page.keyboard.press("Escape")
    page.wait_for_selector("#alert-popup", state="hidden", timeout=3000)


def test_icon_buttons_have_accessible_names_and_toggle_state(stack, page):
    _open_dashboard(page)
    for sel in ("#themeToggleBtn", "#filterBtn", "#openConfigBtn"):
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
    assert {"autoUpdateBtn", "themeToggleBtn", "openConfigBtn"} <= seen


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
def test_admin_config_change_is_pushed_to_every_connected_viewer(stack, browser_ctx):
    """Un administrateur modifie un paramètre : TOUS les spectateurs connectés reçoivent l'événement SSE."""
    viewers = []
    for _ in range(5):
        ctx = browser_ctx.new_context()
        pg = ctx.new_page()
        pg.goto(BASE + "/")
        pg.evaluate("""() => { window.__events = []; const es = new EventSource('/events');
            es.addEventListener('update', e => window.__events.push(JSON.parse(e.data).type)); window.__es = es; }""")
        viewers.append((ctx, pg))
    time.sleep(1.5)
    admin = requests.Session()
    assert admin.post(BASE + "/api/auth", json={"password": CFG_PASSWORD}, timeout=5).status_code == 200
    current = requests.get(BASE + "/api/config", timeout=5).json()["alert_pertes_threshold"]
    assert admin.post(BASE + "/update_config", json={"alert_pertes_threshold": current}, timeout=5).status_code == 200
    for _, pg in viewers:
        pg.wait_for_function("() => window.__events.includes('config_updated')", timeout=8000)
    for ctx, _ in viewers:
        ctx.close()


def test_viewer_cannot_open_admin_features_without_password(stack, page):
    _open_dashboard(page)
    page.keyboard.press("Escape")
    page.click("#openConfigBtn")
    page.wait_for_selector("#password-dialog")
    assert not page.is_visible("#config-drawer")
    assert requests.get(BASE + "/api/data-management", params={"action": "data"}, timeout=5).status_code == 401
    assert requests.get(BASE + "/api/status", timeout=5).status_code == 401
