"""
The dungeon guide (#136) — pinned against the SHIPPED bundle + joins.

The load-bearing claims: every enemy spawner resolved (the extractor refuses
otherwise, but the bundle on disk is what serves), every loot lottery joins
economy, and the two honesty flags the UI keys on actually travel.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import dungeons  # noqa: E402


def test_fifteen_areas_and_no_debug_or_stub_leaked():
    c = dungeons.catalogue()
    ids = {a["areaId"] for a in c["areas"]}
    assert len(ids) == 15
    assert not any(i.startswith("TestDebug") for i in ids)
    # The superseded stubs (old naming scheme) must not resurface as empty
    # guide entries — Desert01 was replaced by Pocketpair's own Dessert001.
    assert "Desert01" in {"Desert01"} - ids
    assert "Dessert001" in ids


def test_every_loot_lottery_joins_economy():
    c = dungeons.catalogue()
    assert c["missingLotteries"] == []
    with_items = [l for a in c["areas"] for l in a["loot"] if l["items"]]
    assert with_items, "no loot resolved at all — the join is dead, not clean"


def test_every_enemy_group_has_a_roster_with_names():
    c = dungeons.catalogue()
    for a in c["areas"]:
        for g in a["enemies"]:
            assert g["roster"], f"{a['areaId']}/{g['spawnerName']} lost its roster"
            for r in g["roster"]:
                assert r["name"], f"{a['areaId']}: unnamed roster entry {r['id']}"


def test_the_honesty_flags_travel():
    c = dungeons.catalogue()
    assert c["weightIsWithinGroup"] is True
    assert c["namedByGame"] is False
    for a in c["areas"]:
        assert a["named"] is False
        # The label is a humanised ID, not an invented name — it must still
        # contain the id's own stem so nobody mistakes it for a game string.
        assert a["label"].replace(" ", "") == a["areaId"]


def test_slot_share_sums_to_one_per_slot():
    c = dungeons.catalogue()
    area = next(a for a in c["areas"] if a["areaId"] == "Sakura001")
    items = area["loot"][0]["items"]
    by_slot: dict = {}
    for it in items:
        by_slot.setdefault(it["slot"], 0.0)
        by_slot[it["slot"]] += it["slotShare"] or 0.0
    for slot, total in by_slot.items():
        assert abs(total - 1.0) < 1e-6, (slot, total)
