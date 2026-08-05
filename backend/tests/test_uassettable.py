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
