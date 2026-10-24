"""Fixtures E2E : démarre source simulée, application et navigateur Playwright.

Prérequis : MariaDB/MySQL joignable (voir seed_demo_data.py), faux Orange sur :9001 et puits SMTP sur :1025
(le script ../../sandbox_stack.sh les lance). Variables : AUTODO_REPO (dépôt à tester), voir env.demo.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import pymysql
import pymysql.cursors
import pytest
import requests
from playwright.sync_api import sync_playwright

REPO = Path(os.environ.get("AUTODO_REPO", Path(__file__).resolve().parents[2])).resolve()
DEMO = REPO / "demo"
BASE = "http://127.0.0.1:8501"
APP_LOG = Path("/tmp/e2e_app.out")
SHOTS = DEMO / "screenshots"
SHOTS.mkdir(exist_ok=True)
ADMIN_USER, ADMIN_INITIAL_PASSWORD = "admin", "Admin-Initial-1"      # voir demo/env.demo
XSS_IP = "<img src=x onerror=window.__xss=1>"   # 34 car. -> VARCHAR(45) l'accepte


def sh(*args, **kw):
    return subprocess.run([sys.executable, str(DEMO / "seed_demo_data.py"), *args], check=True, cwd=DEMO,
                          capture_output=True, text=True, **kw)


def db(sql, params=()):
    c = pymysql.connect(host="127.0.0.1", user="u", password="p", database="tickets_db", cursorclass=pymysql.cursors.DictCursor)
    try:
        with c.cursor() as cur:
            cur.execute(sql, params)
            rows = list(cur.fetchall())
        c.commit()
        return rows
    finally:
        c.close()


def log_text() -> str:
    return APP_LOG.read_text(errors="ignore") if APP_LOG.exists() else ""


def cycles_done() -> int:
    t = log_text()
    return t.count("Cycle terminé") + t.count("Cycle de supervision en échec")


def wait_cycles(n: int, timeout: int = 60):
    end = time.time() + timeout
    while time.time() < end:
        if cycles_done() >= n:
            return True
        time.sleep(1)
    return False


@pytest.fixture(scope="session")
def stack():
    # 1. aligner sur :03 / :08 ... pour que le 2e cycle (cron :06/:11) arrive ~2,5 min après le 1er
    if os.getenv("E2E_ALIGN", "1") == "1":
        now = datetime.now()
        if now.minute % 5 not in (2, 3):          # sinon le 2e cycle (cron :01/:06/:11...) tomberait trop tôt/tard
            wait = ((3 - now.minute) % 5) * 60 - now.second
            print(f"[e2e] alignement sur la fenêtre de 5 min : attente {wait}s"); time.sleep(max(wait, 0))
    # 2. scénarios + données
    for args in (("clear",), ("zero", "douala_camtel", "--minutes-ago", "5"), ("instant_drop", "kribi"), ("gradual", "oci_sat3", "--minutes-ago", "25"),
                 ("loss", "wacs_ic", "--minutes-ago", "30"), ("stale", "garoua_camtel", "--minutes-ago", "90")):
        sh("scenario", *args)
    sh("seed", "--reset")
    # 3. application
    data_dir = tempfile.mkdtemp(prefix="autodo_e2e_")
    for table in ("users", "audit_log", "config_versions", "maintenance_windows", "alert_acks", "ticket_events", "ticket_feedback",
                  "metrics_5min", "detector_settings", "detector_verdicts", "usage_peaks"):
        try:                                    # état initial déterministe : aucun compte, l'admin initial sera recréé au démarrage
            db(f"DELETE FROM {table}")
        except Exception:
            pass                                # table pas encore créée (1ʳᵉ exécution : les migrations la créeront)
    (REPO / "db_config.json").write_text((DEMO / "db_config.json").read_text())
    requests.post("http://127.0.0.1:9001/_debug/reset", timeout=5)
    follow = subprocess.Popen([sys.executable, str(DEMO / "seed_demo_data.py"), "follow", "--interval", "10"], cwd=DEMO)
    app = subprocess.Popen([sys.executable, "main.py"], cwd=REPO, stdout=APP_LOG.open("w"), stderr=subprocess.STDOUT,
                           env={**json.loads(os.environ.get("AUTODO_ORIG_ENV", "{}") or json.dumps(dict(os.environ)), ),
                                "DATA_DIR": data_dir, "LOG_FILE": str(Path(data_dir) / "app.log"),
                                "AUTH_MAX_FAILURES": "50"})
    try:
        for _ in range(60):
            try:
                if requests.get(f"{BASE}/health", timeout=2).status_code == 200: break
            except requests.RequestException: pass
            time.sleep(1)
        else:
            pytest.fail("l'application ne démarre pas:\n" + log_text()[-2000:])
        wait_cycles(1, 90)
        end = time.time() + 90                       # laisse l'outbox Orange (toutes les 10 s en démo) vider ses tickets
        while time.time() < end and len(requests.get("http://127.0.0.1:9001/_debug/tickets", timeout=5).json()["tickets"]) < 5:
            time.sleep(2)
        time.sleep(2)
        snap = {"c0": cycles_done(), "local": len(db("SELECT id FROM tickets_tb")), "orange": len(requests.get(
            "http://127.0.0.1:9001/_debug/tickets", timeout=5).json()["tickets"])}
        yield {"repo": REPO, **snap}
    finally:
        app.terminate(); follow.terminate()
        try: app.wait(10)
        except Exception: app.kill()
        shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def pw(stack):
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser_ctx(pw):
    b = pw.chromium.launch()
    yield b
    b.close()


@pytest.fixture()
def page(browser_ctx):
    ctx = browser_ctx.new_context(viewport={"width": 1500, "height": 950}, locale="fr-FR")
    ctx.set_default_timeout(10000)                      # échec rapide : les écrans attendus apparaissent en quelques secondes
    pg = ctx.new_page()
    pg.on("dialog", lambda d: d.dismiss())
    yield pg
    ctx.close()


# ====================================================================== aides communes aux fichiers E2E (comptes, navigateur)
NEW_PASSWORD = "Admin-Final-2x"
SUP_PASSWORD = "Super-Visor-1x"
XSS = "<img src=x onerror=window.__xss=1>"


# ---------------------------------------------------------------- aides
def watch(page) -> list[str]:
    """Collecte erreurs console (dont violations de CSP), exceptions JS et requêtes échouées."""
    problems: list[str] = []
    page.on("console", lambda m: problems.append(f"console: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.on("requestfailed", lambda r: problems.append(f"requestfailed: {r.url}") if not r.url.endswith("/events") else None)   # SSE coupé à la navigation
    return problems


def real_problems(problems):
    """Écarte les 4xx VOLONTAIRES des tests (« Failed to load resource » : la réponse d'erreur est le comportement attendu)."""
    return [p for p in problems if "status of 4" not in p]


class Api:
    """Client HTTP d'un compte (cookie de session)."""

    def __init__(self, username, password):
        self.s = requests.Session()
        r = self.s.post(f"{BASE}/api/login", json={"username": username, "password": password}, timeout=10)
        assert r.status_code == 200, r.text
        self.user = r.json()["user"]

    def get(self, path, **kw):
        return self.s.get(BASE + path, timeout=30, **kw)

    def post(self, path, body=None):
        return self.s.post(BASE + path, json=body if body is not None else {}, timeout=60)

    def put(self, path, body):
        return self.s.put(BASE + path, json=body, timeout=30)

    def delete(self, path):
        return self.s.delete(BASE + path, timeout=30)


@pytest.fixture(scope="session")
def admin_api(stack):
    """Admin initial : changement de mot de passe obligatoire fait ici (une fois pour toute la session)."""
    first = requests.Session()
    assert first.post(f"{BASE}/api/login", json={"username": ADMIN_USER, "password": ADMIN_INITIAL_PASSWORD}, timeout=10).json()["user"]["must_change_password"] is True
    assert first.post(f"{BASE}/api/me/password", json={"current_password": ADMIN_INITIAL_PASSWORD, "new_password": NEW_PASSWORD}, timeout=10).status_code == 200
    return Api(ADMIN_USER, NEW_PASSWORD)


@pytest.fixture(scope="session")
def supervisor(admin_api):
    """Compte superviseur au mot de passe DÉFINITIF (le changement forcé est testé séparément)."""
    created = admin_api.post("/api/admin/users", {"username": "sup1", "display_name": "Sophie Sup", "role": "supervision", "password": "Temp-Passw0rd"})
    assert created.status_code == 201, created.text
    first = requests.Session()
    first.post(f"{BASE}/api/login", json={"username": "sup1", "password": "Temp-Passw0rd"}, timeout=10)
    assert first.post(f"{BASE}/api/me/password", json={"current_password": "Temp-Passw0rd", "new_password": SUP_PASSWORD}, timeout=10).status_code == 200
    return Api("sup1", SUP_PASSWORD)


def ui_login(page, username, password):
    page.goto(BASE + "/")
    page.click("#loginBtn")
    page.fill("#login-username", username)
    page.fill("#login-password", password)
    page.click("#login-dialog-form button[type=submit]")
    page.wait_for_selector("#account-name", timeout=10000)


def api_login(page, username, password):
    """Connexion rapide : la requête partage les cookies du contexte navigateur."""
    r = page.context.request.post(f"{BASE}/api/login", data={"username": username, "password": password})
    assert r.ok, r.text()


def wait_js(page, expression, timeout=10000):
    """Attend qu'une expression JavaScript devienne vraie. (`wait_for_function` de Playwright utilise eval : la CSP de l'application l'interdit.)"""
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        if page.evaluate(f"() => !!({expression})"):
            return
        page.wait_for_timeout(150)
    state = page.evaluate("() => JSON.stringify({url: location.href, popup: document.getElementById('alert-popup')?.style.display, "
                          "current: typeof STATE === 'undefined' ? null : STATE.currentAlertes.length, shown: typeof STATE === 'undefined' ? null : STATE.shownIds.size})")
    raise AssertionError(f"condition jamais vraie : {expression} — état de la page : {state} — accusés en base : {db('SELECT ticket_id, acked_by, acked_at FROM alert_acks')}")


def clear_acks():
    """Remet tous les accusés à zéro. Le serveur garde la liste d'alertes en cache 3 s : on attend son expiration."""
    db("DELETE FROM alert_acks")
    time.sleep(3.3)


def wait_acked(timeout=8):
    """Attend qu'un accusé (envoyé en arrière-plan par le navigateur) soit enregistré : évite de le supprimer trop tôt."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if db("SELECT COUNT(*) AS n FROM alert_acks")[0]["n"] > 0:
            return
        time.sleep(0.2)
    raise AssertionError("aucun accusé enregistré")


def ack_all():
    """Acquitte toutes les alertes ouvertes PAR L'API (elle invalide le cache serveur ; un INSERT direct laisserait la pop-up s'afficher)."""
    ids = [row["id"] for row in db("SELECT id FROM tickets_tb WHERE status = 'open'")]
    if ids:
        requests.post(f"{BASE}/api/ack", json={"ticket_ids": ids[:200]}, timeout=10)
