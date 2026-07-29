"""
Per-base storage: the join from base camp to item container.

The join is exact rather than spatial, and the thing worth guarding is that it
stays exact — in particular that `group_id_belong_to` (the guild) is never
substituted for `base_camp_id_belong_to` (the base). On a real world that swap
silently collapses every base in a guild into one pile and nothing raises.
"""

from __future__ import annotations

from parser import ZERO_GUID, _base_name, extract_container_ownership, summarise_base_storage


# ─── Builders ────────────────────────────────────────────────────


def map_object(instance_id, container_id=None, base_camp=None, guild=None, kind="ItemChest"):
    """One MapObjectSaveData entry, shaped like the real thing."""
    modules = []
    if container_id is not None:
        modules.append({
            "key": "EPalMapObjectConcreteModelModuleType::ItemContainer",
            "value": {"RawData": {"value": {"target_container_id": container_id}}},
        })

    return {
        "MapObjectId": {"value": kind},
        "Model": {
            "value": {
                "RawData": {
                    "value": {
                        "instance_id": instance_id,
                        "base_camp_id_belong_to": base_camp or ZERO_GUID,
                        "group_id_belong_to": guild or ZERO_GUID,
                        "build_player_uid": ZERO_GUID,
                    }
                }
            }
        },
        "ConcreteModel": {"value": {"ModuleMap": {"value": modules}}},
    }


def gvas_with(objects):
    class FakeGvas:
        properties = {"worldSaveData": {"value": {
            "MapObjectSaveData": {"value": {"values": objects}}
        }}}

    return FakeGvas()


def slots(*pairs):
    return [
        {"slotIndex": i, "itemId": item, "itemName": item, "stackCount": n, "isEmpty": not item}
        for i, (item, n) in enumerate(pairs)
    ]


def base(base_id, name="Base", guild_id="g1", guild_name="Guild"):
    return {"id": base_id, "name": name, "guildId": guild_id, "guildName": guild_name}


# ─── Ownership ───────────────────────────────────────────────────


def test_links_a_container_to_the_base_that_owns_it():
    gvas = gvas_with([map_object("obj1", container_id="c1", base_camp="base-a", guild="guild-1")])
    ownership = extract_container_ownership(gvas)

    assert ownership["c1"]["baseCampId"] == "base-a"
    assert ownership["c1"]["objectId"] == "obj1"
    assert ownership["c1"]["worldPlaced"] is False


def test_the_base_id_is_not_the_guild_id():
    """
    The regression this whole module exists to prevent. Both fields are GUIDs
    sitting next to each other in the same RawData, and reading the wrong one
    still produces a plausible-looking grouping.
    """
    gvas = gvas_with([map_object("obj1", container_id="c1", base_camp="base-a", guild="guild-1")])
    owner = extract_container_ownership(gvas)["c1"]

    assert owner["baseCampId"] == "base-a"
    assert owner["guildId"] == "guild-1"
    assert owner["baseCampId"] != owner["guildId"]


def test_world_placed_containers_have_no_base():
    """A wild chest carries the zero GUID, and must not be filed under a base."""
    gvas = gvas_with([map_object("obj1", container_id="c1")])
    owner = extract_container_ownership(gvas)["c1"]

    assert owner["worldPlaced"] is True
    assert owner["baseCampId"] == ""


def test_objects_without_a_container_are_skipped():
    """Walls, foundations and turrets have no storage module."""
    gvas = gvas_with([
        map_object("wall", container_id=None, base_camp="base-a", kind="DefenseWall"),
        map_object("chest", container_id="c1", base_camp="base-a"),
    ])
    assert set(extract_container_ownership(gvas)) == {"c1"}


def test_object_kinds_resolve_to_real_names():
    """`ItemChest_02` is a Metal Chest — the bundled database already knows."""
    gvas = gvas_with([map_object("obj1", container_id="c1", kind="ItemChest_02")])
    assert extract_container_ownership(gvas)["c1"]["kindName"] == "Metal Chest"


# ─── Summaries ───────────────────────────────────────────────────


def test_summarises_one_base():
    containers = {"c1": slots(("Wood", 100), ("Stone", 50)), "c2": slots(("Wood", 20))}
    ownership = {
        "c1": {"baseCampId": "base-a", "kind": "ItemChest", "kindName": "Wooden Chest",
               "category": "chest", "worldPlaced": False},
        "c2": {"baseCampId": "base-a", "kind": "ItemChest", "kindName": "Wooden Chest",
               "category": "chest", "worldPlaced": False},
    }

    summary = summarise_base_storage(containers, ownership, [base("base-a")])[0]

    assert summary["containerCount"] == 2
    assert summary["itemCount"] == 170
    assert summary["uniqueItems"] == 2
    assert summary["items"][0] == {"itemId": "Wood", "itemName": "Wood", "count": 120}


def test_world_placed_containers_are_excluded_from_base_totals():
    containers = {"base": slots(("Wood", 10)), "wild": slots(("Wood", 9999))}
    ownership = {
        "base": {"baseCampId": "base-a", "kind": "ItemChest", "kindName": "Wooden Chest",
                 "category": "chest", "worldPlaced": False},
        "wild": {"baseCampId": "", "kind": "ItemChest", "kindName": "Wooden Chest",
                 "category": "chest", "worldPlaced": True},
    }

    summary = summarise_base_storage(containers, ownership, [base("base-a")])[0]
    assert summary["itemCount"] == 10


def test_a_base_with_no_storage_still_appears():
    """Reporting zero is an answer; dropping the row looks like a parse failure."""
    summaries = summarise_base_storage({}, {}, [base("empty-base")])

    assert len(summaries) == 1
    assert summaries[0]["containerCount"] == 0
    assert summaries[0]["itemCount"] == 0
    assert summaries[0]["fillPercent"] == 0.0


def test_fill_percent_counts_occupied_slots():
    containers = {"c1": slots(("Wood", 1), ("", 0), ("", 0), ("Stone", 1))}
    ownership = {"c1": {"baseCampId": "b", "kind": "ItemChest", "kindName": "Wooden Chest",
                        "category": "chest", "worldPlaced": False}}

    summary = summarise_base_storage(containers, ownership, [base("b")])[0]
    assert (summary["usedSlots"], summary["totalSlots"], summary["fillPercent"]) == (2, 4, 50.0)


def test_a_container_missing_from_the_parse_is_ignored():
    """Three containers dangle on the reference world; they must not raise."""
    ownership = {"gone": {"baseCampId": "b", "kind": "ItemChest", "kindName": "Wooden Chest",
                          "category": "chest", "worldPlaced": False}}

    summary = summarise_base_storage({}, ownership, [base("b")])[0]
    assert summary["containerCount"] == 0


def test_bases_are_ranked_by_what_they_hold():
    containers = {"small": slots(("Wood", 1)), "big": slots(("Wood", 500))}
    ownership = {
        "small": {"baseCampId": "a", "kind": "ItemChest", "kindName": "Wooden Chest",
                  "category": "chest", "worldPlaced": False},
        "big": {"baseCampId": "b", "kind": "ItemChest", "kindName": "Wooden Chest",
                "category": "chest", "worldPlaced": False},
    }

    summaries = summarise_base_storage(containers, ownership, [base("a"), base("b")])
    assert [s["baseId"] for s in summaries] == ["b", "a"]


# ─── Base naming ─────────────────────────────────────────────────


def test_the_games_placeholder_name_is_replaced():
    """Every base on the reference world carries this untranslated placeholder."""
    name, player_named = _base_name("新規生成拠点テンプレート名3(仮)", 4)
    assert name == "Base Camp 5"
    assert player_named is False


def test_a_real_name_is_kept():
    name, player_named = _base_name("Ore Outpost", 0)
    assert name == "Ore Outpost"
    assert player_named is True


def test_an_empty_name_falls_back_to_position():
    assert _base_name("", 2) == ("Base Camp 3", False)
    assert _base_name("   ", 2) == ("Base Camp 3", False)
