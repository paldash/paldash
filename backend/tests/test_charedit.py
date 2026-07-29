"""
Pal editor (Phase 7).

The interesting failure here is not a crash — it is a write that serialises
fine, loads fine, and silently did nothing because it went in at the wrong
nesting depth. Most of these tests are about that.
"""

from __future__ import annotations

import pytest

import charedit


def byte_prop(value):
    """Level and Talent_* are ByteProperty: one level deeper than Int."""
    return {"value": {"type": "None", "value": value}}


def int_prop(value):
    return {"value": value}


def pal_object(level=10, exp=None, rank=1, hp=50, shot=60, defense=70, nickname="Fluffy"):
    import gamedata

    if exp is None:
        exp = int(gamedata.load()["palExpTable"][str(level)]["PalTotalEXP"])

    return {
        "NickName": int_prop(nickname),
        "Level": byte_prop(level),
        "Exp": int_prop(exp),
        "Rank": byte_prop(rank),
        "Talent_HP": byte_prop(hp),
        "Talent_Shot": byte_prop(shot),
        "Talent_Defense": byte_prop(defense),
    }


# ─── Reading ─────────────────────────────────────────────────────


def test_reads_both_property_shapes():
    obj = pal_object(level=25, rank=3, hp=90)
    view = charedit.read_pal(obj)

    assert view["level"] == 25       # ByteProperty, nested
    assert view["rank"] == 3
    assert view["ivs"]["hp"] == 90
    assert view["nickname"] == "Fluffy"


def test_absent_ivs_are_simply_absent():
    obj = pal_object()
    del obj["Talent_Shot"]
    assert "shot" not in charedit.read_pal(obj)["ivs"]


# ─── Writing into the right shape ────────────────────────────────


def test_writing_a_byte_property_goes_one_level_deeper():
    """
    The bug this guards: writing to `node['value']` on a ByteProperty replaces
    the inner dict with a bare int. It still serialises, still loads, and the
    edit is silently ignored.
    """
    obj = pal_object(level=10)
    charedit._write_property(obj, "Level", 42)

    assert obj["Level"] == {"value": {"type": "None", "value": 42}}
    assert charedit.read_pal(obj)["level"] == 42


def test_writing_an_int_property_stays_at_the_top_level():
    obj = pal_object()
    charedit._write_property(obj, "Exp", 999)

    assert obj["Exp"] == {"value": 999}


def test_writing_an_absent_property_is_refused():
    """Inventing a property means guessing its type tag."""
    obj = pal_object()
    del obj["Talent_HP"]

    with pytest.raises(charedit.EditError, match="no 'Talent_HP' stored"):
        charedit._write_property(obj, "Talent_HP", 100)


# ─── Planning ────────────────────────────────────────────────────


def test_a_valid_edit_plans_cleanly():
    obj = pal_object(rank=1, hp=50)
    plan = charedit.plan_pal_edit(obj, {"rank": 4, "ivs.hp": 95})

    assert plan["ok"], plan["problems"]
    assert plan["fieldsChanged"] == 2
    assert plan["crossFieldChecked"] is True
    assert plan["planHash"]


def test_unchanged_fields_are_not_in_the_plan():
    obj = pal_object(rank=3)
    plan = charedit.plan_pal_edit(obj, {"rank": 3})

    assert plan["ok"]
    assert plan["fieldsChanged"] == 0


def test_out_of_range_values_are_refused():
    obj = pal_object()
    assert not charedit.plan_pal_edit(obj, {"rank": 9})["ok"]
    assert not charedit.plan_pal_edit(obj, {"ivs.hp": 500})["ok"]
    assert not charedit.plan_pal_edit(obj, {"level": 81})["ok"]


def test_exp_beyond_the_new_level_is_refused():
    """
    The cross-field rule, reached through the editor rather than directly.

    Only the upper half fires: EXP past a level's band makes the game level the
    Pal up on load, so the operator gets a level they did not ask for. Low EXP
    is what a freshly caught Pal has and is left alone — see
    `editschema._check_exp_matches_level`.
    """
    import gamedata

    obj = pal_object(level=1, exp=0)
    beyond = int(gamedata.load()["palExpTable"]["51"]["PalTotalEXP"])
    plan = charedit.plan_pal_edit(obj, {"level": 50, "exp": beyond})

    assert not plan["ok"]
    assert any("beyond level 50" in p["problem"] for p in plan["problems"])


def test_a_level_change_alone_is_allowed():
    """Raising a level without touching EXP leaves the Pal below its band, which
    is a state the game itself produces and keeps."""
    assert charedit.plan_pal_edit(pal_object(level=1, exp=0), {"level": 50})["ok"]


def test_level_with_matching_exp_is_accepted():
    import gamedata

    obj = pal_object(level=1, exp=0)
    target = int(gamedata.load()["palExpTable"]["50"]["PalTotalEXP"])
    plan = charedit.plan_pal_edit(obj, {"level": 50, "exp": target})

    assert plan["ok"], plan["problems"]


@pytest.mark.parametrize("field", charedit.PAL_READ_ONLY)
def test_identity_fields_are_refused(field):
    """Species, gender and passives change what the Pal *is*."""
    plan = charedit.plan_pal_edit(pal_object(), {field: "anything"})

    assert not plan["ok"]
    assert plan["changes"] == []


def test_melee_iv_is_not_writable():
    """It is not a 1.0 field; the property map must not carry it."""
    assert "ivs.melee" not in charedit.PAL_PROPERTY_MAP
    assert not charedit.plan_pal_edit(pal_object(), {"ivs.melee": 50})["ok"]


def test_unknown_fields_are_refused():
    assert not charedit.plan_pal_edit(pal_object(), {"isShiny": True})["ok"]


def test_the_plan_hash_tracks_the_effect():
    obj = pal_object(rank=1)
    first = charedit.plan_pal_edit(obj, {"rank": 4})
    same = charedit.plan_pal_edit(obj, {"rank": 4})
    other = charedit.plan_pal_edit(obj, {"rank": 5})

    assert first["planHash"] == same["planHash"]
    assert first["planHash"] != other["planHash"]


def test_the_plan_shows_before_and_after():
    obj = pal_object(rank=1)
    change = charedit.plan_pal_edit(obj, {"rank": 4})["changes"][0]

    assert change["before"] == 1
    assert change["after"] == 4
    assert change["label"] == "Condenser rank"


def test_applying_a_plan_by_hand_round_trips():
    """Plan, write, read back — the whole loop without touching a save file."""
    obj = pal_object(level=10, rank=1, hp=50)
    plan = charedit.plan_pal_edit(obj, {"rank": 5, "ivs.hp": 100})

    for change in plan["changes"]:
        charedit._write_property(obj, charedit.PAL_PROPERTY_MAP[change["field"]], change["after"])

    view = charedit.read_pal(obj)
    assert view["rank"] == 5
    assert view["ivs"]["hp"] == 100
    assert view["level"] == 10, "an unrelated field was disturbed"


# ─── Player editing ──────────────────────────────────────────────


def player_character(level=30, exp=None, nickname="Nirb"):
    import gamedata

    if exp is None:
        exp = int(gamedata.load()["palExpTable"][str(level)]["TotalEXP"])
    return {
        "IsPlayer": int_prop(True),
        "NickName": int_prop(nickname),
        "Level": byte_prop(level),
        "Exp": int_prop(exp),
    }


def player_save(tech=120, ancient=13, with_tech=True):
    save = {"bossTechnologyPoint": int_prop(ancient)}
    if with_tech:
        save["TechnologyPoint"] = int_prop(tech)
    return save


def test_player_view_merges_both_files():
    view = charedit.read_player(player_character(level=30), player_save(tech=120, ancient=13))

    assert view["level"] == 30
    assert view["nickname"] == "Nirb"
    assert view["technologyPoints"] == 120
    assert view["ancientTechnologyPoints"] == 13


def test_an_absent_technology_property_is_absent_not_zero():
    """
    "Nought unspent points" and "this save has never had that property" are
    different, and only one of them can be written. One of the five players in
    the reference world has no `TechnologyPoint` at all.
    """
    view = charedit.read_player(player_character(), player_save(with_tech=False))

    assert "technologyPoints" not in view
    assert view["ancientTechnologyPoints"] == 13


def test_editing_an_absent_property_is_refused_before_any_write():
    plan = charedit.plan_player_edit(
        player_character(), {"technologyPoints": 500}, player_save(with_tech=False)
    )

    assert not plan["ok"]
    assert "no 'TechnologyPoint' stored" in plan["problems"][0]["problem"]
    assert plan["changes"] == []


def test_a_valid_player_edit_plans_cleanly():
    plan = charedit.plan_player_edit(
        player_character(level=30), {"technologyPoints": 300}, player_save()
    )

    assert plan["ok"], plan["problems"]
    assert plan["fieldsChanged"] == 1
    assert plan["touchesPlayerSave"] is True
    assert plan["touchesLevelSav"] is False


def test_the_plan_says_which_files_it_would_touch():
    """A player edit can span two files; the UI should be able to say so."""
    both = charedit.plan_player_edit(
        player_character(level=30),
        {"nickname": "Renamed", "technologyPoints": 300},
        player_save(),
    )

    assert both["touchesLevelSav"] is True
    assert both["touchesPlayerSave"] is True


def test_player_technology_points_are_capped_at_what_exists():
    ok = charedit.plan_player_edit(player_character(), {"technologyPoints": 1413}, player_save())
    over = charedit.plan_player_edit(player_character(), {"technologyPoints": 1414}, player_save())

    assert ok["ok"]
    assert not over["ok"]


def test_player_exp_uses_the_player_curve():
    """
    Players and Pals have different EXP curves. Using the Pal one here would
    reject valid edits and accept invalid ones.
    """
    import gamedata

    level = 40
    table = gamedata.load()["palExpTable"]
    player_exp = int(table[str(level)]["TotalEXP"])

    assert charedit.plan_player_edit(
        player_character(level=1, exp=0), {"level": level, "exp": player_exp}, player_save()
    )["ok"]

    # The curves diverge, so the *next* player level's EXP is past this level's
    # band — and would be accepted if the Pal curve were used by mistake.
    beyond_player = int(table[str(level + 1)]["TotalEXP"])
    assert beyond_player > int(table[str(level + 1)]["PalTotalEXP"])
    assert not charedit.plan_player_edit(
        player_character(level=1, exp=0), {"level": level, "exp": beyond_player}, player_save()
    )["ok"]


def test_player_level_respects_the_cap():
    assert not charedit.plan_player_edit(player_character(), {"level": 81}, player_save())["ok"]


def test_unknown_player_fields_are_refused():
    plan = charedit.plan_player_edit(player_character(), {"godMode": True}, player_save())

    assert not plan["ok"]
    assert "not a writable player field" in plan["problems"][0]["problem"]


def test_pal_only_fields_are_not_writable_on_a_player():
    for field in ("rank", "ivs.hp"):
        assert not charedit.plan_player_edit(
            player_character(), {field: 5}, player_save()
        )["ok"]


def test_writing_player_fields_hits_the_right_shapes():
    char = player_character(level=30)
    save = player_save(tech=120)

    charedit._write_property(char, "Level", 55)
    charedit._write_property(save, "TechnologyPoint", 700)

    assert char["Level"] == {"value": {"type": "None", "value": 55}}   # ByteProperty
    assert save["TechnologyPoint"] == {"value": 700}                   # IntProperty

    view = charedit.read_player(char, save)
    assert view["level"] == 55
    assert view["technologyPoints"] == 700


# ─── Bulk editing ────────────────────────────────────────────────
#
# The property that matters is atomicity. A batch that half-applies leaves no
# record of where it stopped, which is worse than one that refuses outright.


def subjects(*specs):
    """[(instance_id, object, changes), ...] from (id, kwargs, changes) triples."""
    return [(i, pal_object(**kw), changes) for i, kw, changes in specs]


def test_bulk_plans_every_pal():
    plan = charedit.plan_pal_batch(subjects(
        ("a", {"level": 10}, {"rank": 3}),
        ("b", {"level": 20, "rank": 2}, {"rank": 3}),
    ))

    assert plan["ok"], plan["problems"]
    assert plan["palsChanged"] == 2
    assert plan["fieldsChanged"] == 2
    assert plan["planHash"]


def test_bulk_separates_unchanged_from_failed():
    """
    A Pal already at the target value is not an error — any real selection will
    contain some — but it must not be counted as changed either.
    """
    plan = charedit.plan_pal_batch(subjects(
        ("a", {"rank": 3}, {"rank": 3}),
        ("b", {"rank": 1}, {"rank": 3}),
    ))

    assert plan["ok"]
    assert plan["palsChanged"] == 1
    assert plan["palsUnchanged"] == 1
    assert plan["unchanged"] == ["a"]


def test_one_bad_pal_refuses_the_whole_batch():
    plan = charedit.plan_pal_batch(subjects(
        ("a", {}, {"rank": 3}),
        ("b", {}, {"rank": 99}),      # outside 1-5
    ))

    assert not plan["ok"]
    assert plan["pals"] == []
    assert plan["planHash"] == ""
    assert plan["problems"][0]["instanceId"] == "b"


def test_bulk_problems_name_the_pal():
    """Without the instance id, a failure in a 200-Pal batch is unactionable."""
    plan = charedit.plan_pal_batch(subjects(("only", {}, {"ivs.hp": 500})))
    assert plan["problems"][0]["instanceId"] == "only"
    assert "ivs.hp" == plan["problems"][0]["field"]


def test_empty_selection_is_refused():
    plan = charedit.plan_pal_batch([])
    assert not plan["ok"]
    assert "No Pals selected" in plan["problems"][0]["problem"]


def test_batch_size_is_capped():
    too_many = subjects(*[(str(i), {}, {"rank": 2}) for i in range(charedit.MAX_BULK + 1)])
    plan = charedit.plan_pal_batch(too_many)
    assert not plan["ok"]
    assert "exceeds" in plan["problems"][0]["problem"]


def test_plan_hash_covers_every_pal():
    """
    One Pal moving underneath the operator has to invalidate the batch. They
    approved a specific set of before/after pairs, not a filter.
    """
    a = charedit.plan_pal_batch(subjects(
        ("a", {"rank": 1}, {"rank": 3}), ("b", {"rank": 1}, {"rank": 3}),
    ))
    b = charedit.plan_pal_batch(subjects(
        ("a", {"rank": 1}, {"rank": 3}), ("b", {"rank": 2}, {"rank": 3}),
    ))
    assert a["planHash"] != b["planHash"]


# ─── auto-EXP ────────────────────────────────────────────────────


def test_auto_exp_moves_exp_to_the_new_level():
    """
    A level change without it is accepted but leaves the Pal on its old EXP, so
    the first battle it wins snaps it back down toward that level. Carrying EXP
    along is what makes a bulk level change stick.
    """
    import gamedata

    without = charedit.spread_changes(["a"], {"level": 40}, auto_exp=False)
    assert "exp" not in without["a"]

    with_exp = charedit.spread_changes(["a"], {"level": 40}, auto_exp=True)
    assert with_exp["a"]["exp"] == int(gamedata.load()["palExpTable"]["40"]["PalTotalEXP"])
    assert charedit.plan_pal_batch([("a", pal_object(level=10), with_exp["a"])])["ok"]


def test_auto_exp_never_overrides_an_explicit_value():
    import gamedata

    exact = int(gamedata.load()["palExpTable"]["40"]["PalTotalEXP"]) + 5
    spread = charedit.spread_changes(["a"], {"level": 40, "exp": exact}, auto_exp=True)
    assert spread["a"]["exp"] == exact


def test_auto_exp_does_nothing_without_a_level_change():
    spread = charedit.spread_changes(["a"], {"rank": 4}, auto_exp=True)
    assert spread["a"] == {"rank": 4}


def test_spread_gives_each_pal_its_own_dict():
    """Shared dicts would let one Pal's derived EXP leak into another's."""
    spread = charedit.spread_changes(["a", "b"], {"level": 40}, auto_exp=True)
    spread["a"]["rank"] = 5
    assert "rank" not in spread["b"]


# ─── Skill editing ───────────────────────────────────────────────
#
# Lists write a different shape from scalars: values live at
# `node["value"]["values"]`, and `array_type` must survive untouched. A
# PassiveSkillList rewritten as an EnumProperty still serialises and is wrong.


def name_array(values):
    return {"array_type": "NameProperty", "id": None,
            "value": {"values": list(values)}, "type": "ArrayProperty"}


def enum_array(values):
    return {"array_type": "EnumProperty", "id": None,
            "value": {"values": list(values)}, "type": "ArrayProperty"}


def skilled_pal(passives=("Legend",), waza=("PowerShot", "MudShot"), **kw):
    obj = pal_object(**kw)
    obj["PassiveSkillList"] = name_array(passives)
    obj["EquipWaza"] = enum_array(f"EPalWazaID::{w}" for w in waza)
    return obj


def test_reads_skills_with_the_enum_prefix_stripped():
    """
    The save stores `EPalWazaID::PowerShot`; the bundled activeSkills table is
    keyed by `PowerShot`. The API speaks the table's language.
    """
    view = charedit.read_pal(skilled_pal(waza=("PowerShot", "AirCanon")))
    assert view["activeSkills"] == ["PowerShot", "AirCanon"]
    assert view["passiveSkills"] == ["Legend"]


def test_absent_skill_lists_are_absent_from_the_view():
    obj = pal_object()
    view = charedit.read_pal(obj)
    assert "passiveSkills" not in view and "activeSkills" not in view


def test_writing_active_skills_restores_the_prefix():
    obj = skilled_pal()
    charedit._write_list_property(obj, "EquipWaza", ["IceMissile", "Thunderbolt"])

    assert obj["EquipWaza"]["value"]["values"] == [
        "EPalWazaID::IceMissile", "EPalWazaID::Thunderbolt",
    ]
    assert obj["EquipWaza"]["array_type"] == "EnumProperty", "array_type must not change"


def test_writing_an_already_prefixed_value_does_not_double_it():
    obj = skilled_pal()
    charedit._write_list_property(obj, "EquipWaza", ["EPalWazaID::IceMissile"])
    assert obj["EquipWaza"]["value"]["values"] == ["EPalWazaID::IceMissile"]


def test_passives_are_written_without_a_prefix():
    obj = skilled_pal()
    charedit._write_list_property(obj, "PassiveSkillList", ["Swift", "Runner"])

    assert obj["PassiveSkillList"]["value"]["values"] == ["Swift", "Runner"]
    assert obj["PassiveSkillList"]["array_type"] == "NameProperty"


def test_an_absent_list_property_is_refused_not_invented():
    obj = pal_object()
    with pytest.raises(charedit.EditError, match="no 'EquipWaza' stored"):
        charedit._write_list_property(obj, "EquipWaza", ["PowerShot"])


def test_mastered_waza_is_not_offered():
    """
    Absent on 1,563 of the reference world's 1,905 Pals, so it cannot be written
    without inventing the property. Equipped moves are editable; the learned pool
    is not.
    """
    assert "MasteredWaza" not in charedit.PAL_LIST_PROPERTY_MAP.values()


def test_passive_skills_are_no_longer_read_only():
    assert "passiveSkills" not in charedit.PAL_READ_ONLY
    assert set(charedit.PAL_READ_ONLY) == {"speciesId", "gender"}


# ─── Skills through the planner ──────────────────────────────────


def test_planning_a_skill_change_produces_a_diff():
    plan = charedit.plan_pal_edit(skilled_pal(), {"activeSkills": ["IceMissile"]})

    assert plan["ok"], plan["problems"]
    assert plan["changes"][0]["field"] == "activeSkills"
    assert plan["changes"][0]["before"] == ["PowerShot", "MudShot"]
    assert plan["changes"][0]["after"] == ["IceMissile"]


def test_too_many_equipped_skills_is_refused():
    """Measured: across 1,905 Pals, EquipWaza never holds more than 3."""
    plan = charedit.plan_pal_edit(
        skilled_pal(), {"activeSkills": ["PowerShot", "MudShot", "AirCanon", "IceMissile"]}
    )
    assert not plan["ok"]
    assert "at most 3" in plan["problems"][0]["problem"]


def test_unknown_active_skill_is_refused():
    plan = charedit.plan_pal_edit(skilled_pal(), {"activeSkills": ["SuperMegaCheatBeam"]})
    assert not plan["ok"]
    assert "unknown active skill" in plan["problems"][0]["problem"]


def test_duplicate_active_skills_are_refused():
    plan = charedit.plan_pal_edit(skilled_pal(), {"activeSkills": ["PowerShot", "PowerShot"]})
    assert not plan["ok"]
    assert "duplicate" in plan["problems"][0]["problem"]


def test_too_many_passives_is_refused():
    plan = charedit.plan_pal_edit(
        skilled_pal(), {"passiveSkills": ["Legend", "Swift", "Runner", "Nimble", "Lucky"]}
    )
    assert not plan["ok"]


def test_clearing_a_skill_list_is_allowed():
    plan = charedit.plan_pal_edit(skilled_pal(), {"activeSkills": []})
    assert plan["ok"], plan["problems"]
    assert plan["changes"][0]["after"] == []


def test_a_skill_edit_on_a_pal_without_the_property_is_refused_at_plan_time():
    """No pointless backup, and in a batch no discovering it 140 Pals in."""
    plan = charedit.plan_pal_edit(pal_object(), {"activeSkills": ["PowerShot"]})
    assert not plan["ok"]
    assert "no 'EquipWaza' stored" in plan["problems"][0]["problem"]


def test_skills_route_through_the_shared_change_applier():
    """
    Both the single and the batch writer go through `_apply_pal_change`. A batch
    that forgot lists would silently skip every skill edit in it.
    """
    obj = skilled_pal()
    charedit._apply_pal_change(obj, {"field": "activeSkills", "after": ["AirCanon"]})
    charedit._apply_pal_change(obj, {"field": "level", "after": 33})

    assert obj["EquipWaza"]["value"]["values"] == ["EPalWazaID::AirCanon"]
    assert obj["Level"] == {"value": {"type": "None", "value": 33}}
