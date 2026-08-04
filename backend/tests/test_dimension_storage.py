"""
Dimensional Pal Storage — the one place a Pal can be that is not in Level.sav.

`Players/<UID>_dps.sav` holds a `SaveParameterArray` of
`PalDimensionPalStorageSaveParameter`: 9,600 slots, each an ordinary
`SaveParameter` plus its own `InstanceId`. Nothing in this project opened that
file, so a Pal moved into one was missing from every count — My Pals, the owned
totals, and the breeding planner, which then offered routes to species the
player already had.
"""

from __future__ import annotations

import os
import sys

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import parser as P          # noqa: E402
import savefiles            # noqa: E402


def _slot(character_id, level=10, owner=""):
    param = {
        "CharacterID": {"value": character_id},
        "Level": {"value": {"value": level}},
        "Gender": {"value": {"value": "EPalGenderType::Male"}},
    }
    if owner:
        param["OwnerPlayerUId"] = {"value": owner}
    return {"SaveParameter": {"value": param}, "InstanceId": {"value": "abc"}}


class _Gvas:
    def __init__(self, slots):
        self.properties = {
            "SaveParameterArray": {"value": {"values": slots}}
        }


UID = "22b22b02-0000-0000-0000-000000000000"


def test_an_empty_slot_says_none_and_must_not_become_a_pal():
    """
    The failure this pins produced 9,600 Pals per player.

    Every one of the 9,600 slots is materialised; a free one carries the
    *string* "None" as its CharacterID, so a truthiness test on the id accepts
    all of them.
    """
    gvas = _Gvas([_slot("None"), _slot("None"), _slot("SheepBall")])
    pals = P.extract_dimension_storage(gvas, UID)
    assert len(pals) == 1
    assert pals[0]["speciesId"] == "SheepBall"


def test_a_stored_pal_belongs_to_the_file_it_is_in():
    """The file is per player, so a blank OwnerPlayerUId is still that player."""
    pals = P.extract_dimension_storage(_Gvas([_slot("SheepBall")]), UID)
    assert pals[0]["ownerUid"] == UID


def test_an_explicit_owner_is_kept():
    other = "11a11a01-0000-0000-0000-000000000000"
    pals = P.extract_dimension_storage(_Gvas([_slot("SheepBall", owner=other)]), UID)
    assert pals[0]["ownerUid"] == other


def test_it_is_named_and_has_no_container():
    """
    No containerId, deliberately. These Pals are not in
    CharacterContainerSaveData, and inventing an id would let base attribution,
    the slot editor and palclone believe they could address them.
    """
    pal = P.extract_dimension_storage(_Gvas([_slot("SheepBall")]), UID)[0]
    assert pal["location"] == "dimension"
    assert pal["storageKind"] == "Dimensional Pal Storage"
    assert pal["containerId"] == ""


def test_an_absent_array_is_not_an_error():
    class Empty:
        properties = {}

    assert P.extract_dimension_storage(Empty(), UID) == []


def test_the_uid_is_rebuilt_into_the_form_level_sav_uses(tmp_path):
    """
    Filenames are undashed uppercase; Level.sav stores dashed lowercase.
    Returning the filename spelling would reintroduce the mismatch
    `get_player_sav_path` exists to document.
    """
    players = tmp_path / "Players"
    players.mkdir()
    (players / "22B22B02000000000000000000000000_dps.sav").write_bytes(b"x")
    (players / "22B22B02000000000000000000000000.sav").write_bytes(b"x")
    found = savefiles.list_player_dps_paths(str(tmp_path))
    assert list(found) == ["22b22b02-0000-0000-0000-000000000000"]


def test_a_non_hex_name_is_ignored(tmp_path):
    players = tmp_path / "Players"
    players.mkdir()
    (players / "notauid_dps.sav").write_bytes(b"x")
    assert savefiles.list_player_dps_paths(str(tmp_path)) == {}


# ─── Against the real world ──────────────────────────────────────

WORLD = os.path.join(
    os.path.dirname(BACKEND), "refs", "palworld", "Pal", "Saved", "SaveGames", "0",
    "B8A1C9C171F944D3B9287F7390B76548",
)


@pytest.mark.integration
def test_the_live_world_has_dimension_storage_with_real_pals():
    """
    Two of five players have the file; the reported symptom was six Lamballs in
    one of them. Note the spellings: the same file holds `Sheepball` and
    `SheepBall`, which is why every lookup here is canonicalised.
    """
    if not os.path.isdir(WORLD):
        pytest.skip("live world not present")

    paths = savefiles.list_player_dps_paths(WORLD)
    if not paths:
        pytest.skip("no _dps.sav in the reference world")

    counts = {}
    for uid, path in paths.items():
        gvas = P.load_gvas(path)
        assert gvas is not None, f"{path} did not parse"
        pals = P.extract_dimension_storage(gvas, uid)
        counts[uid] = len(pals)
        # The bug that made this file matter: 9,600 slots, few Pals.
        assert len(pals) < 9600

    assert sum(counts.values()) > 0
