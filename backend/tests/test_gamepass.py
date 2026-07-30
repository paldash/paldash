"""
Xbox Game Pass (WGS) save extraction.

**Read this before trusting the module.** No Game Pass save has ever been run
through it, and neither the author of this project nor its operator has one. The
container format is derived from `PalWorldSaveTools/xgp_save_extract.py`, and the
fixture below is built to that same understanding — so these tests prove the parser
matches the spec it was written from, **not** that the spec is correct.

Two things make that acceptable rather than reckless:

  * The module only ever *reads* a WGS tree and writes a fresh directory elsewhere.
    There is no code path that can touch an existing world.
  * `extract` verifies every extracted blob **actually parses as GVAS** before
    reporting success. The blobs in these tests are real `.sav` files taken from the
    reference world, so that verification path is exercised against genuine data
    even though the container wrapper around it is synthetic. If the format
    understanding is wrong on a real save, the blobs will not parse and the caller
    gets a specific error rather than a directory of plausible garbage.
"""

from __future__ import annotations

import os
import struct
import uuid

import pytest

import gamepass


# ─── A synthetic WGS tree ────────────────────────────────


def utf16(value: str) -> bytes:
    """Length-prefixed, counted in characters — not bytes."""
    encoded = value.encode("utf-16-le")
    return struct.pack("<i", len(value)) + encoded


def utf16_fixed(value: str, chars: int = 64) -> bytes:
    encoded = value.encode("utf-16-le")
    return encoded.ljust(chars * 2, b"\x00")[: chars * 2]


def build_wgs(tmp_path, saves: dict[str, bytes], package="PocketpairInc.Palworld_ad4psfrxyesvt"):
    """
    Write a WGS tree containing `{container_name: blob_bytes}`.

    Mirrors the layout `gamepass.read_index` expects, field for field.
    """
    wgs = tmp_path / "wgs"
    wgs.mkdir(parents=True, exist_ok=True)

    index = bytearray()
    index += struct.pack("<I", 14)                 # format version
    index += struct.pack("<i", len(saves))
    index += utf16("Palworld")
    index += utf16(f"{package}!APP")
    index += struct.pack("<Q", 0)                  # creation FILETIME
    index += struct.pack("<I", 0)
    index += utf16("")
    index += b"\x00" * 8

    for slot, (name, blob) in enumerate(saves.items()):
        container_guid = uuid.uuid4()
        blob_guid = uuid.uuid4()

        index += utf16(name)
        index += utf16(name)
        index += utf16("")
        index += struct.pack("B", slot)
        index += struct.pack("<I", 0)
        index += container_guid.bytes_le
        index += struct.pack("<Q", 0)              # container FILETIME
        index += b"\x00" * 16

        container_dir = wgs / container_guid.hex.upper()
        container_dir.mkdir()

        listing = bytearray()
        listing += struct.pack("<I", 0)
        listing += struct.pack("<i", 1)
        listing += utf16_fixed("Data")
        listing += blob_guid.bytes_le
        listing += blob_guid.bytes_le              # identical: no sync in progress
        (container_dir / f"container.{slot}").write_bytes(bytes(listing))
        (container_dir / blob_guid.hex.upper()).write_bytes(blob)

    (wgs / "containers.index").write_bytes(bytes(index))
    return wgs


@pytest.fixture
def real_saves(refworld, palsav_available):
    """Genuine Palworld saves, so the GVAS verification is not itself synthetic."""
    level = open(os.path.join(refworld, "Level.sav"), "rb").read()
    players = os.path.join(refworld, "Players")
    player_name = sorted(
        n for n in os.listdir(players) if n.endswith(".sav") and "_dps" not in n
    )[0]
    player = open(os.path.join(players, player_name), "rb").read()
    return {
        "Level": level,
        f"Players-{player_name[:-4]}": player,
    }


# ─── Names and paths ─────────────────────────────────────


def test_a_container_name_becomes_a_save_path():
    assert gamepass.save_path_for("Level") == "Level.sav"
    assert gamepass.save_path_for("Players-22B22B02") == os.path.join(
        "Players", "22B22B02.sav"
    )


def test_a_traversing_container_name_is_refused():
    """
    The name comes from a file this code did not write. Without this, a crafted
    index could place a file anywhere the process can write.
    """
    for hostile in ("..-..-etc-passwd", "-..-..-evil", "-etc-cron.d-payload"):
        with pytest.raises(gamepass.GamePassError, match="unsafe"):
            gamepass.save_path_for(hostile)


def test_a_bare_dotdot_is_a_filename_not_a_traversal():
    """
    `..` becomes `...sav` because the extension is appended — a harmless file in the
    target directory. Pinned so nobody "hardens" this into refusing it and then
    assumes the check is stricter than it is.
    """
    assert gamepass.save_path_for("..") == "...sav"


# ─── Parsing ─────────────────────────────────────────────


def test_a_missing_index_says_where_to_point_it(tmp_path):
    with pytest.raises(gamepass.GamePassError, match="containers.index"):
        gamepass.read_index(str(tmp_path))


def test_the_index_round_trips(tmp_path):
    wgs = build_wgs(tmp_path, {"Level": b"x" * 32, "Players-ABC": b"y" * 16})
    index = gamepass.read_index(str(wgs))

    assert index["packageName"] == "PocketpairInc.Palworld_ad4psfrxyesvt"
    assert [c["name"] for c in index["containers"]] == ["Level", "Players-ABC"]


def test_utf16_lengths_are_characters_not_bytes(tmp_path):
    """
    Reading `length` bytes instead of `length * 2` produces a half-plausible string
    and desynchronises everything after it — the failure would look like a corrupt
    index rather than a bug here.
    """
    wgs = build_wgs(tmp_path, {"AVeryLongContainerNameIndeed": b"z" * 8})
    index = gamepass.read_index(str(wgs))
    assert index["containers"][0]["name"] == "AVeryLongContainerNameIndeed"


def test_an_implausible_container_count_is_refused(tmp_path):
    """A wrong offset usually shows up first as an absurd count."""
    wgs = tmp_path / "wgs"
    wgs.mkdir()
    (wgs / "containers.index").write_bytes(
        struct.pack("<I", 14) + struct.pack("<i", 999_999_999)
    )
    with pytest.raises(gamepass.GamePassError, match="plausible"):
        gamepass.read_index(str(wgs))


def test_a_truncated_index_is_refused(tmp_path):
    wgs = tmp_path / "wgs"
    wgs.mkdir()
    (wgs / "containers.index").write_bytes(struct.pack("<I", 14) + b"\x02")
    with pytest.raises(gamepass.GamePassError):
        gamepass.read_index(str(wgs))


def test_a_missing_container_file_blames_a_sync(tmp_path):
    wgs = build_wgs(tmp_path, {"Level": b"x" * 32})
    index = gamepass.read_index(str(wgs))
    container = index["containers"][0]
    os.remove(os.path.join(str(wgs), container["directory"], f"container.{container['slot']}"))

    with pytest.raises(gamepass.GamePassError, match="syncing"):
        gamepass.read_container_files(str(wgs), container)


def test_two_blob_copies_are_refused_rather_than_guessed(tmp_path):
    """
    Two GUIDs mean Xbox is mid-sync: one copy is current and one is stale. Picking
    arbitrarily risks restoring an old save over a newer one, which is data loss
    dressed up as success.
    """
    wgs = build_wgs(tmp_path, {"Level": b"x" * 32})
    index = gamepass.read_index(str(wgs))
    container = index["containers"][0]
    container_dir = os.path.join(str(wgs), container["directory"])
    listing_path = os.path.join(container_dir, f"container.{container['slot']}")

    second = uuid.uuid4()
    data = bytearray(open(listing_path, "rb").read())
    data[-16:] = second.bytes_le                 # make the two GUIDs differ
    open(listing_path, "wb").write(bytes(data))
    open(os.path.join(container_dir, second.hex.upper()), "wb").write(b"other")

    with pytest.raises(gamepass.GamePassError, match="mid-sync"):
        gamepass.read_container_files(str(wgs), container)


# ─── Extraction ──────────────────────────────────────────


def test_extraction_refuses_a_blob_that_is_not_a_palworld_save(tmp_path):
    """
    The safeguard that makes an unverified format reader honest. If the offsets are
    wrong, the bytes will not parse, and the caller is told so by name instead of
    receiving a directory of garbage.
    """
    wgs = build_wgs(tmp_path, {"Level": b"definitely not a save"})
    with pytest.raises(gamepass.GamePassError, match="did not parse"):
        gamepass.extract(str(wgs), str(tmp_path / "out"))


def test_a_failed_extraction_leaves_nothing_behind(tmp_path):
    wgs = build_wgs(tmp_path, {"Level": b"nope"})
    destination = tmp_path / "out"
    with pytest.raises(gamepass.GamePassError):
        gamepass.extract(str(wgs), str(destination))
    assert not destination.exists()
    assert not (tmp_path / "out.partial").exists()


def test_a_world_without_a_level_sav_is_refused(tmp_path):
    wgs = build_wgs(tmp_path, {"Players-ABC": b"x"})
    with pytest.raises(gamepass.GamePassError, match="Level.sav"):
        gamepass.extract(str(wgs), str(tmp_path / "out"), verify=False)


def test_inspect_reports_without_extracting(tmp_path):
    wgs = build_wgs(tmp_path, {"Level": b"x" * 40, "Players-ABC": b"y" * 20})
    report = gamepass.inspect(str(wgs))

    assert report["looksLikePalworld"] is True
    assert {s["savePath"] for s in report["saves"]} == {
        "Level.sav", os.path.join("Players", "ABC.sav")
    }
    assert not (tmp_path / "out").exists()


# ─── With genuine Palworld saves inside the containers ───


@pytest.mark.integration
@pytest.mark.slow
def test_extraction_produces_a_readable_world(tmp_path, real_saves):
    """
    The container wrapper is synthetic; the blobs are real. So this exercises the
    GVAS verification against genuine data and proves that a correctly-parsed WGS
    tree yields a world this project can actually read.
    """
    wgs = build_wgs(tmp_path, real_saves)
    destination = str(tmp_path / "extracted")

    result = gamepass.extract(str(wgs), destination)
    assert result["ok"] is True
    assert result["verified"] is True

    assert os.path.isfile(os.path.join(destination, "Level.sav"))
    players = os.listdir(os.path.join(destination, "Players"))
    assert players and all(p.endswith(".sav") for p in players)

    # And the extracted world parses with the ordinary reader, not a special one.
    import parser as save_parser

    gvas = save_parser.load_gvas(os.path.join(destination, "Level.sav"))
    assert gvas is not None
    assert save_parser.extract_guilds(gvas)


@pytest.mark.integration
@pytest.mark.slow
def test_the_extracted_bytes_are_identical_to_the_source(tmp_path, real_saves):
    """Extraction is a rename, not a conversion — the blob is already a `.sav`."""
    wgs = build_wgs(tmp_path, real_saves)
    destination = str(tmp_path / "extracted")
    gamepass.extract(str(wgs), destination)

    written = open(os.path.join(destination, "Level.sav"), "rb").read()
    assert written == real_saves["Level"]
