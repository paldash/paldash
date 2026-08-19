#!/usr/bin/env python3
"""
Read and extract files from Palworld's `Pal-LinuxServer.pak`.

This exists to answer questions the save files cannot: where the game *places*
things. Effigies, fast-travel points, dungeon entrances and chests are actor
placements inside World Partition cell maps, and none of that is in a save until
a player interacts with it.

WHY THIS IS POSSIBLE AT ALL
---------------------------
Two things line up:

- The pak is **not encrypted**. Its footer's EncryptionKeyGuid is all zeroes and
  `bEncryptedIndex` is 0, so no AES key is needed.
- Its entries are **Oodle Kraken** compressed, which is normally the blocker —
  and this project already depends on `palooz` for exactly that codec, because
  Palworld 1.0 saves use it too.

FORMAT NOTES THAT COST TIME
---------------------------
The v10+ index does not store file sizes next to file names. The directory index
maps a name to a **32-bit offset into a separate blob of bit-packed entries**,
and that packed form omits any field it can infer. Rather than trust that
decode, this reads only the *offset* from it and then seeks to the entry itself,
where the game stores a full, plainly-serialised `FPakEntry` header. The packed
blob gets us to the door; the inline header is the authority.

Block offsets in that inline header are relative to the entry's own start, not
the file's — a detail that silently yields garbage rather than an error.
"""

from __future__ import annotations

import argparse
import io
import os
import struct
import sys

PAK_MAGIC = 0x5A6F12E1
# Every cooked .uasset/.umap starts with these bytes. Used to prove a decompress
# worked rather than assuming it did.
#
# Written as bytes deliberately: the constant is usually quoted as 0xC1832A9E,
# which is the big-endian reading. On disk it is C1 83 2A 9E, so a little-endian
# uint32 comparison against 0xC1832A9E fails on a file that is perfectly fine.
PACKAGE_MAGIC = b"\xc1\x83\x2a\x9e"

def _default_pak() -> str:
    """
    Where the server pak lives: an explicit override, the dev checkout's
    refs/ copy, or the shared /palworld mount — in that order.

    The third candidate is what makes in-container bundle regeneration work
    at all (#149): the default compose mounts the game's whole install, so
    the pak the operator is actually running is sitting at a known path. The
    refs/ copy stays ahead of it so a dev machine with both keeps building
    against the checked-in reference.
    """
    explicit = os.environ.get("PALWORLD_PAK", "").strip()
    if explicit:
        return explicit
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(root, "refs", "palworld", "Pal", "Content", "Paks",
                     "Pal-LinuxServer.pak"),
        "/palworld/Pal/Content/Paks/Pal-LinuxServer.pak",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


DEFAULT_PAK = _default_pak()


class PakError(Exception):
    pass


def _fstring(stream) -> str:
    """UE FString: int32 length, negative meaning UTF-16."""
    (length,) = struct.unpack("<i", stream.read(4))
    if length == 0:
        return ""
    if length < 0:
        return stream.read(-length * 2).decode("utf-16-le", errors="replace").rstrip("\0")
    return stream.read(length).decode("utf-8", errors="replace").rstrip("\0")


class Pak:
    """An open pak. Reads the index once; extraction is lazy."""

    def __init__(self, path: str = DEFAULT_PAK) -> None:
        if not os.path.exists(path):
            raise PakError(f"No pak at {path}")
        self.path = path
        self.size = os.path.getsize(path)
        self._read_footer()
        self._read_index()

    # ─── Footer ───

    def _read_footer(self) -> None:
        with open(self.path, "rb") as f:
            f.seek(self.size - 512)
            tail = f.read(512)

        found = tail.rfind(struct.pack("<I", PAK_MAGIC))
        if found < 0:
            raise PakError("No pak magic in the last 512 bytes — not a UE pak?")

        base = self.size - 512 + found
        with open(self.path, "rb") as f:
            f.seek(base - 17)
            guid = f.read(16)
            encrypted_index = f.read(1)[0]
            f.seek(base + 4)
            self.version, self.index_offset, self.index_size = struct.unpack("<IQQ", f.read(20))
            f.read(20)  # index hash
            self.compression_methods = []
            for _ in range(5):
                name = f.read(32).split(b"\0", 1)[0].decode("ascii", errors="replace")
                if name:
                    self.compression_methods.append(name)

        self.encrypted = any(guid) or bool(encrypted_index)
        if self.encrypted:
            raise PakError("This pak is encrypted; an AES key would be required.")
        if self.version < 10:
            raise PakError(f"Pak version {self.version} uses the legacy flat index.")

    # ─── Index ───

    def _read_index(self) -> None:
        with open(self.path, "rb") as f:
            f.seek(self.index_offset)
            raw = f.read(self.index_size)

        s = io.BytesIO(raw)
        self.mount_point = _fstring(s)
        (self.entry_count,) = struct.unpack("<I", s.read(4))
        s.read(8)  # path hash seed

        (has_path_hash,) = struct.unpack("<I", s.read(4))
        if has_path_hash:
            s.read(16 + 20)

        (has_directory,) = struct.unpack("<I", s.read(4))
        if not has_directory:
            raise PakError("Pak has no full directory index")
        dir_offset, dir_size = struct.unpack("<QQ", s.read(16))
        s.read(20)

        # The bit-packed entries follow, in the primary index.
        (encoded_len,) = struct.unpack("<i", s.read(4))
        self._encoded = s.read(encoded_len)

        with open(self.path, "rb") as f:
            f.seek(dir_offset)
            dir_raw = f.read(dir_size)

        d = io.BytesIO(dir_raw)
        (dir_count,) = struct.unpack("<I", d.read(4))
        self.files: dict[str, int] = {}
        for _ in range(dir_count):
            directory = _fstring(d)
            (file_count,) = struct.unpack("<I", d.read(4))
            for _ in range(file_count):
                name = _fstring(d)
                (encoded_offset,) = struct.unpack("<i", d.read(4))
                self.files[f"{self.mount_point}{directory}{name}"] = encoded_offset

    # ─── Entries ───

    def _entry_offset(self, encoded_offset: int) -> int:
        """
        The file's offset in the pak, from the bit-packed entry.

        Only the offset is taken from here. Everything else comes from the
        plainly-serialised header at that offset, which cannot drift out of step
        with what the game itself reads.
        """
        b = io.BytesIO(self._encoded[encoded_offset:encoded_offset + 64])
        (value,) = struct.unpack("<I", b.read(4))

        if (value & 0x3F) == 0x3F:      # explicit compression block size
            b.read(4)
        offset_is_32 = (value >> 31) & 1
        return struct.unpack("<I" if offset_is_32 else "<Q", b.read(4 if offset_is_32 else 8))[0]

    def _header(self, offset: int) -> dict:
        """The inline FPakEntry stored immediately before the file's data."""
        with open(self.path, "rb") as f:
            f.seek(offset)
            head = f.read(4096)

        s = io.BytesIO(head)
        _stored_offset, size, uncompressed = struct.unpack("<qqq", s.read(24))
        (method_index,) = struct.unpack("<I", s.read(4))
        s.read(20)  # sha1

        blocks = []
        if method_index != 0:
            (block_count,) = struct.unpack("<i", s.read(4))
            if block_count < 0 or block_count > 100_000:
                raise PakError(f"Implausible block count {block_count} at offset {offset}")
            for _ in range(block_count):
                blocks.append(struct.unpack("<qq", s.read(16)))

        encrypted = s.read(1)[0]
        (block_size,) = struct.unpack("<I", s.read(4))

        return {
            "offset": offset,
            "headerSize": s.tell(),
            "size": size,
            "uncompressedSize": uncompressed,
            "method": self.compression_methods[method_index - 1] if method_index else None,
            "blocks": blocks,
            "encrypted": bool(encrypted),
            "blockSize": block_size,
        }

    def read(self, path: str) -> bytes:
        """Extract and decompress one file."""
        if path not in self.files:
            raise PakError(f"{path} is not in this pak")

        entry = self._header(self._entry_offset(self.files[path]))
        if entry["encrypted"]:
            raise PakError(f"{path} is encrypted")

        with open(self.path, "rb") as f:
            if entry["method"] is None:
                f.seek(entry["offset"] + entry["headerSize"])
                return f.read(entry["size"])

            if entry["method"] != "Oodle":
                raise PakError(f"{path} uses {entry['method']}, which is not supported here")

            import palooz

            # Block offsets are relative to the entry's own start, not the
            # file's. Getting this wrong decompresses garbage rather than
            # failing, so it is checked against the first block landing exactly
            # after the header.
            base = entry["offset"]
            if entry["blocks"] and entry["blocks"][0][0] == entry["headerSize"]:
                pass                      # relative to entry start
            elif entry["blocks"] and entry["blocks"][0][0] >= base:
                base = 0                  # already absolute
            out = bytearray()
            remaining = entry["uncompressedSize"]
            for start, end in entry["blocks"]:
                f.seek(base + start)
                chunk = f.read(end - start)
                want = min(entry["blockSize"], remaining)
                out += palooz.decompress(chunk, want)
                remaining -= want
            return bytes(out)


def looks_like_package(data: bytes) -> bool:
    return data[:4] == PACKAGE_MAGIC


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pak", default=DEFAULT_PAK)
    parser.add_argument("--grep", default="", help="list matching paths")
    parser.add_argument("--extract", default="", help="exact path to extract")
    parser.add_argument("--out", default="", help="write the extracted file here")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()

    pak = Pak(args.pak)
    print(f"version {pak.version}, {len(pak.files):,} files, codecs "
          f"{', '.join(pak.compression_methods)}", file=sys.stderr)

    if args.extract:
        data = pak.read(args.extract)
        print(f"{len(data):,} bytes; UE package magic: {looks_like_package(data)}",
              file=sys.stderr)
        if args.out:
            with open(args.out, "wb") as f:
                f.write(data)
            print(f"wrote {args.out}", file=sys.stderr)
        return 0

    matches = [p for p in pak.files if args.grep.lower() in p.lower()] if args.grep \
        else list(pak.files)
    print(f"{len(matches):,} matching", file=sys.stderr)
    for p in matches[: args.limit]:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
