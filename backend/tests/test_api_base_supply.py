"""
The supply report through the HTTP API.

Two things these exist for, and neither is reachable from `basesupply`'s unit
tests:

1. **It must hide exactly what `/api/bases/storage` hides.** A supply report
   names per-base container contents, so a filter applied to one of two
   endpoints serving the same data is not a filter. `/api/world/fasttravel` is
   the standing example of getting this wrong, and `/api/inventory/{id}` the
   worse one.

2. **`savecache.get_section` returns `[]` for anything that is not a list**, and
   `containers` and `guildStorage` are both dicts. Reading them through it
   produces a report that looks perfectly healthy with every container empty —
   which no unit test calling `base_report` directly can catch, because it is
   handed its containers.
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

OWNER_UID = "aaaaaaaa-0000-0000-0000-000000000001"
PEER_UID = "cccccccc-0000-0000-0000-000000000003"

BASE_MINE = "base-mine"
BASE_THEIRS = "base-theirs"

BASES = [
    {"id": BASE_MINE, "name": "Mine", "guildId": "guild-1", "guildName": "Alpha",
     "palCount": 12},
    {"id": BASE_THEIRS, "name": "Theirs", "guildId": "guild-2", "guildName": "Beta",
     "palCount": 4},
]

GUILDS = [
    {"id": "guild-1", "name": "Alpha", "adminPlayerUid": OWNER_UID,
     "members": [{"uid": OWNER_UID, "name": "Owner"}]},
    {"id": "guild-2", "name": "Beta", "adminPlayerUid": PEER_UID,
     "members": [{"uid": PEER_UID, "name": "Peer"}]},
]

BASE_STORAGE = [
    {
        "baseId": BASE_MINE, "baseName": "Mine",
        "guildId": "guild-1", "guildName": "Alpha",
        "items": [{"itemId": "Wood", "itemName": "Wood", "count": 40}],
        "containers": [
            {"containerId": "feed-1", "kind": "PalFoodBox", "kindName": "Feed Box",
             "usedSlots": 0, "totalSlots": 10},
            {"containerId": "farm-1", "kind": "BreedFarm", "kindName": "Breeding Farm",
             "usedSlots": 1, "totalSlots": 1},
        ],
    },
    {
        "baseId": BASE_THEIRS, "baseName": "Theirs",
        "guildId": "guild-2", "guildName": "Beta",
        "items": [{"itemId": "Wood", "itemName": "Wood", "count": 99999}],
        "containers": [
            {"containerId": "feed-2", "kind": "PalFoodBox", "kindName": "Feed Box",
             "usedSlots": 1, "totalSlots": 10},
        ],
    },
]

CONTAINERS = {
    "feed-1": [{"isEmpty": True}] * 10,
    "farm-1": [{"itemId": "Cake", "stackCount": 4, "isEmpty": False}],
    "feed-2": [{"itemId": "Berries", "stackCount": 500, "isEmpty": False}],
    "chest-1": [{"itemId": "Stone", "stackCount": 12345, "isEmpty": False}],
    "chest-2": [{"itemId": "Coal", "stackCount": 7, "isEmpty": False}],
}

GUILD_STORAGE = {"guild-1": "chest-1", "guild-2": "chest-2"}

PALS = [
    {"baseId": BASE_MINE, "hungerType": "Hunger"},
    {"baseId": BASE_MINE, "hungerType": "Starvation"},
    {"baseId": BASE_THEIRS, "hungerType": "Hunger"},
    {"baseId": BASE_MINE, "hungerType": ""},
]

DATA = {
    "bases": BASES,
    "guilds": GUILDS,
    "baseStorage": BASE_STORAGE,
    "pals": PALS,
    "containers": CONTAINERS,
    "guildStorage": GUILD_STORAGE,
}


@pytest.fixture
def client(fresh_db, tmp_path, monkeypatch):
    monkeypatch.setattr(policy_module, "POLICY_FILE", str(tmp_path / "policy.json"))
    monkeypatch.setenv("SECURITY_LEVEL", "full")
    monkeypatch.setenv("BASE_VISIBILITY", "everyone")
    policy_module._cache = None
    monkeypatch.setattr(
        savecache, "get_section",
        lambda name, auto=True: DATA.get(name) if isinstance(DATA.get(name), list) else [],
    )
    monkeypatch.setattr(savecache, "get_data", lambda auto=True: DATA)
    import viewcache
    viewcache.clear()
    return TestClient(main.app)


def sign_in(client: TestClient, username: str) -> dict:
    res = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()


def auth(session: dict) -> dict:
    return {"X-Session-Token": session["token"]}


@pytest.fixture
def cast(client):
    accounts.create_user("owner", PASSWORD, role="player", steam_uid=OWNER_UID)
    accounts.create_user("mod", PASSWORD, role="moderator")
    privacy.set_mode("owner", "off")
    return {"owner": sign_in(client, "owner"), "mod": sign_in(client, "mod")}


# ─── Scoping ─────────────────────────────────────────────


def test_a_player_sees_only_their_own_guilds_bases(client, cast):
    res = client.get("/api/bases/supply", headers=auth(cast["owner"]))
    assert res.status_code == 200, res.text
    body = res.json()
    assert [b["baseId"] for b in body["bases"]] == [BASE_MINE]


def test_the_guild_chest_is_scoped_with_the_bases(client, cast):
    """
    The chest is a separate section, so it needs its own scoping — and getting
    that wrong would hand out another guild's entire stock beside a correctly
    filtered base list.
    """
    body = client.get("/api/bases/supply", headers=auth(cast["owner"])).json()
    assert [c["guildId"] for c in body["guildChests"]] == ["guild-1"]


def test_staff_see_every_base_and_every_chest(client, cast):
    body = client.get("/api/bases/supply", headers=auth(cast["mod"])).json()
    assert sorted(b["baseId"] for b in body["bases"]) == [BASE_MINE, BASE_THEIRS]
    assert sorted(c["guildId"] for c in body["guildChests"]) == ["guild-1", "guild-2"]


def test_it_matches_bases_storage_exactly(client, cast):
    """
    The agreement assertion. Two endpoints serving per-base container contents
    must conceal the same set, or the stricter one is decoration.
    """
    headers = auth(cast["owner"])
    storage = {s["baseId"] for s in client.get("/api/bases/storage", headers=headers).json()}
    supply = {b["baseId"] for b in client.get("/api/bases/supply", headers=headers).json()["bases"]}
    assert storage == supply


def test_a_guest_is_refused(client):
    assert client.get("/api/bases/supply").status_code in (401, 403)


# ─── The dict-section trap ───────────────────────────────


def test_container_contents_actually_arrive(client, cast):
    """
    `get_section` would have returned `[]` for `containers`, and the report would
    have come back with every box empty and nothing to say it had failed.
    """
    body = client.get("/api/bases/supply", headers=auth(cast["mod"])).json()
    chest = next(c for c in body["guildChests"] if c["guildId"] == "guild-1")
    assert chest["itemCount"] == 12345
    assert chest["usedSlots"] == 1

    theirs = next(b for b in body["bases"] if b["baseId"] == BASE_THEIRS)
    assert theirs["feedBoxes"][0]["itemCount"] == 500


# ─── The report itself ───────────────────────────────────


def test_empty_feed_box_and_hunger_are_both_reported(client, cast):
    body = client.get("/api/bases/supply", headers=auth(cast["owner"])).json()
    mine = body["bases"][0]
    kinds = {n["kind"] for n in mine["notes"]}
    assert "emptyFeedBox" in kinds
    assert "hungryPals" in kinds
    # Two of the three Pals at this base are hungry; the third is fed and the
    # fourth is at a different base.
    assert mine["hungryPals"] == 2
    assert mine["palCount"] == 12


def test_a_stocked_breeding_farm_raises_no_note(client, cast):
    body = client.get("/api/bases/supply", headers=auth(cast["owner"])).json()
    kinds = {n["kind"] for n in body["bases"][0]["notes"]}
    assert "breedingFarmNoCake" not in kinds


def test_the_floor_is_declared_as_an_operator_setting(client, cast):
    body = client.get("/api/bases/supply?floor=25", headers=auth(cast["owner"])).json()
    assert body["floor"] == 25
    assert body["floorIsOperatorSetting"] is True
    wood = next(s for s in body["bases"][0]["staples"] if s["itemId"] == "Wood")
    # 40 held against a floor of 25, and the game's own stack size beside it.
    assert wood["count"] == 40 and wood["below"] is False
    assert wood["stackSize"] == 9999


def test_no_note_anywhere_tells_anyone_to_move_anything(client, cast):
    """
    The mechanic this project cannot verify from a game file. If a note ever
    starts prescribing, that claim needs a source first.
    """
    body = client.get("/api/bases/supply", headers=auth(cast["mod"])).json()
    for base in body["bases"]:
        for note in base["notes"]:
            assert "move" not in note["text"].lower()
            assert "should" not in note["text"].lower()
