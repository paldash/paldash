"""
Scope thresholds: whose Pals and whose items a request actually covers.

Two settings, one idea. A plain Player should be able to use the breeding
planner and see their own palbox — a 960-slot box the game shows one Pal at a
time is the view that most justifies a dashboard — without that granting them
every other player's Pals. Same for item totals: useful per guild, disclosing per
server.

The rule these tests exist to pin: **below the threshold the query parameter is
ignored, not honoured.** `?owner=` is a convenience for people who already may
see everyone, never a way around the setting.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import accounts
import main
import policy as policy_module
import savecache
import viewcache

PASSWORD = "correct-horse-battery-staple"

ALICE_UID = "aaaaaaaa-0000-0000-0000-000000000001"
BOB_UID = "bbbbbbbb-0000-0000-0000-000000000002"

PALS = [
    {"instanceId": "p1", "ownerUid": ALICE_UID, "speciesId": "SheepBall",
     "nickname": "", "gender": "Male", "level": 10, "exp": 0, "rank": 1,
     "isBoss": False, "ivs": {}, "passiveSkills": [], "activeSkills": []},
    {"instanceId": "p2", "ownerUid": ALICE_UID, "speciesId": "ElecCat",
     "nickname": "", "gender": "Female", "level": 12, "exp": 0, "rank": 1,
     "isBoss": False, "ivs": {}, "passiveSkills": [], "activeSkills": []},
    {"instanceId": "p3", "ownerUid": BOB_UID, "speciesId": "ChickenPal",
     "nickname": "", "gender": "Male", "level": 8, "exp": 0, "rank": 1,
     "isBoss": False, "ivs": {}, "passiveSkills": [], "activeSkills": []},
]

GUILDS = [
    {"id": "guild-a", "name": "Alpha", "members": [{"uid": ALICE_UID, "name": "Alice"}]},
    {"id": "guild-b", "name": "Beta", "members": [{"uid": BOB_UID, "name": "Bob"}]},
]

BASE_STORAGE = [
    {"baseId": "b1", "guildId": "guild-a", "items": [{"itemId": "Wood", "count": 100}]},
    {"baseId": "b2", "guildId": "guild-b", "items": [{"itemId": "Stone", "count": 250}]},
]

WORLD_ITEMS = [{"itemId": "Wood", "count": 100}, {"itemId": "Stone", "count": 250}]


@pytest.fixture
def client(fresh_db, tmp_path, monkeypatch):
    monkeypatch.setattr(policy_module, "POLICY_FILE", str(tmp_path / "policy.json"))
    policy_module._cache = None
    monkeypatch.setattr(
        savecache, "get_section",
        lambda name: {"pals": PALS, "guilds": GUILDS, "baseStorage": BASE_STORAGE}.get(name, []),
    )
    monkeypatch.setattr(
        savecache, "get_data",
        lambda: {"items": WORLD_ITEMS, "containers": {}},
    )
    viewcache.clear()
    return TestClient(main.app)


def sign_in(client, username):
    res = client.post("/api/auth/login",
                      json={"username": username, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return {"X-Session-Token": res.json()["token"]}


@pytest.fixture
def alice_player(client):
    accounts.create_user("alice", PASSWORD, role="player", steam_uid=ALICE_UID)
    return sign_in(client, "alice")


@pytest.fixture
def alice_trusted(client):
    accounts.create_user("atrust", PASSWORD, role="trusted", steam_uid=ALICE_UID)
    return sign_in(client, "atrust")


@pytest.fixture
def owner(client):
    accounts.create_user("owner", PASSWORD, role="owner", steam_uid=ALICE_UID)
    return sign_in(client, "owner")


# ─── Pals ─────────────────────────────────────────────────


def test_a_player_sees_their_own_pals(client, alice_player):
    """The whole point: `VIEW_SELF` is enough for your own palbox."""
    res = client.get("/api/pals", headers=alice_player)
    assert res.status_code == 200, res.text
    assert {p["instanceId"] for p in res.json()} == {"p1", "p2"}


def test_a_player_cannot_ask_for_someone_elses_pals(client, alice_player):
    """The query parameter is ignored below the threshold, not honoured."""
    res = client.get(f"/api/pals?owner={BOB_UID}", headers=alice_player)
    assert res.status_code == 200, res.text
    assert {p["instanceId"] for p in res.json()} == {"p1", "p2"}


def test_above_the_threshold_every_pal_is_visible(client, alice_trusted):
    res = client.get("/api/pals", headers=alice_trusted)
    assert {p["instanceId"] for p in res.json()} == {"p1", "p2", "p3"}


def test_above_the_threshold_owner_narrows_as_asked(client, alice_trusted):
    res = client.get(f"/api/pals?owner={BOB_UID}", headers=alice_trusted)
    assert {p["instanceId"] for p in res.json()} == {"p3"}


def test_raising_the_threshold_takes_it_away_again(client, alice_trusted, client_owner_token):
    """A Trusted player loses the wide view when the bar moves above them."""
    res = client.post("/api/policy", json={"allPalsVisibility": "admin"},
                      headers=client_owner_token)
    assert res.status_code == 200, res.text
    res = client.get("/api/pals", headers=alice_trusted)
    assert {p["instanceId"] for p in res.json()} == {"p1", "p2"}


@pytest.fixture
def client_owner_token(client):
    accounts.create_user("boss", PASSWORD, role="owner")
    return sign_in(client, "boss")


def test_an_unlinked_account_sees_no_pals_rather_than_all(client):
    """
    Own-scoped with no character resolves to nothing. That is the safe
    direction, and the fix is linking the account from the Players tab.
    """
    accounts.create_user("nochar", PASSWORD, role="player")
    res = client.get("/api/pals", headers=sign_in(client, "nochar"))
    assert res.status_code == 200, res.text
    assert res.json() == []


def test_breeding_is_scoped_the_same_way(client, alice_player):
    res = client.get("/api/breeding/palbox", headers=alice_player)
    assert res.status_code == 200, res.text
    species = {s["internalName"] for s in res.json()["species"]}
    assert "ChickenPal" not in species


# ─── Items ────────────────────────────────────────────────


def test_a_player_gets_their_own_guilds_totals(client, alice_player):
    res = client.get("/api/items", headers=alice_player)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["scope"] == "own"
    assert {i["itemId"] for i in body["items"]} == {"Wood"}


def test_an_owner_gets_the_server_total(client, owner):
    body = client.get("/api/items", headers=owner).json()
    assert body["scope"] == "server"
    assert {i["itemId"] for i in body["items"]} == {"Wood", "Stone"}


def test_asking_for_another_guild_is_refused_below_the_threshold(client, alice_player):
    res = client.get("/api/items?guild=guild-b", headers=alice_player)
    assert res.status_code == 403


def test_asking_for_your_own_guild_is_allowed(client, alice_player):
    body = client.get("/api/items?guild=guild-a", headers=alice_player).json()
    assert body["scope"] == "guild:guild-a"
    assert {i["itemId"] for i in body["items"]} == {"Wood"}


def test_the_scope_is_reported_rather_than_assumed(client, alice_player, owner):
    """
    A total labelled server-wide that silently was not would be worse than a
    refusal, so every response says what it counted.
    """
    assert client.get("/api/items", headers=alice_player).json()["scope"] == "own"
    assert client.get("/api/items", headers=owner).json()["scope"] == "server"


def test_scopes_endpoint_offers_only_what_the_caller_may_have(client, alice_player, owner):
    player = client.get("/api/items/scopes", headers=alice_player).json()
    assert player["serverWide"] is False
    assert [g["id"] for g in player["guilds"]] == ["guild-a"]

    boss = client.get("/api/items/scopes", headers=owner).json()
    assert boss["serverWide"] is True


def test_a_guest_cannot_read_items_at_all(client):
    assert client.get("/api/items").status_code == 401
