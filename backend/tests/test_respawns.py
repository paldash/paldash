"""
Respawn pins (#141): the timer classification, the GUID join, and the
honesty rules around both.

The bundle half is pinned against the SHIPPED worldobjects bundle; the save
half against a constructed payload, because the classification is pure logic
over sentinels this project has already been bitten by (-1 idle, 0 never
written, DateTime.MaxValue "never" — 87 million game-hours wearing a number).
"""

import gzip
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import parser as pparser  # noqa: E402
import respawns  # noqa: E402
import worldobjects  # noqa: E402


# ─── The parser's classification ─────────────────────────

class _FakeGvas:
    def __init__(self, spawners, clock=1000 * 36_000_000_000):
        entry = {
            "key": {"InternalId": {"value": "00000000-0000-0000-0000-000000000000"}},
            "value": {"SpawnerDataMapByLevelObjectInstanceId": {"value": spawners}},
        }
        self.properties = {"worldSaveData": {"value": {
            "MapObjectSpawnerInStageSaveData": {"value": [entry]},
            "GameTimeSaveData": {"value": {"GameDateTimeTicks": {"value": clock}}},
        }}}


def _spawner(sid, ticks):
    return {"key": sid, "value": {"ItemMap": {"value": [
        {"key": 0, "value": {"NextLotteryGameTime": {"value": ticks}}},
    ]}}}


def test_sentinels_classify_and_only_pending_travels():
    hour = 36_000_000_000
    gvas = _FakeGvas([
        _spawner("aa" * 16, -1),                       # standing
        _spawner("bb" * 16, 0),                        # never written
        _spawner("cc" * 16, 3155378975999999999),      # DateTime.MaxValue
        _spawner("dd" * 16, 500 * hour),               # already due
        _spawner("ee" * 16, 1010 * hour),              # pending
    ])
    state = pparser.extract_respawn_state(gvas)
    assert state["counts"] == {
        "idle": 1, "neverWritten": 1, "neverRespawns": 1, "due": 1,
        "pending": 1, "otherStages": 0,
    }
    assert [r["id"] for r in state["pending"]] == ["ee" * 16]


def test_a_dungeon_stage_is_counted_not_listed():
    hour = 36_000_000_000
    gvas = _FakeGvas([_spawner("aa" * 16, 1010 * hour)])
    entry = gvas.properties["worldSaveData"]["value"][
        "MapObjectSpawnerInStageSaveData"]["value"][0]
    entry["key"]["InternalId"]["value"] = "12345678-1234-1234-1234-123456789abc"
    state = pparser.extract_respawn_state(gvas)
    # An instanced stage has no world position, so a pin for it would be a
    # guess. Counted so the total still adds up.
    assert state["counts"]["otherStages"] == 1
    assert state["pending"] == []


# ─── The bundle's GUIDs ──────────────────────────────────

def _bundle():
    with gzip.open(worldobjects.DATA_PATH, "rt", encoding="utf-8") as f:
        return json.load(f)


def test_bundle_guids_are_nonzero_and_unique():
    data = _bundle()
    guids = [o["guid"] for g in data["groups"].values()
             for o in g["objects"] if o.get("guid")]
    assert len(guids) > 30_000, "the GUID capture regressed to a fraction"
    assert len(set(guids)) == len(guids), "duplicate instance GUIDs shipped"
    assert "0" * 32 not in set(guids)


def test_gatherable_groups_carry_guids_and_placed_only_groups_do_not():
    data = _bundle()
    groups = data["groups"]
    for name in ("ore", "treasure", "junk", "palegg"):
        assert any(o.get("guid") for o in groups[name]["objects"]), name
    # The first ungated run captured a SHARED content GUID from these and the
    # duplicate refusal fired — they must stay uncaptured.
    for name in ("palspawner", "npc", "fieldboss", "supply"):
        assert not any(o.get("guid") for o in groups[name]["objects"]), name


# ─── The join ────────────────────────────────────────────

def test_report_joins_and_counts_the_unmapped(monkeypatch):
    import savecache

    data = _bundle()
    real_guid = next(o["guid"] for o in data["groups"]["ore"]["objects"]
                     if o.get("guid"))
    hour = 36_000_000_000
    monkeypatch.setattr(savecache, "get_data", lambda auto=True: {
        "respawnState": {
            "clockTicks": 1000 * hour,
            "pending": [
                {"id": real_guid, "readyTicks": 1012 * hour},
                {"id": "f" * 32, "readyTicks": 1005 * hour},
            ],
            "counts": {"pending": 2},
        },
    })
    found = respawns.report()
    assert len(found["pins"]) == 1
    pin = found["pins"][0]
    assert pin["category"] == "ore" and pin["inGameHours"] == 12.0
    assert isinstance(pin["x"], (int, float))
    # The unresolvable id is counted, never guessed onto the map.
    assert found["pendingUnmapped"] == 1


def test_no_parse_is_none_not_an_empty_layer(monkeypatch):
    import savecache

    monkeypatch.setattr(savecache, "get_data", lambda auto=True: {})
    assert respawns.report() is None


# ─── Against the real world ──────────────────────────────

import pytest  # noqa: E402


@pytest.mark.integration
def test_refworld_spawner_keys_resolve_against_the_bundle(palsav_available, refworld):
    """
    The claim the whole feature rests on: the save's spawner keys and the
    bundle's actor GUIDs are one id space. 30,708 of 31,774 (96.6%) when the
    join was built; anything under 90% means the locator or the game moved.
    """
    gvas = pparser.load_gvas(os.path.join(refworld, "Level.sav"))
    world = gvas.properties["worldSaveData"]["value"]
    node = world.get("MapObjectSpawnerInStageSaveData") or {}
    keys = set()
    for entry in node.get("value") or []:
        for spawner in pparser._v(
            entry, "value", "SpawnerDataMapByLevelObjectInstanceId",
            "value", default=[],
        ) or []:
            k = str(spawner.get("key") or "").lower().replace("-", "")
            if k:
                keys.add(k)
    assert len(keys) > 30_000

    index = respawns._guid_index()
    resolved = sum(1 for k in keys if k in index)
    assert resolved / len(keys) > 0.90, (
        f"only {resolved} of {len(keys)} spawner keys resolve"
    )
