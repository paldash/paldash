"""
Decoding server-pak DataTables.

These are the tests for the finding that overturned "rates, thresholds and
coordinates are locked" — a conclusion measured on the CLIENT pak and true only
of it. The server pak writes tagged properties, so its tables decode completely.

They skip without the pak, which is gitignored (4.8 GB), so a clean checkout
still runs green.

**The assertions are deliberately about values, not just shapes.** A drifted
tagged-property reader produces plausible output rather than an exception — the
failure mode that cost two attempts was row names coming out as real Pal ids with
nonsense suffixes. A test that only checked "we got some rows" would have passed
on all of them.
"""

from __future__ import annotations

import os
import struct
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS = os.path.join(PROJECT_ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

SERVER_PAK = os.path.join(
    PROJECT_ROOT, "refs", "palworld", "Pal", "Content", "Paks", "Pal-LinuxServer.pak"
)


@pytest.fixture(scope="module")
def pak():
    if not os.path.exists(SERVER_PAK):
        pytest.skip("server pak not present — integration test skipped")
    try:
        from palpak import Pak
    except ImportError:
        pytest.skip("palpak unavailable")
    return Pak(SERVER_PAK)


@pytest.fixture(scope="module")
def tables(pak):
    import uassettable

    return uassettable, {
        os.path.basename(p)[:-7]: p for p in uassettable.data_tables(pak)
    }


@pytest.mark.integration
@pytest.mark.slow
def test_a_table_decodes_to_real_values(pak, tables):
    """
    `DT_PalShopCreateData`, the table the technique was proved on. Values, not
    counts: a drifted reader returns rows too.
    """
    mod, paths = tables
    rows = mod.read_table(pak, paths["DT_PalShopCreateData"])
    assert len(rows) == 8
    desert = rows["Desert_00"]
    assert desert["MinCharacterLevel"] == 40
    assert desert["MaxCharacterLevel"] == 45
    assert {p["Key"] for p in desert["CharacterIDArray"]} >= {"CactusDoll", "DarkCrow"}


@pytest.mark.integration
@pytest.mark.slow
def test_the_friendship_thresholds_are_readable(pak, tables):
    """
    AGENTS.md recorded this table as yielding "**nothing** — the thresholds are
    numbers". That was the client pak. The numbers are right here.
    """
    mod, paths = tables
    rows = mod.read_table(pak, paths["DT_FriendshipRankTable"])
    assert rows["Friendship_Rank_Minus3"]["RequiredPoint"] == -10000
    assert all("RequiredPoint" in r for r in rows.values())


@pytest.mark.integration
@pytest.mark.slow
def test_drop_tables_carry_weights_not_just_item_names(pak, tables):
    """
    The whole point of #35. An association list would have been useful; a weight
    makes it a drop *rate*.
    """
    mod, paths = tables
    rows = mod.read_table(pak, paths["DT_ItemLotteryDataTable"])
    assert len(rows) > 1000
    weighted = [r for r in rows.values() if isinstance(r.get("WeightInSlot"), float)]
    assert len(weighted) > 1000, "weights should be present on essentially every row"
    assert all(r.get("StaticItemId") for r in weighted[:50])


@pytest.mark.integration
@pytest.mark.slow
def test_technology_rows_carry_cost_and_what_they_unlock(pak, tables):
    mod, paths = tables
    rows = mod.read_table(pak, paths["DT_TechnologyRecipeUnlock_Common"])
    assert len(rows) > 100
    unlocking = [
        r for r in rows.values()
        if r.get("UnlockItemRecipes") or r.get("UnlockBuildObjects")
    ]
    assert len(unlocking) > 100
    assert any(isinstance(r.get("Cost"), int) for r in rows.values())


@pytest.mark.integration
@pytest.mark.slow
def test_shop_rows_carry_stock_and_price(pak, tables):
    mod, paths = tables
    rows = mod.read_table(pak, paths["DT_ItemShopCreateData_Common"])
    products = [p for r in rows.values() for p in (r.get("productDataArray") or [])
                if isinstance(p, dict)]
    assert products, "shops should list products"
    assert any("StaticItemID" in p for p in products)
    assert any("Stock" in p for p in products)


@pytest.mark.integration
@pytest.mark.slow
def test_an_unwalkable_struct_is_labelled_rather_than_losing_the_table(pak, tables):
    """
    THIS TEST USED TO ASSERT THE OPPOSITE, and the old assertion was costing
    real data.

    `DT_BossSpawnerLoactionData` holds a natively-serialised `Vector`, which has
    no tags, so walking into it raises. The reader used to let that abort the
    whole table on the grounds that "half a tagged decode reads as real data —
    coordinates as name indices". The danger was real; the response was too
    broad. **243 of the pak's 912 DataTables were being discarded for it**, this
    one included — and this one carries the field boss levels that AGENTS.md
    describes as unavailable.

    The struct's LENGTH is in its own tag, so skipping lands exactly on the next
    tag whatever the interior holds. So the fear does not apply: nothing is read
    as the wrong type, every surrounding field stays correctly placed, the
    end-of-buffer check still proves the row alignment, and the unread interior
    is labelled `_opaque` instead of being given a value.

    What must never come back is a *plausible* value for something unread.
    """
    mod, paths = tables
    rows = mod.read_table(pak, paths["DT_BossSpawnerLoactionData"])
    assert len(rows) > 100

    row = next(iter(rows.values()))
    assert row["CharacterID"].startswith("BOSS_")
    assert isinstance(row["Level"], int) and row["Level"] > 0
    # The dangerous field says it was not read, rather than carrying a number.
    assert "_opaque" in row["Location"]


@pytest.mark.integration
@pytest.mark.slow
def test_an_unwalkable_struct_costs_one_field_not_the_whole_table(pak, tables):
    """
    THE REFUSAL ABOVE WAS COSTING 243 OF 912 TABLES, and that was too much.

    A natively-serialised struct has no tags, so walking into one raises — and
    the exception used to abort the entire table. But the struct's LENGTH is in
    its own tag, so skipping lands exactly on the next tag whatever the interior
    holds. Every surrounding property stays correctly placed, the end-of-buffer
    check still applies, and the unread interior is labelled `_opaque` rather
    than given a plausible value.

    That is why this is not the partial decode the test above forbids: nothing
    is guessed, one field says "not read", and the table's own alignment is
    still proven by where the walk ends.
    """
    mod, paths = tables
    rows = mod.read_table(pak, paths["DT_PalWildSpawner"])
    assert len(rows) > 1000

    opaque = [
        (row_name, field)
        for row_name, row in rows.items()
        for field, value in row.items()
        if isinstance(value, dict) and "_opaque" in value
    ]
    if opaque:
        # An opaque marker must SAY it is unread rather than look like data.
        _, field = opaque[0]
        sample = next(iter(rows.values()))[field]
        assert sample["_opaque"].endswith("B")


@pytest.mark.integration
@pytest.mark.slow
def test_the_pak_has_the_tables_worth_mining(pak, tables):
    """A sanity floor on discovery, so a path change surfaces here."""
    _mod, paths = tables
    assert len(paths) > 400
    for expected in (
        "DT_ItemLotteryDataTable",
        "DT_TechnologyRecipeUnlock_Common",
        "DT_ItemShopCreateData_Common",
        "DT_PalShopCreateData",
    ):
        assert expected in paths


# ─── MapProperty: the last opaque container ──────────────────────


@pytest.mark.integration
@pytest.mark.slow
def test_a_map_property_decodes_and_the_work_curves_are_NOT_shared(pak):
    """
    **This test exists because the answer it records overturned a shipped
    assumption.** `workrank` applied one curve to all thirteen work types,
    labelled `stated: false`, on the evidence that the three the game states
    separately are identical. The map they were hiding in says otherwise for
    eight of them.

    The decode's verification is threefold and none of it is a threshold:

    1. The map walk must consume **exactly** its tag's declared size, or the
       decoder refuses and labels the property opaque.
    2. The enclosing CDO walk still terminates at 41,416 of 41,420 bytes — a
       1,361-byte map decoded wrongly in the middle of a 41 KB property list
       does not leave the remainder parsing.
    3. The keys are enum names that **complete a known set**: eleven of them,
       minus the pseudo-entry `Anyone`, plus the three standalone types, are
       exactly the 13 work suitabilities the species table ships.
    """
    import upackage
    import uassettable

    path = next(p for p in pak.files if p.endswith("BP_PalGameSetting.uasset"))
    package = upackage.read(pak.read(path))
    uexp = pak.read(path.replace(".uasset", ".uexp"))
    cdo = next(e for e in package.exports if e.name.startswith("Default__"))

    reader = uassettable._Reader(cdo.data(uexp), package.names)
    props = uassettable._properties(reader)

    curves = props.get("WorkSuitabilityDefineDataMap")
    assert isinstance(curves, dict), f"map did not decode: {curves!r}"
    assert len(curves) == 11

    standard = [0, 50, 70, 100, 140, 190, 260, 370, 510, 720, 1000]
    speeds = {
        str(k).rsplit("::", 1)[-1]: (v or {}).get("CraftSpeeds")
        for k, v in curves.items()
    }

    # The correction itself, stated as values rather than as a count.
    assert speeds["Watering"] == standard, "Watering does share the stated curve"
    assert speeds["Handcraft"] == [0, 50, 80, 140, 240, 400, 680, 1100, 1900, 3200, 5400]
    assert speeds["Transport"] == [0, 2, 5, 10, 20, 40, 70, 120, 200, 320, 500]
    assert speeds["MonsterFarm"][0] == 10, "the Ranch produces at rank 0"
    assert speeds["Anyone"] == [100] * 11, "the one entry that is not a work type"

    differ = sum(1 for name, c in speeds.items() if c != standard)
    assert differ == 8, f"expected 8 of 11 to differ from the stated curve, got {differ}"

    # Every curve is 11 entries — one per rank 0-10, matching WorkSuitabilityMaxRank.
    assert all(len(c) == 11 for c in speeds.values())


@pytest.mark.integration
@pytest.mark.slow
def test_a_map_that_cannot_be_walked_is_LABELLED_not_truncated(pak):
    """
    The refusal, and the reason a map needs its own element reader rather than
    reusing `_value`. A map element carries no tag, so there is no size to snap
    past and no way to skip a type we do not understand — a decoder that stopped
    at the first unfamiliar entry would return a dict that looks complete.

    So `_map_half` raises on an unhandled type and the caller labels the whole
    property. A partial map is never returned.
    """
    import uassettable

    reader = uassettable._Reader(b"\x00" * 32, ["None"])
    with pytest.raises(uassettable.TableError):
        uassettable._map_half(reader, "DelegateProperty")


def test_a_garbage_map_header_refuses_instead_of_spinning():
    """
    **`NumKeysToRemove` is 0 on every map in this pak, which is exactly why it
    was left unchecked — and that was the bug.** On a misaligned read it is four
    arbitrary bytes, and `for _ in range(that)` spins over ~2 billion iterations
    instead of raising. Found by pointing `mine-assets.py` at 7,643 blueprints:
    the first asset whose layout this reader does not expect took the whole
    sweep down with no error and no output.

    The bound is a tripwire, not a claim about the data. Nothing here has a map
    remotely near it, so exceeding it means the offset is wrong — which is the
    one thing this reader is built to refuse rather than paper over.
    """
    import uassettable

    # A plausible tag followed by a huge NumKeysToRemove.
    body = struct.pack("<ii", 0x7FFFFFFF, 0) + b"\x00" * 64
    reader = uassettable._Reader(body, ["None", "IntProperty"])
    out = uassettable._value(
        reader, "MapProperty", len(body), {"key": "NameProperty", "value": "IntProperty"}
    )
    assert isinstance(out, str) and out.startswith("<MapProperty")
    assert "NumKeysToRemove" in out or "undecoded" in out


# ─── Refusing beats hanging ──────────────────────────────


@pytest.mark.integration
def test_a_property_walk_terminates_on_the_two_assets_that_used_to_hang(pak):
    """
    THE REGRESSION GUARD FOR A TWO-HOUR BUG.

    `_properties` was an unbounded `while True` with no progress check and no
    bounds check. On `BP_BuildObject_EnergyStorage_Electric` and
    `BP_PalMonsterCaptureSet` it never terminated — a 10-minute run on those two
    alone did not finish — and that is the entire reason `scripts/mine-assets.py`
    had never completed a sweep or committed its index. Everything else about
    that script is fast: 7,992 assets decode in about two seconds.

    Second occurrence of the shape; the unchecked `NumKeysToRemove` is the
    first. **A decoder that cannot make progress must refuse, not loop** — a
    hang presents as slowness rather than as a bug, so it gets waited out.

    Timed, not merely called: a return is not enough if it takes a minute.
    """
    import time

    import upackage
    from uassettable import _properties, _Reader

    for path in (
        "../../../Pal/Content/Pal/Blueprint/MapObject/BuildObject/"
        "BP_BuildObject_EnergyStorage_Electric",
        "../../../Pal/Content/Pal/Blueprint/UI/SceneCaptureWidget/"
        "BP_PalMonsterCaptureSet",
    ):
        package = upackage.read(pak.read(path + ".uasset"))
        uexp = pak.read(path + ".uexp")
        export = next(e for e in package.exports if e.name.startswith("Default__"))

        started = time.time()
        props = _properties(_Reader(export.data(uexp), package.names))
        elapsed = time.time() - started

        assert elapsed < 5, f"{path} took {elapsed:.1f}s — the guard is gone"
        assert props, "should decode real properties, not merely return"


def test_a_property_that_consumes_nothing_refuses_rather_than_looping():
    """
    The progress guard, exercised directly rather than via an asset that
    happens to trip it — so it keeps testing something if the pak changes.

    A zero-length body has no `None` terminator, so the bounds guard fires
    first; both paths must raise rather than spin.
    """
    from uassettable import TableError, _properties, _Reader

    with pytest.raises(TableError):
        _properties(_Reader(b"", []))
