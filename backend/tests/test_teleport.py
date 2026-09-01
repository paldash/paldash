"""
Teleport by save edit.

The game cannot do this — its only teleport is anchored to the issuing admin's
in-game character, and a headless dashboard has none. A save edit can, at the price
of needing the server stopped.

Two things carry the tests:

  * **Bounds catch a typo, not a destination.** The real failure is an extra digit,
    which puts a character outside the world to fall forever. The limits are sized
    well outside every one of the 174 fast-travel points so no legitimate spot is
    refused.
  * **The position is in the player save, not `Level.sav`.** Verified against a real
    world rather than assumed, and it is why a teleport touches one small file
    instead of 55 MB.
"""

from __future__ import annotations

import math
import os

import pytest

import teleport

# ─── Validation ──────────────────────────────────────────


def test_an_extra_digit_is_refused():
    """The failure that actually happens, and the reason bounds exist at all."""
    with pytest.raises(teleport.TeleportError, match="extra digit"):
        teleport._validate(-2_146_222, 4_073, 12_572)


def test_a_wild_height_is_refused():
    with pytest.raises(teleport.TeleportError, match="Height"):
        teleport._validate(0, 0, 900_000)


def test_nan_and_infinity_are_refused():
    """
    A NaN would serialise into the save and produce a character at no position at
    all — worse than a refusal, because it looks like it worked.
    """
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(teleport.TeleportError, match="finite"):
            teleport._validate(bad, 0, 0)


def test_non_numeric_coordinates_are_refused():
    with pytest.raises(teleport.TeleportError, match="numbers"):
        teleport._validate("over there", 0, 0)


def test_the_whole_playable_world_is_accepted():
    """
    Bounds are sized to catch mistakes, not to police destinations. Every real
    fast-travel point must pass, or the limits are wrong.
    """
    for point in teleport.destinations():
        teleport._validate(point["x"], point["y"], point["z"])


def test_the_bounds_sit_outside_every_known_point():
    points = teleport.destinations()
    if not points:
        pytest.skip("bundled fast-travel data not available")
    assert min(p["x"] for p in points) > teleport.MIN_XY
    assert max(p["x"] for p in points) < teleport.MAX_XY
    assert min(p["z"] for p in points) > teleport.MIN_Z
    assert max(p["z"] for p in points) < teleport.MAX_Z


# ─── Destinations ────────────────────────────────────────


def test_destinations_are_the_fast_travel_points_with_a_height():
    """
    The safe answer to the hard part: nothing here knows terrain height, so a
    hand-typed z can drop a character under the map. These are positions the game
    itself puts players at.
    """
    points = teleport.destinations()
    assert len(points) == 174
    assert all({"id", "name", "x", "y", "z"} <= set(p) for p in points)


def test_missing_bundled_data_loses_the_convenience_not_the_feature(monkeypatch):
    import gamedata

    def boom():
        raise gamedata.GameDataUnavailable("no bundle")

    monkeypatch.setattr(gamedata, "fast_travel_points", boom)
    assert teleport.destinations() == []


# ─── Against the real world ──────────────────────────────


@pytest.fixture
def world(refworld, palsav_available):
    return refworld


@pytest.fixture
def UID(world):
    """
    A real player uid, read off the reference world at runtime.

    This used to be a committed constant holding a real refworld uid, and the
    public-release scrub rewrote it to a placeholder — correctly, but
    `refworld/` on disk is gitignored and kept its real ids, so every test
    naming a player by the committed value silently stopped finding one.
    Deriving the uid from the world keeps real Steam IDs out of the repository
    and keeps these tests working whatever world sits in `refworld/`.
    """
    names = sorted(
        n for n in os.listdir(os.path.join(world, "Players"))
        if n.endswith(".sav") and not n.endswith("_dps.sav")
    )
    assert names, "the reference world has no player saves"
    raw = names[0][:-4].lower()
    return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"


@pytest.mark.integration
def test_the_position_lives_in_the_player_save_not_the_level(world, UID):
    """
    Checked rather than assumed. `Level.sav`'s character record carries Exp, Level,
    NickName and a LastJumpedLocation — but no live position, which is why a
    teleport never has to touch the 55 MB world file.
    """
    position = teleport.current_position(UID, world)
    assert set(position) == {"x", "y", "z"}
    assert all(isinstance(v, float) for v in position.values())
    # A real position on Palpagos, not a zeroed default.
    assert position != {"x": 0.0, "y": 0.0, "z": 0.0}


@pytest.mark.integration
def test_a_plan_reports_the_move_without_making_it(world, UID):
    before = teleport.current_position(UID, world)
    point = teleport.destinations()[0]

    plan = teleport.plan_teleport(UID, point["x"], point["y"], point["z"], world)
    assert plan["from"] == before
    assert plan["to"] == {"x": point["x"], "y": point["y"], "z": point["z"]}
    assert plan["distance"] == pytest.approx(
        math.dist((before["x"], before["y"], before["z"]),
                  (point["x"], point["y"], point["z"]))
    )
    # Nothing moved.
    assert teleport.current_position(UID, world) == before


@pytest.mark.integration
def test_a_fast_travel_destination_raises_no_height_warning(world, UID):
    """A verified ground position is exactly what the warning exists to ask for."""
    point = teleport.destinations()[0]
    plan = teleport.plan_teleport(UID, point["x"], point["y"], point["z"], world)
    assert plan["warnings"] == []
    assert plan["nearestPoint"]["id"] == point["id"]


@pytest.mark.integration
def test_a_destination_far_from_anywhere_known_warns_about_height(world, UID):
    """
    Not an error — most of the map is far from a fast-travel point. But combined
    with a hand-typed z it is the shape of a mistake worth naming.
    """
    plan = teleport.plan_teleport(UID, 500_000, 500_000, 5_000, world)
    assert any("terrain height" in w for w in plan["warnings"])


@pytest.mark.integration
def test_a_missing_player_is_refused(world):
    with pytest.raises(teleport.TeleportError, match="No player save"):
        teleport.current_position("ffffffff-0000-0000-0000-000000000000", world)


@pytest.mark.integration
def test_applying_refuses_when_the_server_is_not_provably_stopped(world, UID):
    """
    The fail-closed default, with no patching: nothing in a test environment can
    prove a Palworld server is stopped, so a teleport is refused. This is the state
    the guard exists to produce and the one a misconfigured deployment lands in.
    """
    import safety

    with pytest.raises(safety.ServerRunningError):
        teleport.apply_teleport(UID, 0, 0, 1000, world_dir=world)

    # And nothing moved.
    assert teleport.current_position(UID, world)["z"] != 1000


@pytest.mark.integration
def test_the_write_actually_goes_through_the_guard(world, UID, monkeypatch):
    """
    Proves the refusal above comes from `guarded_save_write` rather than from the
    write failing for some unrelated reason.

    The patch target is `backup.assert_writable`, not `safety.assert_writable`:
    `backup.py` does `from safety import assert_writable`, so the two module
    attributes are separate names for one function and rebinding the source module's
    does nothing. An earlier version of this test patched the wrong one, passed on
    the fail-closed default, and proved nothing about the guard.
    """
    import backup as backup_module

    calls: list[int] = []

    def refuse():
        calls.append(1)
        raise RuntimeError("guard reached")

    monkeypatch.setattr(backup_module, "assert_writable", refuse)
    with pytest.raises(RuntimeError, match="guard reached"):
        teleport.apply_teleport(UID, 0, 0, 1000, world_dir=world)
    assert calls == [1]
