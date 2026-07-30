"""
Write a JSON artifact, honouring a `.gz` suffix.

Split out because both extractors got this wrong the same way: they wrote plain
JSON with `json.dump(open(path, "w"))` no matter what the path said, while
`backend/gamedata.py`, `backend/worldobjects.py` and the effigy loader all read
with `gzip.open`. A file named `.json.gz` containing `{\\n` therefore loaded as:

    World object data unavailable (Not a gzipped file (b'{\\n'))

...and the layer silently rendered empty. The committed bundles were gzipped by
a separate step, so the scripts never round-tripped their own output and nothing
caught it until someone regenerated on a live server.

The filename is a promise about the format. This keeps it.
"""

from __future__ import annotations

import gzip
import json
import os
from typing import Any


def write_json(path: str, payload: Any, *, indent: int = 1) -> None:
    """
    Serialise `payload` to `path`, gzipping when the name ends in `.gz`.

    `mtime=0` keeps the output byte-identical across runs of unchanged input,
    which is what lets a regeneration be diffed rather than merely trusted —
    gzip otherwise stamps the current time into the header.
    """
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    if path.endswith(".gz"):
        with gzip.GzipFile(path, "wb", compresslevel=9, mtime=0) as raw:
            raw.write(json.dumps(payload, indent=indent).encode("utf-8"))
        return

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=indent)
