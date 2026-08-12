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

# FObjectImport: ClassPackage FName(8) | ClassName FName(8) | OuterIndex i32(4)
# | ObjectName FName(8) | bImportOptional i32(4)  — 32 in UE5.1+, 28 before it.
IMPORT_RECORD_SIZE = 32
_IMPORT_CLASS = 8
_IMPORT_OBJECT = 20


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
        self._read_imports()
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

    def _read_imports(self) -> None:
        """
        The import map, which this reader ignored for a year — and that omission
        is why every census of the pak had to enumerate by *path convention*
        instead of by what an asset actually is. An export's class is an
        `FPackageIndex` at offset 0 of its record, and a negative value indexes
        here, so without the import map there was no way to ask "what class is
        this?" and globbing `DT_*` / `/DataTable/` was the only tool available.

        That cost real coverage, repeatedly: the settings CDO, a DataAsset, the
        species blueprints and 7 DataTables that live outside `/DataTable/` were
        each missed by a search that enumerated names rather than classes.

        `FObjectImport` is **32 bytes**, not the 28 of older engine versions —
        UE5.1 appended `bImportOptional`. The stride is not guessable from the
        file, so it is pinned by the acceptance test in `test_upackage.py`:
        known assets must resolve to their known classes. At 28 a DataTable
        still resolved correctly (its class import happens to be index 0) while
        both blueprints came back as unrelated asset paths — a stride error here
        produces plausible wrong answers, never an exception.
        """
        d = self.raw
        self.imports: list[tuple[str, str]] = []
        for i in range(self.import_count):
            b = self.import_offset + i * IMPORT_RECORD_SIZE
            if b + 28 > len(d):
                # Stop rather than raise. The import map exists only to answer
                # `export_class()`, which is an optional query — every other
                # caller wants the name table and the export map, and those are
                # validated separately above. Making a package that parsed fine
                # yesterday raise today, for a field nothing of theirs reads,
                # would be a regression dressed as strictness. A short map costs
                # `export_class` an answer and nothing else.
                break
            class_name, object_name = struct.unpack_from("<i", d, b + _IMPORT_CLASS)[0], \
                struct.unpack_from("<i", d, b + _IMPORT_OBJECT)[0]
            self.imports.append((self._name(class_name), self._name(object_name)))

    def _name(self, index: int) -> str:
        return self.names[index] if 0 <= index < self.name_count else f"<name {index}>"

    # ─── Queries ───

    def export_class(self, index: int = 0) -> Optional[str]:
        """
        The class of an export — `DataTable`, `BlueprintGeneratedClass`,
        `PalBuildObjectCapabilityDataAsset` — or None when the class is itself
        an export of this package (rare, and not what callers want to bucket on).

        This is what makes "catalogue everything of kind X" possible without
        guessing at filenames.
        """
        b = self.export_offset + index * EXPORT_RECORD_SIZE
        if b + 4 > len(self.raw):
            return None
        (class_index,) = struct.unpack_from("<i", self.raw, b)
        if class_index >= 0:
            return None
        j = -class_index - 1
        # The import's ObjectName is the class name; its ClassName is "Class".
        return self.imports[j][1] if 0 <= j < len(self.imports) else None

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
