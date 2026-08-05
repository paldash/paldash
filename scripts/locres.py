"""
Read Unreal's `.locres` localisation archives.

WHY THIS EXISTS. Every display name and description in Palworld is an `FText`,
and `uassettable` does not decode `TextProperty` — measured and total: 1,994 of
1,994 item names opaque, 322 of 322 Pal names, 835 of 835 technology names. The
strings are not in the DataTables in any readable form.

They are here, in `Pal/Content/Localization/Game/<lang>/Game.locres` inside the
client pak, for 17 languages including `en`. This is the last source standing
between the project and dropping its third-party data dependency: the server pak
already supplies every number, verified at 13,836 of 13,836.

THE FORMAT, and the two versions that matter:

    magic     16 bytes, a fixed GUID. Absent on v0 (legacy), which this refuses
              rather than guessing at.
    version   1 byte. 1 = Compact, 2 = Optimized, 3 = OptimizedCityHash64.
    offset    int64 to the localised-string array (v2+)
    then      namespace count, and per namespace: key, entry count, and per
              entry: key, source-string hash, index into the string array

An `FString` is an int32 length: **negative means UTF-16** with `-length` code
units, positive means UTF-8, and both include a trailing NUL that is stripped.
Getting that sign wrong yields mojibake rather than an error, which is why it is
handled in one place.

v3 prefixes each namespace and key with a u32 hash. The hash is not needed to
read the file — the string follows it — but skipping the wrong number of bytes
desynchronises everything after, so the version drives the parse rather than
being ignored.
"""

from __future__ import annotations

import struct

MAGIC = bytes([
    0x0E, 0x14, 0x74, 0x75, 0x67, 0x4A, 0x03, 0xFC,
    0x4A, 0x15, 0x90, 0x9D, 0xC3, 0x37, 0x7F, 0x1B,
])


class LocResError(Exception):
    pass


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.d = data
        self.o = 0

    def u32(self) -> int:
        v, = struct.unpack_from("<I", self.d, self.o)
        self.o += 4
        return v

    def i32(self) -> int:
        v, = struct.unpack_from("<i", self.d, self.o)
        self.o += 4
        return v

    def i64(self) -> int:
        v, = struct.unpack_from("<q", self.d, self.o)
        self.o += 8
        return v

    def fstring(self) -> str:
        """
        An FString. **A negative length means UTF-16**, and reading it as UTF-8
        produces plausible mojibake rather than an exception — so this is the
        only place the sign is interpreted.
        """
        length = self.i32()
        if length == 0:
            return ""
        if length < 0:
            count = -length
            raw = self.d[self.o:self.o + count * 2]
            self.o += count * 2
            return raw.decode("utf-16-le", "replace").rstrip("\0")
        raw = self.d[self.o:self.o + length]
        self.o += length
        return raw.decode("utf-8", "replace").rstrip("\0")


def read(data: bytes) -> dict[str, dict[str, str]]:
    """
    `{namespace: {key: string}}`.

    Namespaces are usually empty in Palworld's archive, so most callers want
    `flatten()` instead.
    """
    if data[:16] != MAGIC:
        raise LocResError(
            "Not a .locres, or a legacy (v0) one with no magic. Refused rather "
            "than parsed on assumption — a wrong version desynchronises the "
            "whole file and yields plausible nonsense."
        )

    r = _Reader(data)
    r.o = 16
    version = r.d[r.o]
    r.o += 1
    if version not in (1, 2, 3):
        raise LocResError(f"Unsupported .locres version {version}")

    strings: list[str] = []
    if version >= 2:
        array_offset = r.i64()
        if array_offset > 0:
            saved = r.o
            r.o = array_offset
            count = r.i32()
            for _ in range(count):
                strings.append(r.fstring())
                if version >= 3:
                    r.i32()          # reference count, unused
            r.o = saved

    if version >= 3:
        r.u32()                      # entry count across the whole file

    out: dict[str, dict[str, str]] = {}
    namespace_count = r.u32()
    for _ in range(namespace_count):
        if version >= 3:
            r.u32()                  # namespace hash
        namespace = r.fstring()
        entries = r.u32()
        bucket = out.setdefault(namespace, {})
        for _ in range(entries):
            if version >= 3:
                r.u32()              # key hash
            key = r.fstring()
            r.u32()                  # source-string hash
            if version >= 2:
                index = r.i32()
                bucket[key] = strings[index] if 0 <= index < len(strings) else ""
            else:
                bucket[key] = r.fstring()

    return out


def flatten(archive: dict[str, dict[str, str]]) -> dict[str, str]:
    """
    `{key: string}` across every namespace.

    Palworld puts everything in the empty namespace, so this loses nothing in
    practice — but a collision would silently drop a string, so it is counted
    and raised rather than ignored.
    """
    out: dict[str, str] = {}
    collisions = []
    for bucket in archive.values():
        for key, value in bucket.items():
            if key in out and out[key] != value:
                collisions.append(key)
            out[key] = value
    if collisions:
        raise LocResError(
            f"{len(collisions)} keys appear in more than one namespace with "
            f"different values, e.g. {collisions[:3]}. Flattening would drop a "
            "string; use `read()` and keep the namespaces."
        )
    return out
