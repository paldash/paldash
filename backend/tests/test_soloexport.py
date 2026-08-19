"""
Exporting a playable copy of the world with a player's uid remapped.

Two properties carry this feature, and both are the opposite of the obvious
implementation:

  * **Completeness.** The reference implementation rewrites four named keys. Counted
    against the reference world, that list misses 1,836 references — most of them
    `LastNickNameModifierPlayerUid`. So the remap matches on *value*, and a rename
    asserts that zero references to the old uid survive.
  * **Exactly one visit per field.** A wrapped GVAS property is
    `{'struct_type': 'Guid', 'value': UUID(...)}`, so a naive recursion matches it
    twice — once at the outer key, once inside. On a swap the second write undoes the
    first, and it undoes it on the *wrapped* fields only, which is the majority.

The other property is safety, and it is structural rather than tested for: this
module has no code path that writes to the source world.
"""

from __future__ import annotations

import os

import pytest

# The subject here is palsav's UUID value type — without palsav (CI has
# no compiled codec) there is nothing to test, so skip as one unit.
pytest.importorskip("palsav")

import soloexport

A = "22b22b02-0000-0000-0000-000000000000"
B = "33c33c03-0000-0000-0000-000000000000"
C = "aaaaaaaa-0000-0000-0000-000000000000"


def uuid_of(text: str):
    from palsav.archive import UUID as PalUUID

    return PalUUID.from_str(text)


def wrapped(text: str) -> dict:
    """A GVAS Guid property, the shape that gets visited twice by a naive walk."""
    return {
        "struct_type": "Guid",
        "struct_id": uuid_of("00000000-0000-0000-0000-000000000000"),
        "id": None,
        "value": uuid_of(text),
    }


# ─── uid handling ────────────────────────────────────────


def test_uids_are_accepted_in_either_spelling():
    """
    Player filenames are uppercase undashed; the world's references are dashed
    lowercase. Both are in circulation, and pasting one where the other is expected
    should not silently match nothing.
    """
    assert soloexport._fmt_uid("22B22B02000000000000000000000000") == A
    assert soloexport._fmt_uid(A) == A
    assert soloexport._file_uid(A) == "22B22B02000000000000000000000000"


def test_a_non_uid_is_refused_with_a_reason():
    with pytest.raises(soloexport.SoloExportError, match="32 hex"):
        soloexport._fmt_uid("nope")
    with pytest.raises(soloexport.SoloExportError, match="hexadecimal"):
        soloexport._fmt_uid("zzzzzzzz000000000000000000000000")


def test_uid_str_reads_all_three_shapes():
    """
    palsav decodes GUIDs as its own UUID class, not str. An `isinstance(v, str)` test
    matches nothing — which is how the first version of this module counted 6,455 uid
    fields and rewrote zero of them.
    """
    assert soloexport._uid_str(uuid_of(A)) == A
    assert soloexport._uid_str(wrapped(A)) == A
    assert soloexport._uid_str(A) == A
    assert soloexport._uid_str(None) is None
    assert soloexport._uid_str("not a guid") is None
    assert soloexport._uid_str(42) is None


def test_a_rewritten_uid_keeps_its_original_type():
    """
    Writing a str where palsav expects its own UUID gives a tree that looks right and
    an encoder that emits wrong bytes.
    """
    from palsav.archive import UUID as PalUUID

    node = {"build_player_uid": uuid_of(A)}
    soloexport._write_uid(node, "build_player_uid", C)
    assert isinstance(node["build_player_uid"], PalUUID)
    assert str(node["build_player_uid"]) == C

    node = {"OwnerPlayerUId": wrapped(A)}
    soloexport._write_uid(node, "OwnerPlayerUId", C)
    assert isinstance(node["OwnerPlayerUId"]["value"], PalUUID)
    assert node["OwnerPlayerUId"]["struct_type"] == "Guid"    # shape preserved


# ─── The walk ────────────────────────────────────────────


def test_a_wrapped_property_is_one_field_not_two():
    """The double-visit bug, in miniature."""
    tree = {"OwnerPlayerUId": wrapped(A)}
    assert soloexport._walk_uids(tree, {A: A}, apply=False) == 1


def test_a_swap_does_not_undo_itself_on_wrapped_fields():
    """
    The failure the double visit produced: the second write maps target back to
    source. It shows up only on a swap, and only on wrapped fields — which are most
    of them — so a rename test would have passed while swap was silently broken.
    """
    tree = {
        "OwnerPlayerUId": wrapped(A),       # wrapped
        "build_player_uid": uuid_of(B),     # bare
    }
    soloexport._walk_uids(tree, {A: B, B: A}, apply=True)
    assert soloexport._uid_str(tree["OwnerPlayerUId"]) == B
    assert soloexport._uid_str(tree["build_player_uid"]) == A


def test_the_walk_reaches_uids_in_lists_and_nested_structures():
    tree = {
        "GroupSaveDataMap": {"value": [
            {"value": {"RawData": {"value": {
                "players": [{"player_uid": uuid_of(A)}, {"player_uid": uuid_of(B)}],
                "admin_player_uid": uuid_of(A),
                "individual_character_handle_ids": [{"guid": uuid_of(A)}],
            }}}},
        ]},
    }
    assert soloexport._walk_uids(tree, {A: A}, apply=False) == 3


def test_the_walk_ignores_uids_that_are_not_mapped():
    tree = {"OwnerPlayerUId": wrapped(B), "other": uuid_of(C)}
    assert soloexport._walk_uids(tree, {A: C}, apply=False) == 0


def test_instance_ids_are_not_touched():
    """
    Character instance ids are full-entropy GUIDs in a different id space from player
    uids, so a value-based remap cannot confuse the two. Pinned because the whole
    safety argument for matching on value rests on it.
    """
    instance = "fd8789db-ccfa-47b1-9b6d-afc3eea30dc0"
    tree = {"guid": uuid_of(A), "instance_id": uuid_of(instance)}
    soloexport._walk_uids(tree, {A: C}, apply=True)
    assert soloexport._uid_str(tree["instance_id"]) == instance


# ─── Planning ────────────────────────────────────────────


def test_the_same_uid_twice_is_refused(tmp_path):
    with pytest.raises(soloexport.SoloExportError, match="same"):
        soloexport.plan_export(A, A, world_dir=str(tmp_path))


def test_a_missing_world_is_refused():
    with pytest.raises(soloexport.SoloExportError, match="not found"):
        soloexport.plan_export(A, C, world_dir="/nonexistent/world")


def test_a_missing_player_names_the_file_it_wanted(tmp_path):
    (tmp_path / "Players").mkdir()
    (tmp_path / "Level.sav").write_bytes(b"x")
    with pytest.raises(soloexport.SoloExportError, match="22B22B02"):
        soloexport.plan_export(A, C, world_dir=str(tmp_path))


# ─── Against the real world ──────────────────────────────


@pytest.fixture
def world(refworld, palsav_available):
    return refworld


@pytest.mark.integration
@pytest.mark.slow
def test_the_reference_key_list_would_miss_most_references(world):
    """
    The measurement behind matching on value rather than on key name. If this ever
    stops being true the design note in `soloexport` should be revisited — but the
    value-based walk is a superset either way, so the export stays correct.
    """
    gvas, _ = soloexport._load(os.path.join(world, "Level.sav"))
    tree = soloexport._world_save_data(gvas)

    by_value = soloexport._walk_uids(tree, {A: A}, apply=False)

    def by_key(node) -> int:
        total = 0
        if isinstance(node, dict):
            for key in soloexport.REFERENCE_OWNER_KEYS:
                if key in node and soloexport._uid_str(node[key]) == A:
                    total += 1
            for value in node.values():
                total += by_key(value)
        elif isinstance(node, list):
            for item in node:
                total += by_key(item)
        return total

    named = by_key(tree)
    assert by_value > named, "value-based walk should find at least as much"
    assert by_value - named > 1000, (
        f"expected the named-key list to miss ~1,800 references; "
        f"value={by_value} named={named}"
    )


@pytest.mark.integration
@pytest.mark.slow
def test_a_rename_moves_every_reference(world, tmp_path):
    plan = soloexport.plan_export(A, C, world_dir=world)
    assert plan["mode"] == "rename"
    assert plan["references"]["characterEntries"] == 1
    assert plan["references"]["total"] > 1000

    out = str(tmp_path / "exported")
    result = soloexport.apply_export(A, C, world_dir=world, destination=out)

    # Plan, apply and verify must agree. They disagreed by 1,176 when wrapped
    # properties were being counted twice.
    assert result["applied"]["total"] == plan["references"]["total"]

    gvas, _ = soloexport._load(os.path.join(out, "Level.sav"))
    tree = soloexport._world_save_data(gvas)
    assert soloexport._walk_uids(tree, {A: A}, apply=False) == 0
    assert soloexport._walk_uids(tree, {C: C}, apply=False) == plan["references"]["total"]


@pytest.mark.integration
@pytest.mark.slow
def test_a_swap_exchanges_two_identities_exactly(world, tmp_path):
    """
    The test the double-visit bug fails. Each player must end up with exactly the
    other's reference count — a partial undo shows up here and nowhere else.
    """
    gvas, _ = soloexport._load(os.path.join(world, "Level.sav"))
    before = soloexport._world_save_data(gvas)
    before_a = soloexport._walk_uids(before, {A: A}, apply=False)
    before_b = soloexport._walk_uids(before, {B: B}, apply=False)
    assert before_a and before_b and before_a != before_b

    out = str(tmp_path / "swapped")
    plan = soloexport.plan_export(A, B, world_dir=world)
    assert plan["mode"] == "swap"
    assert any("exchange identities" in w for w in plan["warnings"])

    soloexport.apply_export(A, B, world_dir=world, destination=out)

    gvas2, _ = soloexport._load(os.path.join(out, "Level.sav"))
    after = soloexport._world_save_data(gvas2)
    assert soloexport._walk_uids(after, {A: A}, apply=False) == before_b
    assert soloexport._walk_uids(after, {B: B}, apply=False) == before_a


@pytest.mark.integration
@pytest.mark.slow
def test_the_source_world_is_never_written_to(world, tmp_path):
    """
    The structural safety property. Every other writer here needs the server provably
    stopped; this one is safe to run live precisely because it only ever reads the
    source.
    """
    def stamps():
        out = {}
        for root, _dirs, files in os.walk(world):
            if os.path.basename(root) == "backup":
                continue
            for name in files:
                path = os.path.join(root, name)
                stat = os.stat(path)
                out[path] = (stat.st_size, stat.st_mtime_ns)
        return out

    before = stamps()
    soloexport.apply_export(A, C, world_dir=world, destination=str(tmp_path / "e"))
    assert stamps() == before


@pytest.mark.integration
@pytest.mark.slow
def test_other_players_are_carried_across_untouched(world, tmp_path):
    """
    This is a copy of the world, not an extraction. Dropping the other players would
    be the destructive operation this module deliberately does not implement.
    """
    out = str(tmp_path / "exported")
    soloexport.apply_export(A, C, world_dir=world, destination=out)

    source_players = set(os.listdir(os.path.join(world, "Players")))
    exported = set(os.listdir(os.path.join(out, "Players")))
    assert len(exported) == len(source_players)

    renamed = f"{soloexport._file_uid(C)}.sav"
    assert renamed in exported
    assert f"{soloexport._file_uid(A)}.sav" not in exported
    # Everyone else keeps their filename.
    assert exported - {renamed} == source_players - {f"{soloexport._file_uid(A)}.sav"}


@pytest.mark.integration
@pytest.mark.slow
def test_the_exported_player_file_claims_its_new_uid(world, tmp_path):
    """A file whose name and contents disagree loads the character as a stranger."""
    out = str(tmp_path / "exported")
    soloexport.apply_export(A, C, world_dir=world, destination=out)

    gvas, _ = soloexport._load(
        os.path.join(out, "Players", f"{soloexport._file_uid(C)}.sav")
    )
    assert soloexport._player_identity(gvas)["playerUid"].lower() == C


@pytest.mark.integration
@pytest.mark.slow
def test_the_export_excludes_the_servers_own_snapshots(world, tmp_path):
    """
    `backup/` holds the game's rotating snapshots. Sweeping it in is what turned a
    2.1 MB world into 66 MB archives once already.
    """
    out = str(tmp_path / "exported")
    result = soloexport.apply_export(A, C, world_dir=world, destination=out)
    assert not os.path.exists(os.path.join(out, "backup"))
    assert result["sizeBytes"] < 20 * 1024 * 1024


@pytest.mark.integration
@pytest.mark.slow
def test_a_stale_plan_is_refused(world, tmp_path):
    with pytest.raises(soloexport.SoloExportError, match="changed since"):
        soloexport.apply_export(
            A, C, world_dir=world, destination=str(tmp_path / "e"),
            expected_plan_hash="deadbeefdeadbeef",
        )


@pytest.mark.integration
@pytest.mark.slow
def test_a_matching_plan_hash_is_accepted(world, tmp_path):
    plan = soloexport.plan_export(A, C, world_dir=world)
    result = soloexport.apply_export(
        A, C, world_dir=world, destination=str(tmp_path / "e"),
        expected_plan_hash=plan["planHash"],
    )
    assert result["ok"] is True


@pytest.mark.integration
@pytest.mark.slow
def test_the_archive_carries_a_checksum(world, tmp_path):
    out = str(tmp_path / "exported")
    soloexport.apply_export(A, C, world_dir=world, destination=out)
    archive = soloexport.archive_export(out)
    assert archive["path"].endswith(".tar.gz")
    assert len(archive["sha256"]) == 64
    assert archive["sizeBytes"] > 0
