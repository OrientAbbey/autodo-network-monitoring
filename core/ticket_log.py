"""JOURNAL DE TICKET — chronologie affichée à l'utilisateur ET file des notes à envoyer à Orange.

Une ligne de ``ticket_events`` = un évènement daté (création, mise à jour, validation, commentaire, note Orange…).
Si ``to_orange`` = 1, le texte est aussi une note à transmettre au ticket distant (``orange.sync_notes`` la lit puis
positionne ``orange_sent``). Les évènements d'un cycle sont écrits dans la MÊME transaction que les tickets.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .config import fmt, now_local
from .db import fetch_all, local_conn, local_tx

# Types d'évènements connus (documentation ; la colonne reste un texte libre de 32 caractères)
KINDS = ("created", "update", "pertes_warning", "validation_requested", "approved", "rejected", "deleted", "comment",
         "orange_created", "orange_failed", "orange_note", "orange_restored", "closure_pending", "closed", "acked",
         "quality_score", "feedback")

SYSTEM_ACTOR = "système"
UNITS = {"traffic": "Gb/s", "pertes": "%", "db_down": "min"}


def _now_text() -> str:
    return fmt(now_local().replace(tzinfo=None))


def make_event(ticket_id: int, kind: str, message: str, actor: str = SYSTEM_ACTOR, to_orange: bool = False,
               at: str | None = None) -> dict:
    """Construit un évènement (pas d'écriture) — utilisé par ``ChangeSet.log``."""
    return {"ticket_id": ticket_id, "at": at or _now_text(), "kind": kind, "message": message[:4000],
            "actor": actor[:64], "to_orange": int(to_orange)}


def insert_events(conn: Connection, events: list[dict]) -> None:
    """Écrit des évènements dans la transaction ``conn`` (aucun COMMIT ici)."""
    if events:
        conn.execute(text("INSERT INTO ticket_events (ticket_id, at, kind, message, actor, to_orange) "
                          "VALUES (:ticket_id, :at, :kind, :message, :actor, :to_orange)"), events)


def log_event(ticket_id: int, kind: str, message: str, actor: str = SYSTEM_ACTOR, to_orange: bool = False) -> None:
    """Écrit un évènement isolé (action d'un utilisateur, synchronisation Orange)."""
    with local_tx() as conn:
        insert_events(conn, [make_event(ticket_id, kind, message, actor, to_orange)])


def timeline(ticket_id: int) -> list[dict]:
    """Chronologie complète d'un ticket, du plus ancien au plus récent."""
    with local_conn() as conn:
        rows = fetch_all(conn, "SELECT id, at, kind, message, actor, to_orange, orange_sent FROM ticket_events "
                               "WHERE ticket_id = :t ORDER BY at, id", t=ticket_id)
    return [{**row, "at": fmt(row["at"]), "to_orange": bool(row["to_orange"]), "orange_sent": bool(row["orange_sent"])} for row in rows]


# ============================================================================
# Notes à envoyer à Orange
# ============================================================================
def format_value(ticket_type: str, value: float) -> str:
    return f"{value:.2f} {UNITS.get(ticket_type, '')}".strip()


def note_message(ticket_type: str, current: float, previous: float) -> str:
    """Texte de la note envoyée à Orange (anglais : langue des tickets distants)."""
    change = 100.0 if previous == 0 and current != 0 else ((current - previous) / previous * 100.0 if previous else 0.0)
    return (f"AUTODO update: current value {format_value(ticket_type, current)} "
            f"(previous {format_value(ticket_type, previous)}, {change:+.0f}%)")


def unsent_orange_notes(limit: int = 50) -> list[dict]:
    """Notes en attente + état du ticket concerné (pour décider d'envoyer, d'attendre ou d'abandonner)."""
    with local_conn() as conn:
        return fetch_all(conn, """SELECT e.id, e.ticket_id, e.at, e.message, e.actor, t.orange_sync_state, t.external_api_ticket_id, t.status
                FROM ticket_events e JOIN tickets_tb t ON t.id = e.ticket_id
                WHERE e.to_orange = 1 AND e.orange_sent = 0 ORDER BY e.id LIMIT :n""", n=limit)


def mark_note_sent(event_id: int) -> None:
    with local_tx() as conn:
        conn.execute(text("UPDATE ticket_events SET orange_sent = 1 WHERE id = :id"), {"id": event_id})
