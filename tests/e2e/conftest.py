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
