"""
`baseVisibility` — the server-wide rule about seeing other guilds' bases.

This exists because per-player privacy could not cover the common case. Privacy
is a **choice a player makes**, and `privacy.all_settings()` reads the `users`
table — so a player who has never signed into the dashboard has no row and
nothing hides them, however private the default mode is. On a normal server most
players never sign in, so "bases are private by default" was true of accounts
and false of the server.

The tests that matter are therefore: it applies without anyone having an
account, it never applies to staff, and all three base endpoints agree about it.
That last one is the same trap `test_api_base_privacy.py` was written for —
dropping a base from the marker list while still returning the objects standing
inside it publishes the coordinates while looking like it concealed them.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import accounts
import main
import policy as policy_module
import privacy
import savecache

PASSWORD = "correct-horse-battery-staple"

ALICE_UID = "aaaaaaaa-0000-0000-0000-000000000001"
STRANGER_UID = "bbbbbbbb-0000-0000-0000-000000000002"

BASE_MINE = "base-mine"
BASE_THEIRS = "base-theirs"

BASES = [
    {"id": BASE_MINE, "name": "Home", "guildId": "guild-mine", "guildName": "Mine",
     "x": 100.0, "y": 200.0},
    {"id": BASE_THEIRS, "name": "Theirs", "guildId": "guild-theirs", "guildName": "Theirs",
     "x": 300.0, "y": 400.0},
]

# Neither guild master has a dashboard account — the normal case, and the one
# per-player privacy cannot cover.
GUILDS = [
    {"id": "guild-mine", "name": "Mine", "adminPlayerUid": ALICE_UID,
     "members": [{"uid": ALICE_UID, "name": "Alice"}]},
    {"id": "guild-theirs", "name": "Theirs", "adminPlayerUid": STRANGER_UID,
     "members": [{"uid": STRANGER_UID, "name": "Stranger"}]},
]

MAP_OBJECTS = [
    {"id": "palbox-mine", "objectId": "PalBox", "baseCampId": BASE_MINE,
     "category": "palbox", "x": 100.0, "y": 200.0, "worldPlaced": False},
    {"id": "chest-theirs", "objectId": "ItemChest_02", "baseCampId": BASE_THEIRS,
     "category": "storage", "x": 300.0, "y": 400.0, "worldPlaced": False},
    {"id": "world-chest", "objectId": "TreasureBox", "baseCampId": "",
     "category": "treasure", "x": 900.0, "y": 900.0, "worldPlaced": True},
]

BASE_STORAGE = [
    {"baseId": BASE_MINE, "baseName": "Home", "containers": 2, "items": 40},
    {"baseId": BASE_THEIRS, "baseName": "Theirs", "containers": 3, "items": 90},
]


@pytest.fixture
def client(fresh_db, tmp_path, monkeypatch):
    monkeypatch.setattr(policy_module, "POLICY_FILE", str(tmp_path / "policy.json"))
    monkeypatch.setenv("SECURITY_LEVEL", "full")
    policy_module._cache = None
    monkeypatch.setattr(
        savecache, "get_section",
        lambda name: {
            "bases": BASES, "guilds": GUILDS,
            "mapObjects": MAP_OBJECTS, "baseStorage": BASE_STORAGE,
        }.get(name, []),
    )
    import viewcache
    viewcache.clear()
    return TestClient(main.app)


def sign_in(client: TestClient, username: str) -> dict:
    res = client.post("/api/auth/login",
                      json={"username": username, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()


def auth(session: dict) -> dict:
    return {"X-Session-Token": session["token"]}


def base_ids(client, session) -> set[str]:
    res = client.get("/api/bases", headers=auth(session))
    assert res.status_code == 200, res.text
    return {b["id"] for b in res.json()}


def set_level(client, session, level: str) -> None:
    res = client.post("/api/policy", json={"baseVisibility": level},
                      headers=auth(session))
    assert res.status_code == 200, res.text


@pytest.fixture
def alice(client):
    """A Trusted player in `guild-mine`. Trusted, because that is the lowest
    role that can read base storage — one of the three endpoints under test."""
    accounts.create_user("alice", PASSWORD, role="trusted", steam_uid=ALICE_UID)
    privacy.set_mode("alice", "off")
    return sign_in(client, "alice")


@pytest.fixture
def moderator(client):
    accounts.create_user("mod", PASSWORD, role="moderator")
    return sign_in(client, "mod")


@pytest.fixture
def owner(client):
    accounts.create_user("owner", PASSWORD, role="owner")
    return sign_in(client, "owner")


# ─── The default ──────────────────────────────────────────


def test_defaults_to_own_guild_only(client, alice):
    """
    The point of the whole feature. Neither guild master has an account, so
    per-player privacy protects nobody here.
    """
    assert base_ids(client, alice) == {BASE_MINE}


def test_it_works_without_anyone_having_an_account(client, alice):
    """Explicitly: no privacy row exists for the stranger, and it still applies."""
    assert privacy.all_settings() == [] or all(
        s["steamUid"] != STRANGER_UID for s in privacy.all_settings()
    )
    assert BASE_THEIRS not in base_ids(client, alice)


# ─── Staff are exempt ─────────────────────────────────────


def test_staff_see_every_base(client, moderator):
    """
    Same rule per-player privacy follows: nobody hides from staff, so moderation
    needs no exemption list. An operator who cannot see the bases they are
    responsible for has misread the switch.
    """
    assert base_ids(client, moderator) == {BASE_MINE, BASE_THEIRS}


def test_an_owner_sees_every_base(client, owner):
    assert base_ids(client, owner) == {BASE_MINE, BASE_THEIRS}


# ─── The levels ───────────────────────────────────────────


def test_everyone_opens_it_up(client, alice, owner):
    set_level(client, owner, "everyone")
    assert base_ids(client, alice) == {BASE_MINE, BASE_THEIRS}


def test_a_role_threshold_lets_that_rank_and_above_through(client, owner):
    accounts.create_user("plain", PASSWORD, role="trusted", steam_uid=ALICE_UID)
    privacy.set_mode("plain", "off")
    set_level(client, owner, "trusted")
    assert base_ids(client, sign_in(client, "plain")) == {BASE_MINE, BASE_THEIRS}


def test_below_the_threshold_is_still_own_guild_only(client, owner):
    accounts.create_user("plain", PASSWORD, role="trusted", steam_uid=ALICE_UID)
    privacy.set_mode("plain", "off")
    # A threshold above Trusted: they no longer clear it.
    set_level(client, owner, "moderator")
    assert base_ids(client, sign_in(client, "plain")) == {BASE_MINE}


def test_an_unknown_level_is_refused(client, owner):
    res = client.post("/api/policy", json={"baseVisibility": "sometimes"},
                      headers=auth(owner))
    assert res.status_code == 400
    assert "sometimes" in res.text


# ─── An account with no character ─────────────────────────


def test_an_unlinked_account_sees_no_guild_bases(client):
    """
    "Only your own guild's" is an empty set when you have no guild. That is the
    setting working, not a bug — and it is why the default is a policy an
    operator can change rather than a hardcoded rule.
    """
    accounts.create_user("nochar", PASSWORD, role="trusted")
    privacy.set_mode("nochar", "off")
    assert base_ids(client, sign_in(client, "nochar")) == set()


# ─── All three endpoints must agree ───────────────────────


def test_the_objects_inside_a_withheld_base_are_withheld_too(client, alice):
    """
    The trap this whole area exists for: map objects carry coordinates, so
    returning them while dropping the base marker publishes the location while
    looking like it concealed it.
    """
    res = client.get("/api/mapobjects", headers=auth(alice))
    assert res.status_code == 200, res.text
    ids = {o["id"] for o in res.json()}
    assert "chest-theirs" not in ids
    assert "palbox-mine" in ids
    # A world-placed object belongs to no base and must not be swept up.
    assert "world-chest" in ids


def test_the_storage_of_a_withheld_base_is_withheld_too(client, alice):
    res = client.get("/api/bases/storage", headers=auth(alice))
    assert res.status_code == 200, res.text
    ids = {s["baseId"] for s in res.json()}
    assert ids == {BASE_MINE}


def test_a_withheld_base_cannot_be_fetched_directly(client, alice):
    res = client.get(f"/api/bases/{BASE_THEIRS}/storage", headers=auth(alice))
    assert res.status_code in (403, 404), res.text
