"""E2E de l'interface (phase C) : comptes et rôles, préférences, accusé partagé, supervision, admin, tickets, mobile, mur d'écran, accessibilité, XSS.

Pile complète (bases DIAGNET simulées, application, faux Orange) : voir conftest.py. Lancer avec E2E_ALIGN=0 pour ne pas attendre l'alignement des cycles.
"""
import json
import re
import time

import pytest
import requests
from conftest import (ADMIN_USER, BASE, NEW_PASSWORD, SHOTS, SUP_PASSWORD, XSS, Api, ack_all, api_login, clear_acks, db, real_problems, ui_login, wait_acked, wait_js, watch)

@pytest.fixture(autouse=True, scope="module")
def remove_test_tickets_at_the_end(stack):
    """Les tickets 99xxxx insérés par ces tests (et leurs journaux, retours, accusés) ne doivent pas fausser les autres fichiers."""
    yield
    for table, column in (("ticket_events", "ticket_id"), ("ticket_feedback", "ticket_id"), ("alert_acks", "ticket_id"), ("tickets_tb", "id")):
        db(f"DELETE FROM {table} WHERE {column} >= 990000")


@pytest.fixture(autouse=True)
def alerts_already_acknowledged(stack):
    """Par défaut la pop-up (modale) est déjà acquittée : elle bloquerait les clics. Les tests d'alertes appellent clear_acks()."""
    ack_all()


def card(page, ticket_id):
    return page.locator(f".ticket-card:has(.ticket-id:text-is('#{ticket_id}'))")


def insert_ticket(ticket_id, **fields):
    """Insère un ticket directement en base (le moteur de règles n'est pas l'objet de ces tests)."""
    base = {"id": ticket_id, "type": "traffic", "site": "Douala", "database_name": "kribi", "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "value": 10.0, "status": "open", "agent": "DIAGNET", "severity": "critical", "description": "d", "reason": "Test E2E", "pertes": "No",
            "last_values": "[]", "category": 2, "pertes_warnings": "[]", "orange_sync_state": "skipped", "validation_state": "auto"}
    base.update(fields)
    cols = ", ".join(f"`{k}`" for k in base)
    marks = ", ".join(["%s"] * len(base))
    db(f"REPLACE INTO tickets_tb ({cols}) VALUES ({marks})", tuple(base.values()))


# ---------------------------------------------------------------- tableau de bord (visiteur)
def test_pages_return_200(stack):
    for path in ("/", "/tickets/", "/supervision", "/admin"):
        assert requests.get(BASE + path, timeout=10).status_code == 200, path


def test_visitor_dashboard_has_no_console_or_csp_error(stack, page):
    problems = watch(page)
    page.goto(BASE + "/")
    page.wait_for_selector("#table-wrapper-international table", timeout=20000)
    page.wait_for_selector("tr[data-db]")
    page.wait_for_timeout(1500)
    assert problems == []
    assert page.locator("#config-drawer, #openConfigBtn").count() == 0                       # l'ancien tiroir a disparu
    assert page.locator("#configLink").get_attribute("href") == "/supervision"
    assert page.locator("#loginBtn").is_visible() and page.inner_text("#presence").startswith("👥")
    page.screenshot(path=str(SHOTS / "C01_dashboard_visiteur.png"), full_page=True)


def test_every_data_row_carries_its_database_key(stack, page):
    page.goto(BASE + "/")
    page.wait_for_selector("tr[data-db]")
    keys = page.eval_on_selector_all("tr[data-db]", "rows => rows.map(r => r.dataset.db)")
    assert "kribi" in keys and all(re.fullmatch(r"[a-z0-9_]+", k) for k in keys)


def test_language_switch_translates_and_persists(stack, page):
    page.goto(BASE + "/")
    page.wait_for_selector("tr[data-db]")
    page.click("#prefsBtn")
    page.check("#prefs-dialog input[name=lang][value=en]")
    assert page.inner_text("#autoUpdateBtn").strip() == "Auto update"
    assert page.get_attribute("html", "lang") == "en"
    assert "International Exit" in page.inner_text("#sections-grid")
    page.screenshot(path=str(SHOTS / "C02_prefs_dialog_en.png"))
    page.click("#prefs-dialog [data-action=closeDialog]")
    page.reload()
    page.wait_for_selector("tr[data-db]")
    assert page.inner_text("#autoUpdateBtn").strip() == "Auto update"                         # mémorisé dans le navigateur
    page.evaluate("localStorage.clear()")


def test_palette_and_theme_preferences(stack, page):
    page.goto(BASE + "/")
    page.click("#prefsBtn")
    page.check("#prefs-dialog input[name=palette][value=colorblind]")
    page.check("#prefs-dialog input[name=theme][value=light]")
    assert page.get_attribute("html", "data-palette") == "colorblind" and page.get_attribute("html", "data-theme") == "light"
    page.evaluate("localStorage.clear()")


def test_share_link_applies_language_theme_palette_but_never_sound(stack, browser_ctx, page):
    page.goto(BASE + "/")
    page.click("#prefsBtn")
    page.check("#prefs-dialog input[name=lang][value=en]")
    page.check("#prefs-dialog input[name=palette][value=colorblind]")
    page.check("#prefs-dialog input[name=sound]")
    link = page.input_value("#prefs-link")
    assert "#prefs=" in link and "sound" not in link
    other = browser_ctx.new_context(locale="fr-FR")
    try:
        friend = other.new_page()
        friend.goto(link)
        friend.wait_for_selector("tr[data-db]")
        assert friend.get_attribute("html", "lang") == "en" and friend.get_attribute("html", "data-palette") == "colorblind"
        assert "#prefs" not in friend.url                                                      # le fragment est retiré de l'URL
        assert friend.evaluate("JSON.parse(localStorage.getItem('autodo.prefs')).sound") is False
    finally:
        other.close()
    page.evaluate("localStorage.clear()")


def test_invalid_share_link_is_ignored(stack, page):
    page.goto(BASE + "/#prefs=!!!pas-du-base64!!!")
    page.goto(BASE + "/#prefs=" + "eyJsIjoiemgiLCJ0IjoiPHNjcmlwdD4ifQ")                    # {"l":"zh","t":"<script>"} : valeurs hors liste blanche
    page.wait_for_selector("tr[data-db]")
    assert page.get_attribute("html", "lang") == "fr" and page.get_attribute("html", "data-theme") == "dark"


# ---------------------------------------------------------------- alertes : accusé partagé, clavier, son
def popup_visible(page):
    return page.evaluate("getComputedStyle(document.getElementById('alert-popup')).display") != "none"


def test_acknowledging_closes_the_popup_for_every_viewer(stack, browser_ctx):
    clear_acks()
    contexts = [browser_ctx.new_context(locale="fr-FR") for _ in range(2)]
    try:
        pages = [c.new_page() for c in contexts]
        for p in pages:
            p.goto(BASE + "/")
            wait_js(p, "getComputedStyle(document.getElementById('alert-popup')).display !== 'none'", timeout=20000)
        pages[0].screenshot(path=str(SHOTS / "C03_popup_alerte.png"))
        pages[0].click("#alert-popup [data-action=acknowledgeAlert]")
        wait_js(pages[1], "getComputedStyle(document.getElementById('alert-popup')).display === 'none'", timeout=15000)   # via SSE « ack »
        assert db("SELECT COUNT(*) AS n FROM alert_acks")[0]["n"] > 0
        pages[1].reload()
        pages[1].wait_for_selector("tr[data-db]")
        pages[1].wait_for_timeout(1500)
        assert not popup_visible(pages[1])                                                       # acquittée pour tous : ne revient pas
    finally:
        for c in contexts:
            c.close()


def test_popup_keyboard_focus_trap_and_escape_acknowledges(stack, page):
    clear_acks()
    page.goto(BASE + "/")
    wait_js(page, "getComputedStyle(document.getElementById('alert-popup')).display !== 'none'", timeout=20000)
    assert page.evaluate("document.getElementById('alert-popup').contains(document.activeElement)")
    for _ in range(4):
        page.keyboard.press("Tab")
        assert page.evaluate("document.getElementById('alert-popup').contains(document.activeElement)")      # le focus ne sort pas
    page.keyboard.press("Escape")
    wait_js(page, "getComputedStyle(document.getElementById('alert-popup')).display === 'none'")
    page.wait_for_timeout(500)
    assert db("SELECT COUNT(*) AS n FROM alert_acks")[0]["n"] > 0


def test_section_titles_are_keyboard_operable(stack, page):
    clear_acks()
    page.goto(BASE + "/")
    page.wait_for_selector("tr[data-db]")
    if popup_visible(page):
        page.keyboard.press("Escape")
    title = page.locator(".section-title[role=button]").first
    assert title.get_attribute("tabindex") == "0"
    before = title.get_attribute("aria-expanded")
    title.focus()
    page.keyboard.press("Enter")
    assert title.get_attribute("aria-expanded") != before
    page.keyboard.press("Space")
    assert title.get_attribute("aria-expanded") == before


def test_sound_is_off_by_default_and_shows_a_banner_when_the_browser_blocks_it(stack, page):
    clear_acks()
    page.goto(BASE + "/")
    wait_js(page, "getComputedStyle(document.getElementById('alert-popup')).display !== 'none'", timeout=20000)
    assert page.evaluate("SoundState.context") is None and page.locator("#sound-banner").count() == 0     # désactivé : aucun AudioContext
    page.keyboard.press("Escape")
    wait_acked()
    page.evaluate("Prefs.set('sound', true)")
    clear_acks()
    page.reload()
    wait_js(page, "getComputedStyle(document.getElementById('alert-popup')).display !== 'none'", timeout=20000)
    state = page.evaluate("SoundState.context && SoundState.context.state")
    assert state in ("suspended", "running")
    if state == "suspended":
        page.wait_for_selector("#sound-banner")
        page.screenshot(path=str(SHOTS / "C04_bandeau_son.png"))
        page.click("#sound-banner")
        wait_js(page, "SoundState.context.state === 'running'", timeout=5000)
        assert page.locator("#sound-banner").count() == 0
    page.evaluate("localStorage.clear()")


# ---------------------------------------------------------------- mur d'écran
def test_wall_mode_shows_one_section_at_a_time_and_rotates(stack, page):
    page.goto(BASE + "/?wall=1&rotate=5")
    page.wait_for_selector("tr[data-db]")
    assert page.evaluate("document.body.classList.contains('wall')")
    visible = lambda: page.evaluate("[...document.querySelectorAll('#sections-grid > .data-section')].filter(s => !s.classList.contains('wall-hidden')).length")  # noqa: E731
    assert visible() == 1 and page.locator("#wall-title").is_visible()
    first = page.inner_text("#wall-title")
    page.screenshot(path=str(SHOTS / "C05_mur_ecran.png"))
    wait_js(page, f"document.getElementById('wall-title').textContent !== {json.dumps(first)}", timeout=9000)
    assert visible() == 1
    assert page.evaluate("SoundState.context") is None                                           # mur sans sound=1 : silence


# ---------------------------------------------------------------- connexion, rôles
def test_forced_password_change_cannot_be_skipped(stack, admin_api, page):
    admin_api.post("/api/admin/users", {"username": "newbie", "display_name": "Nina", "role": "supervision", "password": "Temp-Passw0rd"})
    ui_login(page, "newbie", "Temp-Passw0rd")
    page.wait_for_selector("#password-dialog[open]")
    page.keyboard.press("Escape")
    assert page.locator("#password-dialog[open]").count() == 1                                   # Échap ne contourne pas l'obligation
    assert page.locator("#password-dialog [data-action=closeDialog]").count() == 0
    page.fill("#pw-current", "mauvais")
    page.fill("#pw-new", "Nouveau-Passe-9x")
    page.click("#password-dialog-form button[type=submit]")
    wait_js(page, "document.getElementById('password-dialog-error').textContent.length > 0")
    page.fill("#pw-current", "Temp-Passw0rd")
    page.click("#password-dialog-form button[type=submit]")
    page.wait_for_selector("#password-dialog", state="detached")
    assert page.inner_text("#role-badge") == "SUPERVISION"                                       # badge de rôle en majuscules


def test_bad_credentials_show_one_generic_message(stack, page):
    page.goto(BASE + "/")
    page.click("#loginBtn")
    page.fill("#login-username", "inconnu")
    page.fill("#login-password", "nimporte")
    page.click("#login-dialog-form button[type=submit]")
    wait_js(page, "document.getElementById('login-dialog-error').textContent.length > 0")
    assert "invalides" in page.inner_text("#login-dialog-error")


def test_role_gates_on_reserved_pages(stack, supervisor, admin_api, page):
    page.goto(BASE + "/supervision")
    page.wait_for_selector(".gate")
    assert "Connexion requise" in page.inner_text(".gate")                                       # visiteur
    api_login(page, "sup1", SUP_PASSWORD)
    page.goto(BASE + "/admin")
    page.wait_for_selector(".gate")
    assert "Droits insuffisants" in page.inner_text(".gate")                                     # superviseur ≠ admin
    page.goto(BASE + "/supervision")
    page.wait_for_selector("#tab-params")
    assert page.locator("#nav-admin").count() == 0 and page.locator("#nav-supervision").count() == 1


def test_logout_returns_to_visitor_and_leaves_reserved_pages(stack, supervisor, page):
    api_login(page, "sup1", SUP_PASSWORD)
    page.goto(BASE + "/supervision")
    page.wait_for_selector("#tab-params")
    page.click("#logoutBtn")
    page.wait_for_url(BASE + "/")
    page.wait_for_selector("#loginBtn")


# ---------------------------------------------------------------- supervision
def test_supervision_params_save_shows_the_diff_and_creates_a_version(stack, supervisor, page):
    problems = watch(page)
    api_login(page, "sup1", SUP_PASSWORD)
    page.goto(BASE + "/supervision")
    page.wait_for_selector("#p-alert_pertes_threshold")
    page.fill("#p-alert_pertes_threshold", "4.2")
    page.screenshot(path=str(SHOTS / "C06_supervision_parametres.png"), full_page=True)
    page.click("#saveParamsBtn")
    page.wait_for_selector("#params-result .change-list")
    assert "4.2" in page.inner_text("#params-result") and "Seuil de pertes" in page.inner_text("#params-result")
    assert requests.get(BASE + "/api/config", timeout=5).json()["alert_pertes_threshold"] == 4.2
    page.click("#tab-versions")
    page.wait_for_selector("#panel-versions table")
    assert page.locator("#panel-versions tbody tr").count() >= 2
    assert problems == []


def test_supervision_rejects_out_of_range_values_with_a_readable_message(stack, supervisor, page):
    api_login(page, "sup1", SUP_PASSWORD)
    page.goto(BASE + "/supervision")
    page.wait_for_selector("#p-alert_pertes_threshold")
    page.evaluate("document.getElementById('p-alert_pertes_threshold').removeAttribute('max')")
    page.fill("#p-alert_pertes_threshold", "900")
    page.click("#saveParamsBtn")
    page.wait_for_selector(".toast.error")
    assert requests.get(BASE + "/api/config", timeout=5).json()["alert_pertes_threshold"] == 4.2


def test_restore_a_previous_version_from_the_history_tab(stack, supervisor, page):
    api_login(page, "sup1", SUP_PASSWORD)
    page.goto(BASE + "/supervision")
    page.click("#tab-versions")
    page.wait_for_selector("#panel-versions tbody tr")
    page.locator("#panel-versions tbody tr").last.get_by_role("button").click()               # la plus ancienne (configuration initiale)
    page.wait_for_selector("#version-detail .change-list")
    page.get_by_role("button", name="Restaurer cette version").click()
    page.locator("dialog[open] .btn-primary").click()
    page.wait_for_selector(".toast.success")
    assert requests.get(BASE + "/api/config", timeout=5).json()["alert_pertes_threshold"] == 3.0
    assert len(supervisor.get("/api/supervision/config-versions").json()["versions"]) >= 3     # la restauration est une NOUVELLE version


def test_per_database_override_from_the_ui(stack, supervisor, page):
    api_login(page, "sup1", SUP_PASSWORD)
    page.goto(BASE + "/supervision")
    page.click("#tab-overrides")
    page.wait_for_selector("#override-base")
    page.select_option("#override-base", "kribi")
    page.fill("#o-alert_pertes_threshold", "7.5")
    assert "kribi" in page.inner_text("#override-summary")
    page.click("#saveOverridesBtn")
    page.wait_for_selector("#overrides-result .change-list")
    assert supervisor.get("/api/supervision/config-schema").json()["overrides"] == {"kribi": {"alert_pertes_threshold": 7.5}}
    page.fill("#o-alert_pertes_threshold", "")
    page.click("#saveOverridesBtn")
    wait_js(page, "document.querySelector('#overrides-result').textContent.includes('v')")
    assert supervisor.get("/api/supervision/config-schema").json()["overrides"] == {}


def test_maintenance_window_shows_a_banner_on_the_dashboard(stack, supervisor, page):
    api_login(page, "sup1", SUP_PASSWORD)
    page.goto(BASE + "/supervision")
    page.click("#tab-maintenance")
    page.wait_for_selector("#maintenance-form")
    page.select_option("#maint-base", "kribi")
    page.fill("#maint-reason", "changement de câble")
    page.click("#maintenance-form button[type=submit]")
    page.wait_for_selector("#panel-maintenance tbody tr")
    page.goto(BASE + "/")
    page.wait_for_selector("#maintenance-banner:not([hidden])")
    assert "kribi" in page.inner_text("#maintenance-banner") and "câble" in page.inner_text("#maintenance-banner")
    page.screenshot(path=str(SHOTS / "C07_bandeau_maintenance.png"))
    page.goto(BASE + "/supervision")
    page.click("#tab-maintenance")
    page.wait_for_selector("#panel-maintenance tbody tr")
    page.locator("#panel-maintenance tbody tr button").first.click()
    page.locator("dialog[open] .btn-danger").click()
    page.wait_for_selector("#panel-maintenance .empty")


def test_pending_ticket_is_approved_from_the_validation_tab(stack, supervisor, page):
    supervisor.post("/api/supervision/config", {"ticket_validation_required": True})
    # avant validation, le ticket est retenu : rien chez Orange
    insert_ticket(990003, validation_state="pending", orange_sync_state="held", database_name="kribi")
    time.sleep(1)
    assert db("SELECT orange_sync_state FROM tickets_tb WHERE id = 990003")[0]["orange_sync_state"] == "held"
    db("DELETE FROM tickets_tb WHERE id = 990003")
    insert_ticket(990001, validation_state="pending", orange_sync_state="held", database_name="kribi")
    try:
        api_login(page, "sup1", SUP_PASSWORD)
        page.goto(BASE + "/supervision")
        page.click("#tab-validation")
        page.wait_for_selector("#panel-validation tbody tr")
        assert "990001" in page.inner_text("#panel-validation")
        page.get_by_role("button", name="Valider").click()
        page.wait_for_selector("#panel-validation .empty")
        assert db("SELECT validation_state FROM tickets_tb WHERE id = 990001")[0]["validation_state"] == "approved"
        assert "approved" in [e["kind"] for e in requests.get(f"{BASE}/tickets/api/990001/timeline", timeout=5).json()["events"]]
        # validé ⇒ il PART chez Orange (l'outbox le crée dans la minute) ; avant validation il n'y a rien eu
        deadline, state = time.time() + 120, ""
        while time.time() < deadline and state != "synced":
            time.sleep(3)
            state = db("SELECT orange_sync_state FROM tickets_tb WHERE id = 990001")[0]["orange_sync_state"]
        assert state == "synced", f"ticket validé jamais envoyé à Orange (état : {state})"
        assert "orange_created" in [e["kind"] for e in requests.get(f"{BASE}/tickets/api/990001/timeline", timeout=5).json()["events"]]
    finally:
        supervisor.post("/api/supervision/config", {"ticket_validation_required": False})


def test_rejecting_a_pending_ticket_silences_the_database(stack, supervisor, page):
    supervisor.post("/api/supervision/config", {"ticket_validation_required": True, "rejection_silence_minutes": 30})
    insert_ticket(990002, validation_state="pending", orange_sync_state="held", database_name="autre_base")
    try:
        api_login(page, "sup1", SUP_PASSWORD)
        page.goto(BASE + "/supervision")
        page.click("#tab-validation")
        page.wait_for_selector("#panel-validation tbody tr")
        page.get_by_role("button", name="Rejeter").click()
        page.fill("dialog[open] #confirm-text", "alerte de test")
        page.locator("dialog[open] .btn-danger").click()
        page.wait_for_selector("#panel-validation .empty")
        assert db("SELECT status FROM tickets_tb WHERE id = 990002")[0]["status"] == "deleted"
        assert [w["db_key"] for w in supervisor.get("/api/supervision/maintenance").json()["windows"]] == ["autre_base"]
    finally:
        supervisor.post("/api/supervision/config", {"ticket_validation_required": False, "rejection_silence_minutes": 120})
        for window in supervisor.get("/api/supervision/maintenance").json()["windows"]:
            supervisor.delete(f"/api/supervision/maintenance/{window['id']}")


# ---------------------------------------------------------------- page des tickets
def test_tickets_page_actions_depend_on_the_role(stack, supervisor, page):
    insert_ticket(990010, reason="ticket pour les actions")
    problems = watch(page)
    page.goto(BASE + "/tickets/")
    card(page, 990010).wait_for()
    assert card(page, 990010).locator(".ticket-actions button").count() == 1                    # visiteur : chronologie seulement
    api_login(page, "sup1", SUP_PASSWORD)
    page.reload()
    card(page, 990010).locator(".ticket-actions button").nth(3).wait_for()
    labels = card(page, 990010).locator(".ticket-actions button").all_inner_texts()
    assert any("Commenter" in x for x in labels) and any("Faux positif" in x for x in labels) and any("Supprimer" in x for x in labels)
    page.screenshot(path=str(SHOTS / "C08_tickets_superviseur.png"), full_page=True)
    assert problems == []


def test_timeline_comment_and_feedback_from_the_ticket_card(stack, supervisor, page):
    insert_ticket(990011, reason="chronologie")
    api_login(page, "sup1", SUP_PASSWORD)
    page.goto(BASE + "/tickets/")
    card(page, 990011).locator(".ticket-actions button").nth(3).wait_for()
    card(page, 990011).get_by_role("button", name="Commenter").click()
    page.fill("#comment-text", "équipe en route")
    page.check("#comment-orange")
    page.locator("dialog[open] button[type=submit]").click()
    page.wait_for_selector(".toast.success")
    card(page, 990011).get_by_role("button", name="Vrai").click()
    page.wait_for_selector(".toast.success >> nth=1")
    card(page, 990011).get_by_role("button", name="Chronologie").click()
    page.wait_for_selector("#timeline-dialog .timeline-item")
    text = page.inner_text("#timeline-dialog")
    assert "équipe en route" in text and "Commentaire" in text and "sup1" in text and "Retour" in text
    page.screenshot(path=str(SHOTS / "C09_chronologie.png"))
    assert db("SELECT label FROM ticket_feedback WHERE ticket_id = 990011")[0]["label"] == "real"


def test_delete_a_ticket_with_a_reason(stack, supervisor, page):
    insert_ticket(990012, database_name="base_supprimee")
    api_login(page, "sup1", SUP_PASSWORD)
    page.goto(BASE + "/tickets/")
    card(page, 990012).locator(".ticket-actions button").nth(3).wait_for()
    card(page, 990012).get_by_role("button", name="Supprimer").click()
    page.fill("dialog[open] #confirm-text", "doublon")
    page.locator("dialog[open] .btn-danger").click()
    card(page, 990012).wait_for(state="detached", timeout=10000)
    assert db("SELECT status FROM tickets_tb WHERE id = 990012")[0]["status"] == "deleted"
    for window in supervisor.get("/api/supervision/maintenance").json()["windows"]:
        supervisor.delete(f"/api/supervision/maintenance/{window['id']}")


# ---------------------------------------------------------------- administration
def test_admin_tabs_and_account_management(stack, admin_api, page):
    problems = watch(page)
    api_login(page, ADMIN_USER, NEW_PASSWORD)
    page.goto(BASE + "/admin")
    page.wait_for_selector("#tab-usage")
    assert page.locator(".kit-tab").count() == 7
    page.screenshot(path=str(SHOTS / "C10_admin_usage.png"), full_page=True)
    page.click("#tab-health")
    page.wait_for_selector("#panel-health table")
    assert "kribi" in page.inner_text("#panel-health")
    page.click("#tab-users")
    page.fill("#new-username", "carl")
    page.fill("#new-display", "Carl Dupont")
    page.fill("#new-password", "faible")
    page.click("#user-form button[type=submit]")
    page.wait_for_selector(".toast.error")                                                       # politique de mot de passe
    page.fill("#new-password", "Carl-Passw0rd1")
    page.click("#user-form button[type=submit]")
    page.wait_for_selector("#panel-users td:has-text('carl')")
    page.screenshot(path=str(SHOTS / "C11_admin_comptes.png"), full_page=True)
    page.click("#tab-sessions")
    page.wait_for_selector("#panel-sessions tbody tr")
    assert "vous" in page.inner_text("#panel-sessions")
    page.click("#tab-audit")
    page.wait_for_selector("#panel-audit tbody tr")
    assert "user_create" in page.inner_text("#panel-audit") and page.get_attribute("#audit-csv", "href").startswith("/api/admin/audit.csv")
    assert real_problems(problems) == []


def test_admin_can_revoke_another_session(stack, admin_api, page):
    admin_api.post("/api/admin/users", {"username": "revokee", "display_name": "R", "role": "supervision", "password": "Temp-Passw0rd"})
    first = requests.Session()
    first.post(f"{BASE}/api/login", json={"username": "revokee", "password": "Temp-Passw0rd"}, timeout=10)
    first.post(f"{BASE}/api/me/password", json={"current_password": "Temp-Passw0rd", "new_password": "Revok-Passw0rd1"}, timeout=10)
    victim = Api("revokee", "Revok-Passw0rd1")                                         # un compte dédié : la session partagée de « sup1 » reste intacte
    api_login(page, ADMIN_USER, NEW_PASSWORD)
    page.goto(BASE + "/admin")
    page.click("#tab-sessions")
    rows = page.locator("#panel-sessions tbody tr:has-text('revokee')")
    rows.first.wait_for()
    for _ in range(rows.count()):
        page.locator("#panel-sessions tbody tr:has-text('revokee')").first.get_by_role("button", name="Révoquer").click()
        page.wait_for_timeout(700)
    assert victim.get("/api/me").json() == {"user": None}                              # la session révoquée est coupée immédiatement


def test_history_import_detectors_and_simulation_from_the_admin_page(stack, admin_api, page):
    api_login(page, ADMIN_USER, NEW_PASSWORD)
    page.goto(BASE + "/admin")
    page.click("#tab-detectors")
    page.wait_for_selector("#backfill-days")
    page.fill("#backfill-days", "3")
    page.get_by_role("button", name="Importer").click()
    page.wait_for_selector(".toast.success")
    for _ in range(60):
        state = admin_api.get("/api/admin/history").json()
        if not state["backfill"]["running"] and state["total_rows"] > 0:
            break
        time.sleep(1)
    assert state["total_rows"] > 0 and not [k for k, v in state["backfill"]["progress"].items() if v["error"]]
    page.reload()
    page.click("#tab-detectors")
    page.wait_for_selector("#history-stats")
    assert "relevés" in page.inner_text("#history-stats")
    assert page.locator("input[type=checkbox][aria-label='Référence saisonnière']").count() == 1
    page.screenshot(path=str(SHOTS / "C12_admin_detecteurs.png"), full_page=True)
    page.click("#tab-simulation")
    page.wait_for_selector("#sim-run")
    page.fill("#sim-traffic_consecutive_drops_min_percent", "5")
    page.click("#sim-run")
    page.wait_for_selector("#sim-result .stat-cards", timeout=60000)
    assert "Candidate" in page.inner_text("#sim-result")
    page.screenshot(path=str(SHOTS / "C13_admin_simulation.png"), full_page=True)


def test_forecast_analysis_draws_a_chart(stack, admin_api, page):
    if not admin_api.get("/api/admin/history").json()["total_rows"]:
        pytest.skip("historique vide")
    base = sorted(admin_api.get("/api/admin/history").json()["bases"])[0]
    api_login(page, ADMIN_USER, NEW_PASSWORD)
    page.goto(BASE + "/admin")
    page.click("#tab-detectors")
    page.wait_for_selector("#analysis-base")
    page.select_option("#analysis-base", base)
    page.get_by_role("button", name="Prévision 24 h").click()
    wait_js(page, "document.querySelector('#analysis-output svg, #analysis-output .toast, .toast.error')", timeout=20000)


# ---------------------------------------------------------------- sparklines, mobile
def test_sparklines_appear_once_history_exists(stack, admin_api, page):
    admin_api.post("/api/admin/history/backfill", {"days": 5})
    for _ in range(60):
        if not admin_api.get("/api/admin/history").json()["backfill"]["running"]:
            break
        time.sleep(1)
    problems = watch(page)
    page.goto(BASE + "/")
    page.wait_for_selector("tr[data-db] svg.sparkline", timeout=20000)
    assert page.locator("tr[data-db] svg.sparkline").count() >= 3
    assert page.locator("svg.sparkline").first.get_attribute("role") == "img" and page.locator("svg.sparkline title").count() > 0
    page.screenshot(path=str(SHOTS / "C14_sparklines.png"), full_page=True)
    assert problems == []


def test_mobile_layout_uses_cards(stack, browser_ctx):
    ctx = browser_ctx.new_context(viewport={"width": 390, "height": 844}, locale="fr-FR", is_mobile=True, has_touch=True)
    try:
        page = ctx.new_page()
        problems = watch(page)
        page.goto(BASE + "/")
        page.wait_for_selector("#mobile-cards .mobile-card", timeout=20000)
        assert page.locator("#mobile-cards").is_visible() and page.locator("#table-wrapper-international").is_hidden()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")      # pas de défilement horizontal de la page
        page.screenshot(path=str(SHOTS / "C15_mobile.png"), full_page=True)
        assert problems == []
    finally:
        ctx.close()


# ---------------------------------------------------------------- sécurité du rendu
def test_markup_in_data_is_never_executed(stack, admin_api, page):
    insert_ticket(990020, reason=XSS, description=XSS, database_name="xss_base")
    admin_api.post("/api/admin/users", {"username": "xssuser", "display_name": XSS, "role": "supervision", "password": "Xss-Passw0rd1"})
    page.goto(BASE + "/tickets/")
    card(page, 990020).wait_for()
    page.goto(BASE + "/")
    page.wait_for_selector("tr[data-db]")
    page.evaluate("localStorage.clear()")
    api_login(page, "xssuser", "Xss-Passw0rd1")
    page.reload()
    page.wait_for_selector("#account-name")
    assert page.inner_text("#account-name") == XSS                                              # affiché tel quel, en texte
    page.goto(BASE + "/tickets/")
    card(page, 990020).wait_for()
    page.wait_for_timeout(800)
    assert page.evaluate("window.__xss") is None and page.locator(".ticket-card img").count() == 0


def test_all_pages_are_clean_for_an_admin(stack, admin_api, page):
    problems = watch(page)
    api_login(page, ADMIN_USER, NEW_PASSWORD)
    for path in ("/", "/tickets/", "/supervision", "/admin"):
        page.goto(BASE + path)
        page.wait_for_selector("#account-name", timeout=10000)
        page.wait_for_timeout(1200)
    assert problems == [], problems
