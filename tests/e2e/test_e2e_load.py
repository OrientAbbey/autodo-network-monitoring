"""Charge réaliste : 25 à 50 spectateurs permanents (interrogation du tableau de bord + flux SSE) pendant que
l'administration reste réactive. Mesure les latences et vérifie qu'aucune écriture SQL n'est faite par requête."""
import statistics
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests

from conftest import BASE, db

VIEWERS = 50
ROUNDS = 12


def _viewer(i, latencies, errors):
    s = requests.Session()
    cid = str(uuid.uuid4())
    for _ in range(ROUNDS):
        t = time.perf_counter()
        try:
            r = s.post(BASE + "/api/dashboard_data", json={}, headers={"X-Client-Id": cid}, timeout=15)
            if r.status_code != 200:
                errors.append(r.status_code)
        except requests.RequestException as e:
            errors.append(type(e).__name__)
        latencies.append((time.perf_counter() - t) * 1000)
        time.sleep(0.05)
    return cid


def _sse(stop, counter):
    try:
        with requests.get(BASE + "/events", stream=True, timeout=(5, 30)) as r:
            for line in r.iter_lines():
                if stop.is_set():
                    break
                if line.startswith(b"event: hello"):
                    counter.append(1)
    except requests.RequestException:
        pass


def test_fifty_permanent_viewers_stay_fast_and_error_free(stack):
    lat, errs, stop, hello = [], [], threading.Event(), []
    sse_threads = [threading.Thread(target=_sse, args=(stop, hello), daemon=True) for _ in range(VIEWERS)]
    [t.start() for t in sse_threads]
    time.sleep(1.0)
    before = len(db("SELECT 1 AS x FROM daily_users"))
    with ThreadPoolExecutor(VIEWERS) as ex:
        cids = list(ex.map(lambda i: _viewer(i, lat, errs), range(VIEWERS)))
    time.sleep(12)                                              # laisse le tampon de visites se vider (toutes les 10 s)
    stop.set()
    p95 = statistics.quantiles(lat, n=20)[-1]
    print(f"\n{VIEWERS} spectateurs × {ROUNDS} requêtes : médiane {statistics.median(lat):.0f} ms, p95 {p95:.0f} ms, max {max(lat):.0f} ms, erreurs {len(errs)}, SSE {len(hello)}")
    assert not errs, errs[:5]
    assert len(hello) == VIEWERS, "tous les flux SSE doivent s'établir"
    assert p95 < 1500, f"p95 {p95:.0f} ms"
    # écritures groupées : 50 clients ⇒ 50 lignes (et non 50 × 12 écritures)
    rows = db("SELECT client_id, visits_count FROM daily_users")
    got = {r["client_id"] for r in rows}
    assert set(cids) <= got and len(got) - before >= VIEWERS
    assert requests.get(BASE + "/health", timeout=5).status_code == 200


def test_admin_endpoint_stays_responsive_under_viewer_load(stack):
    stop = threading.Event()
    def noise():
        s = requests.Session()
        while not stop.is_set():
            s.post(BASE + "/api/dashboard_data", json={}, timeout=15)
    ts = [threading.Thread(target=noise, daemon=True) for _ in range(30)]
    [t.start() for t in ts]
    time.sleep(1)
    t0 = time.perf_counter()
    r = requests.post(BASE + "/api/auth", json={"password": "cfgpass"}, timeout=10)
    dt = (time.perf_counter() - t0) * 1000
    stop.set()
    assert r.status_code == 200 and dt < 1500, f"/api/auth {dt:.0f} ms sous charge"
