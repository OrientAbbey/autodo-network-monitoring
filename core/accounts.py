"""COMPTES LOCAUX NOMINATIFS — création, authentification, verrouillage, changement de mot de passe.

Rôles : ``supervision`` et ``admin`` (le « visiteur » n'a pas de compte). Un admin possède tous les droits d'un superviseur.

Règles de sécurité :
* mot de passe haché avec ``hashlib.scrypt`` (bibliothèque standard, sel aléatoire), format ``scrypt$N$r$p$sel$hash`` ;
* politique : au moins 10 caractères, avec majuscule, minuscule et chiffre ;
* 5 échecs consécutifs ⇒ compte verrouillé 15 minutes ;
* un seul message d'erreur (« identifiants invalides ou compte verrouillé ») et un hachage factice quand l'identifiant
  n'existe pas : on ne peut ni deviner les comptes existants ni mesurer la différence de temps de réponse ;
* le dernier administrateur actif ne peut être ni désactivé ni rétrogradé.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta

from sqlalchemy import text

from .config import fmt, now_local, settings
from .db import fetch_all, fetch_one, local_conn, local_tx
from .logger import get_logger

logger = get_logger(__name__)

ROLE_SUPERVISION = "supervision"
ROLE_ADMIN = "admin"
ROLES = (ROLE_SUPERVISION, ROLE_ADMIN)
ROLE_LEVEL = {ROLE_SUPERVISION: 1, ROLE_ADMIN: 2}          # un rôle de niveau supérieur inclut les droits des niveaux inférieurs

MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 15
MIN_PASSWORD_LENGTH = 10
USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
INVALID_CREDENTIALS = "identifiants invalides ou compte verrouillé"

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1


class AccountError(Exception):
    """Erreur métier présentable à l'utilisateur (message en français)."""


# ============================================================================
# Mots de passe
# ============================================================================
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Comparaison en temps constant ; un hachage illisible ne valide jamais."""
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored_hash.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(digest, bytes.fromhex(digest_hex))
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))      # sert à égaliser le temps de réponse quand l'identifiant est inconnu


def check_password_policy(password: str) -> None:
    """Lève ``AccountError`` si le mot de passe est trop faible."""
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"au moins {MIN_PASSWORD_LENGTH} caractères")
    if not re.search(r"[a-z]", password):
        problems.append("une minuscule")
    if not re.search(r"[A-Z]", password):
        problems.append("une majuscule")
    if not re.search(r"\d", password):
        problems.append("un chiffre")
    if problems:
        raise AccountError("Mot de passe trop faible : il faut " + ", ".join(problems) + ".")


# ============================================================================
# Représentation publique d'un compte (jamais de hachage vers l'API)
# ============================================================================
def _now() -> datetime:
    return now_local().replace(tzinfo=None)


def _public(row: dict) -> dict:
    """Convertit une ligne SQL en dict sérialisable : booléens Python (MySQL renvoie des entiers) et dates en texte."""
    out = {key: row[key] for key in ("id", "username", "display_name", "role")}
    out["active"] = bool(row["active"])
    out["must_change_password"] = bool(row["must_change_password"])
    out["failed_logins"] = int(row["failed_logins"] or 0)
    for key in ("created_at", "last_login_at", "locked_until"):
        out[key] = fmt(row[key]) if isinstance(row.get(key), datetime) else None
    return out


def _normalize_username(username: str) -> str:
    cleaned = (username or "").strip().lower()
    if not USERNAME_PATTERN.match(cleaned):
        raise AccountError("Identifiant invalide : 3 à 64 caractères (lettres, chiffres, point, tiret, souligné).")
    return cleaned


# ============================================================================
# Lecture
# ============================================================================
def get_user(user_id: int) -> dict | None:
    with local_conn() as conn:
        row = fetch_one(conn, "SELECT * FROM users WHERE id = :id", id=user_id)
    return _public(row) if row else None


def list_users() -> list[dict]:
    with local_conn() as conn:
        return [_public(r) for r in fetch_all(conn, "SELECT * FROM users ORDER BY username")]


def _active_admin_count(conn) -> int:
    return int(conn.execute(text("SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1")).scalar())


# ============================================================================
# Création et modification
# ============================================================================
def create_user(username: str, display_name: str, role: str, password: str, *, must_change_password: bool = True) -> dict:
    if role not in ROLES:
        raise AccountError("Rôle inconnu.")
    name = _normalize_username(username)
    check_password_policy(password)
    shown_name = (display_name or "").strip()[:100] or name
    try:
        with local_tx() as conn:
            conn.execute(text(
                "INSERT INTO users (username, display_name, role, password_hash, active, must_change_password, created_at) "
                "VALUES (:u, :d, :r, :h, 1, :m, :t)"),
                {"u": name, "d": shown_name, "r": role, "h": hash_password(password),
                 "m": int(must_change_password), "t": fmt(_now())})
            row = fetch_one(conn, "SELECT * FROM users WHERE username = :u", u=name)
    except Exception as error:
        if "Duplicate" in str(error) or "1062" in str(error):
            raise AccountError("Cet identifiant existe déjà.") from None
        raise
    return _public(row)


def update_user(user_id: int, *, role: str | None = None, active: bool | None = None, display_name: str | None = None) -> dict:
    """Modifie rôle / état / nom. Refuse de retirer le dernier administrateur actif."""
    if role is not None and role not in ROLES:
        raise AccountError("Rôle inconnu.")
    with local_tx() as conn:
        current = fetch_one(conn, "SELECT * FROM users WHERE id = :id FOR UPDATE", id=user_id)
        if not current:
            raise AccountError("Compte introuvable.")
        new_role = role if role is not None else current["role"]
        new_active = bool(active) if active is not None else bool(current["active"])
        was_active_admin = current["role"] == ROLE_ADMIN and bool(current["active"])
        will_be_active_admin = new_role == ROLE_ADMIN and new_active
        if was_active_admin and not will_be_active_admin and _active_admin_count(conn) <= 1:
            raise AccountError("Impossible : il doit rester au moins un administrateur actif.")
        conn.execute(text("UPDATE users SET role = :r, active = :a, display_name = :d WHERE id = :id"),
                     {"r": new_role, "a": int(new_active), "d": (display_name or current["display_name"])[:100], "id": user_id})
        row = fetch_one(conn, "SELECT * FROM users WHERE id = :id", id=user_id)
    return _public(row)


def reset_password(user_id: int, new_password: str) -> None:
    """Réinitialisation par un administrateur : le titulaire devra choisir un nouveau mot de passe à la prochaine connexion."""
    check_password_policy(new_password)
    with local_tx() as conn:
        result = conn.execute(text("UPDATE users SET password_hash = :h, must_change_password = 1, failed_logins = 0, "
                                   "locked_until = NULL WHERE id = :id"), {"h": hash_password(new_password), "id": user_id})
        if result.rowcount == 0:
            raise AccountError("Compte introuvable.")


def change_own_password(user_id: int, current_password: str, new_password: str) -> None:
    with local_conn() as conn:
        row = fetch_one(conn, "SELECT password_hash FROM users WHERE id = :id", id=user_id)
    if not row or not verify_password(current_password or "", row["password_hash"]):
        raise AccountError("Mot de passe actuel incorrect.")
    if current_password == new_password:
        raise AccountError("Le nouveau mot de passe doit être différent de l'ancien.")
    check_password_policy(new_password)
    with local_tx() as conn:
        conn.execute(text("UPDATE users SET password_hash = :h, must_change_password = 0 WHERE id = :id"),
                     {"h": hash_password(new_password), "id": user_id})


# ============================================================================
# Authentification
# ============================================================================
def authenticate(username: str, password: str) -> dict:
    """Retourne le compte si les identifiants sont bons ; lève ``AccountError(INVALID_CREDENTIALS)`` sinon."""
    name = (username or "").strip().lower()
    with local_conn() as conn:
        row = fetch_one(conn, "SELECT * FROM users WHERE username = :u", u=name) if name else None
    password_ok = verify_password(password or "", row["password_hash"] if row else _DUMMY_HASH)
    if row is None:
        raise AccountError(INVALID_CREDENTIALS)
    now = _now()
    locked = isinstance(row["locked_until"], datetime) and row["locked_until"] > now
    if locked or not row["active"]:
        raise AccountError(INVALID_CREDENTIALS)
    if not password_ok:
        _register_failure(row["id"], int(row["failed_logins"] or 0) + 1, now)
        raise AccountError(INVALID_CREDENTIALS)
    with local_tx() as conn:
        conn.execute(text("UPDATE users SET failed_logins = 0, locked_until = NULL, last_login_at = :t WHERE id = :id"),
                     {"t": fmt(now), "id": row["id"]})
    return _public({**row, "failed_logins": 0, "locked_until": None, "last_login_at": now})


def _register_failure(user_id: int, failures: int, now: datetime) -> None:
    """Compte l'échec ; au 5ᵉ, verrouille le compte et remet le compteur à zéro."""
    with local_tx() as conn:
        if failures >= MAX_FAILED_LOGINS:
            until = now + timedelta(minutes=LOCK_MINUTES)
            conn.execute(text("UPDATE users SET failed_logins = 0, locked_until = :u WHERE id = :id"),
                         {"u": fmt(until), "id": user_id})
            logger.warning("Compte %s verrouillé jusqu'à %s", user_id, fmt(until))
        else:
            conn.execute(text("UPDATE users SET failed_logins = :n WHERE id = :id"), {"n": failures, "id": user_id})


# ============================================================================
# Premier administrateur
# ============================================================================
def ensure_initial_admin() -> bool:
    """Crée l'administrateur initial (``ADMIN_USERNAME`` / ``ADMIN_PASSWORD``) si AUCUN compte n'existe encore.

    Le changement de mot de passe est imposé à la première connexion. Retourne True si un compte a été créé.
    """
    with local_conn() as conn:
        existing = int(conn.execute(text("SELECT COUNT(*) FROM users")).scalar())
    if existing:
        return False
    username, password = settings.admin_username, settings.admin_password
    if not username or not password:
        logger.warning("Aucun compte et ADMIN_USERNAME / ADMIN_PASSWORD non définis : personne ne pourra se connecter")
        return False
    try:
        create_user(username, username, ROLE_ADMIN, password, must_change_password=True)
    except AccountError as error:
        logger.error("Administrateur initial non créé : %s", error)
        return False
    logger.info("Administrateur initial « %s » créé (changement de mot de passe obligatoire)", username)
    return True
