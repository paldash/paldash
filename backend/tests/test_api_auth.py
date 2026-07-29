"""
End-to-end authorization through the HTTP API.

The unit tests prove the role model is right; these prove the endpoints actually
consult it, and that every privileged action leaves an audit trail.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import accounts
import audit
import main
import policy as policy_module

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def client(fresh_db, tmp_path, monkeypatch):
    monkeypatch.setattr(policy_module, "POLICY_FILE", str(tmp_path / "policy.json"))
    monkeypatch.setenv("SECURITY_LEVEL", "full")
    policy_module._cache = None
    return TestClient(main.app)


def sign_in(client: TestClient, username: str, password: str = PASSWORD) -> dict:
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return res.json()


def auth(token: str) -> dict:
    return {"X-Session-Token": token}


@pytest.fixture
def owner(client):
    accounts.create_user("owner1", PASSWORD, role="owner")
    return sign_in(client, "owner1")


# ─── Sign-in ─────────────────────────────────────────────────────


def test_login_returns_a_session_and_capabilities(client):
    accounts.create_user("owner1", PASSWORD, role="owner")
    body = sign_in(client, "owner1")
    assert body["token"]
    assert body["user"]["role"] == "owner"
    assert "users.manage" in body["capabilities"]


def test_login_with_wrong_password_is_401(client):
    accounts.create_user("owner1", PASSWORD, role="owner")
    res = client.post("/api/auth/login", json={"username": "owner1", "password": "nope"})
    assert res.status_code == 401


def test_repeated_failures_return_429_with_retry_after(client):
    accounts.create_user("owner1", PASSWORD, role="owner")
    for _ in range(accounts.MAX_ATTEMPTS_PER_USER):
        client.post("/api/auth/login", json={"username": "owner1", "password": "nope"})

    res = client.post("/api/auth/login", json={"username": "owner1", "password": PASSWORD})
    assert res.status_code == 429
    assert res.headers.get("Retry-After")


def test_session_endpoint_reports_guest_without_a_token(client):
    body = client.get("/api/auth/session").json()
    assert body["role"] == "guest"
    assert body["user"] is None


def test_logout_revokes_the_session(client, owner):
    token = owner["token"]
    assert client.get("/api/auth/session", headers=auth(token)).json()["user"] is not None

    client.post("/api/auth/logout", headers=auth(token))
    assert client.get("/api/auth/session", headers=auth(token)).json()["user"] is None


# ─── Capability enforcement ──────────────────────────────────────


def test_users_endpoint_requires_users_manage(client, owner):
    accounts.create_user("mod", PASSWORD, role="moderator")
    moderator = sign_in(client, "mod")

    assert client.get("/api/users", headers=auth(owner["token"])).status_code == 200
    assert client.get("/api/users", headers=auth(moderator["token"])).status_code == 403
    assert client.get("/api/users").status_code == 401


def test_audit_endpoint_requires_audit_view(client, owner):
    accounts.create_user("pleb", PASSWORD, role="player")
    player = sign_in(client, "pleb")

    assert client.get("/api/audit", headers=auth(owner["token"])).status_code == 200
    assert client.get("/api/audit", headers=auth(player["token"])).status_code == 403


def test_cannot_grant_a_role_above_your_own(client, owner):
    accounts.create_user("boss", PASSWORD, role="owner")
    accounts.create_user("adm", PASSWORD, role="admin")
    admin = sign_in(client, "adm")

    # An Administrator has no users.manage at all, so this is refused outright.
    res = client.post(
        "/api/users",
        headers=auth(admin["token"]),
        json={"username": "sneaky", "password": PASSWORD, "role": "owner"},
    )
    assert res.status_code == 403


def test_owner_can_create_and_delete_accounts(client, owner):
    res = client.post(
        "/api/users",
        headers=auth(owner["token"]),
        json={"username": "newbie", "password": PASSWORD, "role": "player"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["role"] == "player"

    assert client.delete("/api/users/newbie", headers=auth(owner["token"])).status_code == 200
    assert accounts.get_user("newbie") is None


def test_cannot_delete_your_own_account(client, owner):
    res = client.delete("/api/users/owner1", headers=auth(owner["token"]))
    assert res.status_code == 400


def test_sort_requires_the_matching_capability(client, owner, monkeypatch):
    """A Player must not be able to rewrite Level.sav."""
    accounts.create_user("pleb", PASSWORD, role="player")
    player = sign_in(client, "pleb")

    called = {"n": 0}
    monkeypatch.setattr(
        main.saveedit, "sort_containers",
        lambda **kw: called.__setitem__("n", called["n"] + 1) or {"ok": True},
    )

    res = client.post("/api/edit/sort/stackables", headers=auth(player["token"]), json={})
    assert res.status_code == 403
    assert called["n"] == 0, "the sort must not run at all"


def test_security_level_blocks_even_an_owner(client, owner, monkeypatch):
    monkeypatch.setenv("SECURITY_LEVEL", "readonly")
    policy_module._cache = None

    res = client.post("/api/edit/sort/stackables", headers=auth(owner["token"]), json={})
    assert res.status_code == 403
    assert "security level" in res.json()["detail"].lower()


def test_policy_change_requires_policy_manage(client, owner):
    accounts.create_user("adm", PASSWORD, role="admin")
    admin = sign_in(client, "adm")

    res = client.post(
        "/api/policy", headers=auth(admin["token"]),
        json={"guestVisibility": {"chests": True}},
    )
    assert res.status_code == 403

    res = client.post(
        "/api/policy", headers=auth(owner["token"]),
        json={"guestVisibility": {"chests": True}},
    )
    assert res.status_code == 200


def test_anonymous_cannot_reach_privileged_endpoints(client, owner):
    for method, path in [
        ("get", "/api/users"),
        ("get", "/api/audit"),
        ("post", "/api/policy"),
        ("post", "/api/edit/sort/stackables"),
        ("post", "/api/server/restart"),
    ]:
        res = (
            client.get(path)
            if method == "get"
            else client.post(path, json={})
        )
        assert res.status_code in (401, 403), f"{path} allowed an anonymous caller"


def test_forged_role_header_is_ignored(client, owner):
    """The backend resolves the session itself; headers assert nothing."""
    res = client.get(
        "/api/users",
        headers={"X-Actor-Role": "owner", "X-User-Role": "owner", "X-Actor-Username": "owner1"},
    )
    assert res.status_code == 401


# ─── Auditing ────────────────────────────────────────────────────


def test_successful_login_is_audited(client, owner):
    entries = audit.query(action=audit.LOGIN)["entries"]
    assert any(e["username"] == "owner1" for e in entries)


def test_failed_login_is_audited(client):
    accounts.create_user("owner1", PASSWORD, role="owner")
    client.post("/api/auth/login", json={"username": "owner1", "password": "nope"})

    entries = audit.query(action=audit.LOGIN_FAILED)["entries"]
    assert entries and entries[0]["result"] == audit.RESULT_FAILED


def test_denied_action_is_audited(client, owner):
    accounts.create_user("pleb", PASSWORD, role="player")
    player = sign_in(client, "pleb")
    client.get("/api/audit", headers=auth(player["token"]))

    denials = audit.query(action=audit.DENIED)["entries"]
    assert any(e["username"] == "pleb" and e["result"] == audit.RESULT_DENIED for e in denials)


def test_account_changes_are_audited(client, owner):
    client.post(
        "/api/users", headers=auth(owner["token"]),
        json={"username": "newbie", "password": PASSWORD, "role": "player"},
    )
    entries = audit.query(action=audit.USER_CREATE)["entries"]
    assert any(e["target"] == "newbie" and e["username"] == "owner1" for e in entries)


def test_audit_records_the_client_address(client, owner):
    client.get(
        "/api/users", headers={**auth(owner["token"]), "X-Forwarded-For": "203.0.113.7"}
    )
    accounts.create_user("x", PASSWORD, role="player")
    client.post(
        "/api/users",
        headers={**auth(owner["token"]), "X-Forwarded-For": "203.0.113.7"},
        json={"username": "traced", "password": PASSWORD, "role": "player"},
    )
    entries = audit.query(action=audit.USER_CREATE)["entries"]
    assert any(e["ip"] == "203.0.113.7" for e in entries)


def test_audit_log_has_no_delete_endpoint(client, owner):
    """Append-only in practice, not just in intent."""
    paths = {getattr(r, "path", "") for r in main.app.routes}
    assert "/api/audit" in paths
    for route in main.app.routes:
        if getattr(route, "path", "") == "/api/audit":
            assert set(getattr(route, "methods", set())) <= {"GET", "HEAD"}


def test_password_change_requires_the_current_one(client, owner):
    res = client.post(
        "/api/auth/password",
        headers=auth(owner["token"]),
        json={"currentPassword": "wrong", "newPassword": "a-brand-new-password"},
    )
    assert res.status_code == 403

    res = client.post(
        "/api/auth/password",
        headers=auth(owner["token"]),
        json={"currentPassword": PASSWORD, "newPassword": "a-brand-new-password"},
    )
    assert res.status_code == 200
    # Old session is gone; the new password works.
    assert client.get("/api/auth/session", headers=auth(owner["token"])).json()["user"] is None
    sign_in(client, "owner1", "a-brand-new-password")
