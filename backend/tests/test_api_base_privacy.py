"""
Base privacy through the HTTP API.

These exist because of a leak the unit tests could not see. `/api/bases` filtered
correctly and `/api/mapobjects` did not filter at all — so a base concealed by
`guild` privacy lost its marker while its palbox, chests and benches were still
returned, carrying the same coordinates, to the same map. Hiding the label and
publishing the position is worse than not hiding anything, because the setting
reads as working.

So the assertion that matters here is agreement between endpoints, not the
behaviour of any one of them.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import accounts
import baseprivacy
import main
import policy as policy_module
import privacy
import savecache

PASSWORD = "correct-horse-battery-staple"

HIDER_UID = "aaaaaaaa-0000-0000-0000-000000000001"
PEER_UID = "cccccccc-0000-0000-0000-000000000003"

BASE_HIDDEN = "base-hidden"
BASE_OPEN = "base-open"

BASES = [
    {"id": BASE_HIDDEN, "name": "Secret", "guildId": "guild-1", "guildName": "Alpha",
     "x": 100.0, "y": 200.0},
    {"id": BASE_OPEN, "name": "Public", "guildId": "guild-2", "guildName": "Beta",
     "x": 300.0, "y": 400.0},
]

GUILDS = [
    {"id": "guild-1", "name": "Alpha", "adminPlayerUid": HIDER_UID,
     "members": [{"uid": HIDER_UID, "name": "Hider"}]},
    {"id": "guild-2", "name": "Beta", "adminPlayerUid": PEER_UID,
     "members": [{"uid": PEER_UID, "name": "Peer"}]},
]

MAP_OBJECTS = [
    {"id": "palbox", "objectId": "PalBox", "baseCampId": BASE_HIDDEN,
     "category": "palbox", "x": 100.0, "y": 200.0, "worldPlaced": False},
    {"id": "chest-in-base", "objectId": "ItemChest_02", "baseCampId": BASE_HIDDEN,
     "category": "storage", "x": 101.0, "y": 201.0, "worldPlaced": False},
    {"id": "other-base-chest", "objectId": "ItemChest_02", "baseCampId": BASE_OPEN,
     "category": "storage", "x": 300.0, "y": 400.0, "worldPlaced": False},
    {"id": "world-chest", "objectId": "TreasureBox", "baseCampId": "",
     "category": "treasure", "x": 900.0, "y": 900.0, "worldPlaced": True},
]

BASE_STORAGE = [
    {"baseId": BASE_HIDDEN, "baseName": "Secret", "containers": 4, "items": 900},
    {"baseId": BASE_OPEN, "baseName": "Public", "containers": 1, "items": 3},
]


@pytest.fixture
def client(fresh_db, tmp_path, monkeypatch):
    monkeypatch.setattr(policy_module, "POLICY_FILE", str(tmp_path / "policy.json"))
    monkeypatch.setenv("SECURITY_LEVEL", "full")
    # `baseVisibility` defaults to `own`, which would hide other guilds' bases
    # before per-base privacy ever came into it. These tests are about the
    # per-base switch, so the server-wide rule is opened up to isolate it —
    # the same reason player privacy is switched off in the `cast` fixture.
    monkeypatch.setenv("BASE_VISIBILITY", "everyone")
    policy_module._cache = None
    monkeypatch.setattr(
        savecache, "get_section",
        lambda name: {
            "bases": BASES, "guilds": GUILDS,
            "mapObjects": MAP_OBJECTS, "baseStorage": BASE_STORAGE,
        }.get(name, []),
    )
    # The named-object view is memoised on the parse generation, which never moves
    # in a test, so it would serve one test's world to the next.
    import viewcache
    viewcache.clear()
    return TestClient(main.app)


def sign_in(client: TestClient, username: str) -> dict:
    res = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert res.status_code == 200, res.text
    return res.json()


def auth(session: dict) -> dict:
    return {"X-Session-Token": session["token"]}


@pytest.fixture
def cast(client):
    """
    A hider (guild master), an unrelated peer, and a moderator.

    Player-level privacy is switched **off** for both players, so these tests
    measure base privacy alone. It is not off by default — a new account starts on
    the most private mode, which already conceals its guild's bases from peers.
    `test_player_privacy_already_hides_guild_bases_by_default` covers that.
    """
    accounts.create_user("hider", PASSWORD, role="player", steam_uid=HIDER_UID)
    accounts.create_user("peer", PASSWORD, role="player", steam_uid=PEER_UID)
    accounts.create_user("mod", PASSWORD, role="moderator")
    privacy.set_mode("hider", "off")
    privacy.set_mode("peer", "off")
    return {
        "hider": sign_in(client, "hider"),
        "peer": sign_in(client, "peer"),
        "mod": sign_in(client, "mod"),
    }


def hide_it(client, cast) -> None:
    res = client.post(
        f"/api/privacy/bases/{BASE_HIDDEN}", json={"hidden": True},
        headers=auth(cast["hider"]),
    )
    assert res.status_code == 200, res.text


# ─── The three endpoints must agree ───────────────────────


def test_every_endpoint_hides_the_same_base(client, cast):
    hide_it(client, cast)
    headers = auth(cast["peer"])

    bases = client.get("/api/bases", headers=headers).json()
    assert [b["id"] for b in bases] == [BASE_OPEN]

    objects = client.get("/api/mapobjects", headers=headers).json()
    assert [o["id"] for o in objects] == ["other-base-chest", "world-chest"]

    storage = client.get("/api/bases/storage", headers=headers).json()
    assert [s["baseId"] for s in storage] == [BASE_OPEN]


def test_the_objects_inside_a_hidden_base_carry_its_coordinates(client, cast):
    """
    Spelling out why `/api/mapobjects` has to be filtered: the objects it returns
    plot at the base's position, so leaving them in draws the base without its
    label.
    """
    hide_it(client, cast)
    objects = client.get("/api/mapobjects", headers=auth(cast["peer"])).json()
    positions = {(o["x"], o["y"]) for o in objects}
    assert (100.0, 200.0) not in positions


def test_a_category_filter_does_not_bypass_the_privacy_filter(client, cast):
    """
    `?category=` narrows a cached list. A filter applied before the privacy one
    would happily return the hidden base's palbox to anyone who asked for palboxes.
    """
    hide_it(client, cast)
    objects = client.get(
        "/api/mapobjects?category=palbox", headers=auth(cast["peer"])
    ).json()
    assert objects == []


def test_one_bases_storage_reads_as_absent_rather_than_forbidden(client, cast):
    """
    404, not 403. "You may not see this base" confirms the base exists, which is
    the one thing a hidden base must not say.
    """
    hide_it(client, cast)
    res = client.get(
        f"/api/bases/{BASE_HIDDEN}/storage", headers=auth(cast["peer"])
    )
    assert res.status_code == 404


# ─── Who still sees it ───────────────────────────────────


def test_staff_see_the_hidden_base_everywhere(client, cast):
    hide_it(client, cast)
    headers = auth(cast["mod"])
    assert len(client.get("/api/bases", headers=headers).json()) == 2
    assert len(client.get("/api/mapobjects", headers=headers).json()) == 4
    assert len(client.get("/api/bases/storage", headers=headers).json()) == 2
    assert client.get(
        f"/api/bases/{BASE_HIDDEN}/storage", headers=headers
    ).status_code == 200


def test_the_owner_still_sees_their_own_hidden_base(client, cast):
    hide_it(client, cast)
    headers = auth(cast["hider"])
    assert len(client.get("/api/bases", headers=headers).json()) == 2
    assert len(client.get("/api/mapobjects", headers=headers).json()) == 4


def test_nothing_is_hidden_until_someone_hides_it(client, cast):
    headers = auth(cast["peer"])
    assert len(client.get("/api/bases", headers=headers).json()) == 2
    assert len(client.get("/api/mapobjects", headers=headers).json()) == 4


def test_player_privacy_already_hides_guild_bases_by_default(client):
    """
    The two systems compose, and the *player* one is on out of the box.

    A fresh account starts on the most private mode, which conceals its guild's
    bases from peers before anyone touches a per-base setting. Worth pinning
    because it is why the fixture above turns it off: without that, these tests
    would pass while proving nothing about base privacy.

    It also confirms the object filter covers this path too. That was the leak —
    `/api/bases` honoured player privacy and `/api/mapobjects` did not.
    """
    accounts.create_user("fresh", PASSWORD, role="player", steam_uid=HIDER_UID)
    accounts.create_user("other", PASSWORD, role="player", steam_uid=PEER_UID)
    privacy.set_mode("other", "off")
    headers = auth(sign_in(client, "other"))

    bases = client.get("/api/bases", headers=headers).json()
    assert [b["id"] for b in bases] == [BASE_OPEN]

    objects = client.get("/api/mapobjects", headers=headers).json()
    assert [o["id"] for o in objects] == ["other-base-chest", "world-chest"]


# ─── Authorisation ───────────────────────────────────────


def test_a_peer_cannot_hide_someone_elses_base(client, cast):
    res = client.post(
        f"/api/privacy/bases/{BASE_HIDDEN}", json={"hidden": True},
        headers=auth(cast["peer"]),
    )
    assert res.status_code == 403
    assert "guild" in res.json()["detail"]


def test_staff_cannot_hide_someone_elses_base_either(client, cast):
    """
    No override, on purpose. A Moderator already sees every hidden base, so the
    only thing an override would let them do is change other people's settings.
    """
    res = client.post(
        f"/api/privacy/bases/{BASE_HIDDEN}", json={"hidden": True},
        headers=auth(cast["mod"]),
    )
    assert res.status_code == 403


def test_hiding_is_audited(client, cast):
    import db

    hide_it(client, cast)
    rows = db.connect().execute(
        "SELECT * FROM audit_log WHERE target LIKE 'base_privacy:%'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["username"] == "hider"
    assert "guild master" in rows[0]["detail"]


def test_unhiding_restores_it(client, cast):
    hide_it(client, cast)
    res = client.post(
        f"/api/privacy/bases/{BASE_HIDDEN}", json={"hidden": False},
        headers=auth(cast["hider"]),
    )
    assert res.status_code == 200
    assert len(client.get("/api/bases", headers=auth(cast["peer"])).json()) == 2


def test_the_manageable_list_is_scoped_to_your_own_guild(client, cast):
    body = client.get("/api/privacy/bases", headers=auth(cast["hider"])).json()
    assert [b["baseId"] for b in body["bases"]] == [BASE_HIDDEN]


def test_a_guest_cannot_reach_the_setting(client, cast):
    assert baseprivacy.hidden_base_ids("guest", "") == set()
    res = client.post(f"/api/privacy/bases/{BASE_HIDDEN}", json={"hidden": True})
    assert res.status_code in (401, 403)
