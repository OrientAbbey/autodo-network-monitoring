"""GOUVERNANCE — journal d'audit, versions de configuration, seuils par base, fenêtres de maintenance.

* ``audit``            : une ligne par action sensible (qui, depuis quelle IP, quoi, sur quoi, détails). N'échoue jamais.
* versions de config   : chaque modification enregistre un instantané complet ; ``diff_configs`` produit les écarts lisibles ;
                         ``restore_config_version`` crée une NOUVELLE version (l'historique n'est jamais réécrit).
* seuils par base      : ``config.config_for`` (surcharges validées, voir ``config.OVERRIDABLE_KEYS``).
* maintenance / silence: tant qu'une fenêtre est active pour une base, le cycle ne crée ni ne met à jour aucun ticket pour elle.
                         Rejeter ou supprimer un ticket ouvre automatiquement un silence (``rejection_silence_minutes``).
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta

from sqlalchemy import text

from . import config
from .config import fmt, now_local, parse
from .db import fetch_all, fetch_one, local_conn, local_tx
from .logger import get_logger

logger = get_logger(__name__)

ALL_DATABASES = "*"          # clé spéciale d'une fenêtre de maintenance qui couvre toutes les bases


def _now() -> datetime:
    return now_local().replace(tzinfo=None)


# ============================================================================
# Journal d'audit
# ============================================================================
def audit(actor: str, ip: str, action: str, target: str = "", details: dict | None = None) -> None:
    """Enregistre une action sensible. Une erreur d'écriture est journalisée mais ne bloque jamais l'action elle-même."""
    try:
        with local_tx() as conn:
            conn.execute(text("INSERT INTO audit_log (at, actor, ip, action, target, details) VALUES (:t, :a, :i, :x, :g, :d)"),
                         {"t": fmt(_now()), "a": actor[:64], "i": (ip or "")[:45], "x": action[:64], "g": (target or "")[:128],
                          "d": json.dumps(details, default=str, ensure_ascii=False) if details is not None else None})
    except Exception:
        logger.exception("Écriture du journal d'audit impossible (%s)", action)


def list_audit(action: str | None = None, actor: str | None = None, days: int = 30, limit: int = 500) -> list[dict]:
    where, params = ["at >= :since"], {"since": fmt(_now() - timedelta(days=days)), "lim": max(1, min(limit, 5000))}
    for column, value in (("action", action), ("actor", actor)):
        if value:
            where.append(f"{column} = :{column}")
            params[column] = value
    with local_conn() as conn:
        rows = fetch_all(conn, f"SELECT * FROM audit_log WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT :lim", **params)
    for row in rows:
        row["at"] = fmt(row["at"])
        if isinstance(row["details"], (str, bytes)):
            try:
                row["details"] = json.loads(row["details"])
            except ValueError:
                pass
    return rows


def audit_csv(rows: list[dict]) -> str:
    """Export CSV du journal. Les cellules commençant par = + - @ sont neutralisées (injection de formules tableur)."""
    def safe(value) -> str:
        cell = "" if value is None else (json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value))
        return "'" + cell if cell[:1] in ("=", "+", "-", "@") else cell

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "date", "acteur", "ip", "action", "cible", "détails"])
    for row in rows:
        writer.writerow([row["id"], row["at"], safe(row["actor"]), row["ip"], safe(row["action"]), safe(row["target"]), safe(row["details"])])
    return buffer.getvalue()


# ============================================================================
# Versions de configuration
# ============================================================================
def _effective(raw: dict) -> dict:
    """Paramètres globaux effectifs (défauts inclus) + surcharges aplaties « overrides.<base>.<paramètre> »."""
    flat = config.ConfigParams(**{**raw.get("criteria", {}), **raw.get("alerts", {})}).model_dump()
    for db_key, params in (raw.get("overrides") or {}).items():
        for key, value in params.items():
            flat[f"overrides.{db_key}.{key}"] = value
    return flat


def diff_configs(before_raw: dict, after_raw: dict) -> list[dict]:
    """Liste lisible des écarts : ``[{"parameter", "before", "after"}]`` (None = absent)."""
    before, after = _effective(before_raw), _effective(after_raw)
    return [{"parameter": key, "before": before.get(key), "after": after.get(key)}
            for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)]


def record_config_version(actor: str, comment: str, raw: dict) -> int:
    with local_tx() as conn:
        result = conn.execute(text("INSERT INTO config_versions (at, actor, comment, config_json) VALUES (:t, :a, :c, :j)"),
                              {"t": fmt(_now()), "a": actor[:64], "c": comment[:200], "j": json.dumps(raw, ensure_ascii=False)})
        return int(result.lastrowid)


def ensure_initial_version() -> None:
    """Première version = configuration en place au premier démarrage (point de retour garanti)."""
    with local_conn() as conn:
        count = int(conn.execute(text("SELECT COUNT(*) FROM config_versions")).scalar())
    if count == 0:
        record_config_version("système", "Configuration initiale", config.read_raw())


def list_config_versions(limit: int = 50) -> list[dict]:
    with local_conn() as conn:
        rows = fetch_all(conn, "SELECT id, at, actor, comment FROM config_versions ORDER BY id DESC LIMIT :n", n=max(1, min(limit, 500)))
    return [{**row, "at": fmt(row["at"])} for row in rows]


def get_config_version(version_id: int) -> dict | None:
    """Détail d'une version + écarts entre cette version et la configuration actuelle."""
    with local_conn() as conn:
        row = fetch_one(conn, "SELECT * FROM config_versions WHERE id = :id", id=version_id)
    if not row:
        return None
    stored = json.loads(row["config_json"])
    return {"id": row["id"], "at": fmt(row["at"]), "actor": row["actor"], "comment": row["comment"], "config": stored,
            "changes_vs_current": diff_configs(config.read_raw(), stored)}


def save_global_config(actor: str, ip: str, updates: dict, comment: str = "") -> dict:
    """Applique des modifications globales (champs présents uniquement). Retourne ``{"changes", "version"}``.

    Lève ``pydantic.ValidationError`` si une valeur est hors bornes (rien n'est écrit dans ce cas).
    """
    before = config.read_raw()
    config.save_config_updates(updates)
    return _finish_config_change(actor, ip, before, comment or "Modification des paramètres globaux", "config_update")


def save_overrides(actor: str, ip: str, overrides: dict, comment: str = "") -> dict:
    """Remplace l'ensemble des seuils par base (A6)."""
    before = config.read_raw()
    config.save_overrides(overrides)
    return _finish_config_change(actor, ip, before, comment or "Modification des seuils par base", "config_overrides")


def restore_config_version(actor: str, ip: str, version_id: int) -> dict:
    """Restaure une ancienne configuration : écrite comme une NOUVELLE version, l'historique reste intact."""
    stored = get_config_version(version_id)
    if stored is None:
        raise LookupError("Version de configuration introuvable")
    before = config.read_raw()
    config.write_raw(stored["config"])
    return _finish_config_change(actor, ip, before, f"Restauration de la version {version_id}", "config_restore", force_version=True)


def _finish_config_change(actor: str, ip: str, before: dict, comment: str, action: str, force_version: bool = False) -> dict:
    after = config.read_raw()
    changes = diff_configs(before, after)
    version = None
    if changes or force_version:
        version = record_config_version(actor, comment, after)
        audit(actor, ip, action, f"v{version}", {"changes": changes})
    return {"changes": changes, "version": version}


# ============================================================================
# Maintenance et silences
# ============================================================================
def _as_db_datetime(value: str | datetime) -> str:
    return fmt(parse(value))


def add_window(db_key: str, starts_at: str | datetime, ends_at: str | datetime, reason: str, created_by: str,
               auto_silence: bool = False) -> int:
    start, end = parse(starts_at), parse(ends_at)
    if end <= start:
        raise ValueError("La fin de la fenêtre doit suivre son début.")
    with local_tx() as conn:
        result = conn.execute(text("INSERT INTO maintenance_windows (db_key, starts_at, ends_at, reason, created_by, auto_silence) "
                                   "VALUES (:k, :s, :e, :r, :c, :a)"),
                              {"k": db_key[:50], "s": fmt(start), "e": fmt(end), "r": (reason or "")[:200], "c": created_by[:64], "a": int(auto_silence)})
        return int(result.lastrowid)


def list_windows(active_only: bool = False) -> list[dict]:
    """Fenêtres à venir ou en cours (``active_only`` : seulement celles qui couvrent l'instant présent)."""
    now = fmt(_now())
    sql = "SELECT * FROM maintenance_windows WHERE ends_at >= :now" + (" AND starts_at <= :now" if active_only else "") + " ORDER BY starts_at"
    with local_conn() as conn:
        rows = fetch_all(conn, sql, now=now)
    return [{**row, "starts_at": fmt(row["starts_at"]), "ends_at": fmt(row["ends_at"]), "auto_silence": bool(row["auto_silence"])} for row in rows]


def delete_window(window_id: int) -> bool:
    with local_tx() as conn:
        return conn.execute(text("DELETE FROM maintenance_windows WHERE id = :id"), {"id": window_id}).rowcount == 1


def silenced_databases(now: str | None = None) -> set[str]:
    """Bases actuellement en maintenance ou en silence. ``"*"`` dans l'ensemble = toutes les bases. Lu UNE fois par cycle."""
    now = now or fmt(_now())
    with local_conn() as conn:
        rows = conn.execute(text("SELECT DISTINCT db_key FROM maintenance_windows WHERE starts_at <= :n AND ends_at >= :n"), {"n": now}).all()
    return {row[0] for row in rows}


def is_silenced(db_key: str, silenced: set[str]) -> bool:
    return ALL_DATABASES in silenced or db_key in silenced


def silence_after_rejection(db_key: str, actor: str, minutes: int) -> int | None:
    """Ouvre un silence de ``minutes`` sur la base après un rejet / une suppression de ticket (0 = désactivé)."""
    if minutes <= 0:
        return None
    start = _now()
    return add_window(db_key, start, start + timedelta(minutes=minutes), "Silence automatique après rejet/suppression d'un ticket",
                      actor, auto_silence=True)
