"""SQLite must support workflows that use row locks on MySQL."""
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool

from core import accounts, db, tickets

GOOD = "Abcdefghi1"


@pytest.fixture
def sqlite_state(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'workflow.sqlite').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )
    event.listen(engine, "connect", db._sqlite_pragmas)
    monkeypatch.setattr(db, "local_engine", lambda: engine)
    monkeypatch.setattr(db, "is_sqlite", lambda: True)
    assert db.ensure_schema()
    yield
    engine.dispose()


def _pending_ticket(ticket_id):
    return {
        "id": ticket_id,
        "type": "traffic",
        "site": "Douala",
        "database_name": "kribi",
        "timestamp": "2026-11-17 12:00:00",
        "value": 10.0,
        "status": "open",
        "agent": "DIAGNET",
        "severity": "critical",
        "closure_timestamp": None,
        "closure_pending_timestamp": None,
        "closure_reason": None,
        "autin_id": None,
        "external_api_ticket_id": None,
        "ttr_seconds": None,
        "category": 2,
        "pertes": "No",
        "last_values": [],
        "description": "Test ticket",
        "reason": "Test reason",
        "pertes_warnings": [],
        "orange_sync_state": "held",
        "orange_attempts": 0,
        "orange_next_retry_at": None,
        "orange_last_error": None,
        "validation_state": "pending",
        "last_note_value": None,
        "last_note_at": None,
    }


def test_account_update_works_without_sqlite_for_update(sqlite_state):
    first = accounts.create_user("admin-one", "Admin One", "admin", GOOD)
    accounts.create_user("admin-two", "Admin Two", "admin", GOOD)

    updated = accounts.update_user(first["id"], role="supervision")

    assert updated["role"] == "supervision"


@pytest.mark.parametrize(
    ("action", "expected_status", "expected_validation"),
    [
        (lambda ticket_id: tickets.approve(ticket_id, "alice", False), "open", "approved"),
        (lambda ticket_id: tickets.reject(ticket_id, "alice", "false alarm"), "deleted", "rejected"),
        (lambda ticket_id: tickets.delete(ticket_id, "alice", "duplicate"), "deleted", "pending"),
    ],
)
def test_ticket_decisions_work_without_sqlite_for_update(sqlite_state, action, expected_status, expected_validation):
    ticket_id = 20261117001
    tickets.persist(tickets.ChangeSet(inserts=[_pending_ticket(ticket_id)]))

    decided = action(ticket_id)

    assert decided["id"] == ticket_id
    updated = tickets.get_ticket(ticket_id)
    assert updated["status"] == expected_status
    assert updated["validation_state"] == expected_validation
