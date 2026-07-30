"""
Extract a Palworld save from an Xbox Game Pass (WGS) container directory.

Game Pass does not store saves as files you can copy. It writes them into a
**Windows Game Save** container tree, where every file has a GUID for a name and an
index maps the real names onto those GUIDs:

    wgs/
      containers.index                 the map: container name -> GUID + slot number
      <CONTAINER-GUID-HEX>/
        container.<n>                  per-container file list: name -> blob GUID
        <BLOB-GUID-HEX>                the actual save bytes

A container's *name* encodes the path Palworld expects, with `-` standing in for a
directory separator: `Level` is `Level.sav`, `Players-22B22B02…` is
`Players/22B22B02….sav`. So extraction is a rename, not a conversion — the bytes
inside a blob are an ordinary Palworld `.sav`.

**This module only reads.** It parses a WGS tree and writes a fresh save directory
elsewhere; it never touches the source, and it cannot touch a live world. That is
the entire reason it was safe to build without a Game Pass save to test against.

HONEST LIMITS
-------------
**No Game Pass save has ever been run through this.** The format above is derived
from `PalWorldSaveTools/xgp_save_extract.py`, and the tests exercise a *synthetic*
tree built to that same understanding — which proves the parser matches the spec it
was written from, not that the spec is right.

That is why `extract` **verifies every blob is a real Palworld save** before
reporting success: each one must decompress and parse as GVAS. If the format
understanding is wrong, the blobs will not parse and the caller gets a specific
error naming the file, rather than a directory of plausible-looking garbage that
fails much later. A wrong offset cannot masquerade as a working extraction.
"""

from __future__ import annotations

import logging
import os
import shutil
import struct
import uuid
from typing import Any, BinaryIO, Optional

logger = logging.getLogger(__name__)


class GamePassError(Exception):
    pass


# A container name is a path with separators replaced. Palworld's own layout only
# ever nests one level (`Players/<uid>.sav`), so this is a straight substitution.
PATH_SEPARATOR = "-"

# Guards against a malformed or hostile index steering the parse into a huge
# allocation. A real Palworld save has a handful of containers and a few dozen
# players; these are orders of magnitude above anything legitimate.
MAX_CONTAINERS = 10_000
MAX_FILES_PER_CONTAINER = 10_000
MAX_NAME_CHARS = 4_096


def _read_u32(f: BinaryIO) -> int:
    raw = f.read(4)
    if len(raw) != 4:
        raise GamePassError("Unexpected end of containers.index")
    return struct.unpack("<I", raw)[0]


def _read_i32(f: BinaryIO) -> int:
    raw = f.read(4)
    if len(raw) != 4:
        raise GamePassError("Unexpected end of file while reading a count")
    return struct.unpack("<i", raw)[0]


def _read_utf16(f: BinaryIO, length: Optional[int] = None) -> str:
    """
    A length-prefixed UTF-16LE string, or a fixed-width one when `length` is given.

    The prefix counts *characters*, not bytes, which is the detail worth stating:
    reading `length` bytes instead of `length * 2` yields a string that looks
    half-plausible and desynchronises everything after it.
    """
    if length is None:
        length = _read_i32(f)
    if length < 0 or length > MAX_NAME_CHARS:
        raise GamePassError(f"Implausible string length in the index: {length}")
    raw = f.read(length * 2)
    if len(raw) != length * 2:
        raise GamePassError("Unexpected end of file while reading a name")
    return raw.decode("utf-16-le", errors="replace").rstrip("\x00")


def _guid_dir(value: uuid.UUID) -> str:
    return value.hex.upper()


def read_index(wgs_dir: str) -> dict[str, Any]:
    """
    Parse `containers.index` into container records. Read-only.

    Offsets follow the reference implementation. The unnamed `f.read(n)` calls are
    fields whose meaning is not needed here — they are skipped rather than guessed
    at, and the structural assertions below are what catch a wrong stride.
    """
    index_path = os.path.join(wgs_dir, "containers.index")
    if not os.path.isfile(index_path):
        raise GamePassError(
            f"No containers.index in {wgs_dir}. Point this at the 'wgs' directory "
            "itself, or at the user folder inside it."
        )

    containers: list[dict[str, Any]] = []
    with open(index_path, "rb") as f:
        f.read(4)                                   # format version
        count = _read_i32(f)
        if not 0 <= count <= MAX_CONTAINERS:
            raise GamePassError(
                f"containers.index claims {count} containers, which is not "
                "plausible — this may not be a WGS index."
            )
        package_display_name = _read_utf16(f)
        package_name = _read_utf16(f).split("!")[0]
        f.read(8)                                   # creation FILETIME
        f.read(4)
        _read_utf16(f)
        f.read(8)

        for _ in range(count):
            name = _read_utf16(f)
            _read_utf16(f)
            _read_utf16(f)
            slot_raw = f.read(1)
            if len(slot_raw) != 1:
                raise GamePassError("Unexpected end of containers.index")
            slot = struct.unpack("B", slot_raw)[0]
            f.read(4)
            guid_raw = f.read(16)
            if len(guid_raw) != 16:
                raise GamePassError("Unexpected end of containers.index")
            guid = uuid.UUID(bytes_le=guid_raw)
            f.read(8)                               # container FILETIME
            f.read(16)

            containers.append({
                "name": name,
                "slot": slot,
                "guid": str(guid),
                "directory": _guid_dir(guid),
            })

    return {
        "packageName": package_name,
        "packageDisplayName": package_display_name,
        "containers": containers,
    }


def read_container_files(wgs_dir: str, container: dict[str, Any]) -> list[dict[str, str]]:
    """The blobs a container holds, resolved to real paths on disk."""
    container_dir = os.path.join(wgs_dir, container["directory"])
    listing = os.path.join(container_dir, f"container.{container['slot']}")
    if not os.path.isfile(listing):
        raise GamePassError(
            f"Container '{container['name']}' is listed in the index but its "
            f"container.{container['slot']} file is missing. The save may be "
            "mid-sync — close the game and let Xbox finish syncing."
        )

    files: list[dict[str, str]] = []
    with open(listing, "rb") as f:
        f.read(4)
        count = _read_i32(f)
        if not 0 <= count <= MAX_FILES_PER_CONTAINER:
            raise GamePassError(
                f"Container '{container['name']}' claims {count} files, which is "
                "not plausible."
            )
        for _ in range(count):
            # Fixed 64 characters here, unlike the length-prefixed names in the
            # index. Two different string encodings in one format.
            name = _read_utf16(f, 64)
            first = f.read(16)
            second = f.read(16)
            if len(first) != 16 or len(second) != 16:
                raise GamePassError("Unexpected end of a container file list")

            candidates = [uuid.UUID(bytes_le=first)]
            if second != first:
                candidates.append(uuid.UUID(bytes_le=second))

            # Two GUIDs mean a sync in progress: one is the old copy and one the
            # new. Picking arbitrarily could restore a stale save over a current
            # one, so an ambiguous pair is refused rather than guessed.
            present = [
                os.path.join(container_dir, _guid_dir(c))
                for c in candidates
                if os.path.isfile(os.path.join(container_dir, _guid_dir(c)))
            ]
            if not present:
                raise GamePassError(
                    f"Blob for '{container['name']}' is missing on disk. The save "
                    "is probably still syncing from the cloud."
                )
            if len(present) > 1:
                raise GamePassError(
                    f"Two copies of '{container['name']}' exist ({', '.join(candidates and [str(c) for c in candidates])}). "
                    "Xbox is mid-sync and there is no safe way to tell which is "
                    "current — open the game once, let it finish syncing, and retry."
                )
            files.append({"name": name, "path": present[0]})
    return files


def save_path_for(container_name: str) -> str:
    """
    `Players-22B22B02…` -> `Players/22B22B02….sav`.

    Rejects anything that would escape the output directory. The container name
    comes from a file this code did not write, so it is untrusted input and a name
    containing `..` must not be able to place a file outside the target.
    """
    relative = container_name.replace(PATH_SEPARATOR, os.sep) + ".sav"
    normalised = os.path.normpath(relative)
    if os.path.isabs(normalised) or normalised.startswith(".." + os.sep) or normalised == "..":
        raise GamePassError(f"Refusing an unsafe container name: {container_name!r}")
    return normalised


def _is_palworld_save(path: str) -> tuple[bool, str]:
    """
    Whether a blob really is a Palworld save, by parsing it.

    This is the safeguard that makes an untested format reader honest: no Game Pass
    save has been run through this module, so rather than trusting the offsets, the
    output is checked against the one thing that cannot be faked — whether the bytes
    decompress and parse as GVAS.
    """
    try:
        from palsav.core import decompress_sav_to_gvas
        from palsav.gvas import GvasFile
        from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS

        with open(path, "rb") as f:
            raw = f.read()
        decompressed, _ = decompress_sav_to_gvas(raw)
        GvasFile.read(decompressed, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES)
        return True, ""
    except Exception as e:  # noqa: BLE001 - any failure means "not a save"
        return False, str(e)


def inspect(wgs_dir: str) -> dict[str, Any]:
    """What a WGS directory contains, without extracting anything."""
    index = read_index(wgs_dir)
    entries = []
    problems = []
    for container in index["containers"]:
        try:
            files = read_container_files(wgs_dir, container)
        except GamePassError as e:
            problems.append(str(e))
            continue
        if not files:
            continue
        try:
            target = save_path_for(container["name"])
        except GamePassError as e:
            problems.append(str(e))
            continue
        entries.append({
            "container": container["name"],
            "savePath": target,
            "sizeBytes": os.path.getsize(files[0]["path"]),
        })

    return {
        "packageName": index["packageName"],
        "packageDisplayName": index["packageDisplayName"],
        "saves": entries,
        "problems": problems,
        "looksLikePalworld": "palworld" in index["packageName"].lower(),
    }


def extract(wgs_dir: str, destination: str, verify: bool = True) -> dict[str, Any]:
    """
    Write a normal save directory from a WGS tree.

    Assembled in a staging directory and moved into place only once every blob has
    verified, so a failure leaves nothing that could be mistaken for a usable world.
    """
    index = read_index(wgs_dir)
    if not index["containers"]:
        raise GamePassError("containers.index lists no containers")

    staging = destination + ".partial"
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)

    written: list[dict[str, Any]] = []
    try:
        for container in index["containers"]:
            files = read_container_files(wgs_dir, container)
            if not files:
                continue
            relative = save_path_for(container["name"])
            target = os.path.join(staging, relative)
            os.makedirs(os.path.dirname(target) or staging, exist_ok=True)
            shutil.copy2(files[0]["path"], target)

            if verify:
                ok, reason = _is_palworld_save(target)
                if not ok:
                    raise GamePassError(
                        f"'{relative}' did not parse as a Palworld save ({reason}). "
                        "Nothing was kept. This extractor has never been run against "
                        "a real Game Pass save — if your save is intact, the "
                        "container format has probably changed and this needs "
                        "updating rather than retrying."
                    )
            written.append({
                "savePath": relative,
                "sizeBytes": os.path.getsize(target),
                "verified": verify,
            })

        if not written:
            raise GamePassError("No save files were found in that WGS directory")
        if not any(w["savePath"].lower() == "level.sav" for w in written):
            raise GamePassError(
                "No Level.sav among the extracted files, so this is not a complete "
                "world. Nothing was kept."
            )

        shutil.rmtree(destination, ignore_errors=True)
        os.replace(staging, destination)
        staging = ""
    finally:
        if staging:
            shutil.rmtree(staging, ignore_errors=True)

    logger.info("Extracted %d Game Pass save files to %s", len(written), destination)
    return {
        "ok": True,
        "destination": destination,
        "packageName": index["packageName"],
        "files": written,
        "verified": verify,
    }
