"""
Structured exports (Phase 6, export half).

The envelope is the whole point: an export nobody can validate is not much use
as an import. These tests pin the checksum contract, because that is what the
import half will lean on to decide whether a file can be trusted.
"""

from __future__ import annotations

import json

import pytest

import saveexport


@pytest.fixture
def sections():
    return {
        "worldGuid": "0123456789ABCDEF",
        "counts": {"bases": 2, "players": 1},
        "guilds": [{"id": "g1", "name": "Greed", "members": [{"uid": "p1"}], "baseCampIds": ["b1"]}],
        "bases": [
            {"id": "b1", "name": "Main", "guildId": "g1"},
            {"id": "b2", "name": "Outpost", "guildId": "g2"},
        ],
        "baseStorage": [
            {"baseId": "b1", "baseName": "Main", "itemCount": 10,
             "containers": [{"containerId": "c1"}]},
        ],
        "items": [{"itemId": "Wood", "count": 500}],
        "players": [{"uid": "p1", "name": "Nirb", "level": 40, "guildId": "g1",
                     "steamId": "76561198000000000"}],
        "pals": [{"instanceId": "i1", "ownerUid": "p1", "speciesId": "Sheepball"}],
        "containers": {"c1": [{"slotIndex": 0, "itemId": "Wood", "stackCount": 500,
                               "isEmpty": False}]},
        "containerOwnership": {"c1": {"baseCampId": "b1", "kindName": "Wooden Chest"}},
    }


# ─── Envelope ────────────────────────────────────────────────────


def test_envelope_carries_what_an_importer_needs(sections):
    doc = saveexport.export_world(sections)

    assert doc["schemaVersion"] == saveexport.SCHEMA_VERSION
    assert doc["kind"] == "world"
    assert doc["worldGuid"] == "0123456789ABCDEF"
    assert doc["exportedAt"].endswith("+00:00")
    assert len(doc["checksum"]) == 64


def test_a_fresh_export_verifies(sections):
    for kind, target in [("world", None), ("player", "p1"), ("guild", "g1"),
                         ("base", "b1"), ("container", "c1")]:
        doc = saveexport.build(kind, sections, target)
        report = saveexport.verify(doc)
        assert report["ok"], f"{kind} failed to verify: {report['problems']}"
        assert report["kind"] == kind


def test_a_tampered_payload_fails_verification(sections):
    doc = saveexport.export_world(sections)
    doc["payload"]["items"][0]["count"] = 999999

    report = saveexport.verify(doc)
    assert not report["ok"]
    assert any("Checksum mismatch" in p for p in report["problems"])


def test_reformatting_the_file_does_not_break_the_checksum(sections):
    """
    The checksum covers the payload only. Someone pretty-printing an export, or
    a tool reordering keys, must not turn a good file into a rejected one.
    """
    doc = saveexport.export_world(sections)
    round_tripped = json.loads(json.dumps(doc, indent=4, sort_keys=True))

    assert saveexport.verify(round_tripped)["ok"]


def test_a_wrong_schema_version_is_reported(sections):
    doc = saveexport.export_world(sections)
    doc["schemaVersion"] = 99

    report = saveexport.verify(doc)
    assert not report["ok"]
    assert any("Schema version" in p for p in report["problems"])


def test_junk_input_is_reported_rather_than_raising():
    for junk in [None, [], "a string", 42]:
        report = saveexport.verify(junk)
        assert report["ok"] is False
        assert report["problems"]


def test_a_document_with_no_payload_is_refused():
    report = saveexport.verify({"schemaVersion": saveexport.SCHEMA_VERSION, "kind": "world"})
    assert not report["ok"]
    assert "No payload" in report["problems"]


# ─── Builders ────────────────────────────────────────────────────


def test_world_export_omits_raw_container_slots(sections):
    """A whole-world dump of every slot is hundreds of MB and nobody wants it."""
    payload = saveexport.export_world(sections)["payload"]

    assert "containers" not in payload
    assert payload["items"] == sections["items"]
    assert payload["baseStorage"] == sections["baseStorage"]


def test_world_export_trims_player_records(sections):
    """The world summary carries a player list, not their full save records."""
    player = saveexport.export_world(sections)["payload"]["players"][0]

    assert player["name"] == "Nirb"
    assert "steamId" not in player


def test_player_export_includes_their_pals(sections):
    payload = saveexport.export_player(sections, "p1")["payload"]

    assert payload["player"]["name"] == "Nirb"
    assert payload["palCount"] == 1
    assert payload["pals"][0]["speciesId"] == "Sheepball"


def test_player_lookup_tolerates_guid_formatting(sections):
    """Player uids appear dashed and undashed, in both cases, across the save."""
    sections["players"][0]["uid"] = "0123ABCD-1234-5678-9ABC-DEF012345678"

    for spelling in [
        "0123ABCD-1234-5678-9ABC-DEF012345678",
        "0123abcd-1234-5678-9abc-def012345678",
        "0123ABCD123456789ABCDEF012345678",
    ]:
        assert saveexport.export_player(sections, spelling)["payload"]["player"]["name"] == "Nirb"


def test_guild_export_only_includes_its_own_bases(sections):
    payload = saveexport.export_guild(sections, "g1")["payload"]

    assert [b["id"] for b in payload["bases"]] == ["b1"]
    assert [s["baseId"] for s in payload["baseStorage"]] == ["b1"]


def test_base_export_includes_container_contents(sections):
    payload = saveexport.export_base(sections, "b1")["payload"]

    assert payload["base"]["name"] == "Main"
    assert payload["containerContents"]["c1"][0]["itemId"] == "Wood"


def test_container_export_names_its_owner(sections):
    payload = saveexport.export_container(sections, "c1")["payload"]

    assert payload["owner"]["baseCampId"] == "b1"
    assert payload["slots"][0]["stackCount"] == 500


# ─── Dispatch ────────────────────────────────────────────────────


def test_missing_targets_are_refused(sections):
    for kind in ("player", "guild", "base", "container"):
        with pytest.raises(saveexport.ExportError, match="needs an id"):
            saveexport.build(kind, sections)


def test_a_world_export_takes_no_id(sections):
    with pytest.raises(saveexport.ExportError, match="takes no id"):
        saveexport.build("world", sections, "b1")


def test_unknown_kinds_are_refused(sections):
    with pytest.raises(saveexport.ExportError, match="Unknown export kind"):
        saveexport.build("everything", sections)


def test_unknown_targets_are_refused(sections):
    with pytest.raises(saveexport.ExportError, match="No player"):
        saveexport.export_player(sections, "nobody")
    with pytest.raises(saveexport.ExportError, match="No base"):
        saveexport.export_base(sections, "nowhere")


# ─── Filenames ───────────────────────────────────────────────────


def test_filename_describes_the_export(sections):
    assert saveexport.filename_for(saveexport.export_player(sections, "p1")).startswith(
        "palworld-player-Nirb-"
    )
    assert saveexport.filename_for(saveexport.export_world(sections)).startswith("palworld-world-")


def test_filename_strips_characters_that_break_paths(sections):
    sections["players"][0]["name"] = "../../etc/passwd"
    name = saveexport.filename_for(saveexport.export_player(sections, "p1"))

    assert "/" not in name
    assert ".." not in name
    assert name.endswith(".json")
