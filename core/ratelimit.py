"""LIMITATION DE DÉBIT ET DISJONCTEUR — briques génériques pour les API externes (Orange, SMTP).

* ``TokenBucket``     : débit moyen ``rate`` appels/s avec rafale ``burst`` ; ``acquire`` bloque (avec délai maximal).
                        ``pause_for`` permet d'honorer un ``Retry-After`` (HTTP 429) en gelant tous les appelants.
* ``CircuitBreaker``  : après ``threshold`` échecs consécutifs, rejette les appels pendant ``cooldown`` secondes
                        (évite de marteler une API en panne), puis laisse passer UN appel d'essai (demi-ouvert).
* ``backoff_delay``   : délai exponentiel avec gigue pour planifier les nouvelles tentatives.
Horloge injectable (``clock`` / ``sleep``) : les tests n'ont pas besoin d'attendre.
"""
from __future__ import annotations

import random
import threading
import time
from typing import Callable


class TokenBucket:
    def __init__(self, rate: float, burst: int, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.rate = max(rate, 0.001)
        self.capacity = max(burst, 1)
        self._tokens = float(self.capacity)
        self._clock, self._sleep = clock, sleep
        self._last = clock()
        self._paused_until = 0.0
        self._lock = threading.Lock()

    def _refill(self, now: float) -> None:
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
        self._last = now

    def pause_for(self, seconds: float) -> None:
        """Gèle l'émission (Retry-After) pour tous les threads."""
        with self._lock:
            self._paused_until = max(self._paused_until, self._clock() + max(seconds, 0))
            self._tokens = 0.0

    def acquire(self, timeout: float = 30.0) -> bool:
        """Obtient un jeton (attend si nécessaire). False si le délai maximal est dépassé."""
        deadline = self._clock() + timeout
        while True:
            with self._lock:
                now = self._clock()
                self._refill(now)
                wait = 0.0
                if now < self._paused_until:
                    wait = self._paused_until - now
                elif self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
                else:
                    wait = (1.0 - self._tokens) / self.rate
            if self._clock() + wait > deadline:
                return False
            self._sleep(min(wait, max(deadline - self._clock(), 0)))


class CircuitBreaker:
    def __init__(self, threshold: int = 5, cooldown: float = 300.0, clock: Callable[[], float] = time.monotonic):
        self.threshold, self.cooldown, self._clock = max(threshold, 1), cooldown, clock
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_in_flight = False
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._opened_at is None:
                return "closed"
            return "half-open" if self._clock() - self._opened_at >= self.cooldown else "open"

    def allow(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return True
            if self._clock() - self._opened_at >= self.cooldown and not self._half_open_in_flight:
                self._half_open_in_flight = True          # un seul appel d'essai
                return True
            return False

    def success(self) -> None:
        with self._lock:
            self._failures, self._opened_at, self._half_open_in_flight = 0, None, False

    def failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._half_open_in_flight = False
            if self._failures >= self.threshold:
                self._opened_at = self._clock()


def backoff_delay(attempt: int, base: float = 60.0, cap: float = 1800.0, jitter: float = 0.2) -> float:
    """60 s, 120 s, 240 s… plafonné à 30 min, ± 20 % de gigue."""
    d = min(cap, base * (2 ** max(attempt - 1, 0)))
    return d * (1 + random.uniform(-jitter, jitter))
