"""
Filters that were applied in one place and not the other.

Each test here is a bug that shipped, and they share one shape: **a rule enforced
on one endpoint while a second endpoint served the same data unfiltered.** That
is not a weaker filter, it is no filter — the client reads whichever endpoint
answers.

  * `/api/world/discoveries` hid undiscovered locations; `/api/world/fasttravel`
    returned all 174 to anyone, and the map falls back to it.
  * `/api/bases` withheld other guilds' bases; `/api/guilds` went on naming those
    guilds and counting their bases.
  * `/api/players` hid a player; `/api/players/<uid>` handed them over.
  * `/api/bases/storage` withheld a base's contents; `/api/inventory/<id>`
    returned any container by id.

The fifth is not a second endpoint but a second dialect: `get_discoveries`
compared a dash-stripped account uid against the save's dashed one, so a Player's
own discoveries never matched and — with the default policy withholding the
undiscovered half — **every fast-travel point vanished from their map**.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import accounts
import main
import policy as policy_module
import privacy
import savecache
import viewcache

PASSWORD = "correct-horse-battery-staple"

# Dashed, as `Level.sav` stores it. `accounts` stores the stripped form, and the
# gap between those two spellings is what this file mostly exists for.
ALICE_UID = "aaaaaaaa-0000-0000-0000-000000000001"
STRANGER_UID = "bbbbbbbb-0000-0000-0000-000000000002"

GUILDS = [
    {"id": "guild-mine", "name": "Mine", "adminPlayerUid": ALICE_UID,
     "baseCampIds": ["base-mine"],
     "members": [{"uid": ALICE_UID, "name": "Alice"}]},
    {"id": "guild-theirs", "name": "Theirs", "adminPlayerUid": STRANGER_UID,
     "baseCampIds": ["base-theirs"],
     "members": [{"uid": STRANGER_UID, "name": "Stranger"}]},
]

BASES = [
    {"id": "base-mine", "name": "Home", "guildId": "guild-mine",
     "guildName": "Mine", "x": 1.0, "y": 2.0},
    {"id": "base-theirs", "name": "Theirs", "guildId": "guild-theirs",
     "guildName": "Theirs", "x": 3.0, "y": 4.0},
]

BASE_STORAGE = [
    {"baseId": "base-theirs", "baseName": "Theirs", "guildId": "guild-theirs",
     "containers": [{"containerId": "secret-chest"}], "itemCount": 99},
]

# One fast-travel key the fixture player has found, spelled as the save spells it.
FOUND_KEY = "0C0AF9F34C0491BCAD80B1BF355B9A98"

PLAYERS = [
    {"uid": ALICE_UID, "name": "Alice", "level": 30,
     "progress": {"fastTravel": {"keys": [FOUND_KEY], "obtained": 1},
                  "effigies": {"keys": [], "obtained": 0}}},
    {"uid": STRANGER_UID, "name": "Stranger", "level": 12,
     "progress": {"fastTravel": {"keys": [], "obtained": 0},
                  "effigies": {"keys": [], "obtained": 0}}},
]


@pytest.fixture
def client(fresh_db, tmp_path, monkeypatch):
    monkeypatch.setattr(policy_module, "POLICY_FILE", str(tmp_path / "policy.json"))
    policy_module._cache = None
    monkeypatch.setattr(
        savecache, "get_section",
        lambda name: {
            "bases": BASES, "guilds": GUILDS, "baseStorage": BASE_STORAGE,
            "pals": [],
        }.get(name, []),
    )
    monkeypatch.setattr(
        savecache, "get_data",
        lambda: {"containers": {"secret-chest": [{"isEmpty": False, "itemId": "Gold",
                                                  "stackCount": 5}]}},
    )
    monkeypatch.setattr(main, "get_players", lambda: PLAYERS)
    viewcache.clear()
    return TestClient(main.app)


def sign_in(client, username):
    res = client.post("/api/auth/login",
                      json={"username": username, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return {"X-Session-Token": res.json()["token"]}


@pytest.fixture
def alice(client):
    accounts.create_user("alice", PASSWORD, role="player", steam_uid=ALICE_UID)
    privacy.set_mode("alice", "off")
    return sign_in(client, "alice")


@pytest.fixture
def trusted(client):
    accounts.create_user("trust", PASSWORD, role="trusted", steam_uid=ALICE_UID)
    privacy.set_mode("trust", "off")
    return sign_in(client, "trust")


@pytest.fixture
def owner(client):
    accounts.create_user("boss", PASSWORD, role="owner")
    return sign_in(client, "boss")


# ─── The uid dialect gap ──────────────────────────────────


def test_a_player_sees_their_own_discovered_fast_travel(client, alice):
    """
    The bug that emptied the map.

    `accounts` stores `steam_uid` dash-stripped; the save stores it dashed. The
    raw `==` between them matched nothing, so no player was ever "chosen", so
    nothing read as discovered — and the default `discoveryVisibility` then
    dropped every undiscovered point server-side. A Player's fast-travel layer
    came back empty with no error anywhere.
    """
    body = client.get("/api/world/discoveries", headers=alice).json()
    assert body["fastTravel"]["found"] == 1
    found = [p for p in body["fastTravel"]["points"] if p["discovered"]]
    assert len(found) == 1
    assert found[0]["key"] == FOUND_KEY


def test_a_player_still_does_not_see_undiscovered_ones(client, alice):
    """The fix must not become a policy bypass: only the *own* half changed."""
    body = client.get("/api/world/discoveries", headers=alice).json()
    assert body["showsUndiscovered"] is False
    assert all(p["discovered"] for p in body["fastTravel"]["points"])


def test_the_plain_fast_travel_list_obeys_the_same_policy(client, alice):
    """
    The second endpoint. `/api/world/fasttravel` returned all 174 unconditionally
    while `/api/world/discoveries` filtered — and the map reads this one whenever
    the other is unavailable, so the policy did nothing.
    """
    body = client.get("/api/world/fasttravel", headers=alice).json()
    assert body["filtered"] is True
    assert [p["key"] for p in body["points"]] == [FOUND_KEY]


def test_above_the_threshold_the_full_list_comes_back(client, owner):
    body = client.get("/api/world/fasttravel", headers=owner).json()
    assert body["filtered"] is False
    assert len(body["points"]) == 174


# ─── Effigies, which had no second endpoint at all ────────


def test_the_effigy_list_obeys_the_same_policy(client, alice):
    """
    `/api/world/effigies` is the fallback `/api/world/fasttravel` has always
    been, and it must arrive already filtered rather than trusting the map.

    Alice has found no effigies and sits below the threshold, so the honest
    answer is an empty list — not all 396.
    """
    body = client.get("/api/world/effigies", headers=alice).json()
    assert body["filtered"] is True
    assert body["points"] == []
    # The denominator still travels, so the UI can say "0 of 396" rather than
    # implying the world has none.
    assert body["total"] == 396


def test_an_owner_gets_every_effigy_from_the_plain_list(client, owner):
    body = client.get("/api/world/effigies", headers=owner).json()
    assert body["filtered"] is False
    assert len(body["points"]) == 396


def test_the_effigy_fallback_survives_what_discoveries_refuses(client):
    """
    Why this endpoint exists.

    `/api/world/discoveries` calls `require_user`, so a guest gets 401 and the
    map's `discoveries` goes null — taking the effigy layer with it, because it
    had no fallback the way fast travel did. Both routes are VIEW_BASIC, so a
    guest reaches this one whenever guest viewing is on, and gets whatever the
    policy allows instead of nothing at all.
    """
    assert client.get("/api/world/discoveries").status_code == 401
    res = client.get("/api/world/effigies")
    assert res.status_code == 200
    body = res.json()
    # A guest is below every threshold and is linked to no character, so the
    # filtered list is empty — the same honest "you have discovered nothing"
    # answer, rather than a broken layer.
    assert body["filtered"] is True
    assert body["points"] == []


# ─── Guilds, alongside bases ──────────────────────────────


def test_a_withheld_guilds_bases_are_hidden(client, trusted):
    ids = {b["id"] for b in client.get("/api/bases", headers=trusted).json()}
    assert ids == {"base-mine"}


def test_the_guild_itself_is_hidden_too(client, trusted):
    """
    Naming the guild, its member count and its base count is not hiding its
    bases. Neither guild master has an account, which is exactly the case
    per-player privacy cannot reach and `baseVisibility` exists for.
    """
    names = {g["name"] for g in client.get("/api/guilds", headers=trusted).json()}
    assert names == {"Mine"}


def test_opening_base_visibility_restores_the_guild_list(client, trusted, owner):
    res = client.post("/api/policy", json={"baseVisibility": "everyone"},
                      headers=owner)
    assert res.status_code == 200, res.text
    names = {g["name"] for g in client.get("/api/guilds", headers=trusted).json()}
    assert names == {"Mine", "Theirs"}


def test_staff_see_every_guild(client, owner):
    names = {g["name"] for g in client.get("/api/guilds", headers=owner).json()}
    assert names == {"Mine", "Theirs"}


# ─── Single-record ways around a list filter ──────────────


def test_a_player_cannot_fetch_another_players_record(client, alice):
    res = client.get(f"/api/players/{STRANGER_UID}", headers=alice)
    assert res.status_code == 403


def test_a_player_can_fetch_their_own(client, alice):
    res = client.get(f"/api/players/{ALICE_UID}", headers=alice)
    assert res.status_code == 200, res.text
    assert res.json()["name"] == "Alice"


def test_a_hidden_bases_container_cannot_be_fetched_by_id(client, trusted):
    """
    Container ids are not secret — `/api/bases/storage` hands them out for the
    bases you *can* see. So an unauthenticated-by-id readout of any container
    walked straight around every filter built on `_hidden_base_ids`.

    404 rather than 403: "you may not see this" confirms it exists.
    """
    res = client.get("/api/inventory/secret-chest", headers=trusted)
    assert res.status_code == 404


def test_a_guest_cannot_read_a_container_at_all(client):
    assert client.get("/api/inventory/secret-chest").status_code == 401


# ─── Progress ─────────────────────────────────────────────


def test_progress_is_scoped_to_the_caller(client, alice):
    body = client.get("/api/progress", headers=alice).json()
    assert [p["name"] for p in body["players"]] == ["Alice"]


def test_progress_denominators_still_cover_everyone(client, alice):
    """
    Deliberately *not* narrowed with the rows.

    The totals are a union over every player, so scoping them to the visible
    rows would turn "of 174" into a readout of how much the people you cannot
    see have found — leaking in the opposite direction from the fix.
    """
    scoped = client.get("/api/progress", headers=alice).json()
    everyone = client.get("/api/progress", headers=sign_in(
        client, _make_owner(client))).json()
    assert scoped["knownTotals"] == everyone["knownTotals"]


def _make_owner(client) -> str:
    accounts.create_user("boss2", PASSWORD, role="owner")
    return "boss2"


# ─── Your own guild's storage ─────────────────────────────


def test_a_player_sees_their_own_bases_storage(client, alice, monkeypatch):
    """
    The asymmetry this closes: `/api/items` gave a Player their guild's *total*
    Wood while `/api/bases/storage` withheld which of their own chests it was in
    — the same data, refused in the more useful shape. Your own base's contents
    are something you can walk up to in game.
    """
    monkeypatch.setattr(
        savecache, "get_section",
        lambda name: {
            "bases": BASES, "guilds": GUILDS, "pals": [],
            "baseStorage": [
                {"baseId": "base-mine", "baseName": "Home", "guildId": "guild-mine",
                 "containers": [{"containerId": "my-chest"}], "itemCount": 12},
                *BASE_STORAGE,
            ],
        }.get(name, []),
    )
    viewcache.clear()
    body = client.get("/api/bases/storage", headers=alice).json()
    assert [s["baseId"] for s in body] == ["base-mine"]


def test_a_player_can_open_their_own_container(client, alice, monkeypatch):
    monkeypatch.setattr(
        savecache, "get_section",
        lambda name: {
            "bases": BASES, "guilds": GUILDS, "pals": [],
            "baseStorage": [
                {"baseId": "base-mine", "guildId": "guild-mine",
                 "containers": [{"containerId": "my-chest"}], "itemCount": 12},
            ],
        }.get(name, []),
    )
    monkeypatch.setattr(
        savecache, "get_data",
        lambda: {"containers": {"my-chest": [{"isEmpty": False, "itemId": "Wood",
                                              "stackCount": 12}]}},
    )
    viewcache.clear()
    res = client.get("/api/inventory/my-chest", headers=alice)
    assert res.status_code == 200, res.text
    assert res.json()["usedSlots"] == 1


def test_a_player_still_cannot_open_another_guilds_container(client, alice):
    """
    Below `VIEW_DETAIL` the rule inverts — "must be one of mine" rather than
    "not one of the hidden ones" — so anything belonging to no base of theirs is
    refused rather than defaulting open.
    """
    assert client.get("/api/inventory/secret-chest", headers=alice).status_code == 404


def test_opening_base_visibility_does_not_hand_out_contents(client, alice, owner):
    """
    `baseVisibility` is about **locations on a map**. An inventory is a much
    larger disclosure than a map pin, so widening the map must not widen this —
    otherwise the operator's "let everyone see the map" quietly published every
    guild's chest contents too.
    """
    res = client.post("/api/policy", json={"baseVisibility": "everyone"},
                      headers=owner)
    assert res.status_code == 200, res.text
    body = client.get("/api/bases/storage", headers=alice).json()
    assert all(s["baseId"] != "base-theirs" for s in body)
    assert client.get("/api/inventory/secret-chest", headers=alice).status_code == 404


def test_trusted_still_sees_every_visible_bases_storage(client, trusted, owner):
    client.post("/api/policy", json={"baseVisibility": "everyone"}, headers=owner)
    ids = {s["baseId"] for s in client.get("/api/bases/storage", headers=trusted).json()}
    assert "base-theirs" in ids


# ─── Fast travel and effigies are separately settable ─────


def _set(client, owner, **update):
    res = client.post("/api/policy", json=update, headers=owner)
    assert res.status_code == 200, res.text
    return res.json()


def test_the_two_discovery_categories_inherit_by_default(client, owner):
    """
    A policy written before the split must behave exactly as it did. The
    override map starts empty and both categories resolve to
    `discoveryVisibility`.
    """
    body = client.get("/api/policy", headers=owner).json()
    levels = {c["id"]: c for c in body["discoveryCategories"]}
    assert set(levels) == {"fastTravel", "effigies"}
    assert all(c["inherited"] for c in levels.values())
    assert all(c["level"] == body["discoveryVisibility"] for c in levels.values())


def test_effigies_can_be_closed_while_fast_travel_stays_open(client, alice, owner):
    """
    The reason for splitting them. A fast-travel point is navigation
    infrastructure; a complete map of all 396 effigies removes the hunt. An
    operator should not have to trade one for the other.
    """
    _set(client, owner, discoveryVisibility="everyone")
    _set(client, owner, discoveryCategoryVisibility={"effigies": "nobody"})

    body = client.get("/api/world/discoveries", headers=alice).json()
    assert body["showsUndiscoveredByCategory"] == {"fastTravel": True, "effigies": False}
    # All 174 travel points, but only the effigies this player actually found.
    assert len(body["fastTravel"]["points"]) == 174
    assert all(p["discovered"] for p in body["effigies"]["points"])


def test_the_reverse_split_works_too(client, alice, owner):
    _set(client, owner, discoveryVisibility="everyone")
    _set(client, owner, discoveryCategoryVisibility={"fastTravel": "nobody"})

    body = client.get("/api/world/discoveries", headers=alice).json()
    assert body["showsUndiscoveredByCategory"] == {"fastTravel": False, "effigies": True}
    assert [p["key"] for p in body["fastTravel"]["points"]] == [FOUND_KEY]
    assert len(body["effigies"]["points"]) == 396


def test_the_plain_fast_travel_list_follows_its_own_category(client, alice, owner):
    """
    The second endpoint again. Splitting the setting is worthless if
    `/api/world/fasttravel` keeps answering to the combined one.
    """
    _set(client, owner, discoveryVisibility="nobody")
    _set(client, owner, discoveryCategoryVisibility={"fastTravel": "everyone"})

    body = client.get("/api/world/fasttravel", headers=alice).json()
    assert body["filtered"] is False
    assert len(body["points"]) == 174


def test_the_legacy_flag_is_only_true_when_both_are_open(client, alice, owner):
    """
    `showsUndiscovered` predates the split and callers still read it. It must not
    report a blanket yes when only one half is open.
    """
    _set(client, owner, discoveryVisibility="everyone")
    _set(client, owner, discoveryCategoryVisibility={"effigies": "nobody"})
    assert client.get("/api/world/discoveries", headers=alice).json()["showsUndiscovered"] is False

    _set(client, owner, discoveryCategoryVisibility={"effigies": "everyone"})
    assert client.get("/api/world/discoveries", headers=alice).json()["showsUndiscovered"] is True


def test_an_unknown_discovery_category_is_refused(client, owner):
    res = client.post("/api/policy",
                      json={"discoveryCategoryVisibility": {"treasure": "nobody"}},
                      headers=owner)
    assert res.status_code == 400
    assert "treasure" in res.text


def test_an_unknown_level_for_a_known_category_is_refused(client, owner):
    res = client.post("/api/policy",
                      json={"discoveryCategoryVisibility": {"effigies": "sometimes"}},
                      headers=owner)
    assert res.status_code == 400


# ─── Headers must not claim a scope they do not have ──────


def test_the_palbox_reports_the_scope_it_actually_covered(client, alice, owner):
    """
    The planner's owner selector read "All Pals on the server" while showing a
    Player their own palbox. The data was right and the header was a lie, which
    is worse than either alone — nothing looks broken.
    """
    assert client.get("/api/breeding/palbox", headers=alice).json()["scope"] == "own"
    assert client.get("/api/breeding/palbox", headers=owner).json()["scope"] == "server"


def test_a_player_is_told_they_cannot_scope_to_others(client, alice, owner):
    """What the UI uses to hide a control that would do nothing."""
    assert client.get("/api/breeding/palbox", headers=alice).json()["mayScopeToOthers"] is False
    assert client.get("/api/breeding/palbox", headers=owner).json()["mayScopeToOthers"] is True
