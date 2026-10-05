"""
DASHBOARD ROUTER - Real-time analytics and network monitoring dashboard
Handles data aggregation, metrics calculation, and SSE streaming
"""

import asyncio
import json
import os
import secrets
import time
from contextlib import suppress
from datetime import datetime

import orjson
from fastapi import (APIRouter, Depends, HTTPException, Request, Response,
                     status)
from fastapi.responses import HTMLResponse, ORJSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import core.constants as constants
from core.background_task import precalculate_dashboard_data
from core.logger import get_logger
from core.utils import (ConfigParams, _sse_publish, get_access_logs_from_db,
                        get_alerts_stats_from_db, get_cameroon_time,
                        get_config_changes_from_db, get_real_ip, load_config,
                        log_criteria_access, track_user)

logger = get_logger(__name__)

dashboard_router = APIRouter()

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Simple bearer auth for sensitive endpoints"""
    if credentials.credentials != os.getenv("EXNET_AUTH_SECRET"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return True


# ============================================================================
# ALERT FETCHING
# ============================================================================
def fetch_alerts_from_tickets_tb(request_filters: dict = None) -> dict:
    """Fetch alerts from tickets_tb for display"""
    try:
        request_filters = request_filters or {}
        from core.database_manager import get_local_db_connection

        connexion = get_local_db_connection()
        if not connexion:
            logger.error("Database connection pool unavailable for logs_db")
            return {"traffic": [], "pertes": []}

        where_clauses, params = [], []

        # --- STATUS ---
        status = request_filters.get("status")
        if status in ("open", "closed"):
            where_clauses.append("status = %s")
            params.append(status)

        # --- TYPE ---
        alert_types = request_filters.get("type")
        if alert_types and alert_types != "all":
            types = [
                t
                for t in alert_types.split(",")
                if t in ("traffic", "pertes", "db_down")
            ]
            if types:
                placeholders = ", ".join(["%s"] * len(types))
                where_clauses.append(f"type IN ({placeholders})")
                params.extend(types)

        # --- PERIODE ---
        period = request_filters.get("period")
        if period == "today":
            where_clauses.append("DATE(timestamp) = CURDATE()")
        elif period == "week":
            where_clauses.append("YEARWEEK(timestamp, 1) = YEARWEEK(CURDATE(), 1)")
        elif period == "month":
            where_clauses.append(
                "YEAR(timestamp) = YEAR(CURDATE()) AND MONTH(timestamp) = MONTH(CURDATE())"
            )

        where_sql = " AND ".join(where_clauses)
        query = "SELECT * FROM tickets_tb"
        if where_sql:
            query += f" WHERE {where_sql}"
        # Add order by and execute query
        query += " ORDER BY timestamp DESC"

        with connexion:
            with connexion.cursor(dictionary=True) as cursor:
                cursor.execute(query, params)
                alerts_raw = cursor.fetchall()

        # -------- FORMATAGE DES DONNÉES -------- #
        # Mapper database_name vers le format d'affichage

        formatted_alerts = {"traffic": [], "pertes": []}

        for alert in alerts_raw:
            # Convertir last_values (JSON) en liste de valeurs simples
            try:
                valeurs = [v["value"] for v in json.loads(alert["last_values"] or "[]")]
                if len(valeurs) > 1:
                    valeurs = valeurs[1:]
            except Exception:
                valeurs = [alert.get("value")]

            location = constants.LOCATION_MAPPING.get(
                alert["database_name"], alert["database_name"]
            )

            formatted = {
                "id": alert["id"],
                "type": alert["type"].upper(),
                "location": location,
                "title": f"Alerte {alert['type'].upper()} - {location}",
                "message": alert["reason"],
                "valeurs": valeurs,
                "status": alert["status"],
                "date": alert["timestamp"].strftime("%Y-%m-%d %H:%M"),
                "creation_time": alert["timestamp"].strftime("%Y-%m-%d %H:%M"),
                "external_id": alert.get("external_api_ticket_id"),
                "autin_id": alert.get("autin_id"),
            }

            if alert["type"] in ("traffic", "db_down"):
                formatted_alerts["traffic"].append(formatted)
            elif alert["type"] == "pertes":
                formatted_alerts["pertes"].append(formatted)

        logger.debug(
            f"{len(alerts_raw)} alertes récupérées avec filtres {request_filters}"
        )

        return formatted_alerts

    except Exception as e:
        logger.exception("Erreur lors de la récupération des alertes tickets_tb")
        return {"traffic": [], "pertes": []}


def _verify_dm_token(request: Request) -> bool:
    """Verify if user has valid data management session token"""
    token = request.cookies.get("dm_token")
    if not token:
        logger.warning(
            f"Data management access attempt without token from IP: {get_real_ip(request)}"
        )
        return False

    expiration = constants.TOKENS.get(token)
    if not expiration or time.time() > expiration:
        logger.warning(
            f"Data management access attempt with expired/invalid token from IP: {get_real_ip(request)}"
        )
        return False

    return True


# ============================================================================
# ROUTES
# ============================================================================
@dashboard_router.get("/", response_class=HTMLResponse)
async def dashboard_index(request: Request):
    """Serve dashboard HTML"""
    from main import templates

    return templates.TemplateResponse("dashboard.html", {"request": request})


@dashboard_router.post("/api/dashboard_data", response_class=ORJSONResponse)
async def api_dashboard_data(request: Request, response: Response):
    """API pour retourner les données du dashboard depuis le cache avec support des filtres d'alertes"""
    try:
        # Get filter data from request body
        body = await request.body()
        request_filters = orjson.loads(body) if body else {}

        # Track user
        track_user(request, response_headers=response.headers)
        logger.debug(
            f"User tracked successfully for dashboard_data request: {response.headers}"
        )

        # Récupérer les données depuis le cache
        with constants.DASHBOARD_CACHE_LOCK:
            if not constants.DASHBOARD_CACHE:
                # Si le cache est vide, faire un précalcul immédiat
                logger.warning("Cache vide, précalcul immédiat des données dashboard")
                precalculate_dashboard_data()

            # Copier les données du cache pour éviter les modifications
            cached_data = constants.DASHBOARD_CACHE.copy()

        # Récupération des alertes filtrées selon les critères utilisateur
        filtered_alertes = fetch_alerts_from_tickets_tb(request_filters)
        cached_data["alertes"] = filtered_alertes
        logger.debug(f"Alerte summary (filtered): {filtered_alertes}")

        return ORJSONResponse(cached_data, headers=response.headers)

    except Exception as e:
        logger.exception(f"Error in api_dashboard_data: {e}")
        return ORJSONResponse({"error": str(e)})


@dashboard_router.get("/events")
async def sse_events(request: Request):
    """Server-Sent Events stream"""
    from main import app

    queue: asyncio.Queue = asyncio.Queue(maxsize=6)
    subscribers: set = getattr(app.state, "sse_subscribers", set())
    lock: asyncio.Lock = getattr(app.state, "sse_subscribers_lock", asyncio.Lock())

    # Register sous lock
    async with lock:
        subscribers.add(queue)

    async def event_generator():
        try:
            # Message initial
            hello_payload = {
                "type": "hello",
                "at": datetime.now().isoformat(timespec="seconds"),
            }
            yield f"event: hello\ndata: {orjson.dumps(hello_payload).decode()}\n\n"

            while True:
                # Si le serveur est en train de s'arrêter (scheduler annulé), sortir immédiatement
                if (
                    getattr(app.state, "sse_scheduler", None)
                    and app.state.sse_scheduler.cancelled()
                ):
                    logger.debug(
                        "Server shutdown detected in event_generator; breaking loop"
                    )
                    break

                # Si le client s'est déconnecté, sortir immédiatement
                if await request.is_disconnected():
                    break

                try:
                    # Attendre un événement (max 25s), envoyer keep-alive si timeout
                    event = await asyncio.wait_for(queue.get(), timeout=25)
                    # Si event de type shutdown, on sort
                    if isinstance(event, dict) and event.get("type") == "shutdown":
                        break
                    # Format SSE
                    yield f"data: {orjson.dumps(event).decode()}\n\n"
                except asyncio.TimeoutError:
                    # heartbeat pour garder la connexion ouverte (commentaire côté client)
                    yield ": keep-alive\n\n"
        except asyncio.CancelledError:
            logger.debug("Event generator cancelled for client")
            pass
        except Exception as e:
            logger.debug(f"SSE client disconnected: {e}")
        finally:
            # Remove sous lock pour éviter condition de course
            try:
                async with lock:
                    subscribers.discard(queue)
            except Exception:
                # en shutdown il se peut que le lock ait déjà été nettoyé; on ignore alors
                with suppress(Exception):
                    subscribers.discard(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@dashboard_router.get("/api/config")
async def get_config():
    """Get current configuration"""
    try:
        return load_config()
    except Exception as e:
        logger.error(f"Error getting config: {e}")
        return ORJSONResponse({"error": str(e)}, status_code=500)


@dashboard_router.post("/update_config", response_class=ORJSONResponse)
async def update_config(new_config: ConfigParams, request: Request):
    """Update configuration from config.json only"""
    from main import app

    errors = []
    logger.debug(f"🔄 Updating configuration with: {new_config.model_dump()}")

    try:
        local_config_path = constants.CONFIG_FILE

        logger.debug(f"📁 Saving config to: {local_config_path}")

        # Load existing config or create new one
        if local_config_path.exists():
            with open(local_config_path, "r", encoding="utf-8") as f:
                local_config = json.load(f)
                logger.debug(f"📖 Loaded existing config structure")
        else:
            local_config = {"criteria": {}, "alerts": {}, "database_connections": {}}
            logger.debug(f"🆕 Created new config structure")

        # Update criteria in local config
        if "criteria" not in local_config:
            local_config["criteria"] = {}

        criteria_updates = {
            "alert_traffic_threshold_percent": new_config.alert_traffic_threshold_percent,
            "alert_pertes_threshold": new_config.alert_pertes_threshold,
            "traffic_up_threshold_percent": new_config.traffic_up_threshold_percent,
            "traffic_consecutive_drops_count": new_config.traffic_consecutive_drops_count,
            "traffic_consecutive_drops_min_percent": new_config.traffic_consecutive_drops_min_percent,
            "alert_persistence_duration_minutes": new_config.alert_persistence_duration_minutes,
            "pertes_consecutive_count": new_config.pertes_consecutive_count,
            "day_over_day_drop_percent": new_config.day_over_day_drop_percent,
            "traffic_recovery_ratio": new_config.traffic_recovery_ratio,
            "mode7days_recovery_ratio": new_config.mode7days_recovery_ratio,
            "db_recovery_threshold_minutes": new_config.db_recovery_threshold_minutes,
            "alert_tcp_no_response_threshold": new_config.alert_tcp_no_response_threshold,
            "alert_tcp_block_wp_up_threshold": new_config.alert_tcp_block_wp_up_threshold,
            "alert_latence_threshold_ms": new_config.alert_latence_threshold_ms,
        }

        # Update alert section with section enablement flags
        if "alerts" not in local_config:
            local_config["alerts"] = {}

        local_config["alerts"][
            "alerts_enable_international"
        ] = new_config.alerts_enable_international
        local_config["alerts"]["alerts_enable_gi"] = new_config.alerts_enable_gi
        local_config["alerts"][
            "alerts_enable_international_gi"
        ] = new_config.alerts_enable_international_gi
        local_config["alerts"][
            "alerts_enable_roaming"
        ] = new_config.alerts_enable_roaming
        local_config["alerts"]["alerts_enable_tdd"] = new_config.alerts_enable_tdd

        # Update criteria
        for key, value in criteria_updates.items():
            local_config["criteria"][key] = value
            logger.debug(f"🔧 Updated criteria: {key} = {value}")

        # Save with proper error handling
        try:
            with open(local_config_path, "w", encoding="utf-8") as f:
                json.dump(local_config, f, indent=4, ensure_ascii=False)
            logger.debug(f"✅ Successfully saved local config to {local_config_path}")
            logger.debug(f"📋 Updated criteria: {local_config['criteria']}")
        except PermissionError:
            error_msg = f"Permission denied writing to {local_config_path}"
            logger.error(f"❌ {error_msg}")
            errors.append(error_msg)
        except Exception as write_error:
            error_msg = f"Failed to write config file: {write_error}"
            logger.error(f"❌ {error_msg}")
            errors.append(error_msg)

    except Exception as e:
        error_msg = f"Failed to save local configuration: {e}"
        logger.error(f"❌ {error_msg}")
        errors.append(error_msg)

    if errors:
        error_detail = f"Configuration update failed: {'; '.join(errors)}"
        logger.error(f"❌ {error_detail}")
        raise HTTPException(status_code=500, detail=error_detail)

    # Log criteria change
    log_criteria_access(request, action="criteria_change")

    # Notifier les clients SSE
    try:
        asyncio.create_task(
            _sse_publish(
                {
                    "type": "config_updated",
                    "at": get_cameroon_time().isoformat(timespec="seconds"),
                },
                app=app,
            )
        )
        logger.debug(f"📡 Notified SSE clients of config update")
    except Exception as e:
        logger.warning(f"⚠ Failed to notify SSE clients: {e}")

    logger.debug(f"✅ Configuration update completed successfully")
    return {
        "message": "Configuration mise à jour avec succès. Critères principaux synchronisés avec l'API."
    }


@dashboard_router.post("/api/exnet-auth-url")
async def get_exnet_auth_url(request: Request):
    """Generate ExNet authentication URL with embedded credentials (server-side only)"""
    try:
        data = await request.json()
        base_url = data.get("base_url")

        if not base_url:
            raise HTTPException(status_code=400, detail="base_url parameter required")

        username, password = os.getenv("EXNET_USERNAME"), os.getenv("EXNET_PASSWORD")

        # Build the authentication URL with encoded credentials (never exposed to client)
        auth_url = f"{base_url}/exnet/api.py?action=authenticate&login={username}&passwd={password}&cachbuster={int(time.time() * 1000)}"

        return {"auth_url": auth_url}

    except Exception as e:
        logger.error(f"Error generating ExNet auth URL: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate auth URL")


@dashboard_router.api_route("/api/auth", methods=["GET", "POST"])
async def auth_endpoint(request: Request, response: Response = None):
    """Unified authentication endpoint for both token verification (GET) and authentication (POST)"""
    if request.method == "GET":
        # Verify existing token
        token = request.cookies.get("cfg")
        if not token:
            raise HTTPException(status_code=401, detail="No token provided")

        expiration = constants.TOKENS.get(token)
        if not expiration or time.time() > expiration:
            raise HTTPException(status_code=401, detail="Token expired or invalid")

        return {"valid": True}

    elif request.method == "POST":
        # Authenticate with password
        data = await request.json()
        if data.get("password") != os.getenv("CONFIG_PASSWORD"):
            raise HTTPException(status_code=401, detail="unauthorized")
        token = secrets.token_urlsafe(16)
        constants.TOKENS[token] = time.time() + constants.TOKEN_TTL
        response.set_cookie(
            "cfg", token, httponly=True, samesite="lax", max_age=constants.TOKEN_TTL
        )
        return {"ok": True}


@dashboard_router.post("/api/dismiss_alerte", response_class=ORJSONResponse)
async def dismiss_alerte_api(request: Request):
    """API asynchrone pour supprimer une alerte"""
    data = await request.json()
    alerte_id = data.get("alerte_id")
    try:
        if not alerte_id:
            return {"success": False, "error": "Paramètre 'alerte_id' manquant."}

        # await dismiss_alerte(alerte_id)
        # Notifier les clients SSE qu'une modification d'alertes est intervenue
        # await _sse_publish({"type": "alertes_updated", "id": alerte_id, "at": datetime.now(TZ).isoformat(timespec='seconds')})
        return {"success": True, "message": f"Alerte {alerte_id} supprimée"}
    except Exception as e:
        logger.error(f"Erreur lors de la suppression de l'alerte {alerte_id}: {e}")
        return {"success": False, "error": str(e)}


@dashboard_router.get("/health", response_class=ORJSONResponse)
async def health_check():
    """Health check endpoint"""
    return ORJSONResponse(
        {
            "status": "healthy",
            "service": "AUTODO Unified",
            "timestamp": datetime.now().isoformat(),
        }
    )


@dashboard_router.get("/data-management", response_class=HTMLResponse)
async def data_management_page(request: Request):
    """Data management unified page (login + dashboard)"""
    from main import templates

    return templates.TemplateResponse("data_management.html", {"request": request})


@dashboard_router.api_route(
    "/api/data-management", methods=["GET", "POST"], response_class=ORJSONResponse
)
async def data_management_unified(
    request: Request, response: Response, action: str = "login", period: str = "7days"
):
    """Unified data management endpoint - handles login and data retrieval"""
    try:
        if request.method == "POST":
            data = await request.json()
            action = data.get("action", "login")

            if action == "login":
                password = data.get("password", "")
                dm_password = os.getenv("DATA_MANAGEMENT_PASSWORD")

                if dm_password and password != dm_password:
                    logger.warning(
                        f"Failed data management login attempt from IP: {get_real_ip(request)}"
                    )
                    raise HTTPException(status_code=401, detail="Invalid password")

                token = secrets.token_urlsafe(32)
                constants.TOKENS[token] = time.time() + constants.TOKEN_TTL

                response.set_cookie(
                    "dm_token",
                    token,
                    httponly=True,
                    samesite="lax",
                    max_age=constants.TOKEN_TTL,
                )

                log_criteria_access(request, "data_management_login")
                logger.info(
                    f"Data Management login successful for IP: {get_real_ip(request)}"
                )

                return {"success": True, "message": "Login successful"}

        elif request.method == "GET" and action == "data":
            # Verify authentication token for data retrieval
            if not _verify_dm_token(request):
                logger.warning(
                    f"Unauthorized data management data request from IP: {get_real_ip(request)}"
                )
                raise HTTPException(status_code=401, detail="Not authenticated")

            logger.info(f"Data management data request for period: {period}")
            # Retrieve data with optional period filter
            access_stats = await asyncio.to_thread(get_access_logs_from_db, period)
            alerts_stats = await asyncio.to_thread(get_alerts_stats_from_db, period)
            changes_stats = await asyncio.to_thread(get_config_changes_from_db, period)
            logger.info(
                f"All stats: access_logs={len(access_stats.get('access_logs', []))}, alerts_count={len(alerts_stats)}, config_changes={len(changes_stats)}"
            )

            return {
                "period": period,
                "access_logs": access_stats["access_logs"],
                "access_stats": access_stats["access_stats"],
                "alerts_stats": alerts_stats,
                "config_changes": changes_stats,
                "generated_at": get_cameroon_time().isoformat(),
            }

        raise HTTPException(status_code=400, detail="Invalid action")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in data management: {e}")
        return {
            "period": period,
            "access_logs": [],
            "config_changes": [],
            "access_stats": {
                "daily_data": {},
                "total_unique_ips": 0,
                "avg_daily_unique": 0,
                "today_unique_count": 0,
            },
            "alerts_stats": {},
            "error": str(e),
        }
