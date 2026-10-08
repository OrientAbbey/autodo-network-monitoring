"""Limitation de débit, disjoncteur et client Orange (session HTTP simulée : aucun appel réseau)."""
import logging
import threading

import pytest
import requests

from core import ratelimit
from core.orange import OrangeClient, format_description


class Clock:
    def __init__(self): self.t = 0.0
    def now(self): return self.t
    def sleep(self, s): self.t += s


def test_token_bucket_spaces_calls_at_the_configured_rate():
    c = Clock()
    b = ratelimit.TokenBucket(rate=2, burst=1, clock=c.now, sleep=c.sleep)
    for _ in range(11):
        assert b.acquire(timeout=60)
    assert c.t == pytest.approx(5.0, abs=0.2)             # 10 appels après le premier ≈ 10 / 2 par s


def test_token_bucket_allows_a_burst_then_throttles():
    c = Clock()
    b = ratelimit.TokenBucket(rate=1, burst=3, clock=c.now, sleep=c.sleep)
    for _ in range(3):
        b.acquire()
    assert c.t == 0
    b.acquire()
    assert c.t == pytest.approx(1.0, abs=0.05)


def test_token_bucket_honours_retry_after_for_every_caller():
    c = Clock()
    b = ratelimit.TokenBucket(rate=100, burst=100, clock=c.now, sleep=c.sleep)
    b.pause_for(30)
    b.acquire()
    assert c.t >= 30


def test_token_bucket_gives_up_after_timeout():
    c = Clock()
    b = ratelimit.TokenBucket(rate=0.1, burst=1, clock=c.now, sleep=c.sleep)
    b.acquire()
    assert b.acquire(timeout=1.0) is False


def test_circuit_breaker_opens_then_half_opens_then_closes():
    c = Clock()
    cb = ratelimit.CircuitBreaker(threshold=3, cooldown=60, clock=c.now)
    for _ in range(3):
        assert cb.allow()
        cb.failure()
    assert cb.state == "open" and not cb.allow()
    c.t = 61
    assert cb.allow() and not cb.allow()                  # un seul appel d'essai
    cb.success()
    assert cb.state == "closed" and cb.allow()


def test_backoff_grows_and_is_capped():
    d = [ratelimit.backoff_delay(i, jitter=0) for i in (1, 2, 3, 20)]
    assert d == [60, 120, 240, 1800]


# ------------------------------------------------------------------ client Orange
def resp(status=200, body=b"{}", headers=None):
    r = requests.Response()
    r.status_code, r._content, r.headers = status, body, requests.structures.CaseInsensitiveDict(headers or {})
    return r


class FakeSession:
    def __init__(self, script):
        self.script, self.calls = list(script), []
    def post(self, url, **kw):
        self.calls.append(("POST-token", url, kw)); return resp(200, b'{"access_token":"tok","expires_in":3600}')
    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    def mount(self, *a): pass


def client(script, **kw):
    s = FakeSession(script)
    return OrangeClient(session=s, **kw), s


def test_create_ticket_success_returns_orange_id():
    c, s = client([resp(201, b'{"id":"TT42"}')])
    r = c.create_ticket({"id": 1, "description": "d", "timestamp": "t"})
    assert r.ok and r.orange_id == "TT42"
    assert s.calls[1][2]["verify"] is not False, "la vérification TLS ne doit jamais être désactivée"


def test_429_pauses_all_callers_and_is_retryable_with_retry_after():
    clock = Clock()
    bucket = ratelimit.TokenBucket(100, 100, clock=clock.now, sleep=clock.sleep)
    c, _ = client([resp(429, b"{}", {"Retry-After": "12"})], bucket=bucket)
    r = c.create_ticket({"id": 1})
    assert not r.ok and r.retryable and r.retry_after == 12
    bucket.acquire()
    assert clock.t >= 12


def test_server_error_is_retryable_and_ambiguous_for_post():
    c, _ = client([resp(503)])
    r = c.create_ticket({"id": 1})
    assert not r.ok and r.retryable and r.ambiguous


def test_client_error_is_not_retryable():
    c, _ = client([resp(400, b'{"message":"bad"}')])
    r = c.create_ticket({"id": 1})
    assert not r.ok and not r.retryable


def test_401_refreshes_the_token_once_then_succeeds():
    c, s = client([resp(401), resp(201, b'{"id":"TT7"}')])
    assert c.create_ticket({"id": 1}).orange_id == "TT7"
    assert [x[0] for x in s.calls].count("POST-token") == 2


def test_timeout_after_send_is_flagged_ambiguous():
    c, _ = client([requests.ReadTimeout("slow")])
    r = c.create_ticket({"id": 1})
    assert r.retryable and r.ambiguous


def test_breaker_stops_hammering_a_failing_api():
    br = ratelimit.CircuitBreaker(threshold=2, cooldown=300)
    c, s = client([resp(500), resp(500)], breaker=br)
    c.create_ticket({"id": 1}); c.create_ticket({"id": 2})
    n = len(s.calls)
    r = c.create_ticket({"id": 3})
    assert not r.ok and "circuit" in r.error and len(s.calls) == n        # aucun appel réseau supplémentaire


def test_token_is_fetched_once_for_concurrent_callers():
    c, s = client([resp(201, b'{"id":"A"}')] * 8)
    ts = [threading.Thread(target=lambda: c.create_ticket({"id": 1})) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert [x[0] for x in s.calls].count("POST-token") == 1


def test_secret_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    c, _ = client([resp(201, b'{"id":"A"}')])
    c.create_ticket({"id": 1})
    assert "SuperSecretValue" not in caplog.text


def test_description_header_appears_once_and_is_bounded():
    d = format_description({"id": 1, "description": "X" * 5000, "timestamp": "t"})
    assert d.count("AUTODO Network Monitoring Alert") == 1 and len(d) <= 2500
