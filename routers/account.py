"""ROUTES COMPTE — connexion, déconnexion, identité de la session, changement de mot de passe.

Le visiteur n'a pas de compte : ``GET /api/me`` répond ``{"user": null}`` et il peut consulter le tableau de bord.
"""
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from core import accounts, auth, governance
from core.auth import get_real_ip

account_router = APIRouter()


class LoginBody(BaseModel):
    username: str = Field("", max_length=64)
    password: str = Field("", max_length=256)


class PasswordBody(BaseModel):
    current_password: str = Field("", max_length=256)
    new_password: str = Field("", max_length=256)


def session_view(session: auth.Session) -> dict:
    return {"username": session.username, "display_name": session.display_name, "role": session.role,
            "must_change_password": session.must_change_password, "session_id": session.session_id}


@account_router.post("/api/login")
def login(body: LoginBody, request: Request, response: Response):
    ip = get_real_ip(request)
    if auth.limiter.blocked(ip):
        raise HTTPException(429, "Trop de tentatives, réessayez plus tard", headers={"Retry-After": "900"})
    try:
        user = accounts.authenticate(body.username, body.password)
    except accounts.AccountError:
        auth.limiter.fail(ip)
        governance.audit(body.username.strip().lower()[:64] or "?", ip, "login_failed")
        raise HTTPException(401, accounts.INVALID_CREDENTIALS) from None
    token = auth.sessions.create(user, ip, request.headers.get("user-agent", ""))
    auth.set_session_cookie(response, token)
    governance.audit(user["username"], ip, "login")
    session = auth.sessions.get(token)
    return {"user": session_view(session)}


@account_router.post("/api/logout")
def logout(request: Request, response: Response):
    session = auth.current_session(request)
    auth.sessions.revoke_token(request.cookies.get(auth.COOKIE_NAME))
    auth.clear_session_cookie(response)
    if session:
        governance.audit(session.username, get_real_ip(request), "logout")
    return {"ok": True}


@account_router.get("/api/me")
def me(request: Request):
    session = auth.current_session(request)
    return {"user": session_view(session) if session else None}


@account_router.post("/api/me/password")
def change_password(body: PasswordBody, request: Request,
                    session: auth.Session = auth.require_role("supervision", allow_password_change=True)):
    try:
        accounts.change_own_password(session.user_id, body.current_password, body.new_password)
    except accounts.AccountError as error:
        raise HTTPException(400, str(error)) from None
    auth.sessions.mark_password_changed(session.user_id)
    governance.audit(session.username, get_real_ip(request), "password_change")
    return {"ok": True}
