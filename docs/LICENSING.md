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

| Component | Licence | Note |
|---|---|---|
| `palsav` | GPL-3.0-or-later | The reason for all of the above |
| `palooz` | see upstream | Oodle bindings; used for saves and the pak reader |
| Next.js, React | MIT | No copyleft obligation |
| FastAPI, uvicorn, pydantic | MIT / BSD | No copyleft obligation |
| `lucide-react` icons | ISC | |
| Game data in `backend/data/gamedata.json.gz` | see below | |

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
