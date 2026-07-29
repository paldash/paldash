"""
Pal imports.

`palimport` writes nothing itself — it translates an export document into the
change set `charedit.apply_pal_batch` or `palclone.apply_clone` already takes. So
these tests are about the translation: which fields cross over, which are refused
*and said out loud*, and which documents are rejected before any writer is
reached.
"""

from __future__ import annotations

import pytest

import charedit
import editschema
import palimport
import saveexport

# ─── Fixtures shaped like the real thing ─────────────────
#
# A Pal dict here is exactly what parser.extract_players_and_pals emits, because
# that is what an export contains. Reshaping it for the test would be testing a
# format nothing produces.

PAL = {
    "instanceId": "aaaaaaaa-0000-0000-0000-000000000001",
    "ownerUid": "22b22b02-0000-0000-0000-000000000000",
    "characterId": "Sheepball",
    "isBoss": False,
    "speciesId": "Sheepball",
    "nickname": "Cloudy",
    "gender": "Female",
    "level": 24,
    "exp": 42000,
    "rank": 3,
    "hp": 520,
    "ivs": {"hp": 70, "melee": 55, "shot": 80, "defense": 40},
    "passiveSkills": ["Rare", "PAL_ALLAttack_up2"],
    "activeSkills": ["PowerBall", "AirCannon"],
    "containerId": "cccccccc-0000-0000-0000-000000000000",
    "slotIndex": 4,
    "guildId": "gggggggg-0000-0000-0000-000000000000",
}


def pal_document(pal=None):
    return saveexport.envelope("pal", {"pal": pal or dict(PAL)}, "WORLD")


def player_document(*pals):
    return saveexport.envelope(
        "player", {"player": {"uid": "x"}, "pals": [dict(p) for p in pals], "palCount": len(pals)},
        "WORLD",
    )


# ─── What the document says vs what may be written ───────


def test_the_editable_fields_cross_over():
    """Level, stars (condenser rank), skills and passives are the point."""
    changes, _ignored = palimport.extract_changes(PAL)

    assert changes["level"] == 24
    assert changes["rank"] == 3                      # "stars"
    assert changes["exp"] == 42000
    assert changes["nickname"] == "Cloudy"
    assert changes["passiveSkills"] == ["Rare", "PAL_ALLAttack_up2"]
    assert changes["activeSkills"] == ["PowerBall", "AirCannon"]


def test_ivs_are_flattened_to_schema_names():
    """The export nests them; the schema names them `ivs.hp`. One has to give."""
    changes, _ = palimport.extract_changes(PAL)
    assert changes["ivs.hp"] == 70
    assert changes["ivs.shot"] == 80
    assert "ivs" not in changes


def test_every_change_is_a_field_the_schema_knows():
    changes, _ = palimport.extract_changes(PAL)
    assert set(changes) <= set(editschema.PAL_FIELDS)


def test_placement_fields_are_refused_out_loud_not_dropped():
    """
    Someone importing a Pal from another server would reasonably assume the owner
    came with it. Silence would let them believe that; the reason is reported.
    """
    _changes, ignored = palimport.extract_changes(PAL)
    named = {i["field"] for i in ignored}

    for field in ("ownerUid", "containerId", "slotIndex", "guildId", "instanceId"):
        assert field in named, field
    assert all(i["problem"] for i in ignored)


def test_species_and_gender_are_not_writable():
    _changes, ignored = palimport.extract_changes(PAL)
    named = {i["field"] for i in ignored}
    assert "speciesId" in named
    assert "gender" in named


def test_derived_values_are_not_written_back():
    """`hp` is recomputed by the game; writing it is at best a no-op."""
    changes, ignored = palimport.extract_changes(PAL)
    assert "hp" not in changes
    assert "hp" in {i["field"] for i in ignored}


def test_an_absent_list_is_left_alone_rather_than_emptied():
    """
    `activeSkills` is None on a Pal whose save has no EquipWaza property. Writing
    an ArrayProperty that is not there means guessing its array_type — the same
    refusal MasteredWaza gets.
    """
    pal = {**PAL, "activeSkills": None}
    changes, ignored = palimport.extract_changes(pal)

    assert "activeSkills" not in changes
    assert any(i["field"] == "activeSkills" for i in ignored)


def test_a_field_the_document_omits_is_not_defaulted():
    """A document that says nothing about a field must not zero it."""
    pal = {"instanceId": PAL["instanceId"], "level": 5}
    changes, _ = palimport.extract_changes(pal)
    assert changes == {"level": 5}


def test_an_unknown_field_is_reported_rather_than_ignored():
    changes, ignored = palimport.extract_changes({**PAL, "somethingNew": 1})
    assert "somethingNew" not in changes
    assert any(i["field"] == "somethingNew" for i in ignored)


def test_importable_is_derived_from_the_schema():
    """
    Listing the fields by hand is how a field gets added to the editor and
    silently stays unimportable — or worse, removed from the schema and still
    written here.
    """
    for name in palimport.IMPORTABLE:
        assert name in editschema.PAL_FIELDS
    for name in charedit.PAL_READ_ONLY:
        assert name not in palimport.IMPORTABLE


# ─── Reading documents ───────────────────────────────────


def test_a_pal_document_yields_one_pal():
    assert len(palimport.pals_in(pal_document())) == 1


def test_a_player_document_yields_its_whole_team():
    """
    One format, read two ways: a player export already embeds the team, so
    "restore this Pal" and "restore this player's Pals" are the same file.
    """
    doc = player_document(PAL, {**PAL, "instanceId": "bbbb", "nickname": "Second"})
    assert [p["nickname"] for p in palimport.pals_in(doc)] == ["Cloudy", "Second"]


def test_a_container_document_is_refused_by_pointing_at_the_right_importer():
    doc = saveexport.envelope("container", {"containerId": "c", "slots": []}, "W")
    with pytest.raises(palimport.PalImportRefused, match="container importer"):
        palimport.pals_in(doc)


def test_a_tampered_document_is_refused_before_anything_is_read():
    doc = pal_document()
    doc["payload"]["pal"]["level"] = 80          # checksum now disagrees
    with pytest.raises(palimport.PalImportError, match="[Cc]hecksum"):
        palimport.pals_in(doc)


def test_a_pal_document_with_no_pal_object_is_refused():
    doc = saveexport.envelope("pal", {"pal": "not an object"}, "W")
    with pytest.raises(palimport.PalImportError, match="payload.pal"):
        palimport.pals_in(doc)


# ─── Mode handling, without touching a world ─────────────


def test_an_unknown_mode_is_refused():
    plan = palimport.plan_import(None, pal_document(), "sideways")
    assert plan["ok"] is False
    assert "Unknown mode" in plan["problems"][0]["problem"]


def test_apply_refuses_an_unknown_mode():
    with pytest.raises(palimport.PalImportError, match="Unknown mode"):
        palimport.apply_import(pal_document(), "sideways")


def test_create_takes_one_pal_at_a_time_and_says_why():
    """
    Not caution for its own sake: `apply_clone`'s verification — both arrays grew
    by exactly n, no other container changed length — is written for one request,
    and a batch that reused it would be checking the wrong invariant.
    """
    doc = player_document(PAL, {**PAL, "instanceId": "bbbb"})
    with pytest.raises(palimport.PalImportRefused, match="one Pal per request"):
        palimport.apply_import(doc, "create", container_id="c", template_instance_id="t")


def test_create_needs_a_destination():
    with pytest.raises(palimport.PalImportError, match="destination container"):
        palimport.apply_import(pal_document(), "create", template_instance_id="t")


def test_create_needs_the_template_chosen_at_preview_time():
    """
    The template is carried from the preview for the same reason a planHash is: if
    it has been released since, `apply_clone` cannot find it and refuses.
    """
    with pytest.raises(palimport.PalImportError, match="[Pp]review"):
        palimport.apply_import(pal_document(), "create", container_id="c")


def test_overwrite_refuses_one_target_for_many_pals():
    doc = player_document(PAL, {**PAL, "instanceId": "bbbb"})
    with pytest.raises(palimport.PalImportRefused, match="single target"):
        palimport.apply_import(doc, "overwrite", instance_id="cccc")


def test_overwrite_needs_something_to_match_on():
    doc = pal_document({"level": 5})            # no instanceId anywhere
    with pytest.raises(palimport.PalImportError, match="instanceId"):
        palimport.apply_import(doc, "overwrite")


def test_a_document_with_nothing_writable_is_refused_not_silently_applied():
    doc = pal_document({"instanceId": "aaaa", "ownerUid": "someone", "hp": 5})
    with pytest.raises(palimport.PalImportRefused, match="[Nn]othing to change"):
        palimport.apply_import(doc, "overwrite")


def test_an_oversized_document_is_refused_by_count():
    pals = [{**PAL, "instanceId": f"{i:08d}"} for i in range(palimport.MAX_PALS + 1)]
    with pytest.raises(palimport.PalImportRefused, match="exceeds"):
        palimport.apply_import(player_document(*pals), "overwrite")


def test_the_pal_ceiling_matches_the_bulk_editor():
    """Overwrite mode *is* a bulk edit; two different limits would be a bug."""
    assert palimport.MAX_PALS == charedit.MAX_BULK


# ─── The export side of the same format ──────────────────


def test_a_pal_export_is_the_same_dict_a_player_export_embeds():
    sections = {"pals": [dict(PAL)], "worldGuid": "W"}
    doc = saveexport.build("pal", sections, PAL["instanceId"])

    assert doc["kind"] == "pal"
    assert doc["payload"]["pal"] == PAL
    assert saveexport.verify(doc)["ok"] is True


def test_exporting_a_pal_that_is_not_there_is_an_error():
    with pytest.raises(saveexport.ExportError, match="No Pal"):
        saveexport.build("pal", {"pals": []}, "nope")


def test_a_pal_export_round_trips_into_an_import():
    """The whole point of sharing one format: export then import, unmodified."""
    sections = {"pals": [dict(PAL)], "worldGuid": "W"}
    doc = saveexport.build("pal", sections, PAL["instanceId"])

    changes, _ = palimport.extract_changes(palimport.pals_in(doc)[0])
    assert changes["level"] == PAL["level"]
    assert changes["rank"] == PAL["rank"]
    assert changes["passiveSkills"] == PAL["passiveSkills"]
