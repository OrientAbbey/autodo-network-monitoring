"""ROUTES SUPERVISION ET ADMINISTRATION.

* ``/api/supervision/*`` (rôle supervision, un admin y a aussi accès) : paramètres globaux, seuils par base, maintenance,
  historique des versions de configuration, tickets en attente de validation.
* ``/api/admin/*`` (rôle admin) : comptes, sessions, audit, usage, santé du système.

Chaque action sensible est inscrite dans le journal d'audit. Les accès base de données passent par ``asyncio.to_thread``
(jamais dans la boucle d'événements).
"""
import asyncio
from functools import partial

import orjson
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, ValidationError

from core import accounts, analytics, auth, constants, governance, monitoring, tickets
from core.auth import get_real_ip
from core.config import OVERRIDABLE_KEYS, ConfigParams, load_config, load_overrides, now_local

admin_router = APIRouter()
supervision = auth.require_role("supervision")
admin_only = auth.require_role("admin")


def _json(data, status_code: int = 200) -> Response:
    return Response(orjson.dumps(data, default=str), status_code=status_code, media_type="application/json")


async def _run(function, *args, **kwargs):
    return await asyncio.to_thread(partial(function, *args, **kwargs))


async def _publish(request: Request, event: dict) -> None:
    """Prévient les navigateurs connectés (SSE). Sans effet si le flux temps réel n'est pas démarré."""
    await monitoring.sse_publish({**event, "at": now_local().isoformat(timespec="seconds")}, request.app)


def _validation_message(error: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in error.errors())


# ============================================================================
# SUPERVISION — paramètres globaux et seuils par base
# ============================================================================
def _field_description(name: str, info) -> dict:
    kind = "bool" if info.annotation is bool else ("int" if info.annotation is int else "float")
    bounds = {attr: getattr(meta, attr) for meta in info.metadata for attr in ("ge", "gt", "le", "lt") if hasattr(meta, attr)}
    return {"name": name, "type": kind, "default": info.default, **bounds, "overridable": name in OVERRIDABLE_KEYS}


@admin_router.get("/api/supervision/config-schema")
def config_schema(_s=supervision):
    fields = [_field_description(n, i) for n, i in ConfigParams.model_fields.items()]
    databases = [{"key": key, "site": cfg.get("site", ""), "label": constants.LOCATION_MAPPING.get(key, key)}
                 for key, cfg in constants.DB_CONFIGS.items()]
    return _json({"fields": fields, "overridable": list(OVERRIDABLE_KEYS), "databases": databases,
                  "config": load_config(), "overrides": load_overrides()})


@admin_router.post("/api/supervision/config")
async def update_config(new_config: ConfigParams, request: Request, session=supervision):
    """Seuls les champs envoyés sont modifiés ; une version et une ligne d'audit (avec les écarts) sont créées."""
    updates = new_config.model_dump(exclude_unset=True)
    try:
        result = await _run(governance.save_global_config, session.username, get_real_ip(request), updates)
    except ValidationError as error:
        raise HTTPException(422, _validation_message(error)) from None
    if result["changes"]:
        await _publish(request, {"type": "config_updated"})
    return _json({"message": "Configuration mise à jour.", **result})


class OverridesBody(BaseModel):
    overrides: dict[str, dict[str, float | int]] = Field(default_factory=dict)


@admin_router.put("/api/supervision/overrides")
async def update_overrides(body: OverridesBody, request: Request, session=supervision):
    unknown = [key for key in body.overrides if key not in constants.DB_CONFIGS]
    if unknown:
        raise HTTPException(422, f"Base(s) inconnue(s) : {unknown}")
    try:
        result = await _run(governance.save_overrides, session.username, get_real_ip(request), body.overrides)
    except (ValidationError, ValueError) as error:
        message = _validation_message(error) if isinstance(error, ValidationError) else str(error)
        raise HTTPException(422, message) from None
    if result["changes"]:
        await _publish(request, {"type": "config_updated"})
    return _json({"overrides": load_overrides(), **result})


# ============================================================================
# SUPERVISION — maintenance / silences
# ============================================================================
class MaintenanceBody(BaseModel):
    db_key: str = Field(max_length=50)
    starts_at: str | None = None
    ends_at: str
    reason: str = Field("", max_length=200)


@admin_router.get("/api/supervision/maintenance")
def list_maintenance(_s=supervision):
    return _json({"windows": governance.list_windows()})


@admin_router.post("/api/supervision/maintenance")
def add_maintenance(body: MaintenanceBody, request: Request, session=supervision):
    if body.db_key != governance.ALL_DATABASES and body.db_key not in constants.DB_CONFIGS:
        raise HTTPException(422, "Base inconnue")
    try:
        window_id = governance.add_window(body.db_key, body.starts_at or now_local().replace(tzinfo=None), body.ends_at,
                                          body.reason, session.username)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    governance.audit(session.username, get_real_ip(request), "maintenance_add", body.db_key,
                     {"ends_at": body.ends_at, "reason": body.reason})
    return _json({"id": window_id})


@admin_router.delete("/api/supervision/maintenance/{window_id}")
def delete_maintenance(window_id: int, request: Request, session=supervision):
    if not governance.delete_window(window_id):
        raise HTTPException(404, "Fenêtre introuvable")
    governance.audit(session.username, get_real_ip(request), "maintenance_delete", str(window_id))
    return _json({"ok": True})


# ============================================================================
# SUPERVISION — versions de configuration
# ============================================================================
@admin_router.get("/api/supervision/config-versions")
def config_versions(_s=supervision):
    return _json({"versions": governance.list_config_versions()})


@admin_router.get("/api/supervision/config-versions/{version_id}")
def config_version(version_id: int, _s=supervision):
    version = governance.get_config_version(version_id)
    if version is None:
        raise HTTPException(404, "Version introuvable")
    return _json(version)


@admin_router.post("/api/supervision/config-versions/{version_id}/restore")
async def restore_config_version(version_id: int, request: Request, session=supervision):
    try:
        result = await _run(governance.restore_config_version, session.username, get_real_ip(request), version_id)
    except LookupError:
        raise HTTPException(404, "Version introuvable") from None
    await _publish(request, {"type": "config_updated"})
    return _json(result)


@admin_router.get("/api/supervision/tickets-to-validate")
def tickets_to_validate(_s=supervision):
    return _json({"tickets": tickets.pending_validation()})


# ============================================================================
# ADMIN — comptes
# ============================================================================
class NewUserBody(BaseModel):
    username: str = Field(max_length=64)
    display_name: str = Field("", max_length=100)
    role: str
    password: str = Field(max_length=256)


class UserPatchBody(BaseModel):
    role: str | None = None
    active: bool | None = None
    display_name: str | None = Field(None, max_length=100)


class ResetPasswordBody(BaseModel):
    new_password: str = Field(max_length=256)


@admin_router.get("/api/admin/users")
def list_users(_s=admin_only):
    return _json({"users": accounts.list_users()})


@admin_router.post("/api/admin/users")
def create_user(body: NewUserBody, request: Request, session=admin_only):
    try:
        user = accounts.create_user(body.username, body.display_name, body.role, body.password)
    except accounts.AccountError as error:
        raise HTTPException(400, str(error)) from None
    governance.audit(session.username, get_real_ip(request), "user_create", user["username"], {"role": user["role"]})
    return _json({"user": user}, 201)


@admin_router.patch("/api/admin/users/{user_id}")
def patch_user(user_id: int, body: UserPatchBody, request: Request, session=admin_only):
    try:
        user = accounts.update_user(user_id, role=body.role, active=body.active, display_name=body.display_name)
    except accounts.AccountError as error:
        raise HTTPException(400, str(error)) from None
    auth.sessions.refresh_user(user)
    governance.audit(session.username, get_real_ip(request), "user_update", user["username"],
                     {"role": user["role"], "active": user["active"]})
    return _json({"user": user})


@admin_router.post("/api/admin/users/{user_id}/reset-password")
def reset_user_password(user_id: int, body: ResetPasswordBody, request: Request, session=admin_only):
    target = accounts.get_user(user_id)
    if target is None:
        raise HTTPException(404, "Compte introuvable")
    try:
        accounts.reset_password(user_id, body.new_password)
    except accounts.AccountError as error:
        raise HTTPException(400, str(error)) from None
    closed = auth.sessions.revoke_user(user_id)            # l'ancien mot de passe ne doit plus ouvrir de session
    governance.audit(session.username, get_real_ip(request), "password_reset", target["username"], {"sessions_closed": closed})
    return _json({"ok": True, "sessions_closed": closed})


# ============================================================================
# ADMIN — sessions, audit, usage, santé
# ============================================================================
@admin_router.get("/api/admin/sessions")
def list_sessions(request: Request, session=admin_only):
    sessions = [{**s, "current": s["session_id"] == session.session_id} for s in auth.sessions.list_public()]
    return _json({"sessions": sessions})


@admin_router.delete("/api/admin/sessions/{session_id}")
def revoke_session(session_id: str, request: Request, session=admin_only):
    if not auth.sessions.revoke_session_id(session_id):
        raise HTTPException(404, "Session introuvable")
    governance.audit(session.username, get_real_ip(request), "session_revoke", session_id)
    return _json({"ok": True})


@admin_router.get("/api/admin/audit")
def audit_list(action: str | None = Query(None, max_length=64), actor: str | None = Query(None, max_length=64),
               days: int = Query(30, ge=1, le=400), _s=admin_only):
    return _json({"entries": governance.list_audit(action, actor, days)})


@admin_router.get("/api/admin/audit.csv")
def audit_export(days: int = Query(30, ge=1, le=400), _s=admin_only):
    csv_text = governance.audit_csv(governance.list_audit(days=days, limit=5000))
    return Response(csv_text, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="autodo-audit.csv"'})


@admin_router.get("/api/admin/usage")
def usage(request: Request, period: str = Query("7days", pattern="^(today|7days|1month|3months)$"), _s=admin_only):
    access = analytics.access_logs(period)
    subscribers = getattr(request.app.state, "sse_subscribers", set())
    return _json({"period": period, "viewers_now": len(subscribers), "access_logs": access["access_logs"],
                  "access_stats": access["access_stats"], "alerts_stats": tickets.alerts_stats(period),
                  "peaks": analytics.viewer_peaks(period), "generated_at": now_local().isoformat()})


@admin_router.get("/api/admin/health")
def health(_s=admin_only):
    scheduler = constants.scheduler
    jobs = [{"id": job.id, "next_run": job.next_run_time.strftime("%Y-%m-%d %H:%M:%S") if job.next_run_time else None}
            for job in (scheduler.get_jobs() if scheduler else [])]
    sources = [{"key": key, "failing": key in monitoring.state.source_failures,
                "failed_cycles": monitoring.state.source_failures.get(key, 0)} for key in constants.DB_CONFIGS]
    return _json({"monitoring": monitoring.status(), "sources": sources, "orange_queue": tickets.orange_queue_counts(),
                  "tickets": tickets.counts(), "jobs": jobs, "generated_at": now_local().isoformat()})
