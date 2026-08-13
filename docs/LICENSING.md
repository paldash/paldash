# Licensing

**This project is GPL-3.0-or-later.** `LICENSE` holds the full text.

## Why, specifically

Not a preference — a consequence. `backend/parser.py`, `backend/saveedit.py`
and everything else that touches a save import **`palsav`**, which is
**GPL-3.0-or-later**. Linking GPL code into a program makes the combined work
GPL, so this dashboard inherits it.

There is no realistic way around it either. `palsav` plus `palooz` are the only
things that read Palworld 1.0's Oodle-compressed (`PlM`) saves; the PyPI package
`palworld-save-tools` cannot. Dropping them means dropping the entire reason
this project exists.

## What that means in practice

**Private and LAN use triggers nothing.** The GPL's obligations attach to
*distribution*. Running this on your own server, for your own players, however
you like, requires nothing of you — no source publication, no notices, no
attribution. Most people using this will never need to think about it.

Obligations start the moment you **give the software to someone else**:
publishing the repository, pushing an image to a registry, handing a colleague a
copy, or shipping it as part of a product. Then:

- the complete corresponding **source must be available** to whoever received it,
  including any changes you made
- it must carry **the same licence** — you cannot relicense it, and you cannot
  add restrictions on top
- the licence text and copyright notices must travel with it

Note the asymmetry that catches people out: making the dashboard reachable over
the internet is **not** distribution under GPL-3.0 (that would be the AGPL). You
can host it publicly without publishing anything. Handing over the software
itself is what counts.

## Third-party components

**This table is what actually ships.** A credit for something no longer used is
not a harmless courtesy — it misdescribes which licence covers what. The rule
is that an entry must name a thing present in the built image, and every such
thing must have an entry.

| Component | Licence | What it is here |
|---|---|---|
| `palsav` | **GPL-3.0-or-later** | Reads `PlM` saves — the reason for all of the above |
| `palooz` | see upstream | Oodle bindings, wrapping powzix/`ooz`; saves *and* the pak reader |
| Next.js, React, react-dom | MIT | |
| FastAPI, uvicorn, pydantic | MIT / BSD | |
| `lucide-react` icons | ISC | Every icon in the chrome |
| **Leaflet** | BSD-2-Clause | The map. Bundled, not CDN-loaded |
| **Recharts** | MIT | The metrics charts |
| **Zustand** | MIT | Client state |
| **tylercamp/palcalc** | MIT | The 46,655-pair breeding table in `backend/data/`, and the reference samples `src/lib/map-coordinates.ts` fits against |
| **PalworldSaveTools `resources/game_data/`** | MIT (© 2026 Pylar) | Icon paths, catalogue membership, the 174 fast-travel coordinates, and the stat formula `backend/palstats.py` transcribes — **not** the display names, which come from the game's own `L10N/` tables |
| Game data in `backend/data/*.json.gz` | see below | Pocketpair's, whoever extracted it |

Two credits in `README.md` are **lineage, not dependency**, and are phrased as
such deliberately: cheahjs/`palworld-save-tools` (the GVAS reader this ecosystem
descends from — the PyPI package cannot read 1.0 saves and is not installed) and
RNZ01/`palworld-server-dashboard` (the inspiration). Neither ships.

`backend/data/provenance.json` is the per-artifact answer and is the file to
trust when this table and a docstring disagree.

## Game data and assets

`backend/data/gamedata.json.gz` is compiled from
`refs/PalWorldSaveTools-main.zip`, whose `resources/game_data/` is MIT-licensed.
The **underlying data and artwork are Pocketpair's**, not the packager's and not
ours. An MIT wrapper around someone else's game assets does not grant rights to
those assets.

For private use this is a non-issue. Before publishing anything containing Pal
names, icons, item data or map textures, that is a question to settle
separately from the code licence — and it is the more likely problem of the two.

`refs/` and `refworld/` are gitignored and must stay that way: the first is
~66 MB of third-party archives plus a 4.5 GB game install, and the second is a
real save containing real Steam IDs and player names.
