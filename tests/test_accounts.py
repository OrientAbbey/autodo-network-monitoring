"""Comptes nominatifs : hachage, politique de mot de passe, verrouillage, dernier administrateur, administrateur initial."""
from datetime import timedelta

import pytest
from sqlalchemy import text

from core import accounts
from core.config import fmt, now_local

GOOD = "Abcdefghi1"


def test_scrypt_hash_is_salted_and_verifiable():
    first, second = accounts.hash_password(GOOD), accounts.hash_password(GOOD)
    assert first != second and first.startswith("scrypt$")                       # sel aléatoire : deux hachages différents
    assert accounts.verify_password(GOOD, first) and not accounts.verify_password("autre", first)
    assert not accounts.verify_password(GOOD, "format-illisible")                # un hachage corrompu ne valide jamais


@pytest.mark.parametrize("bad", ["Court1a", "pasdemajuscule1", "PASDEMINUSCULE1", "PasDeChiffreIci"])
def test_password_policy_rejects_weak_passwords(bad):
    with pytest.raises(accounts.AccountError):
        accounts.check_password_policy(bad)
    accounts.check_password_policy(GOOD)


def test_create_user_returns_python_booleans_and_hides_the_hash(clean_state):
    user = accounts.create_user("Alice.Martin", "Alice", "supervision", GOOD)
    assert user["username"] == "alice.martin"                                     # identifiant normalisé
    assert user["active"] is True and user["must_change_password"] is True       # TINYINT MySQL converti en bool
    assert "password_hash" not in user


def test_username_is_unique_case_insensitively(clean_state):
    accounts.create_user("alice", "A", "supervision", GOOD)
    with pytest.raises(accounts.AccountError, match="existe déjà"):
        accounts.create_user("ALICE", "A2", "admin", GOOD)


def test_invalid_username_and_role_are_refused(clean_state):
    with pytest.raises(accounts.AccountError):
        accounts.create_user("a b", "x", "admin", GOOD)
    with pytest.raises(accounts.AccountError):
        accounts.create_user("bob", "x", "visiteur", GOOD)


def test_authenticate_success_and_single_error_message(clean_state):
    accounts.create_user("alice", "Alice", "admin", GOOD)
    assert accounts.authenticate("ALICE", GOOD)["role"] == "admin"
    messages = set()
    for name, password in (("alice", "mauvais"), ("inconnu", GOOD)):
        with pytest.raises(accounts.AccountError) as error:
            accounts.authenticate(name, password)
        messages.add(str(error.value))
    assert messages == {accounts.INVALID_CREDENTIALS}                            # on ne distingue pas « inconnu » de « mauvais mot de passe »


def test_five_failures_lock_the_account_for_fifteen_minutes(clean_state):
    accounts.create_user("alice", "Alice", "admin", GOOD)
    for _ in range(accounts.MAX_FAILED_LOGINS):
        with pytest.raises(accounts.AccountError):
            accounts.authenticate("alice", "mauvais")
    with pytest.raises(accounts.AccountError, match="verrouillé"):
        accounts.authenticate("alice", GOOD)                                      # même le BON mot de passe est refusé pendant le verrouillage
    user = accounts.list_users()[0]
    assert user["locked_until"] is not None
    with clean_state.local_tx() as c:                                             # fin du verrouillage
        c.execute(text("UPDATE users SET locked_until = :t"), {"t": fmt(now_local().replace(tzinfo=None) - timedelta(minutes=1))})
    assert accounts.authenticate("alice", GOOD)["failed_logins"] == 0


def test_a_success_resets_the_failure_counter(clean_state):
    accounts.create_user("alice", "Alice", "admin", GOOD)
    for _ in range(3):
        with pytest.raises(accounts.AccountError):
            accounts.authenticate("alice", "mauvais")
    accounts.authenticate("alice", GOOD)
    for _ in range(3):                                                            # 3 + 3 échecs séparés par un succès : pas de verrouillage
        with pytest.raises(accounts.AccountError):
            accounts.authenticate("alice", "mauvais")
    assert accounts.authenticate("alice", GOOD)


def test_inactive_account_cannot_log_in(clean_state):
    admin = accounts.create_user("root", "Root", "admin", GOOD)
    other = accounts.create_user("bob", "Bob", "supervision", GOOD)
    accounts.update_user(other["id"], active=False)
    with pytest.raises(accounts.AccountError):
        accounts.authenticate("bob", GOOD)
    assert admin["active"]


def test_the_last_active_admin_cannot_be_disabled_or_demoted(clean_state):
    only = accounts.create_user("root", "Root", "admin", GOOD)
    with pytest.raises(accounts.AccountError, match="au moins un administrateur"):
        accounts.update_user(only["id"], active=False)
    with pytest.raises(accounts.AccountError, match="au moins un administrateur"):
        accounts.update_user(only["id"], role="supervision")
    second = accounts.create_user("second", "Second", "admin", GOOD)
    assert accounts.update_user(only["id"], role="supervision")["role"] == "supervision"      # un autre admin existe : autorisé
    with pytest.raises(accounts.AccountError):
        accounts.update_user(second["id"], active=False)                                       # il est devenu le dernier


def test_change_own_password_requires_current_and_clears_the_forced_change(clean_state):
    user = accounts.create_user("alice", "Alice", "supervision", GOOD)
    with pytest.raises(accounts.AccountError, match="actuel incorrect"):
        accounts.change_own_password(user["id"], "faux", "Nouveau-Passe-2")
    with pytest.raises(accounts.AccountError, match="différent"):
        accounts.change_own_password(user["id"], GOOD, GOOD)
    with pytest.raises(accounts.AccountError, match="trop faible"):
        accounts.change_own_password(user["id"], GOOD, "faible")
    accounts.change_own_password(user["id"], GOOD, "Nouveau-Passe-2")
    assert accounts.authenticate("alice", "Nouveau-Passe-2")["must_change_password"] is False


def test_reset_password_forces_a_new_change_and_unlocks(clean_state):
    user = accounts.create_user("alice", "Alice", "supervision", GOOD, must_change_password=False)
    accounts.reset_password(user["id"], "Temporaire-Pass-3")
    logged = accounts.authenticate("alice", "Temporaire-Pass-3")
    assert logged["must_change_password"] is True


def test_initial_admin_is_created_once_from_the_environment(clean_state):
    assert accounts.ensure_initial_admin() is True
    admin = accounts.list_users()[0]
    assert admin["username"] == "admin" and admin["role"] == "admin" and admin["must_change_password"] is True
    assert accounts.ensure_initial_admin() is False                               # un compte existe déjà : rien de plus
