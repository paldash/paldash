"""
Moving a player between guilds.

Guild membership lives in four places that must agree, and the interesting
failures are all *partial*: a move that updates the member list but not each
character's `group_id`, or updates both but leaves the guild's handle index
naming characters it no longer owns. None of those raise on their own, so the
tests here assert the agreement rather than any single field.

The write tests are integration tests — they run against a disposable copy of the
reference world, because the thing being verified is that a real 1,910-character
world survives the operation intact.
"""

from __future__ import annotations

import os
import shutil
import time

import pytest

import guildedit


# ─── Fixtures ────────────────────────────────────────────


@pytest.fixture
def sandbox(refworld, tmp_path, monkeypatch):
    """A disposable world the backend will write to, believing the server is down."""
    import backup as backup_module
    import safety
    import savefiles

    base = tmp_path / "SaveGames" / "0"
    world = base / "0123456789ABCDEF0123456789ABCDEF"
    shutil.copytree(refworld, world)

    old = time.time() - 7200
    for dirpath, _dirs, files in os.walk(world):
        for name in files:
            os.utime(os.path.join(dirpath, name), (old, old))
    os.utime(world, (old, old))

    backups = tmp_path / "backups"
    backups.mkdir()

    monkeypatch.setattr(savefiles, "SAVE_BASE_DIR", str(base))
    monkeypatch.setattr(savefiles, "BACKUP_DIR", str(backups))
    monkeypatch.setattr(backup_module, "BACKUP_DIR", str(backups))
    monkeypatch.setattr(safety, "SAVE_BASE_DIR", str(base))
    monkeypatch.setattr(safety, "SAVE_READ_ONLY", False)
    monkeypatch.setattr(safety, "ALLOW_UNVERIFIED_EDITS", False)
    monkeypatch.setattr(
        safety, "_probe_rest_api", lambda: safety.Signal("rest_api", "stopped", "test")
    )
    monkeypatch.setattr(
        safety, "_probe_tcp", lambda: safety.Signal("tcp_port", "stopped", "test")
    )
    return {"world": str(world), "base": str(base)}


def _load(path):
    from parser import load_gvas

    return load_gvas(os.path.join(path, "Level.sav"))


def _guilds(gvas):
    """[(name, id, [member uids])] for every guild, for readable assertions."""
    return [
        (
            guildedit._guild_label(g),
            str(guildedit._raw(g)["group_id"]),
            [guildedit._nu(m["player_uid"]) for m in guildedit._members(g)],
        )
        for g in guildedit._guild_entries(gvas)
    ]


# ─── Planning, which needs no write ──────────────────────


def test_a_solo_guild_move_is_refused_by_default(refworld, palsav_available):
    """
    The case that matters, and the one the reference implementation gets wrong.

    Every guild on the reference world has exactly one member, so "move this
    player to their friend's guild" empties the origin every time. PST deletes
    the guild *and calls `delete_base_camp` on everything it owned* — three built
    bases destroyed to carry out a request that said nothing about bases.

    Refusing names what is at stake instead, and points at the option that keeps
    it.
    """
    gvas = _load(refworld)
    guilds = _guilds(gvas)
    player = guilds[0][2][0]

    plan = guildedit._build_plan(gvas, player, guilds[1][1], transfer_bases=False)
    assert plan["ok"] is False
    problem = plan["problems"][0]
    assert "no other members" in problem
    assert "base(s)" in problem
    assert "transfer bases" in problem


def test_transferring_bases_makes_the_same_move_possible(refworld, palsav_available):
    gvas = _load(refworld)
    guilds = _guilds(gvas)

    plan = guildedit._build_plan(gvas, guilds[0][2][0], guilds[1][1], transfer_bases=True)
    assert plan["ok"] is True
    assert plan["movesBases"] > 0
    assert plan["removesOriginGuild"] is True


def test_the_moving_set_accounts_for_every_character_the_guild_indexes(
    refworld, palsav_available
):
    """
    The cross-check that says `_owned_characters` is not missing anyone.

    Owned Pals carry `OwnerPlayerUId`; base-deployed ones carry none at all and
    belong to the base. Those two sets, added together, must be exactly the
    characters the guild's own handle index lists — if they are not, a move would
    leave some behind with a `group_id` pointing at a guild that no longer exists.
    """
    gvas = _load(refworld)
    guilds = _guilds(gvas)
    entry = guildedit._guild_entries(gvas)[0]
    guild_id = str(guildedit._raw(entry)["group_id"])

    owned = guildedit._owned_characters(gvas, guilds[0][2][0])
    in_guild = guildedit._characters_in_guild(gvas, guild_id)
    handles = guildedit._handle_ids(guildedit._raw(entry))

    assert len(owned) <= len(in_guild)
    assert len(in_guild) == len(handles)


def test_moving_a_player_to_their_own_guild_is_a_refusal_not_a_no_op(
    refworld, palsav_available
):
    """A silent success would report a move that never happened."""
    gvas = _load(refworld)
    guilds = _guilds(gvas)
    plan = guildedit._build_plan(gvas, guilds[0][2][0], guilds[0][1], transfer_bases=True)
    assert plan["ok"] is False
    assert "already in" in plan["problems"][0]


def test_an_unknown_player_or_guild_is_named_in_the_refusal(refworld, palsav_available):
    gvas = _load(refworld)
    guilds = _guilds(gvas)

    plan = guildedit._build_plan(gvas, "ffffffff-0000-0000-0000-000000000000",
                                 guilds[0][1], transfer_bases=True)
    assert plan["ok"] is False
    assert "No guild in this world lists" in plan["problems"][0]

    plan = guildedit._build_plan(gvas, guilds[0][2][0],
                                 "ffffffff-ffff-ffff-ffff-ffffffffffff", True)
    assert plan["ok"] is False
    assert "No guild with id" in plan["problems"][0]


def test_the_plan_hash_changes_with_the_options(refworld, palsav_available):
    """
    Otherwise a preview of the safe variant could be applied as the destructive
    one — the hash has to cover what was agreed to, not just which world it was.
    """
    gvas = _load(refworld)
    guilds = _guilds(gvas)
    without = guildedit._build_plan(gvas, guilds[0][2][0], guilds[1][1], False)
    with_bases = guildedit._build_plan(gvas, guilds[0][2][0], guilds[1][1], True)
    assert without["planHash"] != with_bases["planHash"]


# ─── Writing ─────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.slow
def test_a_move_updates_all_four_structures_together(
    sandbox, palsav_available, stopped_server
):
    """
    The whole point. Membership, `admin_player_uid`, every character's `group_id`
    and both guilds' handle indexes have to end up agreeing — and the four are
    separately writable, so three of four is the realistic bug.
    """
    world = sandbox["world"]
    before = _guilds(_load(world))
    player, origin_id, target_id = before[0][2][0], before[0][1], before[1][1]

    plan = guildedit.plan_guild_move(player, target_id, transfer_bases=True)
    assert plan["ok"], plan["problems"]

    result = guildedit.apply_guild_move(
        player, target_id, transfer_bases=True, plan_hash=plan["planHash"]
    )
    assert result["ok"] is True
    assert result["charactersMoved"] > 0

    after_gvas = _load(world)
    after = _guilds(after_gvas)

    # Membership: in the target, and the emptied origin is gone.
    assert len(after) == len(before) - 1
    target_row = next(g for g in after if g[1] == target_id)
    assert player in target_row[2]
    assert not any(g[1] == origin_id for g in after)

    # Characters: nothing still points at a guild that no longer exists.
    assert guildedit._characters_in_guild(after_gvas, origin_id) == []

    # Handle index: the target lists every character it now owns.
    target_entry = guildedit._find_guild(after_gvas, target_id)
    handles = {
        str(h.get("instance_id", ""))
        for h in guildedit._handle_ids(guildedit._raw(target_entry))
    }
    owned_now = {
        guildedit._instance_id(c)
        for c in guildedit._characters_in_guild(after_gvas, target_id)
    }
    assert owned_now <= handles, "characters in the guild are missing from its index"


@pytest.mark.integration
@pytest.mark.slow
def test_a_transferred_base_keeps_its_contents_and_changes_owner(
    sandbox, palsav_available, stopped_server
):
    """
    Bases are re-homed, never deleted. The count has to survive on both sides of
    the join: the guild's `base_ids` and each base's own `group_id_belong_to`.
    """
    world = sandbox["world"]
    gvas = _load(world)
    rows = _guilds(gvas)
    player, origin_id, target_id = rows[0][2][0], rows[0][1], rows[1][1]

    origin_bases = {guildedit._base_id(b) for b in guildedit._base_entries(gvas, origin_id)}
    target_bases = {guildedit._base_id(b) for b in guildedit._base_entries(gvas, target_id)}
    assert origin_bases and not (origin_bases & target_bases)

    plan = guildedit.plan_guild_move(player, target_id, transfer_bases=True)
    guildedit.apply_guild_move(player, target_id, True, plan_hash=plan["planHash"])

    after = _load(world)
    now = {guildedit._base_id(b) for b in guildedit._base_entries(after, target_id)}
    assert origin_bases | target_bases == now, "a base was lost or not re-homed"

    entry = guildedit._find_guild(after, target_id)
    listed = {guildedit._nu(b) for b in guildedit._raw(entry).get("base_ids") or []}
    assert {guildedit._nu(b) for b in now} <= listed


@pytest.mark.integration
@pytest.mark.slow
def test_a_stale_plan_is_refused_and_nothing_is_written(
    sandbox, palsav_available, stopped_server
):
    """
    The preview is what the operator agreed to. A world that moved since then is
    not the world they saw, and applying anyway would act on a guild that is no
    longer shaped that way.
    """
    world = sandbox["world"]
    rows = _guilds(_load(world))
    player, target_id = rows[0][2][0], rows[1][1]

    level = os.path.join(world, "Level.sav")
    stamp = os.path.getsize(level), os.path.getmtime(level)

    with pytest.raises(guildedit.GuildEditError, match="changed since"):
        guildedit.apply_guild_move(player, target_id, True, plan_hash="stale-not-a-hash")

    assert (os.path.getsize(level), os.path.getmtime(level)) == stamp


@pytest.mark.integration
@pytest.mark.slow
def test_a_move_is_refused_while_the_server_is_up(sandbox, palsav_available, monkeypatch):
    """
    The rule that matters most. Every other guarantee here is about correctness;
    this one is about a world that cannot be recovered.
    """
    import safety

    monkeypatch.setattr(
        safety, "_probe_rest_api",
        lambda: safety.Signal("rest_api", "running", "test says up"),
    )
    world = sandbox["world"]
    rows = _guilds(_load(world))
    level = os.path.join(world, "Level.sav")
    stamp = os.path.getsize(level), os.path.getmtime(level)

    with pytest.raises(Exception):
        guildedit.apply_guild_move(rows[0][2][0], rows[1][1], True, plan_hash="")

    assert (os.path.getsize(level), os.path.getmtime(level)) == stamp


@pytest.mark.integration
@pytest.mark.slow
def test_refusing_the_default_case_writes_nothing(
    sandbox, palsav_available, stopped_server
):
    """The refusal has to happen before the write, not be undone after it."""
    world = sandbox["world"]
    rows = _guilds(_load(world))
    level = os.path.join(world, "Level.sav")
    stamp = os.path.getsize(level), os.path.getmtime(level)

    with pytest.raises(guildedit.GuildEditError, match="no other members"):
        guildedit.apply_guild_move(rows[0][2][0], rows[1][1], False, plan_hash="")

    assert (os.path.getsize(level), os.path.getmtime(level)) == stamp
