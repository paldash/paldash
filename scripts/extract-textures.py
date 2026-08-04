#!/usr/bin/env python3
"""
Extract UTexture2D pixel data from the game pak and write WebP.

WHY THIS EXISTS
---------------
`public/icons/map/` shipped seven markers traced from the Palworld wiki, with
`PROVENANCE.md` recording that the game's own art was unavailable: the *server*
pak carries texture packages with no pixel data at all (`T_worldmap_icon_fasttravel`
is 195 bytes there). The client pak has it — 4,249 bytes, and 21,056 `.ubulk`
files besides — so this is the tool that was missing rather than a missing
capability.

WHAT IT DOES NOT DO
-------------------
It is not a general asset reader and must not become one. Palworld's packages are
cooked with **unversioned properties**, so a texture's property list cannot be
decoded — the same limit `scripts/upackage.py` documents. What *is* plainly
serialised is the name table, the export map, and the `FTexturePlatformData`
block, and that is enough: the name table gives the pixel format, and the
platform data gives the dimensions and the mip bytes.

HOW THE MIP IS LOCATED, AND WHY NOT BY OFFSET
---------------------------------------------
The bulk-data descriptor's field layout varies with engine version and bulk-data
flags, and there is no version number in these files to branch on — the same
problem `upackage` solves by measuring offsets and asserting them.

So the mip is found by **anchor**, not by offset. Every `FTexture2DMipMap` is
followed by its own `SizeX, SizeY, SizeZ` as three int32s, and the payload
immediately precedes that trailer at a size fully determined by the dimensions
and the block format. Searching for the trailer and taking the computed number of
bytes before it is self-checking: a layout change makes the anchor not match, and
the script raises instead of writing a plausible-looking smear of wrong pixels.

DECODING
--------
Pillow decodes BC1/BC3/BC7 from a DDS container, so the raw mip is wrapped in a
DDS header rather than decoded here. Hand-writing a BC7 decoder (eight modes,
several hundred lines) to duplicate a library that already ships with the project
would be a second implementation to keep correct.

USAGE
    python3 scripts/extract-textures.py --list-formats
    python3 scripts/extract-textures.py \\
        --asset .../T_worldmap_icon_fasttravel --out public/icons/map/fasttravel.webp
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
from io import BytesIO
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import upackage  # noqa: E402
from palpak import Pak  # noqa: E402

DEFAULT_PAK = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "refs", "Pal-Windows.pak",
)

# Bytes per 4x4 block, and the DDS/DXGI code Pillow needs to decode it.
#
# Only the formats the UI textures actually use. An unlisted format raises rather
# than being guessed at — a wrong block size produces an image of the right
# dimensions full of noise, which is the failure mode hardest to notice.
BLOCK_FORMATS: dict[str, dict[str, Any]] = {
    "PF_DXT1":   {"block_bytes": 8,  "fourcc": b"DXT1", "dxgi": None},
    "PF_DXT3":   {"block_bytes": 16, "fourcc": b"DXT3", "dxgi": None},
    "PF_DXT5":   {"block_bytes": 16, "fourcc": b"DXT5", "dxgi": None},
    "PF_BC4":    {"block_bytes": 8,  "fourcc": b"DX10", "dxgi": 80},
    "PF_BC5":    {"block_bytes": 16, "fourcc": b"DX10", "dxgi": 83},
    "PF_BC7":    {"block_bytes": 16, "fourcc": b"DX10", "dxgi": 98},
    # Uncompressed. Handled separately — there are no blocks.
    "PF_B8G8R8A8": {"block_bytes": 0, "fourcc": None, "dxgi": None},
}


class TextureError(Exception):
    """The package is not a texture, or is not one this script can read."""


def _blocks(size: int) -> int:
    """Block count along one axis. BCn rounds a partial block up to a whole one."""
    return max(1, (size + 3) // 4)


def mip_size(width: int, height: int, fmt: str) -> int:
    spec = BLOCK_FORMATS[fmt]
    if not spec["block_bytes"]:
        return width * height * 4
    return _blocks(width) * _blocks(height) * spec["block_bytes"]


def find_format(package: upackage.Package) -> str:
    """
    The pixel format, from the package's name table.

    The name table is plainly serialised even though the properties are not, and
    a texture package names exactly one `PF_*` — so this is a lookup rather than
    an inference. More than one, or none, means this is not the kind of package
    this script handles, and saying so is better than picking the first.
    """
    found = [n for n in package.names if n.startswith("PF_")]
    if len(found) != 1:
        raise TextureError(
            f"Expected exactly one PF_* pixel format in the name table, found {found}"
        )
    if found[0] not in BLOCK_FORMATS:
        raise TextureError(
            f"Unsupported pixel format {found[0]}. Add it to BLOCK_FORMATS with the "
            "right block size — guessing produces an image of the right shape full "
            "of noise."
        )
    return found[0]


def find_dimensions(uexp: bytes, fmt: str) -> tuple[int, int]:
    """
    (width, height), read relative to the pixel-format string in the `.uexp`.

    `FTexturePlatformData` serialises `SizeX, SizeY, NumSlices` immediately before
    the format name, so those three int32s sit at a fixed distance *behind* a
    string this script can find by content. That is stable across the layout
    variation that makes an absolute offset unusable.

    Three assertions, because a mislocated dimension is silent: both sides must be
    positive, both must be within a sane texture range, and `NumSlices` must be 1
    (these are 2D UI textures, not arrays or cubemaps).
    """
    encoded = fmt.encode("ascii") + b"\0"
    marker = struct.pack("<i", len(encoded)) + encoded
    at = uexp.find(marker)
    if at < 0:
        raise TextureError(f"Could not find the {fmt} string in the .uexp")
    if at < 12:
        raise TextureError("Pixel format string is too near the start to hold dimensions")

    width, height, slices = struct.unpack_from("<iii", uexp, at - 12)
    if not (0 < width <= 16384 and 0 < height <= 16384):
        raise TextureError(f"Implausible texture dimensions {width}x{height}")
    if slices != 1:
        raise TextureError(f"NumSlices is {slices}; only plain 2D textures are handled")
    return width, height


def find_mip(uexp: bytes, width: int, height: int, fmt: str) -> bytes:
    """
    The top mip's bytes, located by its own trailer rather than by an offset.

    Every `FTexture2DMipMap` is followed by `SizeX, SizeY, SizeZ`. Searching for
    that triple and taking `mip_size` bytes before it means the anchor and the
    payload length are two independent facts that have to agree — so a layout
    change raises here rather than yielding a wrongly-sliced image.

    Searched from the END. A texture with several mips has the same trailer after
    each, and the largest is the one this wants; scanning forward would find the
    smallest that happens to match first.
    """
    expected = mip_size(width, height, fmt)
    trailer = struct.pack("<iii", width, height, 1)

    at = uexp.rfind(trailer)
    while at >= 0:
        start = at - expected
        if start >= 0:
            return uexp[start:at]
        at = uexp.rfind(trailer, 0, at)

    raise TextureError(
        f"No {expected}-byte mip found before a {width}x{height}x1 trailer. "
        "The texture's pixel data most likely lives in a separate .ubulk file, "
        "which this script does not read."
    )


def to_dds(data: bytes, width: int, height: int, fmt: str) -> bytes:
    """Wrap a raw mip in a DDS container so Pillow can decode the block format."""
    spec = BLOCK_FORMATS[fmt]
    if spec["fourcc"] is None:
        raise TextureError(f"{fmt} is uncompressed; it needs no DDS wrapper")

    DDSD = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000      # caps|height|width|pixelformat|linearsize
    # 4-byte magic + the 124-byte DDS_HEADER: seven dwords, 11 reserved dwords,
    # the 32-byte DDS_PIXELFORMAT (eight dwords), then five caps dwords.
    header = struct.pack(
        "<4s" "7I" "44s" "8I" "5I",
        b"DDS ",
        124, DDSD, height, width, len(data), 0, 1,  # size flags h w linearSize depth mips
        b"\0" * 44,                                 # dwReserved1[11]
        32, 0x4,                                    # ddspf.dwSize, DDPF_FOURCC
        int.from_bytes(spec["fourcc"], "little"),
        0, 0, 0, 0, 0,                              # bitcount and the four masks
        0x1000, 0, 0, 0, 0,                         # DDSCAPS_TEXTURE, caps2-4, reserved2
    )
    if spec["dxgi"] is None:
        return header + data
    # DX10 extension header: dxgiFormat, resourceDimension=TEXTURE2D, misc, array, misc2
    return header + struct.pack("<IIIII", spec["dxgi"], 3, 0, 1, 0) + data


def extract(pak: Pak, asset_path: str) -> dict[str, Any]:
    """Read one texture out of the pak and decode it. Returns image plus metadata."""
    from PIL import Image

    base = asset_path[:-7] if asset_path.endswith(".uasset") else asset_path
    for suffix in (".uasset", ".uexp"):
        if base + suffix not in pak.files:
            raise TextureError(f"{base}{suffix} is not in this pak")
    # A `.ubulk` sibling means the pixel data was streamed out of the `.uexp`
    # entirely, and `find_mip` would search a package that no longer holds it.
    # Said here rather than as a confusing "no mip found" further down.
    if base + ".ubulk" in pak.files:
        raise TextureError(
            f"{base} stores its pixels in a separate .ubulk file. This script reads "
            "the inline case only — the streamed one needs the bulk-data offset "
            "table decoded, which is a different job."
        )
    uasset = pak.read(base + ".uasset")
    uexp = pak.read(base + ".uexp")

    package = upackage.read(uasset)
    fmt = find_format(package)
    width, height = find_dimensions(uexp, fmt)
    mip = find_mip(uexp, width, height, fmt)

    if BLOCK_FORMATS[fmt]["fourcc"] is None:
        image = Image.frombytes("RGBA", (width, height), mip, "raw", "BGRA")
    else:
        image = Image.open(BytesIO(to_dds(mip, width, height, fmt)))
        image.load()
    return {
        "image": image.convert("RGBA"),
        "format": fmt,
        "width": width,
        "height": height,
        "bytes": len(mip),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pak", default=DEFAULT_PAK)
    parser.add_argument("--asset", default="", help="package path, with or without .uasset")
    parser.add_argument("--out", default="", help="where to write the .webp")
    parser.add_argument("--grep", default="", help="list texture packages matching this")
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()

    if not os.path.exists(args.pak):
        print(f"No pak at {args.pak}", file=sys.stderr)
        return 2

    pak = Pak(args.pak)

    if args.grep:
        matches = sorted(p for p in pak.files
                         if args.grep.lower() in p.lower() and p.endswith(".uasset"))
        print(f"{len(matches)} matching")
        for path in matches[:args.limit]:
            print(" ", path)
        return 0

    if not args.asset:
        parser.error("--asset or --grep is required")

    try:
        result = extract(pak, args.asset)
    except TextureError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"{result['width']}x{result['height']} {result['format']} "
          f"({result['bytes']:,} bytes of pixel data)")

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        # Lossless: these are small UI sprites with hard edges and an alpha
        # channel, which is exactly what lossy WebP handles worst.
        result["image"].save(args.out, "WEBP", lossless=True)
        print(f"wrote {args.out} ({os.path.getsize(args.out):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
