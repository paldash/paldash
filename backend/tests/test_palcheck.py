"""
Illegal-Pal detection and repair (Phase 7).

Two things are being pinned here. The first is that the scan agrees with
`editschema` rather than holding a second opinion about what Palworld allows —
if the bounds move, they move in one place. The second is the line between
detected and repairable: reporting a passive-skill problem and then silently not
fixing it would be the worst outcome, so the counts have to stay honest.
"""

from __future__ import annotations

import editschema
import gamedata
import palcheck


def legal_exp(level):
    return int(gamedata.load()["palExpTable"][str(level)]["PalTotalEXP"])


def pal(**overrides):
    base = {
        "instanceId": "pal-1",
        "characterId": "Sheepball",
        "speciesId": "Sheepball",
        "nickname": "Woolly",
        "ownerUid": "uid-1",
        "level": 20,
        "exp": legal_exp(20),
        "rank": 1,
        "ivs": {"hp": 50, "shot": 50, "defense": 50},
        "passiveSkills": [],
    }
    base.update(overrides)
    return base


# ─── A clean Pal is clean ────────────────────────────────────────


def test_a_legal_pal_is_not_flagged():
    assert palcheck.inspect_pal(pal()) == []


def test_boundary_values_are_legal():
    """0 and 100 are rollable IVs; 1 and 5 are real condenser ranks."""
    assert palcheck.inspect_pal(pal(ivs={"hp": 0, "shot": 100, "defense": 50})) == []
    assert palcheck.inspect_pal(pal(rank=editschema.MAX_RANK)) == []
    assert palcheck.inspect_pal(pal(rank=editschema.MIN_RANK)) == []


def test_the_level_cap_itself_is_legal():
    cap = editschema._max_level()
    assert palcheck.inspect_pal(pal(level=cap, exp=legal_exp(cap))) == []


# ─── Detection ───────────────────────────────────────────────────


def codes(pal_record):
    return {i["code"] for i in palcheck.inspect_pal(pal_record)}


def test_out_of_range_ivs_are_caught_and_clamped():
    issues = palcheck.inspect_pal(pal(ivs={"hp": 255, "shot": 50, "defense": 50}))
    iv_issue = next(i for i in issues if i["code"] == palcheck.IV_OUT_OF_RANGE)

    assert iv_issue["field"] == "ivs.hp"
    assert iv_issue["found"] == 255
    assert iv_issue["fix"] == editschema.MAX_IV
    assert iv_issue["repairable"]


def test_negative_iv_clamps_to_zero():
    issues = palcheck.inspect_pal(pal(ivs={"hp": -5, "shot": 50, "defense": 50}))
    assert next(i for i in issues if i["field"] == "ivs.hp")["fix"] == 0


def test_out_of_range_rank_is_caught():
    issues = palcheck.inspect_pal(pal(rank=12))
    rank_issue = next(i for i in issues if i["code"] == palcheck.RANK_OUT_OF_RANGE)
    assert rank_issue["fix"] == editschema.MAX_RANK


def test_over_cap_level_is_caught():
    cap = editschema._max_level()
    issues = palcheck.inspect_pal(pal(level=cap + 20, exp=legal_exp(cap)))
    level_issue = next(i for i in issues if i["code"] == palcheck.LEVEL_OUT_OF_RANGE)
    assert level_issue["fix"] == cap


def test_exp_is_checked_against_the_repaired_level_not_the_illegal_one():
    """
    A Pal illegal on both level and EXP has to come out of the repair coherent.
    Checking EXP against the level it *claims* would produce a fix pair the
    cross-field rule then rejects, and the batch would refuse itself.
    """
    cap = editschema._max_level()
    record = pal(level=cap + 40, exp=legal_exp(cap) * 100)
    issues = palcheck.inspect_pal(record)

    level_fix = next(i for i in issues if i["code"] == palcheck.LEVEL_OUT_OF_RANGE)["fix"]
    exp_fix = next(i for i in issues if i["code"] == palcheck.EXP_MISMATCH)["fix"]

    assert level_fix == cap

    # And the pair the repair produces has to survive validation.
    report = editschema.validate(
        "pal", {"level": level_fix, "exp": exp_fix},
        current={"level": record["level"], "exp": record["exp"]},
    )
    assert report["ok"], report["problems"]


def test_exp_beyond_the_level_is_caught():
    """
    Above the band the game levels the Pal up on load, so what it displays is
    not what it is. That direction never occurs naturally — 0 of 1,905 Pals on
    the reference world.
    """
    assert palcheck.EXP_MISMATCH in codes(pal(level=5, exp=legal_exp(60)))


def test_exp_below_the_level_is_not_flagged():
    """
    A freshly caught Pal arrives at its wild level with almost no EXP and the
    game leaves it there — 8 of the reference world's 1,905 Pals look like this.
    Flagging them would report a clean world as thoroughly cheated on.
    """
    assert palcheck.EXP_MISMATCH not in codes(pal(level=40, exp=legal_exp(5)))
    assert palcheck.inspect_pal(pal(level=11, exp=0)) == []


def test_npcs_are_not_reported_as_modded_pals():
    """
    CharacterSaveParameterMap holds humans as well as Pals. 100 of the reference
    world's 1,905 entries are guards, merchants and villagers, and a Pal-table
    lookup alone reported every one of them as modded content.
    """
    for npc in ("Police_Rifle", "Male_People03", "Hunter_Rifle", "Male_Trader01_v24"):
        assert palcheck.UNKNOWN_SPECIES not in codes(pal(speciesId=npc, characterId=npc)), npc


def test_unknown_species_is_caught_but_not_repairable():
    issues = palcheck.inspect_pal(pal(speciesId="TotallyModdedPal", characterId="TotallyModdedPal"))
    species_issue = next(i for i in issues if i["code"] == palcheck.UNKNOWN_SPECIES)
    assert not species_issue["repairable"]


def test_too_many_passives_is_caught_but_not_repairable():
    issues = palcheck.inspect_pal(pal(passiveSkills=["Legend", "Swift", "Runner", "Nimble", "Lucky"]))
    issue = next(i for i in issues if i["code"] == palcheck.TOO_MANY_PASSIVES)
    assert not issue["repairable"]


def test_duplicate_passives_are_caught():
    assert palcheck.DUPLICATE_PASSIVES in codes(pal(passiveSkills=["Swift", "Swift"]))


def test_unknown_passive_is_caught():
    assert palcheck.UNKNOWN_PASSIVE in codes(pal(passiveSkills=["Cheat_Infinite_Damage"]))


def test_melee_iv_is_ignored_rather_than_flagged():
    """
    `parser._TALENTS` still reads Talent_Melee, but 1.0 does not store it and
    the schema has no field for it. Iterating the schema's IV list rather than
    the Pal's keys is what stops a stray value from being reported as illegal —
    and from being "repaired" into a field the game never reads.
    """
    assert palcheck.inspect_pal(pal(ivs={"hp": 50, "shot": 50, "defense": 50, "melee": 999})) == []


def test_repairable_set_matches_what_can_be_written():
    """
    Only scalar fields are repairable. Passive lists are an ArrayProperty and
    `_write_property` handles scalars only; species changes what a Pal is.
    """
    assert set(palcheck.REPAIRABLE) == {
        palcheck.IV_OUT_OF_RANGE,
        palcheck.RANK_OUT_OF_RANGE,
        palcheck.LEVEL_OUT_OF_RANGE,
        palcheck.EXP_MISMATCH,
    }


# ─── Scanning ────────────────────────────────────────────────────


def test_scan_counts_and_groups():
    report = palcheck.scan(
        [
            pal(instanceId="ok"),
            pal(instanceId="bad-iv", ivs={"hp": 200, "shot": 50, "defense": 50}),
            pal(instanceId="bad-rank", ownerUid="uid-2", rank=9),
        ],
        owners={"uid-1": "Alice", "uid-2": "Bob"},
    )

    assert report["palsScanned"] == 3
    assert report["palsFlagged"] == 2
    assert report["palsRepairable"] == 2
    assert report["byCode"][palcheck.IV_OUT_OF_RANGE] == 1
    assert report["byOwner"] == {"Alice": 1, "Bob": 1}


def test_scan_records_the_bounds_it_used():
    """A report read months later must say which version's limits produced it."""
    bounds = palcheck.scan([pal()])["bounds"]
    assert bounds["maxIv"] == editschema.MAX_IV
    assert bounds["maxLevel"] == editschema._max_level()


def test_unowned_pals_are_grouped_rather_than_dropped():
    report = palcheck.scan([pal(instanceId="wild", ownerUid="", rank=9)])
    assert report["byOwner"] == {"(unowned)": 1}


def test_scan_of_an_empty_world_is_not_an_error():
    report = palcheck.scan([])
    assert report["palsScanned"] == 0 and report["pals"] == []


# ─── Repair planning ─────────────────────────────────────────────


def test_repair_plans_only_the_fixable_fields():
    report = palcheck.scan([
        pal(instanceId="mixed", rank=9, passiveSkills=["Cheat_Thing"]),
    ])
    plan = palcheck.plan_repair(report)

    assert plan["edits"] == {"mixed": {"rank": editschema.MAX_RANK}}
    assert plan["palsWithUnfixableIssues"] == 1
    assert plan["unfixable"][0]["issues"][0]["code"] == palcheck.UNKNOWN_PASSIVE


def test_a_pal_with_only_unfixable_issues_gets_no_edit():
    """An unknown passive is a real violation this build cannot write a fix for."""
    report = palcheck.scan([pal(instanceId="cheated", passiveSkills=["Cheat_Thing"])])
    plan = palcheck.plan_repair(report)

    assert plan["edits"] == {}
    assert plan["palsToRepair"] == 0
    assert plan["palsWithUnfixableIssues"] == 1


def test_an_unrecognised_species_is_an_advisory_not_a_violation():
    """
    The bundled tables are incomplete, not the world: 13 of the reference
    world's own characters are ordinary NPCs missing from them. Counting those
    as cheating would put a dozen false accusations on every clean world, so
    they are reported separately and never inflate `palsFlagged`.
    """
    report = palcheck.scan([pal(instanceId="npcish", speciesId="Nope", characterId="Nope")])

    assert report["palsFlagged"] == 0
    assert report["palsUnrecognised"] == 1
    assert report["advisories"][0]["issues"][0]["code"] == palcheck.UNKNOWN_SPECIES
    assert palcheck.plan_repair(report)["edits"] == {}


def test_a_stat_violation_still_counts_when_the_species_is_unrecognised():
    """The advisory must not suppress a real finding on the same Pal."""
    report = palcheck.scan([
        pal(instanceId="both", speciesId="Nope", characterId="Nope", rank=9)
    ])

    assert report["palsFlagged"] == 1
    assert report["palsUnrecognised"] == 1
    assert [i["code"] for i in report["pals"][0]["issues"]] == [palcheck.RANK_OUT_OF_RANGE]


def test_repair_can_be_narrowed_to_chosen_pals():
    report = palcheck.scan([
        pal(instanceId="a", rank=9),
        pal(instanceId="b", rank=9),
    ])
    plan = palcheck.plan_repair(report, instance_ids=["b"])
    assert list(plan["edits"]) == ["b"]


def test_narrowing_to_nothing_yields_nothing():
    report = palcheck.scan([pal(instanceId="a", rank=9)])
    assert palcheck.plan_repair(report, instance_ids=["not-in-the-world"])["edits"] == {}


def test_repair_changes_of_a_clean_pal_is_empty():
    assert palcheck.repair_changes({"issues": []}) == {}


def test_repaired_values_pass_the_editor_schema():
    """
    The repair is applied through `charedit.plan_pal_batch`, so every value it
    picks has to survive the same validation any manual edit does. A clamp that
    the schema then rejects would make the whole batch refuse itself.
    """
    cap = editschema._max_level()
    records = [
        pal(instanceId="a", ivs={"hp": 255, "shot": -3, "defense": 50}),
        pal(instanceId="b", rank=99),
        pal(instanceId="c", level=cap + 5, exp=legal_exp(cap) * 50),
    ]
    plan = palcheck.plan_repair(palcheck.scan(records))

    for record in records:
        changes = plan["edits"][record["instanceId"]]
        report = editschema.validate("pal", changes, current=record)
        assert report["ok"], (record["instanceId"], report["problems"])
