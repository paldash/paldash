"""
The `TransportItemDirector` decoder — and mostly its refusals.

`scripts/decode-basecamp-modules.py` reads a fixed-layout blob at measured
offsets, which this project only allows with a verification attached. In the
script that verification is external (every decoded position must land inside
its own base). Here it is internal: the walk must consume the buffer exactly,
and anything else must return **None** rather than a partial answer.

That distinction is the whole point. A decoder that returns what it managed is
how a changed layout becomes a confident wrong answer about which resource a
base is hauling.
"""

import importlib.util
import os
import struct
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_spec = importlib.util.spec_from_file_location(
    "decode_basecamp_modules",
    os.path.join(ROOT, "scripts", "decode-basecamp-modules.py"),
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["decode_basecamp_modules"] = _mod
_spec.loader.exec_module(_mod)

decode_transport = _mod.decode_transport
PAD = _mod.PAD_BYTES


def _entry(item: str, unknown_a: int = 1, unknown_b: int = 5, pad: bytes | None = None):
    encoded = item.encode("ascii") + b"\0"
    return (
        struct.pack("<i", unknown_a)
        + struct.pack("<i", len(encoded))
        + encoded
        + (pad if pad is not None else b"\0" * PAD)
        + struct.pack("<i", unknown_b)
        + struct.pack("<ddd", -276000.0, 88000.0, 5800.0)
    )


def _blob(*entries: bytes) -> bytes:
    return struct.pack("<i", len(entries)) + b"".join(entries) + struct.pack("<i", 0)


def test_the_observed_refworld_blob_is_82_bytes():
    """
    The size arithmetic is the first check, and it is what proves the layout
    rather than merely fitting it: `4 + 4 + strlen + 32 + 4 + 24` per entry.
    A one-entry `Wheat` blob must come to exactly the 82 bytes on disk.
    """
    blob = _blob(_entry("Wheat"))
    assert len(blob) == 82
    decoded = decode_transport(blob)
    assert decoded is not None
    assert decoded[0]["item"] == "Wheat"
    assert decoded[0]["unknownA"] == 1
    assert decoded[0]["unknownB"] == 5


def test_two_entries_reproduce_the_observed_159_bytes():
    """`Coal` + `CopperOre`, the pair on the 07-29 snapshot."""
    blob = _blob(_entry("Coal"), _entry("CopperOre"))
    assert len(blob) == 159
    decoded = decode_transport(blob)
    assert [row["item"] for row in decoded] == ["Coal", "CopperOre"]


def test_an_empty_module_is_not_a_decode():
    """8 zero bytes is what 50 of the 53 bases carry. Not data, not an error."""
    assert decode_transport(b"\0" * 8) is None
    assert decode_transport(b"") is None


def test_a_short_buffer_is_refused_rather_than_truncated():
    blob = _blob(_entry("Wheat"))[:-9]
    assert decode_transport(blob) is None


def test_a_long_buffer_is_refused_even_though_the_entries_parse():
    """
    THE ONE THAT MATTERS. Trailing bytes mean the layout is not what this thinks
    it is, even though every field read cleanly. `uassettable`'s rule: the walk
    must end exactly at the end.
    """
    blob = _blob(_entry("Wheat")) + b"\x01\x02\x03\x04"
    assert decode_transport(blob) is None


def test_a_non_zero_pad_is_refused():
    """
    The 32 bytes between the item id and the position are zero on every
    observation. They are asserted rather than skipped: if they ever carry
    something, the position that follows is no longer where this thinks it is.
    """
    pad = bytearray(PAD)
    pad[0] = 1
    assert decode_transport(_blob(_entry("Wheat", pad=bytes(pad)))) is None


def test_an_absurd_count_is_refused_before_it_allocates():
    assert decode_transport(struct.pack("<i", 1 << 20) + b"\0" * 64) is None
    assert decode_transport(struct.pack("<i", -3) + b"\0" * 64) is None


def test_an_absurd_string_length_is_refused():
    blob = struct.pack("<i", 1) + struct.pack("<i", 1) + struct.pack("<i", 1 << 20)
    assert decode_transport(blob + b"\0" * 64) is None


@pytest.mark.parametrize("item", ["Wheat", "Coal", "CopperOre", "Stone"])
def test_every_observed_item_id_resolves_in_the_catalogue(item):
    """
    The external half of the verification. A decode producing an id the game
    does not have would be reading the wrong bytes, however tidy the walk.
    """
    import gamedata

    assert gamedata.item_name(item), item
    # ...and it is a real entry rather than `humanize()` inventing a name.
    catalogue = {row["id"].lower() for row in gamedata.all_items()}
    assert item.lower() in catalogue
