# Publishing this project

What has to be true before this repository goes public, and what is still an
open decision. `docs/LICENSING.md` covers the **code** licence; this file covers
everything else, because the code licence turned out to be the easy half.

**I am not a lawyer and this is not legal advice.** What follows is a factual
inventory of what the repository distributes and where each piece came from, so
that the decision is made against measurements rather than against a vibe.

---

## 1. The code licence is settled, and it is GPL-3.0

Not a choice. `palsav` is GPL-3.0-or-later and is the only thing that reads
Palworld 1.0's `PlM` saves, so the combined work inherits it. See
`docs/LICENSING.md`. Nothing about going public changes this; it just makes it
*bind*, because GPL obligations attach to distribution.

Practical consequence: contributors' changes are GPL-3.0 too, and the project
cannot later be relicensed or taken closed-source without replacing `palsav`.
Decide now whether that is acceptable, because it is much harder to undo after
the first outside contribution.

---

## 2. The real question: what the repository ships that is Pocketpair's

Measured against the current tree, not estimated:

| What | Amount | Where it came from |
|---|---:|---|
| Item and Pal icons, map textures | **1,493 files, 15 MB** | Extracted from the game install / community wiki. **Pocketpair's artwork, byte for byte** |
| `backend/data/*.json.gz` (English) | ~35 files | Names, descriptions, stats, coordinates extracted from the paks |
| `backend/data/lang/*.json.gz` | 15 files, ~3.4 MB | Pocketpair's own translated strings, all 15 languages |

An MIT licence on a third-party *packaging* of this data does not grant rights
to the underlying assets, and `docs/LICENSING.md` already says so. The artwork
is the least ambiguous part: a `.webp` of a Pal icon is Pocketpair's picture, in
this repository, redistributable by anyone who clones it.

**For private and LAN use none of this matters.** It becomes a question only on
publication, which is exactly what this file is for.

### What actually reduces the exposure, and it is nearly free here

The repository already contains a script to regenerate every one of those
artifacts from a local game install:

```
scripts/build-gamedata.py        -> backend/data/gamedata.json.gz
scripts/extract-language.py      -> backend/data/lang/*.json.gz
scripts/install-icons.py         -> public/icons/
scripts/install-map-assets.py    -> public/maps/
```

So the option of **shipping code and not assets** — where each operator extracts
from the copy of the game they already own and must own to run a server — costs
a setup step rather than a rewrite. That is an unusually good position to be in,
and it is worth spending it.

Three shapes, in increasing order of caution:

| | What ships | Cost |
|---|---|---|
| **A. As today** | Everything, ready to run | Distributes Pocketpair's artwork and strings |
| **B. Data yes, art no** | The `.json.gz` bundles; icons and maps extracted locally | One setup step; the UI degrades to no artwork until it is run, which `GameIcon` already handles |
| **C. Code only** | Neither; `setup.sh` builds all bundles from the operator's install | Two more minutes of setup, and `refs/` becomes a hard requirement rather than a dev convenience |

**Recommendation: B, and make the extraction step part of first-run setup.** The
numeric and textual data has a much better argument behind it than the artwork
does — a Pal's HP value is closer to a fact about the game than a drawing of it
is — while the icons are the part with no argument at all. B removes the
clearest problem for the least friction, and C stays available if anyone ever
objects.

**Whichever is chosen, `scripts/` must keep working**, because the ability to
regenerate is what makes B and C possible at all. A bundle that can only be
copied and not rebuilt forecloses the choice.

### What other projects do is evidence about risk, not about permission

Palworld save editors, wikis and dashboards ship this data widely and Pocketpair
has not moved against them. That is genuinely useful information about the
practical risk, and it is not a licence. Do not let "everyone does it" get
written down here as though it settled the question.

---

## 3. Pre-publication checklist

Verified against the tree on 2026-08-13 unless marked.

- [x] **No save, install or secret has ever been committed.** `git log --all`
      over `refworld/`, `refs/`, `PalWorldSettings.ini`, `banlist.txt`, `.env`
      and `*.sav` returns nothing. History is clean, so no rewrite is needed and
      **a fresh repository is not required for safety reasons.**
- [x] **`.gitignore`, `.dockerignore` and `next.config.ts`
      `outputFileTracingExcludes` all exclude `refs/` and `refworld/`.** Three
      mechanisms that have to agree; pinned by `src/lib/build-config.test.ts`.
- [x] **Credits name only what ships.** Audited 2026-08-13 — see §4.
- [x] **No runtime dependency on any external API.** Grep finds no outbound
      `fetch` to any host; the only `urllib` callers are `gameapi.py` and
      `safety.py`, both talking to the operator's own game server.
- [x] **Decide A / B / C above.** Decided 2026-08-19: **B**, implemented as
      the self-provisioning boot (#149) — artwork auto-fetched at first boot,
      stale bundles auto-rebuilt from the operator's own pak — and the art
      stripped from the repo AND its full history with `git filter-repo`
      (286 commits preserved, zero history references remain; the pre-filter
      history lives in a bundle outside the repo).
- [x] **Add `CONTRIBUTING.md`** — done 2026-08-18, with `SECURITY.md`, a
      `NOTICE` for the Pocketpair-owned data, and issue templates beside it.
- [ ] **Decide whether `AGENTS.md` ships as-is.** It is the most valuable file
      here and it is also a candid record of wrong answers, some of them
      embarrassing. Recommendation: **ship it unchanged.** Its credibility is
      the corrections.

---

## 4. The attribution audit (2026-08-13)

Two failure directions, both real. The rule adopted: **an entry must name
something present in the built image, and everything in the built image must
have an entry.**

**Overcredited — fixed.** `README.md` credited PalworldSaveTools'
`resources/game_data/` with "every item, Pal, passive, active skill, technology
and structure name". That stopped being true when `scripts/gametext.py` started
taking display strings from the game's own `L10N/` tables; the archive now
supplies icon paths, catalogue membership, the 174 fast-travel coordinates and
the stat formula. `backend/data/provenance.json` had it right the whole time and
is the file to trust in a disagreement.

**Uncredited — fixed.** `docs/LICENSING.md`'s dependency table was missing
Leaflet (BSD-2-Clause), Recharts, Zustand and tylercamp/palcalc, all of which
ship. The Palworld community wiki was uncredited in `README.md` despite being
used twice — the seven map-marker icons, and the condenser table
`backend/condenser.py` tests against the game's files. `condenser.py` said "the
community table" without naming it, which fails this project's own standard that
a citation must be checkable.

**Correct as they stood.** palcalc (still supplies the breeding pair table *and*
the `map-coordinates.ts` calibration samples), Rock Paper Shotgun (still the
element chart in `elements.py`), and the two credits that are **lineage rather
than dependency** — cheahjs/`palworld-save-tools`, whose PyPI package cannot read
1.0 saves and is not installed, and RNZ01's dashboard, the inspiration. Both are
now labelled as lineage in `docs/LICENSING.md` so neither reads as a shipped
component.
