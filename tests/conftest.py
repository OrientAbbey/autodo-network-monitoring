"""Environnement de test : tout est configuré AVANT l'import de ``core`` (les réglages sont lus une fois)."""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# environnement d'origine (utilisé par tests/e2e pour lancer l'application avec SA configuration, pas celle des tests unitaires)
os.environ.setdefault("AUTODO_ORIG_ENV", json.dumps(dict(os.environ)))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="autodo_test_"))
os.environ.setdefault("LOG_FILE", str(Path(os.environ["DATA_DIR"]) / "test.log"))
os.environ.update({
    "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "Admin-Initial-1",
    "ORANGE_API_SERVER_INTRA": "http://orange.test", "ORANGE_API_TOKEN_ENDPOINT": "/oauth/v3/token",
    "ORANGE_API_BASE_ENDPOINT": "/tt/v3", "ORANGE_API_CLIENT_ID": "cid", "ORANGE_API_CLIENT_SECRET": "SuperSecretValue123456",
    "CLIENT_USER_ID": "TESTUSER", "ORANGE_API_RATE_PER_SECOND": "50", "ORANGE_API_BURST": "50",
    "LOCAL_DB_HOST": os.environ.get("TEST_DB_HOST", "127.0.0.1"), "LOCAL_DB_USER": "u", "LOCAL_DB_PASS": "p",
    "LOCAL_DB_NAME": "autodo_test", "LOCAL_DB_PORT": os.environ.get("TEST_DB_PORT", "3306"),
    "DB_USER": "diag", "DB_PASS": "diagpass",
})

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def test_db():
    """Base MariaDB/MySQL de test (créée avec le compte administrateur local, comme demo/seed_demo_data.py)."""
    sys.path.insert(0, str(ROOT / "demo"))
    try:
        from seed_demo_data import admin_conn
        c = admin_conn()
        cur = c.cursor()
        cur.execute("DROP DATABASE IF EXISTS autodo_test")
        cur.execute("CREATE DATABASE autodo_test")
        # GitHub Actions reaches the MariaDB service through the runner's bridge IP.
        for host in ("127.0.0.1", "localhost", "%"):
            cur.execute(f"CREATE USER IF NOT EXISTS 'u'@'{host}' IDENTIFIED BY 'p'")
            cur.execute(f"GRANT ALL ON autodo_test.* TO 'u'@'{host}'")
        c.commit()
    except Exception as e:
        pytest.skip(f"base de test indisponible : {e}")
    from core import db
    assert db.ensure_schema()
    yield db
    db.dispose_all()


# Tables de la phase A : vidées avant chaque test qui en dépend (fixture réutilisable)
PHASE_A_TABLES = ("users", "audit_log", "config_versions", "maintenance_windows", "alert_acks", "ticket_events", "ticket_feedback",
                  "tickets_tb", "ticket_counters", "usage_peaks", "metrics_5min", "detector_settings", "detector_verdicts")


@pytest.fixture
def clean_state(test_db):
    """Base locale vide + configuration par défaut + sessions et limiteur remis à zéro."""
    from sqlalchemy import text
    from core import auth, config, tickets
    default = json.loads((ROOT / "config.default.json").read_text(encoding="utf-8"))

    def wipe():
        with test_db.local_tx() as c:
            for table in PHASE_A_TABLES:
                c.execute(text(f"DELETE FROM {table}"))
        config.write_raw({**default, "overrides": {}})
        auth.limiter.reset()
        auth.sessions._by_digest.clear()
        tickets.invalidate_alert_cache()

    wipe()
    yield test_db
    wipe()
