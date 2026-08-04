"""
Pals held by a structure, and who they belong to.

Two bugs met here and they looked like one. A Pal in a Pal-storage structure
classified as `location: "other"` — this file used to call those containers
"orphaned" — and, separately, it commonly carries no `OwnerPlayerUId` at all,
so the breeding planner and the owned-Pal counts dropped it. The visible symptom
was a Pal standing in plain sight in someone's base being absent from their own
dashboard, with nothing saying where it had gone.

`extract_pal_storage` fixes the first by reading the game's own module map, and
`main._pals_for` fixes the second by treating an ownerless Pal as guild property
rather than as nobody's.

The measured shape: `MapObjectSaveData[].ConcreteModel.ModuleMap` carries a
`…::CharacterContainer` module whose `RawData.target_container_id` names an entry
in `CharacterContainerSaveData`. Unlike `WorkerDirector`, that field has a name,
so nothing here reads at an offset.
"""

from __future__ import annotations

import pytest

import parser as P


# ─── The join, on a fabricated world ─────────────────────


CONTAINER = "ffd28cd3-3940-4d74-aece-8cfef1f4df77"
BASE = "c7dd2270-943d-4488-9261-2ab99ba1bae9"
GUILD = "49923822-e48d-42b7-b1f0-41b16464bcb1"


class FakeGvas:
    def __init__(self, containers, objects):
        self.properties = {
            "worldSaveData": {
                "value": {
                    "CharacterContainerSaveData": {
                        "value": [
                            {"key": {"ID": {"value": c}}, "value": {}} for c in containers
                        ]
                    },
                    "MapObjectSaveData": {"value": {"values": objects}},
                }
            }
        }


def _object(container_id, kind="DimensionPalStorage", base=BASE, guild=GUILD,
            module="EPalMapObjectConcreteModelModuleType::CharacterContainer"):
    return {
        "MapObjectId": {"value": kind},
        "Model": {
            "value": {
                "RawData": {
                    "value": {
                        "base_camp_id_belong_to": base,
                        "group_id_belong_to": guild,
                    }
                }
            }
        },
        "ConcreteModel": {
            "value": {
                "ModuleMap": {
                    "value": [
                        {
                            "key": module,
                            "value": {
                                "RawData": {
                                    "value": {"target_container_id": container_id}
                                }
                            },
                        }
                    ]
                }
            }
        },
    }


def test_a_character_container_module_names_its_container():
    storage = P.extract_pal_storage(FakeGvas([CONTAINER], [_object(CONTAINER)]))
    assert set(storage) == {CONTAINER}
    assert storage[CONTAINER]["baseCampId"] == BASE
    assert storage[CONTAINER]["guildId"] == GUILD
    assert storage[CONTAINER]["kind"] == "DimensionPalStorage"


def test_an_id_that_resolves_to_no_container_is_dropped():
    """
    The verification, and the reason this is allowed to walk an opaque-ish
    structure at all. A layout change must yield *nothing* — a Pal whose
    location is unknown is a smaller error than a Pal confidently placed in
    the wrong guild's storage.
    """
    storage = P.extract_pal_storage(
        FakeGvas(["11111111-1111-1111-1111-111111111111"], [_object(CONTAINER)])
    )
    assert storage == {}


def test_an_item_container_module_is_not_mistaken_for_a_character_one():
    """
    Chests outnumber Pal stores 3,370 to 2 on the reference world, and both
    modules carry a field called `target_container_id`. Matching loosely would
    file every chest as Pal storage.
    """
    storage = P.extract_pal_storage(
        FakeGvas(
            [CONTAINER],
            [_object(CONTAINER, module="EPalMapObjectConcreteModelModuleType::ItemContainer")],
        )
    )
    assert storage == {}


def test_a_world_placed_store_has_no_base_but_keeps_its_guild():
    storage = P.extract_pal_storage(
        FakeGvas([CONTAINER], [_object(CONTAINER, base="None")])
    )
    assert storage[CONTAINER]["baseCampId"] == ""
    assert storage[CONTAINER]["guildId"] == GUILD


# ─── Ownership: a guild Pal is not an unowned Pal ────────


NO_OWNER = "00000000-0000-0000-0000-000000000000"
ALICE = "11a11a01-0000-0000-0000-000000000000"
BOB = "22b22b02-0000-0000-0000-000000000000"


PALS = [
    {"instanceId": "a1", "ownerUid": ALICE, "guildId": "g1", "location": "palbox"},
    {"instanceId": "a2", "ownerUid": NO_OWNER, "guildId": "g1", "location": "base"},
    {"instanceId": "a3", "ownerUid": "", "guildId": "g1", "location": "storage"},
    {"instanceId": "b1", "ownerUid": BOB, "guildId": "g2", "location": "palbox"},
    {"instanceId": "b2", "ownerUid": NO_OWNER, "guildId": "g2", "location": "base"},
]

GUILDS = [
    {"id": "g1", "members": [{"uid": ALICE}]},
    {"id": "g2", "members": [{"uid": BOB}]},
]


@pytest.fixture
def world(monkeypatch):
    """A two-guild world: each player owns one Pal, each guild shares one."""
    import main
    import savecache

    monkeypatch.setattr(
        savecache,
        "get_section",
        lambda name: PALS if name == "pals" else GUILDS if name == "guilds" else [],
    )
    return main


def test_a_players_pals_include_their_guilds_shared_ones(world):
    """
    The bug in one line. 159 of the reference world's 1,905 Pals carry no owner
    uid: base workers, and anything in a shared Pal store. They are not nobody's
    — every member of the guild can take one out and breed it.
    """
    got = {p["instanceId"] for p in world._pals_for(ALICE)}
    assert got == {"a1", "a2", "a3"}


def test_another_guilds_shared_pals_are_never_included(world):
    """A shared palbox is not a shared Pal. `b2` is g2's, and stays g2's."""
    assert "b2" not in {p["instanceId"] for p in world._pals_for(ALICE)}


def test_another_players_own_pal_is_never_included(world):
    assert "b1" not in {p["instanceId"] for p in world._pals_for(ALICE)}


def test_the_zero_uid_counts_as_unowned_not_as_a_player(world):
    """
    `00000000-…` is what the parser writes when `OwnerPlayerUId` is absent, so
    it has to read as "no owner". Treating it as a uid would put every base
    worker on the server into one imaginary player's palbox.
    """
    assert world._unowned_pal({"ownerUid": NO_OWNER})
    assert world._unowned_pal({"ownerUid": ""})
    assert not world._unowned_pal({"ownerUid": ALICE})


def test_no_owner_means_the_whole_world(world):
    """`None` is the admin case — every Pal, unscoped."""
    assert len(world._pals_for(None)) == 5


def test_a_player_who_owns_nothing_still_sees_their_guilds_shared_pals(world):
    """
    This is the bug reintroducing itself at the point of being fixed.

    Deriving the caller's guilds from the Pals they already own is the obvious
    shortcut and it collapses in exactly this case: someone with everything
    deployed at a base owns nothing to derive a guild *from*, so the set comes
    out empty and they are shown nothing — which is the original complaint.
    Membership comes from the guild list instead.
    """
    carol = "0deadbee-0000-0000-0000-000000000000"
    guilds = [{"id": "g1", "members": [{"uid": carol}]}]
    got = world._scope_pals(PALS, carol, {g["id"] for g in guilds})
    assert {p["instanceId"] for p in got} == {"a2", "a3"}

    # And without the membership hint, the fallback legitimately finds nothing.
    assert world._scope_pals(PALS, carol) == []


# ─── Against the real world ──────────────────────────────


@pytest.mark.integration
def test_every_character_container_on_the_reference_world_classifies(
    refworld, palsav_available
):
    """
    The check that the module type means what it looks like, and the one that
    would catch a game update quietly moving this.

    All 23 character containers must land in exactly one bucket: a player's
    palbox or party, a base's workforce, or a Pal-storage structure. The two
    that used to be called orphans are `PalBooth` stands, and they resolve here.
    """
    import glob
    import os

    from parser import (
        extract_base_workers,
        extract_characters,
        extract_pal_storage,
        extract_player_save,
        load_gvas,
    )

    gvas = load_gvas(os.path.join(refworld, "Level.sav"))
    _players, pals = extract_characters(gvas)

    workers = {k.lower() for k in extract_base_workers(gvas)}
    storage = extract_pal_storage(gvas)

    player_owned: set[str] = set()
    for path in sorted(glob.glob(os.path.join(refworld, "Players", "*.sav"))):
        info = extract_player_save(load_gvas(path), "")
        for field in ("palStorageContainerId", "otomoCharacterContainerId"):
            value = str(info.get(field) or "").lower()
            if value:
                player_owned.add(value)

    holding = {str(p.get("containerId") or "").lower() for p in pals}
    holding.discard("")

    unclassified = holding - workers - player_owned - set(storage)
    assert unclassified == set(), (
        f"{len(unclassified)} container(s) hold Pals that belong to nothing"
    )

    # Both are PalBooth stands, and both attribute to a real base.
    assert len(storage) == 2
    assert {s["kind"] for s in storage.values()} == {"PalBooth"}
    assert all(s["baseCampId"] for s in storage.values())
