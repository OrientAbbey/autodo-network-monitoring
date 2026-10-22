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

from core import accounts, analytics, anomaly, anomaly_ml, auth, constants, governance, history, monitoring, shadow, simulation, tickets
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


# ============================================================================
# ADMIN — historique local (D2)
# ============================================================================
DB_KEY_PATTERN = "^[A-Za-z0-9_-]{1,50}$"


class BackfillBody(BaseModel):
    days: int = Field(30, ge=1, le=90)


@admin_router.get("/api/admin/history")
async def history_stats(_s=admin_only):
    return _json(await _run(history.stats))


@admin_router.post("/api/admin/history/backfill", status_code=202)
def history_backfill(body: BackfillBody, request: Request, session=admin_only):
    """Importe l'historique en arrière-plan (suivi via GET /api/admin/history)."""
    if not history.start_backfill(body.days):
        raise HTTPException(409, "Un import est déjà en cours")
    governance.audit(session.username, get_real_ip(request), "history_backfill", "", {"days": body.days})
    return _json({"started": True, "days": body.days}, 202)


# ============================================================================
# ADMIN — détecteurs « futurs » (mode ombre)
# ============================================================================
@admin_router.get("/api/admin/detectors")
async def list_detectors(_s=admin_only):
    def build():
        states, counts = shadow.get_states(), shadow.verdict_counts(24)
        items = []
        for key, info in shadow.DETECTORS.items():
            item = {"key": key, "code": info.code, "title": info.title, "description": info.description,
                    "needs_training": info.needs_training, "shadow": info.shadow, **states[key], "verdicts_24h": counts.get(key, 0)}
            if info.needs_training:
                item["available"] = anomaly_ml.ml_available()
            if key == "isolation":
                item["models"] = [b for b in history.stats()["bases"] if anomaly_ml.has_model(f"iforest_{b}")]
            elif key == "quality":
                item["model_trained"] = anomaly_ml.has_model("quality")
            items.append(item)
        return {"detectors": items, "ml_available": anomaly_ml.ml_available(), "shadow_enabled": bool(load_config().get("shadow_detectors"))}
    return _json(await _run(build))


class DetectorToggle(BaseModel):
    enabled: bool


@admin_router.post("/api/admin/detectors/{key}")
def toggle_detector(key: str, body: DetectorToggle, request: Request, session=admin_only):
    if key not in shadow.DETECTORS:
        raise HTTPException(404, "Détecteur inconnu")
    shadow.set_enabled(key, body.enabled)
    governance.audit(session.username, get_real_ip(request), "detector_toggle", key, {"enabled": body.enabled})
    return _json({"key": key, "enabled": body.enabled})


class TrainBody(BaseModel):
    db_key: str | None = Field(None, pattern=DB_KEY_PATTERN)
    days: int = Field(anomaly_ml.ISOLATION_TRAINING_DAYS, ge=7, le=120)


def _train(key: str, body: TrainBody) -> dict:
    anomaly_ml.require_ml()                       # « bibliothèque absente » (501) prime sur « pas assez de données » (422)
    if key == "quality":
        metrics = anomaly_ml.train_quality()
        shadow.record_training(key, metrics)
        return metrics
    bases = [body.db_key] if body.db_key else list(history.stats()["bases"])
    trained, skipped = [], []
    for db_key in bases:
        try:
            trained.append(anomaly_ml.train_isolation(db_key, body.days))
        except anomaly_ml.NotEnoughData as error:
            skipped.append({"db_key": db_key, "reason": str(error)})
    if not trained:
        raise anomaly_ml.NotEnoughData("Aucune base n'a assez d'historique : " + "; ".join(s["reason"] for s in skipped[:3]) if skipped
                                       else "Aucun historique : importez-le d'abord.")
    summary = {"bases": len(trained), "rows": sum(t["rows"] for t in trained), "skipped": skipped}
    shadow.record_training(key, summary)
    return {**summary, "trained": trained}


@admin_router.post("/api/admin/detectors/{key}/train")
async def train_detector(key: str, request: Request, body: TrainBody = TrainBody(), session=admin_only):
    if key not in shadow.DETECTORS or not shadow.DETECTORS[key].needs_training:
        raise HTTPException(400, "Ce détecteur n'a pas d'entraînement")
    try:
        result = await _run(_train, key, body)
    except anomaly_ml.MlUnavailable as error:
        raise HTTPException(501, str(error)) from None
    except anomaly_ml.NotEnoughData as error:
        raise HTTPException(422, str(error)) from None
    governance.audit(session.username, get_real_ip(request), "detector_train", key, {k: v for k, v in result.items() if k != "trained"})
    return _json(result)


@admin_router.get("/api/admin/detectors/report")
async def detectors_report(days: int = Query(7, ge=1, le=90), _s=admin_only):
    return _json(await _run(shadow.shadow_report, days))


# ============================================================================
# ADMIN — analyses à la demande (D3 prévision / capacité, D5 ruptures)
# ============================================================================
@admin_router.get("/api/admin/analysis/forecast")
async def analysis_forecast(db_key: str = Query(pattern=DB_KEY_PATTERN), days: int = Query(14, ge=2, le=60), _s=admin_only):
    """Courbe attendue pour les 24 h suivant le dernier relevé (bande ≈ 95 %)."""
    grid = await _run(history.traffic_grid, db_key, days)
    if grid is None:
        raise HTTPException(404, "Aucun historique pour cette base")
    latest, values = grid
    try:
        forecast = await _run(anomaly.holt_winters_forecast, values)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    first = latest + anomaly.STEP
    return _json({"db_key": db_key, "history_days": days, "last_point": history.fmt(latest), "forecast_start": history.fmt(first),
                  "step_minutes": history.STEP_MINUTES, **forecast})


@admin_router.get("/api/admin/analysis/capacity")
async def analysis_capacity(db_key: str = Query(pattern=DB_KEY_PATTERN), capacity_gbps: float = Query(gt=0, le=10000),
                            days: int = Query(60, ge=7, le=180), _s=admin_only):
    daily = await _run(history.daily_stats, db_key, days)
    return _json({"db_key": db_key, **anomaly.capacity_projection([d["peak"] for d in daily], capacity_gbps)})


@admin_router.get("/api/admin/analysis/level-shifts")
async def analysis_level_shifts(db_key: str = Query(pattern=DB_KEY_PATTERN), days: int = Query(90, ge=20, le=400), _s=admin_only):
    daily = await _run(history.daily_stats, db_key, days)
    shifts = anomaly.level_shifts([(d["day"], d["mean"]) for d in daily])
    return _json({"db_key": db_key, "days_of_data": len(daily),
                  "shifts": [{**shift, "explanation": anomaly.shift_explanation(shift)} for shift in shifts]})


# ============================================================================
# SUPERVISION — simulation « et si ? » avant d'enregistrer une configuration
# ============================================================================
class SimulationBody(BaseModel):
    config: dict = Field(default_factory=dict)
    overrides: dict[str, dict[str, float | int]] | None = None
    days: int = Field(7, ge=1, le=simulation.MAX_DAYS)


@admin_router.post("/api/supervision/simulation")
async def run_simulation(body: SimulationBody, _s=supervision):
    """Rejoue la configuration candidate sur l'historique local. N'enregistre RIEN."""
    unknown = [k for k in body.config if k not in ConfigParams.model_fields]
    if unknown:
        raise HTTPException(422, f"Paramètre(s) inconnu(s) : {unknown}")
    try:
        result = await _run(simulation.simulate, body.config, body.overrides, body.days)
    except (ValidationError, ValueError) as error:
        message = _validation_message(error) if isinstance(error, ValidationError) else str(error)
        raise HTTPException(422, message) from None
    return _json(result)
