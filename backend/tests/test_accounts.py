"""
Accounts, password hashing, sessions and login throttling.

These cover the findings that were rated Critical and High in the audit: one
shared password, unlimited guesses, and sessions that could not be revoked.
"""

from __future__ import annotations

import pytest

import accounts
import roles
from accounts import AccountError, RateLimited

GOOD_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def owner(fresh_db):
    return accounts.create_user("owner1", GOOD_PASSWORD, role="owner")


# ─── Password hashing ────────────────────────────────────────────


def test_hash_is_salted_and_verifiable():
    a = accounts.hash_password(GOOD_PASSWORD)
    b = accounts.hash_password(GOOD_PASSWORD)
    assert a != b, "identical passwords must not produce identical hashes"
    assert accounts.verify_password(GOOD_PASSWORD, a)
    assert accounts.verify_password(GOOD_PASSWORD, b)


def test_hash_records_its_parameters():
    """Parameters travel with the hash so they can be raised later."""
    stored = accounts.hash_password(GOOD_PASSWORD)
    scheme, n, r, p, _salt, _digest = stored.split("$")
    assert scheme == "scrypt"
    assert int(n) >= 2 ** 14 and int(r) >= 8 and int(p) >= 1


def test_wrong_password_rejected():
    stored = accounts.hash_password(GOOD_PASSWORD)
    assert not accounts.verify_password("wrong", stored)
    assert not accounts.verify_password("", stored)


@pytest.mark.parametrize(
    "malformed",
    ["", "notahash", "scrypt$only$three", "bcrypt$1$2$3$4$5", "scrypt$x$8$1$aa$bb"],
)
def test_malformed_hash_never_raises(malformed):
    assert accounts.verify_password(GOOD_PASSWORD, malformed) is False


def test_short_passwords_are_refused(fresh_db):
    with pytest.raises(AccountError, match="at least"):
        accounts.create_user("bob", "short")


# ─── User management ─────────────────────────────────────────────


def test_create_and_fetch(fresh_db):
    accounts.create_user("alice", GOOD_PASSWORD, role="moderator", steam_uid="ABC-123")
    user = accounts.get_user("alice")
    assert user["role"] == "moderator"
    assert user["steamUid"] == "abc123", "steam UID is normalised for matching"


def test_usernames_are_case_insensitive(fresh_db):
    accounts.create_user("Alice", GOOD_PASSWORD)
    assert accounts.get_user("alice") is not None
    with pytest.raises(AccountError, match="already exists"):
        accounts.create_user("ALICE", GOOD_PASSWORD)


@pytest.mark.parametrize("bad", ["", "has space", "semi;colon", "sql'inject", "a" * 65])
def test_invalid_usernames_refused(fresh_db, bad):
    with pytest.raises(AccountError):
        accounts.create_user(bad, GOOD_PASSWORD)


def test_unknown_role_refused(fresh_db):
    with pytest.raises(AccountError, match="Unknown role"):
        accounts.create_user("bob", GOOD_PASSWORD, role="superuser")
    with pytest.raises(AccountError):
        accounts.create_user("bob", GOOD_PASSWORD, role="guest")


def test_last_owner_cannot_be_demoted_or_disabled(owner):
    with pytest.raises(AccountError, match="only Owner"):
        accounts.update_user("owner1", role="admin")
    with pytest.raises(AccountError, match="only Owner"):
        accounts.update_user("owner1", disabled=True)
    with pytest.raises(AccountError, match="only Owner"):
        accounts.delete_user("owner1")


def test_owner_can_be_demoted_once_another_exists(owner):
    accounts.create_user("owner2", GOOD_PASSWORD, role="owner")
    accounts.update_user("owner1", role="admin")
    assert accounts.get_user("owner1")["role"] == "admin"


# ─── Sessions ────────────────────────────────────────────────────


def test_authenticate_opens_a_session(owner):
    token, user = accounts.authenticate("owner1", GOOD_PASSWORD, ip="10.0.0.1")
    assert user["username"] == "owner1"
    assert accounts.resolve_session(token)["username"] == "owner1"


def test_session_token_is_not_stored_verbatim(owner):
    """A stolen database must not hand over live sessions."""
    import db

    token, _ = accounts.authenticate("owner1", GOOD_PASSWORD)
    rows = db.connect().execute("SELECT token_hash FROM sessions").fetchall()
    assert rows
    assert all(r["token_hash"] != token for r in rows)


def test_logout_revokes_immediately(owner):
    token, _ = accounts.authenticate("owner1", GOOD_PASSWORD)
    assert accounts.resolve_session(token) is not None
    assert accounts.revoke_session(token) is True
    assert accounts.resolve_session(token) is None


def test_disabling_a_user_kills_their_sessions(owner):
    accounts.create_user("bob", GOOD_PASSWORD, role="player")
    token, _ = accounts.authenticate("bob", GOOD_PASSWORD)
    assert accounts.resolve_session(token) is not None

    accounts.update_user("bob", disabled=True)
    assert accounts.resolve_session(token) is None


def test_role_change_kills_existing_sessions(owner):
    """A demotion must not wait for a cookie to expire."""
    accounts.create_user("bob", GOOD_PASSWORD, role="admin")
    token, _ = accounts.authenticate("bob", GOOD_PASSWORD)
    accounts.update_user("bob", role="readonly")
    assert accounts.resolve_session(token) is None


def test_password_change_signs_out_everywhere(owner):
    first, _ = accounts.authenticate("owner1", GOOD_PASSWORD)
    second, _ = accounts.authenticate("owner1", GOOD_PASSWORD)
    accounts.set_password("owner1", "another-long-password")
    assert accounts.resolve_session(first) is None
    assert accounts.resolve_session(second) is None


def test_disabled_user_cannot_sign_in(owner):
    accounts.create_user("bob", GOOD_PASSWORD, role="player")
    accounts.update_user("bob", disabled=True)
    with pytest.raises(AccountError, match="disabled"):
        accounts.authenticate("bob", GOOD_PASSWORD)


def test_expired_session_is_rejected_and_cleaned_up(owner, monkeypatch):
    import db
    from datetime import datetime, timedelta, timezone

    token, _ = accounts.authenticate("owner1", GOOD_PASSWORD)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with db.transaction() as conn:
        conn.execute("UPDATE sessions SET expires_at = ?", (past,))

    assert accounts.resolve_session(token) is None
    assert db.connect().execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 0


def test_garbage_token_is_rejected(owner):
    assert accounts.resolve_session("") is None
    assert accounts.resolve_session("not-a-real-token") is None


# ─── Failure messages ────────────────────────────────────────────


def test_unknown_user_and_wrong_password_are_indistinguishable(owner):
    """Different messages would let an attacker enumerate account names."""
    with pytest.raises(AccountError) as missing:
        accounts.authenticate("nobody", GOOD_PASSWORD, ip="10.0.0.9")
    with pytest.raises(AccountError) as wrong:
        accounts.authenticate("owner1", "wrong-password", ip="10.0.0.9")
    assert str(missing.value) == str(wrong.value)


# ─── Rate limiting ───────────────────────────────────────────────


def test_repeated_failures_lock_out_by_username(owner):
    for _ in range(accounts.MAX_ATTEMPTS_PER_USER):
        with pytest.raises(AccountError):
            accounts.authenticate("owner1", "wrong", ip="10.0.0.2")

    with pytest.raises(RateLimited) as excinfo:
        accounts.authenticate("owner1", GOOD_PASSWORD, ip="10.0.0.2")
    assert excinfo.value.retry_after > 0


def test_lockout_backs_off_exponentially(owner):
    first = accounts._lockout_seconds(accounts.MAX_ATTEMPTS_PER_USER, accounts.MAX_ATTEMPTS_PER_USER)
    later = accounts._lockout_seconds(accounts.MAX_ATTEMPTS_PER_USER + 3, accounts.MAX_ATTEMPTS_PER_USER)
    assert later > first
    assert accounts._lockout_seconds(500, 5) <= accounts.MAX_LOCKOUT_SECONDS


def test_below_threshold_is_not_limited(owner):
    for _ in range(accounts.MAX_ATTEMPTS_PER_USER - 1):
        with pytest.raises(AccountError):
            accounts.authenticate("owner1", "wrong", ip="10.0.0.3")
    # Still allowed through — a couple of typos must not lock someone out.
    accounts.check_rate_limit("10.0.0.3", "owner1")


def test_successful_login_clears_the_budget(owner):
    for _ in range(accounts.MAX_ATTEMPTS_PER_USER - 1):
        with pytest.raises(AccountError):
            accounts.authenticate("owner1", "wrong", ip="10.0.0.4")

    accounts.authenticate("owner1", GOOD_PASSWORD, ip="10.0.0.4")
    assert accounts._recent_failures("username", "owner1") == 0


def test_ip_lockout_is_independent_of_username(fresh_db):
    """Spraying many usernames from one address is still throttled."""
    for i in range(accounts.MAX_ATTEMPTS_PER_IP):
        with pytest.raises(AccountError):
            accounts.authenticate(f"victim{i}", "wrong", ip="10.0.0.5")

    with pytest.raises(RateLimited):
        accounts.check_rate_limit("10.0.0.5", "someone-else")


# ─── Bootstrap ───────────────────────────────────────────────────


def test_bootstrap_creates_first_owner(fresh_db, monkeypatch):
    monkeypatch.setenv("PANEL_PASSWORD", GOOD_PASSWORD)
    created = accounts.bootstrap_from_env()
    assert created == "admin"
    assert accounts.get_user("admin")["role"] == "owner"


def test_bootstrap_is_a_no_op_once_users_exist(owner, monkeypatch):
    monkeypatch.setenv("PANEL_PASSWORD", GOOD_PASSWORD)
    assert accounts.bootstrap_from_env() is None
    assert accounts.user_count() == 1


def test_bootstrap_refuses_a_weak_panel_password(fresh_db, monkeypatch):
    monkeypatch.setenv("PANEL_PASSWORD", "1234")
    assert accounts.bootstrap_from_env() is None
    assert accounts.user_count() == 0


def test_bootstrap_without_password_does_nothing(fresh_db, monkeypatch):
    monkeypatch.delenv("PANEL_PASSWORD", raising=False)
    assert accounts.bootstrap_from_env() is None


# ─── Role model ──────────────────────────────────────────────────


def test_roles_are_ordered_and_cumulative():
    ranks = [roles.rank(r) for r in ("guest", "readonly", "player", "trusted",
                                     "moderator", "admin", "owner")]
    assert ranks == sorted(ranks)
    assert roles.capabilities_for("owner") >= roles.capabilities_for("admin")
    assert roles.capabilities_for("admin") >= roles.capabilities_for("moderator")
    assert roles.capabilities_for("moderator") >= roles.capabilities_for("trusted")


def test_only_owner_manages_users_and_policy():
    for role in ("guest", "readonly", "player", "trusted", "moderator", "admin"):
        assert roles.USERS_MANAGE not in roles.capabilities_for(role)
        assert roles.POLICY_MANAGE not in roles.capabilities_for(role)
    assert roles.USERS_MANAGE in roles.capabilities_for("owner")


def test_moderator_cannot_edit_saves():
    caps = roles.capabilities_for("moderator")
    assert roles.SAVE_SORT_STACKABLES not in caps
    assert roles.SAVE_EDIT_FULL not in caps
    assert roles.SERVER_CONTROL in caps


def test_security_level_can_withhold_a_granted_capability():
    """
    Both gates must agree. An Owner on a read-only server still cannot write —
    that dial protects the world from mistakes, not from untrusted people.
    """
    granted = roles.effective_capabilities("owner", policy_allowed=set())
    assert roles.SAVE_SORT_ALL not in granted
    assert roles.SETTINGS_WRITE not in granted
    # Reads and account management are unaffected by the security level.
    assert roles.VIEW_DETAIL in granted
    assert roles.USERS_MANAGE in granted


def test_security_level_cannot_grant_what_the_role_lacks():
    granted = roles.effective_capabilities(
        "player", policy_allowed={roles.SAVE_EDIT_FULL, roles.SETTINGS_WRITE}
    )
    assert roles.SAVE_EDIT_FULL not in granted
    assert roles.SETTINGS_WRITE not in granted


def test_nobody_may_grant_a_role_above_their_own():
    assert roles.can_manage("owner", "owner")
    assert roles.can_manage("owner", "admin")
    assert not roles.can_manage("admin", "owner")
    assert not roles.can_manage("moderator", "admin")
    # Lacking users.manage is disqualifying regardless of rank.
    assert not roles.can_manage("admin", "player")
