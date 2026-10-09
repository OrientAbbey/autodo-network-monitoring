"""CONFIGURATION — variables d'environnement, paramètres d'alerte (fichier JSON) et utilitaires de temps.

* ``settings``     : réglages techniques lus une fois dans l'environnement (BD, API Orange, SMTP, concurrence…).
* ``load_config``  : paramètres d'alerte modifiables depuis l'interface. Fichier dans ``DATA_DIR`` (hors dépôt),
                     relu seulement si son ``mtime`` change, écrit de façon atomique.
* utilitaires temps: convention unique — les bases DIAGNET sont en UTC, les tickets en heure locale de Douala.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from .logger import get_logger

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
logger = get_logger(__name__)

LOCAL_TZ = ZoneInfo("Africa/Douala")          # UTC+1, pas d'heure d'été
UTC = timezone.utc
TS_FMT = "%Y-%m-%d %H:%M:%S"


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # --- application
    app_port: int = field(default_factory=lambda: _env_int("APP_PORT_UNIFIED", 8501))
    app_tickets_url: str = field(default_factory=lambda: _env("APP_TICKETS_URL", "http://localhost:8501/tickets"))
    token_ttl: int = field(default_factory=lambda: _env_int("TOKEN_TTL", 180))
    cookie_secure: bool = field(default_factory=lambda: _env_bool("COOKIE_SECURE", False))
    cors_origins: tuple = field(default_factory=lambda: tuple(o.strip() for o in _env("CORS_ORIGINS").split(",") if o.strip()))
    trusted_proxies: frozenset = field(default_factory=lambda: frozenset(x.strip() for x in _env("TRUSTED_PROXIES").split(",") if x.strip()))
    max_sse_subscribers: int = field(default_factory=lambda: _env_int("MAX_SSE_SUBSCRIBERS", 200))
    # --- base locale (tickets, visites, journaux) : lecture + écriture
    local_db_host: str = field(default_factory=lambda: _env("LOCAL_DB_HOST", "localhost"))
    local_db_port: int = field(default_factory=lambda: _env_int("LOCAL_DB_PORT", 3306))
    local_db_user: str = field(default_factory=lambda: _env("LOCAL_DB_USER", "root"))
    local_db_pass: str = field(default_factory=lambda: _env("LOCAL_DB_PASS", ""))
    local_db_name: str = field(default_factory=lambda: _env("LOCAL_DB_NAME", "tickets_db"))
    local_pool_size: int = field(default_factory=lambda: _env_int("LOCAL_DB_POOL_SIZE", 8))
    local_pool_overflow: int = field(default_factory=lambda: _env_int("LOCAL_DB_POOL_OVERFLOW", 8))
    # --- bases DIAGNET : LECTURE SEULE
    diag_user: str = field(default_factory=lambda: _env("DB_USER"))
    diag_pass: str = field(default_factory=lambda: _env("DB_PASS"))
    diag_port: int = field(default_factory=lambda: _env_int("DB_PORT", 3306))
    diag_connect_timeout: int = field(default_factory=lambda: _env_int("DB_CONNECT_TIMEOUT", 5))
    diag_read_timeout: int = field(default_factory=lambda: _env_int("DB_READ_TIMEOUT", 30))
    diag_statement_timeout: int = field(default_factory=lambda: _env_int("DB_STATEMENT_TIMEOUT_S", 25))
    collect_max_workers: int = field(default_factory=lambda: _env_int("COLLECT_MAX_WORKERS", 8))
    collect_db_timeout: int = field(default_factory=lambda: _env_int("COLLECT_DB_TIMEOUT_S", 60))
    mode7d_cache_ttl: int = field(default_factory=lambda: _env_int("MODE7D_CACHE_TTL_S", 1800))
    # --- API Orange (limites de débit)
    orange_client_id: str = field(default_factory=lambda: _env("ORANGE_API_CLIENT_ID"))
    orange_client_secret: str = field(default_factory=lambda: _env("ORANGE_API_CLIENT_SECRET"))
    orange_server: str = field(default_factory=lambda: _env("ORANGE_API_SERVER_INTRA") or _env("ORANGE_API_SERVER_INTER"))
    orange_token_endpoint: str = field(default_factory=lambda: _env("ORANGE_API_TOKEN_ENDPOINT"))
    orange_base_endpoint: str = field(default_factory=lambda: _env("ORANGE_API_BASE_ENDPOINT"))
    orange_cuid: str = field(default_factory=lambda: _env("CLIENT_USER_ID"))
    orange_related_resource_id: str = field(default_factory=lambda: _env("ORANGE_RELATED_RESOURCE_ID"))
    orange_verify_tls: bool = field(default_factory=lambda: _env_bool("ORANGE_API_VERIFY_TLS", True))
    orange_ca_bundle: str = field(default_factory=lambda: _env("ORANGE_API_CA_BUNDLE"))
    orange_rate_per_s: float = field(default_factory=lambda: _env_float("ORANGE_API_RATE_PER_SECOND", 2.0))
    orange_burst: int = field(default_factory=lambda: _env_int("ORANGE_API_BURST", 4))
    orange_max_concurrency: int = field(default_factory=lambda: _env_int("ORANGE_API_MAX_CONCURRENCY", 2))
    orange_connect_timeout: float = field(default_factory=lambda: _env_float("ORANGE_API_CONNECT_TIMEOUT", 5))
    orange_read_timeout: float = field(default_factory=lambda: _env_float("ORANGE_API_READ_TIMEOUT", 20))
    orange_max_attempts: int = field(default_factory=lambda: _env_int("ORANGE_API_MAX_ATTEMPTS", 8))
    orange_sync_interval: int = field(default_factory=lambda: _env_int("ORANGE_SYNC_INTERVAL_S", 60))
    orange_batch_size: int = field(default_factory=lambda: _env_int("ORANGE_SYNC_BATCH", 10))
    orange_breaker_threshold: int = field(default_factory=lambda: _env_int("ORANGE_BREAKER_THRESHOLD", 5))
    orange_breaker_cooldown: int = field(default_factory=lambda: _env_int("ORANGE_BREAKER_COOLDOWN_S", 300))
    orange_lookup_external_id: bool = field(default_factory=lambda: _env_bool("ORANGE_API_LOOKUP_EXTERNAL_ID", False))
    orange_terminal_statuses: tuple = field(default_factory=lambda: tuple(
        s.strip().lower() for s in _env("ORANGE_TERMINAL_STATUSES", "restored,closed,cancelled,resolved").split(",") if s.strip()))
    # --- e-mail
    smtp_host: str = field(default_factory=lambda: _env("SERVER_MAIL"))
    smtp_port: int = field(default_factory=lambda: _env_int("SERVER_PORT", 25))
    smtp_timeout: int = field(default_factory=lambda: _env_int("SMTP_TIMEOUT_S", 15))
    smtp_max_per_minute: int = field(default_factory=lambda: _env_int("SMTP_MAX_PER_MINUTE", 30))
    sender_email: str = field(default_factory=lambda: _env("SENDER_EMAIL"))
    sender_name: str = field(default_factory=lambda: _env("SENDER_NAME", "AUTODO"))
    email_recipients: str = field(default_factory=lambda: _env("EMAIL_RECIPIENTS"))
    email_recipients_file: str = field(default_factory=lambda: _env("EMAIL_RECIPIENTS_FILE", str(BASE_DIR / "email_recipients.txt")))
    # --- exploitation
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(BASE_DIR / "data"))))
    retention_days: int = field(default_factory=lambda: _env_int("RETENTION_DAYS", 90))
    watchdog_max_age_min: int = field(default_factory=lambda: _env_int("WATCHDOG_MAX_AGE_MIN", 15))
    dns_reference_ips: tuple = field(default_factory=lambda: tuple(
        x.strip() for x in _env("DNS_REFERENCE_IPS", "8.8.8.0/24,8.8.4.0/24,2001:4860:4860::/128").split(",") if x.strip()))
    mode_bin_gbps: float = field(default_factory=lambda: _env_float("MODE_BIN_GBPS", 2.5))
    exnet_autologin: bool = field(default_factory=lambda: _env_bool("EXNET_AUTOLOGIN_ENABLED", False))
    exnet_username: str = field(default_factory=lambda: _env("EXNET_USERNAME"))
    exnet_password: str = field(default_factory=lambda: _env("EXNET_PASSWORD"))


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = settings.data_dir / "config.json"
CONFIG_DEFAULT_FILE = BASE_DIR / "config.default.json"


# ============================================================================
# PARAMÈTRES D'ALERTE
# ============================================================================
class ConfigParams(BaseModel):
    """Paramètres d'alerte : bornes validées, défauts = config.default.json."""
    alert_traffic_threshold_percent: float = Field(40.0, gt=0, le=100)
    alert_pertes_threshold: float = Field(3.0, ge=0, le=100)
    pertes_consecutive_count: int = Field(2, ge=1, le=50)
    traffic_up_threshold_percent: float = Field(4.0, ge=0, le=1000)
    traffic_consecutive_drops_count: int = Field(5, ge=2, le=50)
    traffic_consecutive_drops_min_percent: float = Field(17.0, ge=0, le=100)
    alert_persistence_duration_minutes: int = Field(25, ge=1, le=1440)
    traffic_recovery_ratio: float = Field(0.9, gt=0, le=2)
    mode7days_recovery_ratio: float = Field(0.85, gt=0, le=2)
    db_down_threshold_minutes: int = Field(70, ge=1, le=10080)       # seuil de DÉCLARATION
    db_recovery_threshold_minutes: int = Field(35, ge=1, le=10080)   # seuil de LEVÉE (hystérésis)
    zero_traffic_min_samples: int = Field(2, ge=1, le=12)            # 2 = un « zéro » isolé (bruit de collecte) n'ouvre pas de ticket
    source_unreachable_cycles: int = Field(3, ge=1, le=100)          # cycles d'échec avant alerte interne
    alert_tcp_no_response_threshold: float = Field(10.0, ge=0, le=100)
    alert_tcp_block_wp_up_threshold: float = Field(10.0, ge=0, le=100)
    alert_latence_threshold_ms: float = Field(250.0, ge=0, le=60000)
    # Les deux premiers pilotent aussi la CRÉATION de tickets ; les autres, l'affichage du tableau de bord.
    alerts_enable_international: bool = True
    alerts_enable_gi: bool = True
    alerts_enable_international_gi: bool = True
    alerts_enable_roaming: bool = False
    alerts_enable_tdd: bool = False

    model_config = ConfigDict(extra="ignore")


_ALERT_KEYS = {k for k in ConfigParams.model_fields if k.startswith("alerts_enable_")}
_cfg_lock = threading.Lock()
_cfg_cache: dict = {"mtime": None, "params": None}


def _read_file(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {**data.get("criteria", {}), **data.get("alerts", {})}


LEGACY_CONFIG_FILE = BASE_DIR / "config.json"      # ancien emplacement (versionné, réécrit par l'application)


def _ensure_config_file() -> None:
    if not CONFIG_FILE.exists():
        if LEGACY_CONFIG_FILE.exists() and LEGACY_CONFIG_FILE.resolve() != CONFIG_FILE.resolve():
            shutil.copyfile(LEGACY_CONFIG_FILE, CONFIG_FILE)          # migration transparente
            logger.info("config.json migré vers %s", CONFIG_FILE)
            return
        if CONFIG_DEFAULT_FILE.exists():
            shutil.copyfile(CONFIG_DEFAULT_FILE, CONFIG_FILE)
        else:
            CONFIG_FILE.write_text(json.dumps({"criteria": {}, "alerts": {}}, indent=4), encoding="utf-8")
        logger.info("Configuration initialisée dans %s", CONFIG_FILE)


def load_config() -> dict:
    """Paramètres d'alerte (dict). Relus uniquement si le fichier a changé (cache par mtime)."""
    with _cfg_lock:
        try:
            _ensure_config_file()
            mtime = CONFIG_FILE.stat().st_mtime_ns
            if _cfg_cache["mtime"] != mtime or _cfg_cache["params"] is None:
                params = ConfigParams(**_read_file(CONFIG_FILE)).model_dump()
                _cfg_cache.update(mtime=mtime, params=params)
            return dict(_cfg_cache["params"])
        except Exception:
            logger.exception("Lecture de la configuration impossible — valeurs par défaut utilisées")
            return ConfigParams().model_dump()


def save_config_updates(updates: dict) -> dict:
    """Fusionne ``updates`` (champs présents uniquement) dans le fichier, de façon atomique. Retourne la config."""
    with _cfg_lock:
        _ensure_config_file()
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        raw.setdefault("criteria", {})
        raw.setdefault("alerts", {})
        for k, v in updates.items():
            raw["alerts" if k in _ALERT_KEYS else "criteria"][k] = v
        merged = ConfigParams(**{**raw["criteria"], **raw["alerts"]})        # revalide l'ensemble
        fd, tmp = tempfile.mkstemp(dir=CONFIG_FILE.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=4, ensure_ascii=False)
        os.replace(tmp, CONFIG_FILE)                                          # atomique
        _cfg_cache["mtime"] = None
        return merged.model_dump()


# ============================================================================
# TEMPS : DIAGNET = UTC naïf, tickets = heure locale Douala naïve
# ============================================================================
def now_local() -> datetime:
    return datetime.now(LOCAL_TZ)


def fmt(dt: datetime) -> str:
    return dt.strftime(TS_FMT)


def parse(ts: str | datetime) -> datetime:
    """Parse « YYYY-mm-dd HH:MM:SS » (ou ISO avec 'T') en datetime naïf."""
    if isinstance(ts, datetime):
        return ts.replace(tzinfo=None)
    return datetime.fromisoformat(ts.replace("Z", "")).replace(tzinfo=None)


def round5(dt: datetime) -> datetime:
    """Arrondi vers le bas au multiple de 5 minutes."""
    return dt.replace(minute=dt.minute - dt.minute % 5, second=0, microsecond=0)


def local_to_utc_naive(dt: datetime) -> datetime:
    """Heure locale (naïve ou aware) → UTC naïf (format des bases DIAGNET)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(UTC).replace(tzinfo=None)


def utc_naive_to_local_naive(dt: datetime) -> datetime:
    """UTC naïf (DIAGNET) → heure locale naïve (tickets, affichage)."""
    return dt.replace(tzinfo=UTC).astimezone(LOCAL_TZ).replace(tzinfo=None)


def period_to_cutoff(period: str) -> datetime:
    days = {"today": 0, "7days": 7, "1month": 30, "3months": 90}.get(period, 7)
    now = now_local().replace(tzinfo=None)
    return now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days)
