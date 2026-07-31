"""
Which Pals work at which base.

This was documented as impossible and shipped as a guild-wide figure stamped
onto every base — so a three-base guild's 613 Pals were reported three times and
the Bases tab summed the reference world to **5,152 of 1,905**.

It is not impossible. Every base's `WorkerDirector` names the character container
holding its workers, and the join is exact. The catch is that `WorkerDirector.RawData`
is an opaque `ByteProperty`, so the id is read at a **measured offset** rather than
by name — which is only acceptable with a verification attached, and that
verification is what most of these tests are about.

The measured shape, on all 11 reference-world bases: a 118-byte blob, the base
camp's own id at 0, the worker container id at 98.
"""

from __future__ import annotations

import struct

import pytest

import parser as P


def _guid_bytes(text: str) -> bytes:
    """A GUID as Palworld writes it: four little-endian uint32s."""
    hexed = text.replace("-", "")
    return b"".join(
        struct.pack("<I", int(hexed[i:i + 8], 16)) for i in (0, 8, 16, 24)
    )


BASE_ID = "c6d29067-824d-ef7c-fdd1-b3a49c3e248b"
CONTAINER_ID = "c7de2256-b6e7-4d7a-9654-a590cde94aad"


def _blob(base_id: str = BASE_ID, container_id: str = CONTAINER_ID,
          length: int = 118) -> bytes:
    out = bytearray(length)
    out[0:16] = _guid_bytes(base_id)
    if container_id is not None:
        out[98:114] = _guid_bytes(container_id)
    return bytes(out)


class FakeGvas:
    def __init__(self, containers, camps):
        self.properties = {
            "worldSaveData": {
                "value": {
                    "CharacterContainerSaveData": {
                        "value": [
                            {"key": {"ID": {"value": c}}, "value": {}}
                            for c in containers
                        ]
                    },
                    "BaseCampSaveData": {"value": camps},
                }
            }
        }


def _camp(base_id: str, blob: bytes | None) -> dict:
    value: dict = {"RawData": {"value": {"id": base_id}}}
    if blob is not None:
        value["WorkerDirector"] = {"value": {"RawData": {"value": {"values": blob}}}}
    return {"value": value}


# ─── The happy path ───────────────────────────────────────


def test_a_base_resolves_to_its_worker_container():
    gvas = FakeGvas([CONTAINER_ID], [_camp(BASE_ID, _blob())])
    assert P.extract_base_workers(gvas) == {CONTAINER_ID: BASE_ID}


def test_the_id_is_read_at_the_measured_offset():
    """
    Not a scan. A substring search would find the id wherever it happened to
    sit and would keep "working" through a layout change that moved it —
    silently attributing whatever was at the new offset instead.
    """
    blob = bytearray(_blob())
    # Same bytes, moved four along. The offset read must now miss.
    blob[98:114] = b"\x00" * 16
    blob[102:118] = _guid_bytes(CONTAINER_ID)
    gvas = FakeGvas([CONTAINER_ID], [_camp(BASE_ID, bytes(blob))])
    assert P.extract_base_workers(gvas) == {}


# ─── Verification: a wrong read must yield nothing ────────


def test_an_unknown_container_id_is_dropped():
    """
    The check that makes reading by offset defensible. A layout change decodes
    sixteen arbitrary bytes into a well-formed GUID that resolves to no
    container — so it is discarded rather than attributed to a base.
    """
    gvas = FakeGvas(["11111111-2222-3333-4444-555555555555"],
                    [_camp(BASE_ID, _blob())])
    assert P.extract_base_workers(gvas) == {}


def test_a_short_blob_is_skipped_rather_than_unpacked():
    gvas = FakeGvas([CONTAINER_ID], [_camp(BASE_ID, _blob(length=60))])
    assert P.extract_base_workers(gvas) == {}


def test_a_base_with_no_worker_director_is_skipped():
    gvas = FakeGvas([CONTAINER_ID], [_camp(BASE_ID, None)])
    assert P.extract_base_workers(gvas) == {}


def test_no_bases_is_not_an_error():
    assert P.extract_base_workers(FakeGvas([], [])) == {}


def test_degrading_is_total_rather_than_partial(caplog):
    """
    A game update that moves the field must produce *no* attribution, not some.

    Half-attributed bases would be worse than none: the tab would show plausible
    per-base numbers that quietly excluded whichever bases failed to decode, and
    nothing would look wrong. Zero results fall back to the guild figure, which
    is at least labelled as one.
    """
    camps = [_camp(f"{i}0000000-0000-0000-0000-00000000000{i}", _blob(length=40))
             for i in range(1, 4)]
    gvas = FakeGvas([CONTAINER_ID], camps)
    assert P.extract_base_workers(gvas) == {}
    assert "WorkerDirector" in caplog.text


# ─── Against the real world ───────────────────────────────


@pytest.mark.integration
@pytest.mark.slow
def test_every_reference_base_resolves(level_sav):
    """
    11 of 11, one container each, and every container distinct — a base sharing
    another's worker container would mean the offset is reading something else.
    """
    gvas = P.load_gvas(level_sav, include_items=False)
    workers = P.extract_base_workers(gvas)

    camps = P._v(P._world_save_data(gvas), "BaseCampSaveData", "value", default=[])
    assert len(workers) == len(camps) == 11
    assert len(set(workers.values())) == 11, "each base must own its own container"


@pytest.mark.integration
@pytest.mark.slow
def test_worker_containers_are_neither_palboxes_nor_parties(level_sav):
    """
    The independent check on the whole result.

    A palbox holds 960 and a party 5. If the offset were reading something else,
    the ids it produced would land on those instead — so the fact that all
    eleven are the 20/16/13/8-slot containers, and *only* those, is evidence the
    field means what it appears to mean.
    """
    gvas = P.load_gvas(level_sav, include_items=False)
    workers = P.extract_base_workers(gvas)

    capacity = {}
    for entry in P._v(P._world_save_data(gvas),
                      "CharacterContainerSaveData", "value", default=[]):
        cid = str(P._v(entry, "key", "ID", "value") or "").lower()
        capacity[cid] = P._v(entry, "value", "SlotNum", "value")

    sizes = sorted(capacity[c] for c in workers)
    assert sizes == [8, 13, 13, 16, 16, 20, 20, 20, 20, 20, 20]


@pytest.mark.integration
@pytest.mark.slow
def test_pals_at_bases_are_a_small_slice_of_the_world(level_sav):
    """
    165 of 1,905 on the reference world — and that gap is the whole point.

    The old per-base figure was the *guild* total repeated per base, which
    summed to 5,152 across eleven bases: nearly three times the number of Pals
    that exist. A number larger than the population it counts is the shape of
    bug this pins.
    """
    gvas = P.load_gvas(level_sav, include_items=False)
    workers = P.extract_base_workers(gvas)
    _, pals = P.extract_characters(gvas)

    deployed = sum(
        1 for p in pals if str(p.get("containerId") or "").lower() in workers
    )
    assert deployed == 165
    assert deployed < len(pals)
