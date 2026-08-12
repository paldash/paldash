"""
An asset's CLASS, resolved through the import map.

`upackage` parsed the export map from the start and never parsed the import
map — so an export's class, which is an `FPackageIndex` at offset 0 of its
record and negative for an import, was simply not reachable. Every census of the
pak therefore enumerated by **path convention** (`DT_*`, `/DataTable/`, a prefix
exclusion list for art), and that has cost real coverage four separate times:

    searched DataTables      -> missed BP_PalGameSetting's 347 constants
    searched DataTables      -> missed DA_BreedingItemEffectData
    searched `BP_Pal_*`      -> missed the species blueprints, `BP_<Species>`
    globbed `/DataTable/`    -> missed 7 tables that live elsewhere

**The stride is the whole risk, and it fails silently.** `FObjectImport` is 32
bytes in UE5.1+ (`bImportOptional` was appended) and 28 before it. At 28 a
DataTable still resolves correctly, because its class import happens to sit at
index 0 — while blueprints come back as unrelated asset paths. There is no
exception and no version field in the file to branch on, so the acceptance
criterion is this test: known assets must resolve to their known classes.

These skip without the pak, which is gitignored, so a clean checkout runs green.
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

CONTENT = "../../../Pal/Content/"

# Deliberately one of each kind this project actually reads, because the stride
# error is invisible on a DataTable alone.
KNOWN = [
    ("Pal/DataTable/Character/DT_PalMonsterParameter.uasset", "CompositeDataTable"),
    ("Pal/DataTable/PassiveSkill/DT_PassiveSkill_Main.uasset", "CompositeDataTable"),
    ("Pal/DataTable/Item/DT_ItemDataTable.uasset", "CompositeDataTable"),
    ("Pal/Blueprint/System/BP_PalGameSetting.uasset", "BlueprintGeneratedClass"),
    ("Pal/Blueprint/Character/Monster/PalActorBP/Alpaca/BP_Alpaca.uasset",
     "BlueprintGeneratedClass"),
    ("Pal/DataAsset/MapObject/CapabilityData/DA_PalBuildObjectCapabilityData.uasset",
     "PalBuildObjectCapabilityDataAsset"),
    ("Pal/DataAsset/MapObject/Breeding/DA_BreedingItemEffectData.uasset",
     "PalBreedingItemEffectDataAsset"),
]


@pytest.fixture(scope="module")
def pak():
    if not os.path.exists(SERVER_PAK):
        pytest.skip("server pak not present — integration test skipped")
    try:
        from palpak import Pak
    except ImportError:
        pytest.skip("palpak unavailable")
    return Pak(SERVER_PAK)


@pytest.mark.integration
@pytest.mark.parametrize("path,expected", KNOWN)
def test_known_assets_resolve_to_their_known_class(pak, path, expected):
    import upackage

    package = upackage.read(pak.read(CONTENT + path))
    assert package.export_class() == expected


@pytest.mark.integration
def test_a_composite_table_is_not_reported_as_a_plain_one(pak):
    """
    `_Common` twins are not a Palworld quirk — they are the parent rows of a
    `CompositeDataTable`, which is why the pairs are byte-identical on every
    non-text table. Collapsing the two classes would erase the explanation.
    """
    import upackage

    composite = upackage.read(
        pak.read(CONTENT + "Pal/DataTable/Character/DT_PalMonsterParameter.uasset")
    ).export_class()
    assert composite == "CompositeDataTable"


@pytest.mark.integration
def test_the_import_stride_is_not_28(pak):
    """
    The regression guard for the one mistake that produces no exception.

    Re-resolves every known asset with the pre-UE5.1 stride and asserts the
    result is WRONG for at least one of them — so if somebody "simplifies" the
    constant back to 28, this fails rather than the census quietly mislabelling
    seven thousand blueprints.
    """
    import struct

    import upackage

    package = upackage.read(
        pak.read(CONTENT + "Pal/Blueprint/System/BP_PalGameSetting.uasset")
    )
    (class_index,) = struct.unpack_from("<i", package.raw, package.export_offset)
    assert class_index < 0, "expected the class to be an import"

    at_28 = package.import_offset + (-class_index - 1) * 28
    (name_index,) = struct.unpack_from("<i", package.raw, at_28 + 20)
    wrong = package.names[name_index] if 0 <= name_index < package.name_count else None

    assert wrong != "BlueprintGeneratedClass", (
        "stride 28 happened to resolve correctly — this guard no longer "
        "discriminates and needs a different asset"
    )
    assert package.export_class() == "BlueprintGeneratedClass"
