"""AUTHENTIFICATION — sessions en mémoire à portée (scope), mots de passe fail-closed, anti-brute-force.

Remplace constants.TOKENS (jetons partagés entre `cfg` et `dm`, jamais purgés) et la comparaison `!=`.
Hypothèse : un seul worker (l'état est en mémoire) — à documenter ; sinon, utiliser un stockage partagé (Redis/DB).
"""
import hmac
import ipaddress
import os
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request, Response

from .config import settings
from .logger import get_logger

logger = get_logger(__name__)

def _valid_ip(value):
    try:
        return str(ipaddress.ip_address((value or "").strip())) if value else None
    except ValueError:
        return None


def get_real_ip(request: Request) -> str:
    """IP du client. X-Forwarded-For n'est lu que si le pair TCP est un proxy déclaré (TRUSTED_PROXIES)."""
    peer = request.client.host if request.client else None
    trusted = settings.trusted_proxies
    if peer in trusted:
        hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
        for hop in reversed(hops):                 # on remonte la chaîne depuis la droite jusqu'au 1er saut non fiable
            if hop not in trusted:
                return _valid_ip(hop) or peer
        if _valid_ip(request.headers.get("x-real-ip")):
            return _valid_ip(request.headers.get("x-real-ip"))
    return _valid_ip(peer) or (peer or "unknown")


SCOPE_CONFIG = "cfg"
SCOPE_DATA = "dm"
COOKIE_NAMES = {SCOPE_CONFIG: "cfg", SCOPE_DATA: "dm_token"}


def check_password(supplied, expected) -> bool:
    """Comparaison en temps constant ; FAIL-CLOSED si aucun mot de passe n'est configuré."""
    if not expected or not isinstance(supplied, str):
        return False
    return hmac.compare_digest(supplied.encode(), expected.encode())


class SessionStore:
    def __init__(self):
        self._data: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def create(self, scope: str, ttl: int) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = time.time()
            for t in [t for t, (_, exp) in self._data.items() if exp < now]:   # purge
                del self._data[t]
            self._data[token] = (scope, now + ttl)
        return token

    def valid(self, token: str | None, scope: str | None = None) -> bool:
        if not token:
            return False
        with self._lock:
            item = self._data.get(token)
        return bool(item and item[1] > time.time() and (scope is None or item[0] == scope))


sessions = SessionStore()


class RateLimiter:
    """Verrouillage simple : max `limit` échecs par IP sur `window` secondes."""

    def __init__(self, limit=5, window=900):
        self.limit, self.window = limit, window
        self._fails: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, ip):
        q, now = self._fails[ip], time.time()
        while q and q[0] < now - self.window:
            q.popleft()
        return q

    def blocked(self, ip) -> bool:
        with self._lock:
            return len(self._prune(ip)) >= self.limit

    def fail(self, ip):
        with self._lock:
            self._prune(ip).append(time.time())


limiter = RateLimiter(limit=int(os.getenv("AUTH_MAX_FAILURES", "5")))


def issue_cookie(response: Response, scope: str, ttl: int) -> None:
    response.set_cookie(
        COOKIE_NAMES[scope], sessions.create(scope, ttl), max_age=ttl, httponly=True,
        samesite="strict", secure=settings.cookie_secure,
    )


def login(request: Request, response: Response, supplied, expected, scope: str, ttl: int) -> None:
    ip = get_real_ip(request)
    if limiter.blocked(ip):
        raise HTTPException(429, "Trop de tentatives, réessayez plus tard", headers={"Retry-After": "900"})
    if not check_password(supplied, expected):
        limiter.fail(ip)
        logger.warning("Échec d'authentification (%s) depuis %s", scope, ip)
        raise HTTPException(401, "unauthorized")
    issue_cookie(response, scope, ttl)


def require_session(scope: str):
    """Dépendance FastAPI : exige une session valide de la portée demandée."""
    def dep(request: Request):
        if not sessions.valid(request.cookies.get(COOKIE_NAMES[scope]), scope):
            raise HTTPException(401, "Authentication required")
        return True
    return Depends(dep)


def require_any_session():
    def dep(request: Request):
        if not any(sessions.valid(request.cookies.get(n)) for n in COOKIE_NAMES.values()):
            raise HTTPException(401, "Authentication required")
        return True
    return Depends(dep)
