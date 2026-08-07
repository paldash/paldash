"""
The optimiser routes, through HTTP.

The unit tests pin the ranking rules; these pin the two things only the request
path can be wrong about — **scope** (a ranking is over somebody's Pals, and below
`allPalsVisibility` it must be over the caller's own) and the **no-multiplier
declaration** actually reaching the client that is about to render a number.
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


def _pal(instance, uid, species, **kw):
    base = {
        "instanceId": instance, "ownerUid": uid,
        "characterId": species, "speciesId": species,
        "nickname": "", "gender": "Male", "level": 20, "exp": 0, "rank": 1,
        "isBoss": False, "ivs": {"hp": 50, "shot": 50, "defense": 50},
        "soulRanks": {}, "passiveSkills": [], "activeSkills": [],
        "elements": [], "workSuitabilities": {}, "workRanks": None,
    }
    base.update(kw)
    return base


PALS = [
    # Ids are the game's, not the player's: Lamball is `SheepBall`, Foxparks is
    # `Kitsunebi`. A display name here yields no stats and an empty ranking.
    _pal("p1", ALICE_UID, "SheepBall", elements=["Neutral"],
         workSuitabilities={"Collection": 1}),
    _pal("p2", ALICE_UID, "Kitsunebi", elements=["Fire"],
         workSuitabilities={"EmitFlame": 2}, workRanks={"EmitFlame": 1}),
    _pal("p3", BOB_UID, "Penguin", elements=["Water", "Ice"],
         workSuitabilities={"EmitFlame": 5}),
]

GUILDS = [
    {"id": "guild-a", "name": "Alpha", "members": [{"uid": ALICE_UID, "name": "Alice"}]},
    {"id": "guild-b", "name": "Beta", "members": [{"uid": BOB_UID, "name": "Bob"}]},
]


@pytest.fixture
def client(fresh_db, tmp_path, monkeypatch):
    monkeypatch.setattr(policy_module, "POLICY_FILE", str(tmp_path / "policy.json"))
    policy_module._cache = None
    monkeypatch.setattr(
        savecache, "get_section",
        lambda name, auto=True: {"pals": PALS, "guilds": GUILDS}.get(name, []),
    )
    monkeypatch.setattr(savecache, "get_data", lambda auto=True: {"containers": {}})
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
    return sign_in(client, "alice")


@pytest.fixture
def owner(client):
    accounts.create_user("owner", PASSWORD, role="owner", steam_uid=ALICE_UID)
    return sign_in(client, "owner")


# ─── Scope ───────────────────────────────────────────────


def test_a_player_is_ranked_over_their_own_pals_only(client, alice):
    body = client.get("/api/optimise/work?work=EmitFlame", headers=alice).json()
    ids = [p["instanceId"] for p in body["rankings"][0]["pals"]]
    # Bob's Penguin has the highest Kindling level in the world and must not
    # appear — a ranking is a disclosure of whose Pals exist as much as of how
    # good they are.
    assert ids == ["p2"]


def test_the_scope_travels_with_the_answer(client, alice):
    """
    Same reason `_breeding_scope` is on every breeding route: a ranking computed
    from one palbox and shown under a server-wide heading reads as a wrong
    answer rather than as a narrower question.
    """
    body = client.get("/api/optimise/work", headers=alice).json()
    assert body["scope"] == "own"
    assert body["mayScopeToOthers"] is False
    assert body["linkedToPlayer"] is True


def test_owner_query_is_ignored_below_the_threshold(client, alice):
    body = client.get(f"/api/optimise/combat?owner={BOB_UID}", headers=alice).json()
    assert {p["instanceId"] for p in body["ranking"]} == {"p1", "p2"}
    assert body["scope"] == "own"


def test_an_owner_may_scope_to_someone_else(client, owner):
    body = client.get(f"/api/optimise/combat?owner={BOB_UID}", headers=owner).json()
    assert [p["instanceId"] for p in body["ranking"]] == ["p3"]


def test_a_guest_is_refused(client):
    assert client.get("/api/optimise/work").status_code in (401, 403)
    assert client.get("/api/optimise/combat").status_code in (401, 403)


# ─── The rankings ────────────────────────────────────────


def test_every_work_type_is_ranked_when_none_is_named(client, alice):
    body = client.get("/api/optimise/work", headers=alice).json()
    assert len(body["rankings"]) == 13
    assert len(body["workTypes"]) == 13
    # The bundled table's key is `display_name`, not `name` — reading the wrong
    # one silently labels every ranking with an internal id.
    kindling = next(r for r in body["rankings"] if r["workId"] == "EmitFlame")
    assert kindling["workName"] == "Kindling"


def test_bought_ranks_are_visible_in_the_row(client, alice):
    body = client.get("/api/optimise/work?work=EmitFlame", headers=alice).json()
    row = body["rankings"][0]["pals"][0]
    # base 1, not the 2 the fixture asks for: `/api/pals` enrichment fills
    # `workSuitabilities` from the bundled species table, which is authoritative
    # and overwrites whatever a caller supplied. Foxparks really is Kindling 1.
    # Subset, not equality — the row also carries the rank->speed curve now, and
    # a test that breaks on an addition rather than a regression teaches people
    # to edit the expectation instead of reading it.
    assert row["work"]["base"] == 1
    assert row["work"]["bought"] == 1
    assert row["work"]["level"] == 2
    # And the curve came with it: rank 2 is 70 against rank 3's 100.
    assert row["work"]["speed"] == 70


def test_an_unknown_work_type_is_a_404_not_an_empty_list(client, alice):
    """
    An empty ranking is a legitimate answer ("nobody here can do this"), so it
    must not double as "that work type does not exist".
    """
    assert client.get("/api/optimise/work?work=Nonsense", headers=alice).status_code == 404


# ─── The element declaration ─────────────────────────────


def test_the_absence_of_a_multiplier_reaches_the_client(client, alice):
    """
    The client is the one about to render a damage figure. Telling it only in a
    docstring is telling nobody.
    """
    body = client.get("/api/optimise/combat?against=Grass", headers=alice).json()
    assert body["hasMultiplier"] is False
    assert body["counters"]["hasMultiplier"] is False
    assert body["chartIsCurrent"] is True
    assert body["unknownElements"] == []


def test_a_matchup_does_not_reorder_the_ranking(client, alice):
    plain = [p["instanceId"] for p in
             client.get("/api/optimise/combat", headers=alice).json()["ranking"]]
    against = [p["instanceId"] for p in
               client.get("/api/optimise/combat?against=Grass", headers=alice).json()["ranking"]]
    assert plain == against


def test_counters_are_only_computed_when_a_target_is_given(client, alice):
    body = client.get("/api/optimise/combat", headers=alice).json()
    assert body["counters"] is None
    assert body["against"] == []
