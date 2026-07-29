#!/usr/bin/env python3
"""
Read the file index out of a UE5 `.pak`, without extracting anything.

Why this exists
---------------
Two open questions need data that only lives inside the game's own pak:
effigy/relic world coordinates, and a World Tree landmark to calibrate that map
region against. Both are placements inside `.umap` files.

This script answers the *first* question — is the pak even readable — before
anyone invests in the much larger one of parsing UE5 assets. It reads the footer
and the index and lists paths. It never writes to the pak and never extracts.

What makes it possible
----------------------
Palworld's `Pal-LinuxServer.pak` is **not encrypted**: the footer's
EncryptionKeyGuid is all zeroes and `bEncryptedIndex` is 0. Its entries are
Oodle-compressed, which is normally the blocker — and this project already
depends on `palooz` for exactly that codec, because Palworld 1.0 saves use it
too.

Usage
-----
    python3 scripts/read-pak-index.py [--pak PATH] [--grep PATTERN] [--limit N]
"""

from __future__ import annotations

import argparse
import io
import os
import struct
import sys

PAK_MAGIC = 0x5A6F12E1

DEFAULT_PAK = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "refs", "palworld", "Pal", "Content", "Paks", "Pal-LinuxServer.pak",
)

# Footer sizes differ per version. v11 carries a 16-byte encryption GUID, an
# encrypted-index flag, magic, version, index offset/size, a 20-byte hash, and
# a fixed table of 32-byte compression-method names.
FOOTER_SIZE_V8_PLUS = 16 + 1 + 4 + 4 + 8 + 8 + 20 + (32 * 5)


class PakError(Exception):
    pass


def _read_string(stream: io.BufferedReader) -> str:
    """UE FString: int32 length, negative meaning UTF-16."""
    (length,) = struct.unpack("<i", stream.read(4))
    if length == 0:
        return ""
    if length < 0:
        raw = stream.read(-length * 2)
        return raw.decode("utf-16-le", errors="replace").rstrip("\0")
    raw = stream.read(length)
    return raw.decode("utf-8", errors="replace").rstrip("\0")


def read_footer(path: str) -> dict:
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(size - FOOTER_SIZE_V8_PLUS)
        blob = f.read(FOOTER_SIZE_V8_PLUS)

    guid = blob[:16]
    encrypted_index = blob[16]
    (magic, version, index_offset, index_size) = struct.unpack("<IIQQ", blob[17:41])

    if magic != PAK_MAGIC:
        # Fall back to scanning the tail, since the footer size varies by version.
        with open(path, "rb") as f:
            f.seek(size - 512)
            tail = f.read(512)
        found = tail.rfind(struct.pack("<I", PAK_MAGIC))
        if found < 0:
            raise PakError("No pak magic in the last 512 bytes — not a UE pak?")
        base = size - 512 + found
        with open(path, "rb") as f:
            f.seek(base - 17)
            guid = f.read(16)
            encrypted_index = f.read(1)[0]
            f.seek(base + 4)
            (version, index_offset, index_size) = struct.unpack("<IQQ", f.read(20))

    methods = []
    for i in range(5):
        start = 61 + i * 32
        name = blob[start:start + 32].split(b"\0", 1)[0].decode("ascii", errors="replace")
        if name:
            methods.append(name)

    return {
        "size": size,
        "version": version,
        "encrypted": any(guid) or bool(encrypted_index),
        "encryptionKeyGuid": guid.hex(),
        "indexOffset": index_offset,
        "indexSize": index_size,
        "compressionMethods": methods,
    }


def read_index(path: str, footer: dict) -> dict:
    """
    The v10+ index: mount point, entry count, then a path-hash index and a full
    directory index. Only the directory index is needed to list paths.
    """
    if footer["encrypted"]:
        raise PakError(
            "This pak's index is encrypted. An AES key would be required, and "
            "obtaining one is out of scope here."
        )

    with open(path, "rb") as f:
        f.seek(footer["indexOffset"])
        raw = f.read(footer["indexSize"])

    stream = io.BytesIO(raw)
    mount_point = _read_string(stream)
    (entry_count,) = struct.unpack("<I", stream.read(4))

    if footer["version"] < 10:
        raise PakError(
            f"Pak version {footer['version']} uses the legacy flat index; this reader "
            "handles the v10+ path-hash/directory layout only."
        )

    (_path_hash_seed,) = struct.unpack("<Q", stream.read(8))

    # Path hash index — skipped; the directory index carries the same names.
    (has_path_hash,) = struct.unpack("<I", stream.read(4))
    if has_path_hash:
        (_off, _size) = struct.unpack("<QQ", stream.read(16))
        stream.read(20)  # hash

    (has_directory,) = struct.unpack("<I", stream.read(4))
    if not has_directory:
        raise PakError("Pak has no full directory index — cannot list paths")

    (dir_offset, dir_size) = struct.unpack("<QQ", stream.read(16))
    stream.read(20)  # hash

    with open(path, "rb") as f:
        f.seek(dir_offset)
        dir_raw = f.read(dir_size)

    directory = io.BytesIO(dir_raw)
    (dir_count,) = struct.unpack("<I", directory.read(4))

    files: list[str] = []
    for _ in range(dir_count):
        dir_name = _read_string(directory)
        (file_count,) = struct.unpack("<I", directory.read(4))
        for _ in range(file_count):
            file_name = _read_string(directory)
            directory.read(4)  # offset into the encoded-entry blob
            files.append(f"{mount_point}{dir_name}{file_name}")

    return {"mountPoint": mount_point, "entryCount": entry_count, "files": files}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pak", default=DEFAULT_PAK)
    parser.add_argument("--grep", default="", help="case-insensitive substring filter")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--count-only", action="store_true")
    args = parser.parse_args()

    if not os.path.exists(args.pak):
        print(f"No pak at {args.pak}", file=sys.stderr)
        return 1

    footer = read_footer(args.pak)
    print(f"pak       {args.pak}")
    print(f"size      {footer['size'] / 2**30:.2f} GiB")
    print(f"version   {footer['version']}")
    print(f"encrypted {footer['encrypted']}")
    print(f"codecs    {', '.join(footer['compressionMethods']) or '(none)'}")
    print(f"index     offset={footer['indexOffset']:,} size={footer['indexSize']:,}")

    index = read_index(args.pak, footer)
    print(f"mount     {index['mountPoint']}")
    print(f"entries   {index['entryCount']:,} declared, {len(index['files']):,} listed")

    matches = index["files"]
    if args.grep:
        needle = args.grep.lower()
        matches = [p for p in matches if needle in p.lower()]
        print(f"matching  {len(matches):,} for {args.grep!r}")

    if not args.count_only:
        for p in matches[: args.limit]:
            print("   ", p)
        if len(matches) > args.limit:
            print(f"    … and {len(matches) - args.limit:,} more")

    return 0


if __name__ == "__main__":
    sys.exit(main())
