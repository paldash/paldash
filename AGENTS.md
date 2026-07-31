# AGENTS.md

Guidance for agents working in this repository. `CLAUDE.md` is a pointer to this file.

## What this is

A web dashboard for a self-hosted Palworld server: a Next.js UI and a Python
save-parsing backend, running as one container beside the game server on a
shared bind mount.

- `src/` — Next.js 16 App Router UI. It is the only process listening on the
  network; `src/app/api/` proxies to the backend and enforces a route allowlist.
- `backend/` — FastAPI service on loopback that parses and (carefully) mutates
  save files, and owns the SQLite database holding accounts, sessions, the audit
  log and the backup schedule. It authenticates for itself — see "Security
  boundary".
- `docs/AUDIT.md` — current state, gap analysis, and the phased roadmap. Read
  this before planning work.
- `docs/FEATURES.md` — what already exists. The roadmap tracks *gaps*, so
  finished work (breeding, the map, backups) does not appear there and reads as
  missing. Check this before concluding something is unbuilt.
- `docs/ARCHITECTURE.md` — how the pieces fit: the request path, the module map,
  and the three invariants everything else falls out of.
- `refs/` — third-party reference archives (gitignored, ~66 MB). Contains the
  authoritative Palworld 1.0 game database; see "Reference data" below.
- `refworld/` — a real world save used for integration tests (gitignored,
  contains real Steam IDs and player names — never commit it, never paste its
  contents into an issue).

## Commands

```bash
# Setup (one time). Builds .venv and compiles palsav/palooz out of refs/.
./scripts/setup-dev.sh

# Tests
.venv/bin/python -m pytest                       # backend, everything: 991 tests, ~21 min
.venv/bin/python -m pytest -m "not integration"  # backend unit only: 931 tests, ~2 min
.venv/bin/python -m pytest -m "not slow"         # skip full-world parses
.venv/bin/python -m pytest backend/tests/test_safety.py -k read_only  # one test
npm test                                          # frontend (vitest): 82 tests, <1s

# Frontend
npm run dev
npm run lint
npm run build

# Backend alone
.venv/bin/python backend/main.py    # binds 127.0.0.1:8400

# Regenerate bundled assets from refs/ (rarely — only on a new PST release)
python3 scripts/build-gamedata.py      # -> backend/data/gamedata.json.gz (212 KB)
python3 scripts/install-map-assets.py  # -> public/maps/{palpagos,worldtree}.webp
```

Integration tests skip automatically when `refworld/` or `palsav` is absent, so
a clean checkout still runs green.

**The 60 integration tests cost ~19 of those 21 minutes** — each parses a real
55 MB world, and the write paths take a full verified backup on top. `soloexport`
is the most expensive single test in the suite because it walks the entire node
tree for uid matches. Use `-m "not integration"` while iterating; run the whole
thing before calling anything done.

They write those backup archives into `$TMPDIR`. If that is a tmpfs, repeated
interrupted runs will fill it, and a full `/tmp` presents as **every shell
command failing with no output** rather than as a disk error.

## The rule that matters most

**Never write to a save file unless the server is provably stopped.** A corrupted
world is unrecoverable and is the one failure mode that actually costs users
something irreplaceable.

`backend/safety.py` fails *closed*: it combines four independent signals (REST
API, TCP port, save-file mtime, process scan) and only reports "editable" when
REST, TCP and file activity all positively say stopped. Anything ambiguous —
unreachable API, unmounted volume, wrong password — resolves to "running".
An HTTP 401 counts as **running** (something is listening and rejecting us).

Every mutation goes through `backup.guarded_save_write`, which re-checks safety,
takes a full verified backup, then re-checks again before yielding.

`backend/saveedit.py` adds the conservation invariant: the total quantity of
every item in every container must be identical after a sort, verified in memory
*and* again after re-reading from disk, with automatic rollback on mismatch.

Backups (`backend/backupstore.py`) are `.tar.gz` archives with a SHA-256 per file
plus one for the archive. Two things not to undo:

- **Never go back to copying the world directory.** `copytree` silently swept in
  the server's own rotating snapshots under `<world>/backup/` — on the reference
  world that turned a 2.1 MB world into 66 MB archives, each containing copies of
  all the earlier ones. `collect_world_files` uses an explicit include list and
  prunes excluded directories during the walk.
- **A restore verifies the archive before touching anything** and leaves its own
  rollback point. Restoring a corrupt backup over a working world is the worst
  outcome available here.

When you touch any of this, run the full suite including `-m slow`. Those tests
exercise the real pipeline against a real world.

## Palworld 1.0 specifics

- Saves use Oodle Kraken with magic bytes `PlM` (previously `PlZ`/zlib). The PyPI
  package `palworld-save-tools` **cannot read them**. Use `palsav` + `palooz`.
- `palsav` is **GPL-3.0-or-later**. Distributing this dashboard publicly would
  make the combined work GPL-3.0. Private/LAN use triggers nothing.
- Field shapes that bite: `Level` and `Talent_*` are ByteProperty and nest one
  level deeper than Int (`{'value': {'value': 24}}`); the field is `SlotId` not
  `SlotID`; item `count` sits at the slot root while `static_id` is nested under
  `item`. `backend/parser.py` has `_num`/`_slot`/`_v` helpers for these — use
  them rather than indexing directly.
- Player `.sav` filenames are uppercase undashed hex; `Level.sav` stores
  lowercase dashed GUIDs. Match via `savefiles.get_player_sav_path`.

## The tower bosses were never missing from the map

They are fast-travel points. Eight of the 174, named `… Tower Entrance`, and they
rendered as the same gold diamond as the other 166 — so "the map doesn't show
towers" was a fair complaint about a map that was showing all eight.

`gamedata.fast_travel_kind` splits the layer into `tower` (8), `watchtower` (22)
and `travel` (144), each independently filterable and drawn differently. The
eight are exactly Palworld's eight tower bosses, which is the check that the name
rule picks out the right thing rather than merely something.

**Do not go looking for them in the pak.** There is no `BP_Tower*` world actor.
Grepping for "Tower" finds fortress set-dressing and
`BP_LevelObject_TowerLockBarrier`, which looks like the answer and is not:
extracted, it gives 108 objects in **64 clusters across the whole map** against a
game with 8 towers. It is the sealed-door lock minigame. The count check is the
transferable part — a category whose size disagrees with what the game has is
wrong however plausible its class name reads.

The classification is **English-dependent** and fails safe: an unrecognised name
is a plain travel point, which is what all 174 were before.

## Map work on `zoom` is work on every animation frame

`map-inner.tsx`. Live-player markers scale with zoom through a `--player-scale`
custom property on the map container. The first version updated it on Leaflet's
`zoom` event, reasoning that one style write per frame is cheap.

It is not, because of *where* it writes. Setting a custom property on
`.leaflet-container` invalidates style for the whole map subtree (custom
properties inherit), and `classList.toggle` on it invalidates selector matching
for every descendant. Doing that per frame made the map **visibly lurch sideways
mid-zoom and snap back on settle**.

Both now run on `zoomend`, behind a guard that skips the write when the value has
not changed — `zoomSnap: 0` settles several times per gesture and most settles
land in the same bucket. Marker size holding steady during a zoom is both cheaper
and calmer than tracking it continuously.

**And the scale must go on a child, not the marker root.** Leaflet positions
`.player-pin` with an *inline* `transform: translate3d(…)`; an inline style beats
a stylesheet rule, so a `transform: scale()` written against the root is silently
discarded — the first version paid the whole reflow cost and never scaled
anything. `.player-pin-inner` exists so the positioning transform and the scaling
transform cannot compete for the same property.

## The map has two regions, not one

`src/lib/map-coordinates.ts`. Palworld 1.0's landmasses are **separate maps with
separate framings**, not one continuous image — verified by checking all 174
fast-travel points against the transform: 157/157 Palpagos points land on the
Palpagos image, 0/17 World Tree points do. Anything that assumes a single
transform is wrong.

In-game map *coordinates* (`worldToGameMap`, what players read and type) are one
continuous scale across both. Only the image placement differs.

**Palpagos is calibrated; World Tree is `calibrated: false` and says so in the
UI.** There is no ground truth to fit it against yet — the reference save has
zero objects on that landmass. It becomes fittable the moment anyone builds or
opens a chest there; then replace four constants and flip the flag. Do not
quietly present the provisional transform as exact.

Axes swap: in-game map X derives from world **Y**, and map Y from world X.

## A `.gz` filename is a promise, and the extractors broke it

`scripts/jsonout.py` exists because both pak extractors wrote **plain JSON
regardless of the output path**, while `gamedata.py`, `worldobjects.py` and the
effigy loader all read with `gzip.open`. A file named `effigies.json.gz`
containing `{\n` therefore loaded as:

    World object data unavailable (Not a gzipped file (b'{\n')); the layer will be empty

...and the map silently rendered an empty layer. The committed bundles had been
gzipped by a separate step, so the scripts never round-tripped their own output
and nothing caught it until someone regenerated on a live server.

`write_json` honours the suffix and sets `mtime=0`, so unchanged input produces
byte-identical output and a regeneration can be **diffed** rather than trusted.

## Field bosses were hiding inside the spawner count

The alpha Pals that drop Ancient Technology Points were extracted all along — as
99 of the 13,851 `palspawner` placements, indistinguishable from ordinary spawn
points and therefore unfindable. They are now their own category, **named**.

The naming reuses the habitat trick (`extract-pal-habitats.py`): properties are
undecodable, name tables are not, so intersecting a sheet's name table with the
known species list says what it references. **71 of 73 sheets resolve, and every
single one names a `BOSS_`-prefixed species** — which is the verification, not
the search key. The sheets were found by the `FBOSS` class-name convention, and
that they independently resolve to boss forms is what confirms the convention
means what it looks like. A sheet often names two species (`BOSS_QueenBee` +
`SoldierBee`); the prefixed one is the encounter.

`palspawner` excludes them by **negative lookahead**, not by `--targets` ordering
— first-match-wins would have made the split depend on a command line.

**Level is not available.** It lives in the spawner's unversioned properties, and
the bundled tables carry a boss's name, icon, rarity and description but no
level. Name and artwork are what the data supports; do not invent the rest.

## Spawn habitats come from name tables, not from properties

`scripts/extract-pal-habitats.py`. Spawner actors placed in the world name a
**sheet**, not a species — `BP_PalSpawner_Sheets_2_1_forest_1` — and which
species a sheet spawns lives in properties cooked with unversioned property
names, so it cannot be decoded. The reference archive has no habitat field
either.

The way through is the same one `extract-effigies.py` uses: a package's **name
table is plainly serialised** even when its properties are not. Intersecting a
sheet's name table with the known species list gives its roster. Measured:
**348 species, 13,440 of 13,851 spawners attributed (97.0%)**.

**The claim is narrower than it looks.** A name-table hit means "this blueprint
references this species", not "this species spawns here at this rate". Good
enough to shade a region; do not present it as a spawn-rate table.

**Encounter-only forms legitimately have no habitat.** `_Oilrig` and `_Tower`
variants are placed by encounter logic rather than by world spawners, so
`HadesBird_Oilrig` has zero cells while `HadesBird` has 132. That is why the
Paldeck merges variants sharing a Paldeck number and **unions** their ranges —
and why a zero there must not read as missing data.

**`paldeckNumber` is not "0 when absent".** The game uses negative zukan indices
for things the Paldeck does not list: **-2** for gym bosses, **-1** for species
present in the files but unreleased. A plain `or 9999` leaves negatives intact
and sorts them ahead of Lamball — which put "Axel & Orserk (Gym)" at entry #1.

## World Tree orientation cannot be derived from the cell grid

`scripts/fit-worldtree.py` is a recorded **negative** result. The idea was to
skip needing a player up there by correlating the occupied-cell silhouette
against the map texture and picking the best of 8 flips/rotations.

It fails its own control. Palpagos' orientation is known correct (157/157
fast-travel points), and the correlation ranks it **6th of 8** — the winner takes
it by 0.015 IoU, which is noise.

The premise is wrong, not the tuning: **occupied cells are not a coastline.** The
game ships a streaming cell for anything containing content, including open ocean
with fishing spots and oil rigs. Measured on Palpagos, the occupied set fills
**51.8%** of its bounding box while the texture's land mask covers **24.4%** — two
masks describing different things, so their overlap is bounded low at every
orientation.

The cell grid remains exactly right for the **extent**, which is what
`map-coordinates.ts` uses it for. It carries no usable shape. Do not "fix" the
script by changing metrics until the control passes; that is fitting the method
to the answer. Orientation still needs a real point on that landmass.

## Friendly names

`backend/gamedata.py` resolves internal IDs (`Sheepball`, `AIcore`) to what
players see, from `backend/data/gamedata.json.gz` — bundled and committed, never
fetched.

**Lookups are case-insensitive, deliberately.** The upstream data is
inconsistently capitalised: a save stores `Sheepball`, `OctopusGirl`,
`SwordCutlassfish` while the reference spells them `SheepBall`, `OctopusGIrl` (a
typo in their data), `SwordCutlassFish`. Exact matching silently loses eight real
Pals. Resolve through this module rather than indexing the blob directly, and let
unknown IDs fall back to `humanize()` rather than failing.

`CharacterSaveParameterMap` holds humans as well as Pals, so use
`character_name()` for anything out of it — `pal_name()` alone leaves merchants
and guards showing internal IDs.

**`breeding.py` had the same bug and it cost more than names.** Its lookups were
exact `dict.get` against palcalc's table until 2026-07-30. The visible symptom
was a breeding path rendering "Sheepball + ElecCat"; the invisible ones were
worse — `_breedable` used the same exact match, so those Pals were classified
unbreedable and dropped from the palbox entirely, and `_pair_key` joins raw ids,
so every pair involving them missed the table.

So canonicalisation happens at the **boundary** (`breeding.canonical_species`),
not in the display helper. Fixing only `pal_info` would have left the names right
and the breeding maths wrong, which is the harder failure to notice.

**Icons need no lookup at all, and building one was the mistake.**
`gamedata.json.gz` already records each entry's icon path
(`AIcore -> /icons/items/T_itemicon_Material_AIcore.webp`) and `describe_item()`
already returns it, so `scripts/install-icons.py` preserves the archive's
filenames and every existing path just resolves.

A first version renamed files to the ids the API speaks and shipped a lowercased
manifest to resolve them case-insensitively. It matched **0 of 2,466** items:
item icons are named after their *texture*, and nothing turns
`T_itemicon_Material_AIcore` into `AIcore`. Deriving a mapping the data already
contained created a second source of truth that disagreed with the first.
Preserving filenames took coverage to **99.6% of items and 93.4% of Pals** — the
Pal shortfall is boss and variant forms, which have no artwork of their own.

Bundled `maxStack` values are wired into the sorter as of Phase 5. The merge
ceiling is `max(authoritative, observed)`, and the `max()` is the point: raising
a ceiling only ever packs items into fewer slots, while lowering one could
require *more* slots than a container already uses. So a stack larger than the
game's own cap — modded, or from an older version — is preserved rather than
split. `test_saveedit.py` pins all three cases.

## Which base a Pal works at — the WorkerDirector names its container

This file previously said per-base Pal attribution was unavailable, and shipped a
**guild** total stamped onto every base. On the reference world that summed to
**5,152 across eleven bases, against 1,905 Pals in the world** — a count larger
than the population it counts.

It is available. `BaseCampSaveData[].WorkerDirector.RawData` is an opaque
ByteProperty, but its layout is fixed: **118 bytes, the base camp id at offset 0
and the worker container id at 98**. `parser.extract_base_workers` reads it, and
resolves **11 of 11** bases on the reference world.

Reading a `ByteProperty` at a measured offset is only defensible with a
verification attached, so the decoded id **must resolve to a real entry in
`CharacterContainerSaveData`** or it is dropped. A layout change therefore yields
*nothing*, and per-base counts fall back to the guild figure — never a confident
wrong answer about whose Pal is where.

The independent check that the field means what it looks like: all 23 character
containers on the reference world now classify, and they classify **by capacity
without being told to**.

| Kind | Count | `SlotNum` | Source |
|---|---:|---|---|
| Palbox | 5 | 960 | player save `PalStorageContainerId` |
| Party | 5 | 5 | player save `OtomoCharacterContainerId` |
| Base workers | 11 | 20/16/13/8 | `WorkerDirector`, offset 98 |
| Orphaned | 2 | 5 | none — one still holds a Pal |

Eleven bases, eleven worker containers, and every one of them lands in the
20/16/13/8 group rather than on a palbox or a party. **165 of 1,905 Pals are
deployed at a base**; the rest are in palboxes, which is a guild-level thing.

So `palCount` (this base) and `guildPalCount` (this base's guild, repeated on
each of its bases) are both present and named for what they are. Never sum the
second.

Those two orphaned five-slot containers are why Pal `location` has an `other`
value. It is a real state, not a parse failure.

## Bases own containers — via the object, not the guild

Per-base inventory rests on one join, and it is exact rather than spatial:

```
BaseCampSaveData[].RawData.id
  <- MapObjectSaveData[].Model.RawData.base_camp_id_belong_to
     MapObjectSaveData[]
  -> .ConcreteModel.ModuleMap["…::ItemContainer"].RawData.target_container_id
  -> ItemContainerSaveData[].key.ID
```

`parser.extract_container_ownership` walks it; `summarise_base_storage` folds
container contents up into per-base totals.

**`group_id_belong_to` is the guild, not the base.** Both are GUIDs sitting
beside each other in the same `RawData`, and substituting one for the other
still produces a plausible-looking grouping — it just silently merges every base
in a guild into one pile. On the reference world none of its six values match a
base camp id. `test_base_storage.py` pins this specifically.

`BaseCampModuleMap::ItemStorages` looks like the obvious link and is **empty**
on a real world. Don't reach for it.

Reference-world figures, useful as a regression signal: 3,370 objects carry a
container id, 3 dangle, 262 attribute to the 11 bases, 3,105 are world-placed.
The remaining ~8,000 containers are player inventories and palboxes, which come
from elsewhere.

Unnamed bases keep the game's placeholder (`新規生成拠点テンプレート名0(仮)`)
rather than an empty string, so `_base_name` swaps in positional numbering and
flags `playerNamed: false`. Every base on the reference world hits this.

## The container: three things that will bite

All three were invisible to the test suite and only showed up on a real build
and run. If you touch the Dockerfile or the entrypoint, build and run it.

- **The builder and runtime Python minor versions must match.** The runtime
  installs Debian bookworm's `python3` (**3.11**). `orjson` and `palooz` are
  compiled extensions, so a `python:3.12` builder produces cp312 wheels that pip
  refuses outright and the image does not build.
- **`docker-entrypoint.sh` is `#!/bin/bash`, not `sh`.** It uses `wait -n`, a
  bashism; Debian's `/bin/sh` is dash, which errors, and `set -e` then killed
  the container about a second after boot — every time, silently.
- **`.dockerignore` must exclude `refworld/` and `refs/`.** The first stage does
  `COPY . .`, so without it 132 MB including a real world save with real Steam
  IDs goes into the build context and cache.

**Three ignore mechanisms have to agree, and `.next/` is the one that gets
forgotten.** `output: "standalone"` copies traced files out of the project root,
and the tracer over-includes: with no `outputFileTracingExcludes` in
`next.config.ts` it swept `refs/` (5.1 GB, including the
`PalWorldSettings.ini` that holds live server passwords) and `refworld/` into
`.next/standalone/`. **5.8 GB of build output for a 73 MB app.** `.gitignore` and
`.dockerignore` do not govern `.next/`, and the Dockerfile copies
`.next/standalone` wholesale out of the builder stage — so this is one
misconfigured ignore file away from publishing a real world save. Pinned by
`src/lib/build-config.test.ts`.

**Turbopack's glob parser rejects character classes** —
`TurbopackInternalError: Parsing glob pattern` fails the build outright rather
than degrading. So `.gitignore`'s date-prefix pattern for the session transcripts
cannot be copied into that config verbatim.

Runs as uid/gid 1000 (`APP_UID`/`APP_GID`), matching the Palworld server image's
PUID/PGID so the shared bind mount is readable without root. `/app/cache` and
`/app/backups` are chowned *in the image* because Docker seeds a named volume's
ownership from it, and a non-root process cannot fix it afterwards.

Still broken: `STOP_COMMAND`/`START_COMMAND` invoke `docker`, which is not
installed in the runtime image.

## Imports write; exports do not

`saveexport.py` has no write path at all. `saveimport.py` does. They are separate
modules on purpose — keep it that way, so the risky code is never one typo from
the safe code.

**Conservation does not apply to an import.** A sort must end with identical item
totals; an import changes them deliberately. The substitute guarantee is *scope*:
after writing, the file is re-read and the target container must match the plan
exactly **while every other container is unchanged**. Anything else rolls back.

`apply_container_import` re-plans against the live tree, never the parse cache,
and refuses if the caller's `planHash` no longer matches — that is what stops a
world that moved between preview and apply from being written blind.

**Durability items are refused, not handled.** A non-zero
`item.dynamic_id.local_id_in_created_world` means a record in
`DynamicItemSaveData`; overwriting the slot orphans it and a replacement cannot
be fabricated. `extract_containers` exposes `hasDynamicId` so this is caught at
preview time. An empty slot on disk is `static_id: ""`, `count: 0` and a zeroed
`dynamic_id` — read off the reference world, not assumed.

`container` and Pal imports exist. Player and technology imports are still refused
with a reason.

## A Pal import is a translation, not a third writer

`palimport.py` has no `guarded_save_write` call, no property writing and no record
creation. Like `slotedit.py`, it turns a document into the change set an existing
writer already takes:

    overwrite  ->  charedit.plan_pal_batch / apply_pal_batch
    create     ->  palclone.plan_clone / apply_clone

**`apply_import` reads the document only — it never loads the world.** Both writers
open `Level.sav` inside the write guard and re-plan against that live tree. Planning
here as well would validate against a copy read moments earlier: a second source of
truth, and guaranteed to be the staler one.

**One format, shared with the export.** `saveexport` kind `pal` emits the *same
dict* a `player` export embeds in `pals`, which is the same dict the parser
produces. So "restore this Pal" and "restore this player's team" are one file read
two ways, and adding a parser field does not need a second shape updated.

**An export says more than an import may write**, and the difference is *reported*,
never dropped. `ownerUid`, `containerId`, `slotIndex`, `guildId`, `hp`, `isBoss`,
`speciesId` and `gender` all appear in an export and none are settable — they
describe where a Pal *is*, not what it is. `extract_changes` returns them in
`ignored` with a reason and the UI lists them before the apply button, because
someone moving a Pal between servers would otherwise reasonably believe ownership
came with it.

`IMPORTABLE` is **derived from `editschema.PAL_FIELDS`**, not listed. A hand-written
list is how a new field silently stays unimportable, or a removed one keeps being
written by something that no longer validates it.

**Create needs a same-species template already in the world.** `palclone`
deep-copies a record precisely because the right `CustomVersionData` and
`permission_tribe_id` are whatever this save uses, so a species cannot be
fabricated — no template means a refusal naming the species, not a guess. Gender
comes from the template too, and a document that disagrees is reported rather than
half-applied.

**Create is one Pal per request.** Not caution: `apply_clone`'s verification — both
arrays grew by exactly *n*, no other container changed length — is written for one
request, and a batch reusing it would be checking the wrong invariant. `overwrite`
takes any number because `apply_pal_batch` is already a batch writer with the same
all-or-nothing guarantee.

## Security boundary

**The backend authenticates for itself.** The session token travels as
`X-Session-Token` and `backend/authz.py` resolves it against SQLite. The proxy
forwards a credential; it does not assert an identity, so a forged header does
nothing. Do not reintroduce trust in proxy-supplied roles.

Two gates must both agree before anything is written:

1. the caller's **role** grants the capability (`backend/roles.py`)
2. the **security level** permits it (`backend/policy.py`, where environment
   variables are a ceiling the web UI cannot raise)

The Next.js proxy additionally enforces an **allowlist** of backend routes
(`src/lib/permissions.ts`). It is not a prefix match with a default — anything
not explicitly listed is refused, and traversal is rejected before matching. Add
a route there when you add one to the backend, or it is unreachable.

Sessions are server-side and revocable; passwords are scrypt-hashed; sign-in is
throttled per IP and per username. Every mutating action is audited
(`backend/audit.py`) — add an `audit.record` call to any new one.

**"The backend authenticates for itself" was an aspiration in eleven places.**
A sweep of all 112 routes found `/api/refresh`, `/api/progress`,
`/api/inventory/{id}`, `/api/players/{uid}`, `/api/settings/ini`,
`/api/world/fasttravel`, `/api/world/reference`, `/api/roles`, `/api/policy`,
`/api/reports` and the breeding reference routes with **no `authz.require` at
all** — reachable only through the proxy, and therefore trusting exactly what
this section says not to trust. Two of them were also filter bypasses:
`/api/inventory/{id}` returned any container's contents by id, going around every
base-privacy check built on top of it, and `/api/players/{uid}` returned players
the roster had hidden. When you add a route, add `authz.require` *and* the
allowlist entry; neither substitutes for the other.

**A filter applied to one of two endpoints serving the same data is not a
filter.** `/api/world/discoveries` dropped undiscovered locations server-side
while `/api/world/fasttravel` returned all 174 to anyone — and the map reads the
second whenever the first is unavailable, so `discoveryVisibility` did nothing.

**`/api/refresh` is the expensive one.** A parse is the heaviest thing this
dashboard can do to a machine also running a game server. One parse at a time was
enforced; *back-to-back* parses were not, because the 15-minute floor only
applied to automatic parses and the Refresh button posts `force=true`. Forcing
now needs an account and respects `PARSE_FORCE_MIN_INTERVAL` (120s).

Remaining gaps are catalogued in `docs/AUDIT.md` §5. Roles, capabilities and the
visibility settings are documented for operators in `docs/ROLES.md`.

## Reference data

`refs/PalWorldSaveTools-main.zip` contains `resources/game_data/` — the
authoritative Palworld 1.0 database (2,466 items, 753 Pals, 1,905 passives, 588
technologies, 174 fast-travel points with coordinates, 2,468 icons, both map
textures). MIT-licensed, and validated against the reference save: a player's 117
unlocked fast-travel IDs matched 117/117.

The compiled subset is already committed (`backend/data/gamedata.json.gz`), so
`refs/` is only needed to regenerate it. Exact figures live there too — 1,413
technology points over 537 techs, 185 ancient over 51 — and supersede the
web-sourced estimates that remain in `reference_totals.json` for the handful of
categories the data tables do not enumerate.

**Do not hand-write or scrape game data that already exists there.** Do not add a
runtime dependency on any external API — the container must work offline on a LAN,
so anything adopted is fetched once and bundled.

## Conventions

- Comments explain *why*, especially where a subtlety already caused a bug.
  Match the existing density; do not narrate obvious code.
- Backend modules are flat and import each other directly. Module-level constants
  capture environment variables **at import time**, so tests monkeypatch the
  module attribute, not `os.environ`.
- Backend tests are pytest, no plugins. Frontend tests are vitest
  (`src/**/*.test.ts`); `vitest.config.ts` excludes `.next/` so a stale build
  copy cannot be discovered and pass in place of the real source.
- New backend module needing storage? Use `backend/db.py` (SQLite). The Python
  process owns that file exclusively — Next.js asks over loopback rather than
  opening a second driver.

## Bulk and slot edits reuse, they do not duplicate

Three Phase 7 features write, and none of them opened a new path to the save:

- `slotedit.py` turns a slot patch into a container **import document** and hands
  it to `saveimport`. It exists so the risky code stays in one file.
- `palcheck.py` repairs by producing values and calling `charedit.apply_pal_batch`.
- `charedit.apply_pal_batch` is the one batch writer; `plan_pal_batch` takes
  **per-Pal** change sets, and `spread_changes` builds the same-for-everyone shape
  from it. Bulk edits and repairs are the same code path.

A batch is **all-or-nothing on purpose.** Half of 200 Pals moved, with nothing
recording where it stopped, is worse than a refusal.

A slot-edit document names **only the patched slots**. `plan_container_import`
leaves unnamed indices alone, so a partial document is a first-class thing: an
unrecognised item elsewhere in the chest cannot block an edit that never touched
it, and a stale view of the rest cannot revert someone else's change.

## Breeding routes are gender-aware, but only on what you own

`possible_offspring` always enforced gender; `breeding_paths` and
`indirect_targets` did not, and said so in a docstring. That was survivable while
the planner ran over a whole server's Pals and stopped being so when it was
scoped to one palbox, where single-gender species are common.

**The constraint binds on owned species only, and that is not a shortcut.**
Parents are not consumed, so any pair that works once works again — an
*intermediate* can be re-bred until it comes out the gender the next step needs.
An owned species cannot: if your only Relaxaurus is male, no amount of breeding
turns it female. So a route is blocked exactly when a step pairs two species you
already own whose genders do not oppose. `breeding._pairable` is that test, shared
with the one-step view so the two cannot disagree.

An unreachable target re-runs the search **without** the constraint to say which
kind of unreachable it is. "Reachable by species but not with your genders" and
"not reachable at all" call for completely different actions from the player.

## EXP-vs-level is one-sided, and that was measured

The obvious rule — EXP must sit inside its level's band — is half wrong. On the
reference world: **0** of 1,905 Pals and 0 of 5 players sit *above* their band;
**8** Pals sit below it. A freshly caught Pal arrives at its wild level with
almost no EXP and the game leaves it there.

So low EXP is a state Palworld produces itself and must not be rejected or
flagged. High EXP never occurs naturally and *is* acted on at load, so that half
is a hard rejection. `editschema._check_exp_matches_level` and
`palcheck.inspect_pal` both follow this; don't "fix" the asymmetry.

## An unrecognised character id is not evidence of cheating

`CharacterSaveParameterMap` holds humans, and the bundled tables are incomplete:
13 of the reference world's 1,905 characters are ordinary NPCs (`Male_Soldier`,
`Female_DesertPeople`, `Scientist_LaserRifle`) that the 753-Pal and NPC tables do
not list. They carry IVs and passive skills exactly like a Pal, so **there is no
structural way to tell them apart.**

`palcheck` therefore classifies `unknown_species` as an **advisory** and never
counts it in `palsFlagged`. Use `gamedata.character()` (Pals *or* NPCs), never
`gamedata.pal()`, for anything out of that map. A first pass using `pal()` alone
reported 108 of 1,905 Pals as illegal on a completely clean world.

## The game's own files are readable, and they settle arguments

`refs/palworld/` is a dedicated server install. Two things it is good for:

- **`Pal-LinuxServer.pak` is unencrypted** (zero key GUID, `bEncryptedIndex=0`),
  v11, Oodle-compressed — and this project already ships an Oodle decompressor
  for saves. `scripts/palpak.py` lists all 158,444 entries without
  extracting anything.

  The main world is **World Partition**: 9,978 streaming cells named
  `MainGrid_L0_X<col>_Y<row>`. Those names *are* coordinates. **Cell size is
  25,600 world units** — at that value all 174 fast-travel points land on an
  occupied cell (12,800 gets 66, 51,200 gets 157). Connected components give one
  cluster per landmass, which is how the World Tree's extent was finally pinned
  down. A future update's new landmass will show up the same way.

- **`DefaultPalWorldSettings.ini`** is the authoritative 119-setting list. Check
  presets and highlight groups against it rather than against memory —
  `EggDefaultHatchingTime` sat in a highlight group matching nothing for months;
  the real key is `PalEggDefaultHatchingTime`.

**Never commit anything from `refs/palworld/`.** Besides the size, its
`PalWorldSettings.ini` holds live server passwords. `settings_ini.SECRET_KEYS`
masks those on read and in the audit log; `read_ini(reveal=True)` is for the
write path only.

## Lists write a different shape from scalars

`PassiveSkillList` and `EquipWaza` are ArrayProperties: the values live at
`node["value"]["values"]`, and `array_type` must survive untouched. A
`PassiveSkillList` rewritten as an EnumProperty still serialises and is silently
wrong — the same class of failure as the ByteProperty depth bug.

`charedit._write_list_property` handles them, and `_apply_pal_change` is the one
place that routes scalar vs list. Both the single and the batch writer go through
it, because a batch that forgot lists would skip every skill edit in it.

**`EquipWaza` values carry an `EPalWazaID::` prefix; the bundled `activeSkills`
table does not.** The API speaks bare ids everywhere — parser, editor, validation
— and the prefix is re-attached only on write. Bounds are measured: at most
**3** equipped moves (never more across 1,905 Pals), at most 4 passives.

**`MasteredWaza` is deliberately not editable.** It is absent on 1,563 of the
reference world's 1,905 Pals, and inventing an ArrayProperty means guessing its
`array_type`. Equipped moves are editable; the learned-move pool is not.

## Cloning creates records — everything else overwrites fields

`palclone.py` is separate for the same reason `saveimport` is separate from
`saveexport`: it is the only code here that *adds* to a save.

A Pal is **two records that must agree** — a `CharacterSaveParameterMap` entry
whose `SaveParameter.SlotId` names a container and index, and a
`CharacterContainerSaveData` slot whose `RawData.instance_id` names the Pal. Miss
either half and you get a ghost.

**There are no empty slots to fill.** Across the reference world's 23 character
containers there are 1,905 slot entries and 1,905 Pals. `SlotNum` is the
*capacity* (960 for a palbox); the array holds only occupied slots, so free space
is `SlotNum - len(slots)` and adding a Pal means **appending**.

New slots and characters are **deep-copied from existing ones**, never
constructed: the slot carries `CustomVersionData` and a `permission_tribe_id`
whose right values are whatever this save already uses. Verification counts
records rather than comparing values — both arrays must grow by exactly `count`,
every new id must resolve to its slot, and **no other container may change
length**.

Character-container slots only decode with the item custom-property set. Without
it `RawData` is an opaque 38-byte blob and `_new_slot` refuses rather than
hand-writing binary.

## The INI is not the source of truth on a containerised server

`thijsvanloef/palworld-server-docker` **regenerates PalWorldSettings.ini from
environment variables on every start.** A setting the dashboard writes survives
until the next restart and is then silently reverted — worse than a refusal,
because the operator watched it work.

**`jammsen/palworld-dedicated-server` does not, by default** — corrected 2026-07-30
by reading the image's own metadata rather than its docs. It ships
`SERVER_SETTINGS_MODE=manual`, and only `auto` regenerates the INI. This file
previously stated both images always rewrite it, which would have made the warning
wrong for a default jammsen deployment.

The variable names differ too: `REST_API_PORT` on thijsvanloef, `RESTAPI_PORT` on
jammsen. `settings_ini.ENV_MANAGED` names both spellings, so the warning points at
something the operator will actually find in their compose file.

This cannot be a *detection* — the dashboard container cannot read the game
container's environment — so it is worded as a conditional warning, not a fact about
the user's setup. `docs/COMPATIBILITY.md` has the full matrix and the one-line
`skopeo inspect` command that re-verifies it without pulling an image.

## Reading cooked UE5 packages: structure yes, properties no

`scripts/upackage.py` parses `.umap`/`.uasset` **headers**. It is not a general
asset reader and should not become one.

Palworld's packages are cooked with **unversioned properties** —
`FileVersionUE4` and `FileVersionUE5` are both 0, so property *names* are absent
from the stream and implied by a per-class schema we do not have. Decoding a
property list is off the table.

What is plainly serialised is the **name table and export map**, and that turns
out to be enough, because it provides *attribution*: for every object, its name,
its parent, and the exact byte range of its data in the `.uexp`. Scanning one
object's own bytes for a shape is a different proposition from scanning 740 KB
and guessing whose bytes you found.

Offsets are **measured, not looked up** — the export record layout varies by
engine version and there is no version number here to branch on. 96-byte stride;
name index at 16, SerialSize at 28, SerialOffset at 36. Three assertions guard
them: the name table must end exactly at `import_offset`, the first export must
start at `total_header_size`, and name indices must be in range. A game update
that changes the layout raises instead of returning plausible nonsense.

**`FPackageIndex` reads the opposite way to how it looks:** positive is an
**export** (`value - 1`), negative an import (`-value - 1`), 0 is null. Getting
it backwards produces no error — every parent lookup simply misses and the
package appears to have no hierarchy at all.

## Effigies: 396, and the GUID is the point

`backend/data/effigies.json.gz` holds all 396 with world positions **and the
instance GUID that saves key on**. Positions alone would only ever show every
effigy; the GUID is what lets the map show which ones a given player still needs,
because `RelicObtainForInstanceFlag` uses exactly these values.

The pairing: the relic actor export carries its GUID at **byte 252**; its
`DefaultSceneRoot` child carries the position. All of them live in one World
Partition cell (`MainGrid_L15_X0_Y0`, the always-loaded layer), and that cell
contains nothing but relics.

GUID byte order is **`u32le`** — four little-endian uint32s printed big-endian.

**Do not trust an actor count taken from the package name table.** That table
holds unique strings and many exports share one, so counting distinct
`_UAID_` names gave 149 when the real figure is 396. The export map is the
authority. The previously shipped figure of 313 came from a community tracker
and was never verified.

## Undiscovered content is the operator's call

`policy.DISCOVERY_LEVELS` — `everyone`, `detail` (the default: Trusted and
above), `nobody`. Whether a Player sees effigies they have not found is a taste
question about how the server is run, not a security one, so it is configurable
rather than hardcoded.

What is *not* negotiable is where the filtering happens: `/api/world/discoveries`
drops undiscovered entries **server-side**. A UI that received everything and
hid some of it would be handing out the answers in the network tab.

Accounts link to characters via `users.steam_uid`. An account without one has no
"own" progress — every location simply reads as undiscovered, which is not an
error.

## Per-player privacy applies to peers and below, never upward

`backend/privacy.py`. The whole rule is `hidden ⟺ viewer_rank <= hider_rank`,
and that single comparison is load-bearing:

- **A player can never hide from staff**, so moderation works without anyone
  maintaining an exemption list.
- Equal rank *is* concealed — peers are exactly who a privacy setting is for.

**The default is the most private mode**, not the least. Nobody should have to
discover a privacy setting exists before they stop being exposed. It costs
little because staff see everyone regardless.

Four modes, because bases belong to **guilds** rather than individuals and "hide
me" therefore has more than one honest meaning: `off`, `player`, `player_bases`
(solo guilds only), `guild` (the whole guild — the one mode with a social cost).

**Normalise uids on both sides.** `accounts` stores `steam_uid` dash-stripped and
lowercased; `Level.sav` stores dashed lowercase GUIDs. Comparing them raw matches
nothing and fails *silently* — privacy hides nobody while every setting still
reads as enabled. Use `privacy.normalise_uid`.

**You never hide from your own guild, and the absence of that rule broke a live
server.** `baseprivacy.py` had reasoned it out — "a guild always sees its own
bases; without that, a guild master hiding a base would hide it from themselves
and their guildmates, which reads as data loss rather than as a privacy setting"
— and `privacy.py` never got the same treatment. Combined with `DEFAULT_MODE`
being the *most* private option, two friends in one guild on default settings
could not see each other's base, each other's position, or each other at all.
Nothing errored; the map was simply empty, which is why it read as a broken
dashboard rather than as a setting.

A guild shares a palbox and shares bases. Concealing a shared asset from the
people who share it is not privacy, it is breakage. The exemption sits *above*
the mode check in `hidden_uids`, not per-mode, because it is true of every mode.
It fails towards **more** privacy: an unparsed world means no guild data, so the
exemption does not apply and the plain rank rule stands.

**Filter in two places.** Save-derived data goes through the backend endpoints;
**live positions come from the game's REST API through the Next.js proxy**. A
filter in only one leaves a hidden player gone from the map and still showing as
a live dot on the same screen.

Privacy governs map and roster visibility only. The audit log, account management
and save editing all work on real identities regardless.

## Caching is keyed on what changes, never on a timer

`backend/viewcache.py`. Two keys, because there are two reasons data changes:

- **`derived(key, build)`** — anything computed from `Level.sav`, keyed on
  `savecache.generation()`. That counter moves only when a parse completes, and
  **replacing the parse result is itself the invalidation**, so there is no
  `invalidate()` call next to a new write for anyone to forget.
- **`per_file(path, build)`** — anything computed from a file, keyed on its
  `(size, mtime)`. A player save rewritten by the game, by the editor, or by a
  backup restore invalidates itself. Same stamp `read_sav_bytes` uses for its
  torn-read guard: if it is stable enough to trust a read against, it is stable
  enough to key a cache on.

No TTLs. A time-based cache is wrong in both directions here — stale while the
world has already changed, and re-doing work while it hasn't.

**The on-disk parse cache carries a schema version, because it outlives the code
that wrote it — and discarding it is only HALF the fix.** `level_cache.json` survives an upgrade, and a newer dashboard
reading an older payload does not raise — it reads a field that is not there.
Renaming the per-base Pal count produced `undefined` in the API and a literal
**"NaN"** on the Bases tab of a server whose only mistake was upgrading without
re-parsing, with nothing anywhere saying why. `parse_worker.SCHEMA_VERSION` is
bumped whenever a field is added, removed or renamed, and `savecache` discards a
mismatched cache rather than adapting it.

**Shipping only the discard was worse than the bug it fixed.** `PARSE_AUTO` is
false by default — nothing re-parses on its own — so throwing the cache away left
a live server's entire dashboard empty: no Pals, no bases, no breeding, for every
role, with no error and no path back except a human happening to press Refresh.
A stale number is bad; an empty dashboard that never recovers is worse.

So the discard sets `_state["schemaStale"]`, `status()` reports it, the UI says
"Re-parsing after update" instead of the identical-looking "Save not parsed yet",
and `recover_stale_schema()` forces a parse from the lifespan hook **regardless of
`PARSE_AUTO`** — that setting means "do not parse speculatively", and rebuilding a
cache we just deleted is not speculative. It runs from the lifespan hook rather
than at import because `request_parse` reads the metrics table, which `db.init()`
has not created yet.

The general rule: **invalidation without a rebuild path is not invalidation, it is
deletion.** `test_savecache_schema.py` pins both halves together.

**What was actually slow, measured on the reference world:**

| Path | Before | After |
|---|---:|---:|
| `get_players()` (5 players) | 11,500 µs | 34 µs |
| `/api/pals` enrichment (1,905) | 12 ms | ~0 |
| `/api/mapobjects` naming (3,370) | 10 ms | ~0 |

`get_players()` was the one that mattered: four endpoints call it, the cost is
*per player*, and each player's `.sav` was being Oodle-decompressed and
GVAS-parsed from scratch every time. A 32-player server was paying ~73 ms of
identical parsing on every roster, progress and discovery request.

`savefiles._player_index` is part of the same fix. The old exact-match fast path
**could never fire on a case-sensitive filesystem** — `Level.sav` supplies a
lowercase dashed GUID and the file on disk is uppercase undashed — so every
lookup fell through to a full `os.listdir` plus a normalise-and-compare per
entry, once per player per request. It is now a normalised index keyed on the
directory's mtime, which changes exactly when a save is added or removed.
Sanitisation still runs on the raw uid *before* the index: the index is a lookup
table, not a permission check.

**Authorisation and privacy decisions are deliberately not cached.** Measured on
a 20-account database the entire per-request privacy filter costs **~60 µs** —
against ~12 ms to name a world's Pals. The failure mode of a stale entry is not a
slow page, it is a player who asked to be hidden still being shown to the peer
they hid from. 60 µs does not buy that risk.

**Cached values are returned by reference, not copied** — copying a 1.3 MB
payload per request gives back most of what the cache saves. Endpoints that
narrow a cached list (`?owner=`, `?category=`) filter into a new list and never
edit an element in place. Filtering happens *after* the cached build for the same
reason: keying on the query would mean the shared work is never shared.

## Commands go through the backend, because the proxy cannot audit

Phase 8's real finding: kick, ban, announce, save and shutdown were already
reachable through the Next.js game-REST proxy, gated on `server.control` — and
they left **no audit record at all**. The proxy has no `audit.record` call and
cannot sensibly have one, because SQLite is owned exclusively by the Python
process.

So `backend/gameapi.py` is the backend's own client for the game's REST API, and
`backend/moderate.py` issues commands through it and records them. The proxy
(`src/app/api/palworld/[...path]/route.ts`) now serves **reads only** and returns
405 for anything else, with a message naming the right route — a 404 would read
as "feature removed".

Do not add a POST path back to that proxy. If a new command is needed, it goes in
`moderate.py` or beside the other `/api/server/*` endpoints, with an
`audit.record` call.

**A failed command is audited too.** `moderate._run` writes the record on both
paths, because an attempt that did not land still says who tried — and auditing
only successes hides exactly the case being investigated.

**The target's display name is captured at the time of the action.** A uid is
unreadable and players rename themselves, so "who was `22b22b02`?" has no answer
later unless it was written down when it was still knowable. The name lookup is
best-effort: an offline server means an unnamed record, never a refused command.

**`gameapi` is not a replacement for `safety.py`.** That module keeps its own
probe, timeout and fail-closed logic because it answers "is it safe to write to a
save file" and must not depend on anything that could be made to say "stopped"
more easily.

## Two capabilities, because two kinds of trust

`server.control` was documented as "kick/ban/announce/restart", which bundled an
operations decision with a social one. It is now split:

- `server.control` — restart, stop, start, force-save, shutdown
- `players.moderate` — kick, ban, unban, announce

Moderator and above get both, so no existing account changed what it can do. The
point is that an operator can now withdraw one without the other.

## The ban list is not mirrored

`moderate.list_bans` reads the server's own `banlist.txt`. Do not copy it into
SQLite: a local copy drifts the moment someone edits the file by hand, and a ban
list that disagrees with the game's is worse than not having one.

**"Not found" and "empty" are different answers.** An empty array says nobody is
banned; a missing file says we do not know. The reader returns `found: false` with
a note so the UI cannot present the second as the first.

## Metrics: raw rows, and a gap is data

`backend/metrics.py`, stored in the `metrics` table. At the default 60s interval a
30-day window is ~43,000 rows — SQLite answers that instantly, and it is cheaper
than downsampled tables that can disagree with the raw ones. Bucketing happens at
query time in `series()`, so changing a chart needs no migration.

**A sample is written even when the game is unreachable**, with `reachable = 0`
and NULL game fields. Skipping it would let a chart interpolate a smooth line
straight through an outage. For the same reason `players` is never coerced to 0
when the server is down: "nobody was playing" and "we could not ask" must not
share a representation.

**`reachable` is averaged into a fraction, not a flag.** A bucket at 0.5 is an
intermittently crashing server — precisely what an operator is hunting — and a
boolean would round that away in one direction or the other.

`ts` is epoch seconds, unlike the ISO text the older tables use, because charts
bucket on it arithmetically.

Host CPU and memory come from cgroup v2 files where present, so under Docker they
describe **this container's** limits rather than the machine's. That is the useful
reading given `cpus: 1.0` in compose. Disk is the filesystem holding the save
directory — the one whose filling up stops the game saving.

**The first `cpu_percent()` call returns None.** A rate needs two samples; one
sample is not a slow rate, it is no rate.

## Parse throttling fails OPEN, unlike everything else here

`savecache.load_verdict`. Gameplay wins over dashboard responsiveness, so a parse
defers when the server is already struggling — but the direction of failure is the
opposite of `safety.py`'s, and deliberately so. Writing to a live save destroys a
world, hence fail closed. Refusing to parse forever because a signal is missing
merely breaks the dashboard, so no data, a stale sample, an unreachable server and
a missing table all read as "fine to parse". Only positive evidence defers.

**It gates the start of a parse and never interrupts one in flight.** A running
parse has already paid most of its cost and runs niced; killing it wastes that and
frees capacity only until the next request starts it from scratch.

**The check runs before any filesystem access**, including the `getsize`. A save
directory on a slow or unmounted volume makes even a stat cost real time, and the
cheapest response to a struggling server is to do nothing at all. A test pins the
ordering, because the first version had the comment and not the behaviour.

An explicit Refresh gets a lower floor (`PARSE_FORCE_MIN_SERVER_FPS`, 12 fps vs
20): the operator asked and is watching, so overriding them needs real trouble
rather than mere load.

## A world copy is the only save operation that never writes to the world

`soloexport.py` remaps one player's uid across a copy of the world — for carrying a
character from the dedicated server into co-op, or onto another server.

**It reads the live world and writes a new directory.** Every other writer here goes
through `backup.guarded_save_write` because it mutates in place; this one cannot
corrupt anything, so it deliberately does *not* require the server stopped. The
reference implementation (`PalWorldSaveTools/fix_host_save.py`) mutates in place.
Producing a copy is what an operator actually wants and costs nothing but disk.

**Match uids by value, never by key name.** PST rewrites four named keys. Counted
against the reference world that misses **1,836 references**:

| key | count | in PST's list |
|---|---:|---|
| `LastNickNameModifierPlayerUid` | 1,817 | no |
| `OwnerPlayerUId` | 1,740 | yes |
| `build_player_uid` | 973 | yes |
| `SkinAppliedCharacterId` | 12 | no |
| `LostPlayerUId` | 4 | no |
| `last_guild_name_modifier_player_uid` | 2 | no |
| `seller_player_uid` | 1 | no |

A key list is also a promise about a schema this project does not control. Matching
on value is exhaustive and cannot mistake one thing for another: a field holding a
player's uid *means* that player, and a Palworld player uid is a Steam ID32 followed
by zeros (`11a11a01-0000-...`), so it cannot collide with the full-entropy GUIDs used
for bases, guilds and character instances.

**A handled field must not be descended into.** A wrapped GVAS property is
`{'struct_type': 'Guid', 'value': UUID(...)}`, so a plain recursion matches it twice
— once at the outer key, once inside. Counting it twice inflates a number; *rewriting*
it twice is a correctness failure that appears only on a swap, where the mapping is
its own inverse and the second write undoes the first. On wrapped fields only, which
are the majority. Measured: 1,176 of 3,148 apparent matches were this double visit.

**palsav decodes GUIDs as its own `UUID` class, not `str`.** An `isinstance(v, str)`
test matches nothing — the first version of this module counted 6,455 uid fields and
rewrote zero of them. `_write_uid` also reconstructs the same type, because writing a
`str` where the encoder expects a `UUID` produces a tree that looks right and bytes
that are not.

**Rename and swap are different operations and the plan says which.** If the target
uid already has a character in the world, the two exchange identities; a rename
asserts that *zero* references to the old uid survive, which a swap cannot.

## Mod detection exists to qualify a report, not to manage mods

`mods.py` answers "is this server modded" so `palcheck` can say whether unrecognised
species have an innocent explanation. A Pal-adding mod puts species in the save that
no bundled table will ever contain.

**"Cannot see the game directory" must never render as "no mods installed."** The
normal deployment mounts only the save path, so not knowing is the *common* case, and
`explains_unknown_ids` returns false for it — an unexamined server must not become a
confident claim. UE4SS is reported separately because it loads Lua mods that leave no
pak at all, which is a real limit on what this can see.
