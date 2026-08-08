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
    # And the curve came with it — **EmitFlame's own**, which is the correction
    # of 2026-08-07. This line asserted 70, the rank-2 value of the
    # Collection/Deforest/Mining curve that was being applied to all thirteen
    # work types. Kindling is on the crafting curve, where rank 2 is 80 and rank
    # 10 is 5,400 against the other's 1,000. The old number was not a rounding
    # difference; it was another work type's answer.
    assert row["work"]["speed"] == 80
    # Against **this** work type's rank 3, which is 140 for the crafting curve
    # rather than the 100 the standard one uses. Comparable down a column and
    # meaningless across two work types, which is safe here only because this
    # endpoint ranks one at a time. The full curve is deliberately not repeated
    # on every row — `/api/optimise/curves` carries it once.
    assert row["work"]["relativeToRank3"] == round(80 / 140, 2)


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


# ─── Suitability-granting passives ───────────────────────────────


def test_farmhand_and_ranch_master_count_toward_the_level():
    """
    **Real Pal passives, and they were being dropped.** 73 Pals on the live world
    carry one — 66 Farmhand, 7 Ranch Master — every one with an empty
    `workRanks`, so this was a gap rather than a double count.
    """
    from optimise import passive_work_rank

    assert passive_work_rank(["WorkSuitabilityAddRank_MonsterFarm_1"], "MonsterFarm") == 1
    assert passive_work_rank(["WorkSuitabilityAddRank_MonsterFarm_2"], "MonsterFarm") == 2


def test_the_HANDBOOK_effects_must_never_count():
    """
    THE TRAP THIS WHOLE COMPONENT HAD TO AVOID, and it caught two people.

    Fourteen `WorkSuitabilityAddRank_*` entries look identical to the two above
    and are the effect applied by the **Applied … Handbook** items. The rank a
    handbook grants is written into `GotWorkSuitabilityAddRankList` and is
    already counted as `bought`, so counting the effect too would double it.

    They are separated by `ToBaseCampPal` / `InvokeInBaseCamp`, not by an id
    list — a list is how the next one added by an update slips through.
    """
    from optimise import passive_work_rank

    for work in ("Mining", "Handcraft", "Collection", "Watering", "Transport"):
        assert passive_work_rank([f"WorkSuitabilityAddRank_{work}"], work) == 0, (
            f"{work} handbook effect leaked into the Pal's own level"
        )


def test_a_passive_for_another_work_type_does_not_leak():
    from optimise import passive_work_rank

    assert passive_work_rank(["WorkSuitabilityAddRank_MonsterFarm_2"], "Mining") == 0


def test_an_unknown_passive_contributes_nothing_rather_than_raising():
    """A modded or newer passive should cost the term, not the ranking."""
    from optimise import passive_work_rank

    assert passive_work_rank(["Modded_Nonsense"], "Mining") == 0
    assert passive_work_rank(None, "Mining") == 0


def test_the_three_components_stay_separate_and_the_total_is_capped():
    """
    `base`, `bought` and `passive` are three different facts — the species, the
    owner's handbooks, and what the Pal was born with — and one number hides
    which. The sum clamps at `WorkSuitabilityMaxRank`.
    """
    import optimise

    row = optimise.work_level(
        {"workSuitabilities": {"MonsterFarm": 9}, "workRanks": {"MonsterFarm": 2},
         "passiveSkills": ["WorkSuitabilityAddRank_MonsterFarm_2"]},
        "MonsterFarm",
    )
    assert row["base"] == 9 and row["bought"] == 2 and row["passive"] == 2
    assert row["level"] == 10, "9 + 2 + 2 must clamp to WorkSuitabilityMaxRank"


def test_condensing_is_NOT_folded_in_while_it_is_unverified():
    """
    Believed true, unverified, and undetermined for half the roster by ties and
    fallthrough — see AGENTS.md. A third term that is wrong half the time is
    worse than a missing one, so `rank` must not move the level.
    """
    import optimise

    plain = optimise.work_level(
        {"workSuitabilities": {"Mining": 5}, "rank": 1, "passiveSkills": []}, "Mining")
    condensed = optimise.work_level(
        {"workSuitabilities": {"Mining": 5}, "rank": 5, "passiveSkills": []}, "Mining")
    assert plain["level"] == condensed["level"] == 5


def test_welfare_pals_is_the_ARRAY_not_the_scope_count(client, alice):
    """
    **This took out the My Pals tab and nothing errored server-side.**

    `_breeding_scope` returns its own `"pals"` — the COUNT of Pals the answer was
    built from — and `/api/welfare` spread it LAST, so an integer silently
    replaced the array of affected Pals. The client then ran
    `report.pals.length.toLocaleString()` on a number: `.length` is undefined and
    `.toLocaleString()` on undefined throws, killing the tab.

    It survived three rounds of fixes because every symptom pointed at the
    frontend. What identified it was a guard added along the way rendering the
    non-array as a dash — "— Pals of 874 need attention" — which is the shape of
    a wrong TYPE rather than a missing value.

    A generic key name in a helper spread into other people's payloads is the
    hazard. The scope spreads FIRST now, so an explicit key always wins.
    """
    body = client.get("/api/welfare", headers=alice).json()
    assert isinstance(body["pals"], list), (
        f"pals must be the affected-Pal array, got {type(body['pals']).__name__}"
    )
    # Spreading first must not drop the scope keys the planner header reads.
    for key in ("scope", "linkedToPlayer", "mayScopeToOthers"):
        assert key in body
    assert isinstance(body["counts"], dict)
    assert isinstance(body["scanned"], int)


def test_a_boxed_sick_pal_is_RECOVERING_not_a_problem(client, alice, monkeypatch):
    """
    Reported 2026-08-07: the panel said 53 Pals were sick, the operator checked
    them in game, and they were all fine — because they were all in a palbox.

    `WorkerSick` is `EPalBaseCampWorkerSickType`. Its own name says it is a
    base-camp worker state, and the game ships `PalBoxTimePeriodRecoverySick`
    (3,600s) as the statement that the box cures it. So a boxed Pal carrying the
    flag is not something to go and fix, which is the only question this panel
    answers.

    **Split, not dropped.** It is still true, and discarding every flag on a
    world where none of them is at a base would be its own kind of wrong.
    """
    import main

    monkeypatch.setattr(
        main.savecache, "get_section",
        lambda name, auto=True: {
            "pals": [
                _pal("s1", ALICE_UID, "SheepBall", workerSick="Sprain", location="base"),
                _pal("s2", ALICE_UID, "SheepBall", workerSick="Sprain", location="palbox"),
                _pal("s3", ALICE_UID, "SheepBall", workerSick="Sprain", location="storage"),
            ],
            "guilds": GUILDS,
        }.get(name, []),
    )
    viewcache.clear()

    body = client.get("/api/welfare", headers=alice).json()
    assert body["counts"].get("sick") == 1, "only the base worker is actionable"
    assert body["counts"].get("sickRecovering") == 2, "boxed and stored are recovering"

    problems = {p["instanceId"]: p["problems"] for p in body["pals"]}
    assert problems["s1"] == ["sick"]
    assert problems["s2"] == ["sickRecovering"]
    assert problems["s3"] == ["sickRecovering"]
