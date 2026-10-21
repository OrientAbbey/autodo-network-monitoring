"""AUTHENTIFICATION — sessions nominatives en mémoire, rôles, limiteur de connexions par IP, contrôle d'origine (CSRF).

* Le navigateur reçoit un jeton aléatoire dans un cookie ``autodo_session`` (HttpOnly, SameSite=Strict, Secure selon réglage).
  Le serveur ne garde que le **SHA-256** du jeton : une fuite de la mémoire ne donne pas de cookie réutilisable.
* Chaque session a un identifiant public (``session_id``), distinct du jeton, pour pouvoir la lister et la révoquer (page Admin).
* Expiration glissante (inactivité ``SESSION_IDLE_MINUTES``) bornée par une durée absolue (``SESSION_MAX_HOURS``).
* Rôles : ``require_role("supervision")`` ou ``require_role("admin")`` (un admin passe partout). Le visiteur n'a pas de session.
* Hypothèse : un seul worker (l'état est en mémoire). Redémarrer l'application déconnecte tout le monde.
"""
from __future__ import annotations

import hashlib
import ipaddress
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request, Response

from .accounts import ROLE_LEVEL
from .config import settings
from .logger import get_logger

logger = get_logger(__name__)

COOKIE_NAME = "autodo_session"
UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


# ============================================================================
# Adresse IP réelle du client
# ============================================================================
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


# ============================================================================
# Sessions
# ============================================================================
@dataclass
class Session:
    session_id: str
    user_id: int
    username: str
    display_name: str
    role: str
    must_change_password: bool
    ip: str
    user_agent: str
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    def public(self) -> dict:
        """Vue sans secret pour la liste des sessions (page Admin)."""
        return {"session_id": self.session_id, "user_id": self.user_id, "username": self.username, "role": self.role,
                "ip": self.ip, "user_agent": self.user_agent,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.created_at)),
                "last_seen": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.last_seen))}


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class SessionStore:
    def __init__(self):
        self._by_digest: dict[str, Session] = {}
        self._lock = threading.Lock()

    def _expired(self, session: Session, now: float) -> bool:
        idle = settings.session_idle_minutes * 60
        absolute = settings.session_max_hours * 3600
        return now - session.last_seen > idle or now - session.created_at > absolute

    def create(self, user: dict, ip: str, user_agent: str) -> str:
        """Ouvre une session pour ``user`` (dict public de ``accounts``) et retourne le jeton en clair (à poser en cookie)."""
        token = secrets.token_urlsafe(32)
        session = Session(session_id=secrets.token_hex(8), user_id=user["id"], username=user["username"],
                          display_name=user["display_name"], role=user["role"],
                          must_change_password=bool(user["must_change_password"]), ip=ip, user_agent=(user_agent or "")[:200])
        with self._lock:
            now = time.time()
            for digest in [d for d, s in self._by_digest.items() if self._expired(s, now)]:       # purge au passage
                del self._by_digest[digest]
            self._by_digest[_digest(token)] = session
        return token

    def get(self, token: str | None) -> Session | None:
        """Session valide (et prolongée) ou None."""
        if not token:
            return None
        digest = _digest(token)
        with self._lock:
            session = self._by_digest.get(digest)
            if session is None:
                return None
            now = time.time()
            if self._expired(session, now):
                del self._by_digest[digest]
                return None
            session.last_seen = now
            return session

    def revoke_token(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._by_digest.pop(_digest(token), None)

    def revoke_session_id(self, session_id: str) -> bool:
        with self._lock:
            for digest, session in list(self._by_digest.items()):
                if session.session_id == session_id:
                    del self._by_digest[digest]
                    return True
        return False

    def revoke_user(self, user_id: int) -> int:
        """Ferme toutes les sessions d'un compte (réinitialisation de mot de passe, désactivation)."""
        with self._lock:
            doomed = [d for d, s in self._by_digest.items() if s.user_id == user_id]
            for digest in doomed:
                del self._by_digest[digest]
        return len(doomed)

    def refresh_user(self, user: dict) -> None:
        """Répercute un changement de rôle/nom sur les sessions ouvertes ; un compte désactivé est déconnecté."""
        if not user["active"]:
            self.revoke_user(user["id"])
            return
        with self._lock:
            for session in self._by_digest.values():
                if session.user_id == user["id"]:
                    session.role, session.display_name = user["role"], user["display_name"]

    def mark_password_changed(self, user_id: int) -> None:
        with self._lock:
            for session in self._by_digest.values():
                if session.user_id == user_id:
                    session.must_change_password = False

    def list_public(self) -> list[dict]:
        now = time.time()
        with self._lock:
            return [s.public() for s in self._by_digest.values() if not self._expired(s, now)]


sessions = SessionStore()


# ============================================================================
# Limiteur d'échecs de connexion par IP (en plus du verrouillage par compte)
# ============================================================================
class RateLimiter:
    """Max ``limit`` échecs par IP sur ``window`` secondes."""

    def __init__(self, limit: int = 10, window: int = 900):
        self.limit, self.window = limit, window
        self._fails: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, ip):
        queue, now = self._fails[ip], time.time()
        while queue and queue[0] < now - self.window:
            queue.popleft()
        return queue

    def blocked(self, ip) -> bool:
        with self._lock:
            return len(self._prune(ip)) >= self.limit

    def fail(self, ip):
        with self._lock:
            self._prune(ip).append(time.time())

    def reset(self):
        with self._lock:
            self._fails.clear()


limiter = RateLimiter(limit=settings.login_max_failures_per_ip)


# ============================================================================
# Cookie
# ============================================================================
def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(COOKIE_NAME, token, max_age=settings.session_max_hours * 3600, httponly=True,
                        samesite="strict", secure=settings.cookie_secure, path="/")


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


# ============================================================================
# Dépendances FastAPI
# ============================================================================
def current_session(request: Request) -> Session | None:
    """Session de la requête, ou None pour un visiteur."""
    return sessions.get(request.cookies.get(COOKIE_NAME))


def require_role(minimum_role: str, *, allow_password_change: bool = False):
    """401 sans session, 403 si le rôle est insuffisant, 403 ``password_change_required`` tant que le mot de passe initial n'a pas été changé."""
    needed = ROLE_LEVEL[minimum_role]

    def dependency(request: Request) -> Session:
        session = current_session(request)
        if session is None:
            raise HTTPException(401, "Authentification requise")
        if session.must_change_password and not allow_password_change:
            raise HTTPException(403, "password_change_required")
        if ROLE_LEVEL.get(session.role, 0) < needed:
            raise HTTPException(403, "Droits insuffisants")
        return session

    return Depends(dependency)


def optional_session():
    """Dépendance qui retourne la session ou None (le visiteur est autorisé)."""
    return Depends(current_session)


def actor_name(session: Session | None) -> str:
    return session.username if session else "visiteur"


# ============================================================================
# Contrôle d'origine (CSRF)
# ============================================================================
def origin_allowed(request: Request) -> bool:
    """Une requête qui modifie l'état doit venir de notre hôte (ou d'une origine déclarée dans CORS_ORIGINS).

    Pas d'en-tête Origin (curl, tests, anciens clients) : accepté — le cookie SameSite=Strict protège déjà ce cas.
    """
    if request.method not in UNSAFE_METHODS:
        return True
    origin = request.headers.get("origin")
    if origin is None:
        return True
    if origin in settings.cors_origins:
        return True
    return urlparse(origin).netloc == request.headers.get("host", "")
