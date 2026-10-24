"""Temps réel : JSON du tableau de bord sérialisé une fois, ETag/304, SSE sans nom, publication depuis le cycle, présence."""
import asyncio
import json
import threading

import pytest
from fastapi.testclient import TestClient

from core import analytics, constants, monitoring, tickets
from routers import dashboard
from tests.test_ticket_workflow import mk


@pytest.fixture
def client(clean_state):
    import main
    yield TestClient(main.app)
    with constants.DASHBOARD_CACHE_LOCK:
        constants.DASHBOARD_CACHE.clear()
        constants.DASHBOARD_JSON = b""


def publish_cycle(**extra):
    return constants.set_dashboard({"recap": {"kribi": {"v": 1}}, **extra})


def test_no_data_before_the_first_cycle_answers_503(client):
    assert client.get("/api/dashboard_data").status_code == 503


def test_body_is_the_cached_json_plus_the_alerts(client):
    tickets.persist(tickets.ChangeSet(inserts=[mk(1)]))
    publish_cycle()
    response = client.get("/api/dashboard_data")
    data = response.json()                                           # le JSON assemblé à la main doit être valide
    assert response.status_code == 200 and data["recap"] == {"kribi": {"v": 1}}
    assert [a["id"] for a in data["alertes"]["traffic"]] == [1] and data["alertes"]["traffic"][0]["acked"] is False


def test_etag_gives_a_304_until_a_cycle_or_an_alert_change(client):
    publish_cycle()
    first = client.get("/api/dashboard_data")
    etag = first.headers["etag"]
    assert etag.startswith('W/"') and first.headers["cache-control"] == "no-cache"
    same = client.get("/api/dashboard_data", headers={"If-None-Match": etag})
    assert same.status_code == 304 and same.content == b"" and same.headers["etag"] == etag
    tickets.persist(tickets.ChangeSet(inserts=[mk(1)]))                                       # une alerte apparaît
    changed = client.get("/api/dashboard_data", headers={"If-None-Match": etag})
    assert changed.status_code == 200 and changed.headers["etag"] != etag
    publish_cycle()                                                                            # nouveau cycle : nouvelle version
    assert client.get("/api/dashboard_data", headers={"If-None-Match": changed.headers["etag"]}).status_code == 200


def test_acknowledging_changes_the_etag_so_every_browser_refreshes(client):
    tickets.persist(tickets.ChangeSet(inserts=[mk(1)]))
    publish_cycle()
    before = client.get("/api/dashboard_data").headers["etag"]
    client.post("/api/ack", json={"ticket_ids": [1]})
    tickets.invalidate_alert_cache()
    assert client.get("/api/dashboard_data", headers={"If-None-Match": before}).status_code == 200


def test_gzip_response_is_one_gzip_member_identical_in_content_and_shared_between_viewers(client):
    """Réponse complète mémorisée par (ETag, gzip) : compressée UNE fois pour tous les spectateurs aux mêmes filtres."""
    import gzip as gz
    dashboard._BODY_CACHE.clear()
    tickets.persist(tickets.ChangeSet(inserts=[mk(1)]))
    publish_cycle()
    plain = client.get("/api/dashboard_data", headers={"Accept-Encoding": "identity"})
    zipped = client.get("/api/dashboard_data", headers={"Accept-Encoding": "gzip"})
    assert zipped.headers["content-encoding"] == "gzip" and zipped.headers["vary"] == "Accept-Encoding" and "content-encoding" not in plain.headers
    assert plain.headers["etag"] == zipped.headers["etag"] and zipped.json() == plain.json()           # httpx décode : un seul membre gzip
    assert zipped.json()["alertes"]["traffic"][0]["id"] == 1
    for _ in range(5):
        client.get("/api/dashboard_data", headers={"Accept-Encoding": "gzip"})
    assert len(dashboard._BODY_CACHE) == 2                                                             # 1 variante brute + 1 gzip, quel que soit le nombre de spectateurs
    assert gz.decompress(dashboard._BODY_CACHE[(zipped.headers["etag"], True)]) == plain.content


def test_a_new_cycle_never_serves_a_stale_cached_body(client):
    dashboard._BODY_CACHE.clear()
    publish_cycle()
    first = client.get("/api/dashboard_data").json()
    constants.set_dashboard({"recap": {"kribi": {"v": 2}}})
    assert client.get("/api/dashboard_data").json()["recap"] == {"kribi": {"v": 2}} != first["recap"]


def test_etag_comparison_handles_lists_and_weak_prefix():
    assert dashboard._etag_matches('"a", W/"b"', 'W/"b"') and dashboard._etag_matches("*", 'W/"x"')
    assert not dashboard._etag_matches('"a"', 'W/"b"') and not dashboard._etag_matches(None, 'W/"b"')


def test_dashboard_filters_are_validated(client):
    publish_cycle()
    assert client.get("/api/dashboard_data?status=open&type=traffic,pertes&period=week").status_code == 200
    assert client.get("/api/dashboard_data?status=bidon").status_code == 422
    assert client.get("/api/dashboard_data?period=jamais").status_code == 422


def test_the_dashboard_is_serialised_once_per_cycle_not_once_per_request(client, monkeypatch):
    publish_cycle()
    calls = []
    real = dashboard.orjson.dumps
    monkeypatch.setattr(dashboard.orjson, "dumps", lambda *a, **k: calls.append(a[0]) or real(*a, **k))
    for _ in range(5):
        client.get("/api/dashboard_data")
    assert all(set(c) <= {"traffic", "pertes"} for c in calls if isinstance(c, dict))        # seules les alertes sont sérialisées par requête


def test_set_dashboard_increments_the_version_and_keeps_the_dict_cache():
    before = constants.DASHBOARD_VERSION
    assert publish_cycle() == before + 1 and constants.DASHBOARD_CACHE["recap"]
    assert json.loads(constants.DASHBOARD_JSON + b"}")["recap"] == {"kribi": {"v": 1}}


def test_sse_events_are_unnamed_so_that_onmessage_receives_them():
    frame = dashboard.format_sse({"type": "data_version", "version": 7})
    assert frame.startswith("data: ") and "event:" not in frame and frame.endswith("\n\n")
    assert json.loads(frame[6:]) == {"type": "data_version", "version": 7}


# ------------------------------------------------------------------ publication depuis le thread du cycle
def test_publish_threadsafe_is_a_noop_outside_the_server():
    monitoring.runtime.loop = monitoring.runtime.app = None
    monitoring.publish_threadsafe({"type": "data_version", "version": 1})                      # ne lève pas


def test_the_cycle_thread_can_publish_into_the_event_loop():
    """Le cycle tourne dans un thread APScheduler : la publication doit atterrir dans la file du bon spectateur (boucle asyncio)."""
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    class FakeApp:
        class state:
            sse_subscribers = set()
            sse_subscribers_lock = None

    async def prepare():
        FakeApp.state.sse_subscribers_lock = asyncio.Lock()
        queue = asyncio.Queue(maxsize=6)
        FakeApp.state.sse_subscribers.add(queue)
        return queue

    queue = asyncio.run_coroutine_threadsafe(prepare(), loop).result(2)
    monitoring.bind_runtime(loop, FakeApp)
    try:
        assert monitoring.viewer_count() == 1
        monitoring.publish_threadsafe({"type": "data_version", "version": 3})
        event = asyncio.run_coroutine_threadsafe(asyncio.wait_for(queue.get(), 2), loop).result(3)
        assert event["type"] == "data_version" and event["version"] == 3 and "at" in event
    finally:
        monitoring.runtime.loop = monitoring.runtime.app = None
        loop.call_soon_threadsafe(loop.stop)


# ------------------------------------------------------------------ présence
def test_presence_endpoint_and_daily_peak(client):
    assert client.get("/api/presence").json() == {"viewers": 0, "peak_today": 0}
    analytics.record_viewer_peak(3)
    analytics.record_viewer_peak(7)
    analytics.record_viewer_peak(5)                                                            # un pic plus bas ne remplace pas le plus haut
    analytics.record_viewer_peak(0)
    assert client.get("/api/presence").json()["peak_today"] == 7
    assert analytics.viewer_peaks("today")[0]["peak_viewers"] == 7


def test_the_peak_job_records_the_current_viewer_count(clean_state):
    class Subs:
        class state:
            sse_subscribers = {1, 2, 3, 4}

    monitoring.runtime.app = Subs
    try:
        monitoring.job_viewer_peak()
    finally:
        monitoring.runtime.app = None
    assert analytics.peak_today() == 4
