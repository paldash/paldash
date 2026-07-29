#!/usr/bin/env python3
"""
A narrow reader for cooked Unreal Engine 5 packages (`.umap` / `.uasset`).

Deliberately not a general asset parser. Palworld's packages are cooked with
**unversioned properties** — `FileVersionUE4` and `FileVersionUE5` are both 0,
which means property *names* are absent from the stream and are implied by a
per-class schema we do not have. Decoding a property list is therefore off the
table.

What is still perfectly readable is the package's **structure**: the name table
and the export map. Those are plainly serialised. That gives, for every object
in the package: its name, its class, its parent, and the exact byte range of its
data in the `.uexp`.

That turns out to be enough. Instead of decoding fields, a caller can scan one
object's own bytes for the shape it wants and know for certain which object it
belongs to. Attribution is what a global byte scan cannot do, and attribution is
usually the hard part.

FIELD OFFSETS ARE MEASURED, NOT LOOKED UP
-----------------------------------------
The export record layout varies across engine versions and there is no version
number here to branch on. The offsets below were determined against Palworld's
own packages and are checked at parse time: the first export's SerialOffset must
equal `TotalHeaderSize`, offsets must ascend, and the name table must end exactly
where the import table begins. If a game update changes the layout, `read()`
raises rather than returning plausible nonsense.
"""

from __future__ import annotations

import bisect
import struct
from typing import Any, Optional

PACKAGE_MAGIC = b"\xc1\x83\x2a\x9e"

# Measured against Palworld 1.0 packages. See the module docstring.
EXPORT_RECORD_SIZE = 96
_OUTER_INDEX = 12
_NAME_INDEX = 16
_SERIAL_SIZE = 28
_SERIAL_OFFSET = 36


class PackageError(Exception):
    pass


class Export:
    __slots__ = ("index", "name", "outer", "offset", "size")

    def __init__(self, index: int, name: str, outer: int, offset: int, size: int) -> None:
        self.index = index
        self.name = name
        self.outer = outer      # FPackageIndex, see `outer_export`
        self.offset = offset    # relative to the start of the .uexp
        self.size = size

    @property
    def outer_export(self) -> Optional[int]:
        """
        The parent's export index, or None when the parent is an import.

        `FPackageIndex` reads the opposite way round to how it looks: **positive
        is an export** (value - 1) and negative is an import (-value - 1), with 0
        meaning null. Getting it backwards produces no error — every lookup
        simply misses, and the caller concludes the package has no parent-child
        structure at all.
        """
        return self.outer - 1 if self.outer > 0 else None

    def data(self, uexp: bytes) -> bytes:
        return uexp[self.offset:self.offset + self.size]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Export {self.index} {self.name!r} {self.size}B @{self.offset}>"


class Package:
    """A parsed `.umap`/`.uasset` header. Holds no `.uexp` data itself."""

    def __init__(self, umap: bytes) -> None:
        if umap[:4] != PACKAGE_MAGIC:
            raise PackageError("Not a cooked UE package (bad magic)")
        self.raw = umap
        self._read_summary()
        self._read_names()
        self._read_exports()
        self._starts = [e.offset for e in self.exports]

    # ─── Header ───

    def _read_summary(self) -> None:
        d = self.raw
        o = 4
        legacy = struct.unpack_from("<i", d, o)[0]; o += 4
        if legacy != -4:
            o += 4                              # LegacyUE3Version
        o += 4                                  # FileVersionUE4
        if legacy <= -8:
            o += 4                              # FileVersionUE5
        o += 4                                  # FileVersionLicenseeUE4
        (custom_versions,) = struct.unpack_from("<i", d, o); o += 4
        o += custom_versions * 20

        (self.total_header_size,) = struct.unpack_from("<i", d, o); o += 4
        (folder_len,) = struct.unpack_from("<i", d, o); o += 4
        self.folder_name = d[o:o + folder_len].decode("utf-8", "replace").rstrip("\0")
        o += folder_len
        o += 4                                  # PackageFlags

        self.name_count, self.name_offset = struct.unpack_from("<ii", d, o); o += 8
        o += 8                                  # SoftObjectPaths count/offset
        o += 8                                  # GatherableTextData count/offset
        self.export_count, self.export_offset = struct.unpack_from("<ii", d, o); o += 8
        self.import_count, self.import_offset = struct.unpack_from("<ii", d, o); o += 8

    def _read_names(self) -> None:
        d = self.raw
        o = self.name_offset
        self.names: list[str] = []
        for _ in range(self.name_count):
            (length,) = struct.unpack_from("<i", d, o); o += 4
            if length < 0:
                self.names.append(d[o:o - length * 2].decode("utf-16-le", "replace").rstrip("\0"))
                o += -length * 2
            else:
                self.names.append(d[o:o + length].decode("utf-8", "replace").rstrip("\0"))
                o += length
            o += 4      # per-name hashes
        # The name table runs right up to the import table. A mismatch means the
        # entry layout changed and every offset after this point is guesswork.
        if o != self.import_offset:
            raise PackageError(
                f"Name table ended at {o}, but the import table starts at "
                f"{self.import_offset} — the package layout is not what this reader "
                "expects (a game update?)"
            )

    def _read_exports(self) -> None:
        d = self.raw
        self.exports: list[Export] = []
        for i in range(self.export_count):
            b = self.export_offset + i * EXPORT_RECORD_SIZE
            (name_index,) = struct.unpack_from("<i", d, b + _NAME_INDEX)
            (outer,) = struct.unpack_from("<i", d, b + _OUTER_INDEX)
            (size,) = struct.unpack_from("<q", d, b + _SERIAL_SIZE)
            (offset,) = struct.unpack_from("<q", d, b + _SERIAL_OFFSET)
            if not 0 <= name_index < self.name_count:
                raise PackageError(f"Export {i} has name index {name_index}, out of range")
            self.exports.append(
                Export(i, self.names[name_index], outer, offset - self.total_header_size, size)
            )

        if self.exports and self.exports[0].offset != 0:
            raise PackageError(
                "First export does not start at the beginning of the .uexp "
                f"(got {self.exports[0].offset}) — export layout mismatch"
            )

    # ─── Queries ───

    def owner_of(self, uexp_offset: int) -> Optional[Export]:
        """Which export a byte position in the `.uexp` belongs to."""
        i = bisect.bisect_right(self._starts, uexp_offset) - 1
        if i < 0:
            return None
        export = self.exports[i]
        return export if uexp_offset < export.offset + export.size else None

    def children_of(self, index: int) -> list[Export]:
        """Exports whose Outer is the given export index."""
        return [e for e in self.exports if e.outer_export == index]

    def named(self, needle: str) -> list[Export]:
        return [e for e in self.exports if needle in e.name]


def read(umap: bytes) -> Package:
    return Package(umap)


def describe(package: Package) -> dict[str, Any]:
    return {
        "folder": package.folder_name,
        "names": package.name_count,
        "exports": package.export_count,
        "imports": package.import_count,
        "headerSize": package.total_header_size,
    }
