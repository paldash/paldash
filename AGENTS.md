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
- `docs/SAVE-FIELDS.md` — every field in a save, its occupancy, and which ones
  nothing here reads. Check it before concluding the save does not carry
  something; that conclusion has been wrong four times.
- `docs/GAMEDATA-SOURCES.md` — the same for the game's own files.
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

The naming reuses the name-table trick (`extract-effigies.py`,
`extract-world-objects.py`): properties are undecodable, name tables are not, so
intersecting a sheet's name table with the known species list says what it
references. **71 of 73 sheets resolve, and every
single one names a `BOSS_`-prefixed species** — which is the verification, not
the search key. The sheets were found by the `FBOSS` class-name convention, and
that they independently resolve to boss forms is what confirms the convention
means what it looks like. A sheet often names two species (`BOSS_QueenBee` +
`SoldierBee`); the prefixed one is the encounter.

`palspawner` excludes them by **negative lookahead**, not by `--targets` ordering
— first-match-wins would have made the split depend on a command line.

**Level IS available, and this paragraph used to say the opposite.** It claimed
level lived in the spawner's unversioned properties and instructed the reader not
to invent it. The instruction was right; the premise was wrong. Level was in
`DT_BossSpawnerLoactionData` the whole time, behind a `read_table` refusal that
was too broad — see the boss-spawner section below. `backend/data/boss_spawners.json.gz`
now carries **90 placed bosses, levels 11-79**, with positions verified against
the cell grid.

Note this is the *spawner* count, not the `palspawner` category described above:
these are two different extractions of overlapping things, and the 99 `FBOSS`
placements found by name-table intersection are not the same 90 rows. Do not
assume one supersedes the other without checking which species each covers.

### They do overlap, and the overlap is exact — but only on POSITION

The warning above says do not *assume*; it never said do not *check*, and for a
while the map's boss popup read **"Level is on the Field bosses layer"** — true,
useless, and wrong-feeling to anybody looking at a Pal whose level the game's own
map prints. `gamedata.boss_level_at` is the check.

Join the 99 placements to the 90 rows on species and keep the nearest:

| | |
|---|---:|
| placements whose species appears in the boss table | 93 of 99 |
| **matched at a distance of ~0.0** — the same actor read twice | **60** |
| matched within one cell (25,600 units) | 64 |
| **control: species labels shuffled, best of 200 trials** | **7** |

Sixty coincident points is not a threshold that was tuned until it looked good.
Two readers — one walking a world cell's actor bytes, one walking a DataTable's
`Vector` struct — landing on identical coordinates is the same class of evidence
as the 157/157 fast-travel fit, and the shuffled control is what makes it
evidence rather than density.

**Species alone is not enough, and the data itself says why.**
`BOSS_GrassGolem` is placed twice, at **level 55 and level 75** — which is also
why `remainsIsland_1_GrassGolem_FBOSS` appears twice in the table. A
species-keyed lookup hands one of those placements the other's level and looks
completely fine doing it. The join keys on position for that reason, and the
35 placements with no row standing beside them get **no level at all** rather
than borrowing their species' level from somewhere else on the map.

The threshold is `25600` — the cell size already pinned everywhere else here,
*not* fitted to this join's own gap. Fitting it would have been fitting the
method to the answer; as it happens the measured gap (17,628 then 30,722) sits
either side of it anyway.

### And 155 of the 396 effigies were labelled "Effigy"

Which is 39% of the layer wearing its own category name — the failure shape this
file records for the empty work-suitability panel and the empty base list: it
reads as a lookup that failed, not as the answer.

The two unsuffixed classes (`BP_LevelObject_Relic`, `BP_RelicObject`) are the
plain relic, and the catalogue has always named it: `Relic` -> **"Lifmunk
Effigy"**. Resolved through `item_name` rather than hardcoded, so it degrades to
"Effigy" if the bundle is missing instead of to a stale English string.

**The suffix rule is now verified rather than trusted.** Turning
`…_IceCrocodile` into "Munchill Effigy" is a two-hop derivation off a class name,
exactly the sort that reads right and is wrong — so `test_effigy_names.py`
asserts all ten placed classes land on a name the item table actually ships
(`Relic`, `Relic_01`..`Relic_12`). **10 of 10.** Three catalogue entries —
Lunaris, Relaxaurus, Mimog — have no placed class at all, which is the world
not referencing them rather than a gap in the rule.

## Spawn habitats come from the spawner DataTables — the name-table trick is retired

**This section used to be titled "Spawn habitats come from name tables, not from
properties", and it described a workaround as if it were the only way.** It was
the only way *out of the client pak*. `scripts/extract-pal-habitats.py` inferred
a spawner's roster by intersecting its package name table with the known species
list, because a sheet's properties are cooked with unversioned names — 348
species, and explicitly only the claim "this blueprint *references* this
species", never "spawns here at this rate". That script is deleted; the section
is kept because the trick is still right elsewhere (`extract-effigies.py`,
`extract-world-objects.py`) and because the correction is the lesson.

`scripts/build-habitats.py` reads the server pak's own tables, which say all of
it outright:

    DT_PalSpawnerPlacement  8,253   {spawnerName, x, y, radius, type}
        |  spawnerName
        v
    DT_PalWildSpawner         420   [{weight, onlyTime, onlyWeather,
                                      entries: [{speciesId, levelMin, levelMax,
                                                 countMin, countMax}]}]

**478 species, every one with a level range**, which the workaround could not
produce at any effort. Melpaca reads "56 cells, levels 5-17, 1-2 at a time".

**The coverage check is the part worth copying, and it nearly failed.** The new
source is missing 32 forms the old one had, because `DT_PalWildSpawner` contains
**zero `PREDATOR_` entries** — predators are placed by a different mechanism, so
a name-table scan saw them and the real tables do not. A raw species count
(478 > 348) waves that straight through and is the wrong criterion: what matters
is whether anything a player can *look up* got worse. 30 of the 32 have their
base species covered — `PREDATOR_Gorilla`'s habitat is `Gorilla`'s — and the
other 2 are `_Quest` variants with no world habitat under either source.
`coverage_check` encodes that and **refuses the build** if a species ever loses
its habitat with no base form covering it.

**`weight` is relative within one spawner group only.** Two groups' weights are
not comparable and nothing says how often a spawner fires, so it is honest as
relative frequency and dishonest as a spawn rate. `weightIsWithinGroup` travels
in the bundle rather than only in a docstring.

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

## A MapProperty is not opaque, and it was hiding two answers

`scripts/uassettable.py`. The reader's `_tag()` had always pulled a map's **key
and value type names** out of the tag; `_value()` then returned
`<MapProperty NNNB>` and moved on. That one gap stood in front of the
work-suitability curves above and of the breeding item effects below.

The body is `int32 NumKeysToRemove` (0 on everything here), `int32 NumEntries`,
then each key and value written **raw** — no tags, because the tag already named
both types.

**A map element needs its own reader, and the reason is the interesting part.**
`_value` can skip a type it does not understand by snapping to `start + size`,
because a tagged property carries its own length. **A map element carries
nothing.** There is no size to snap past, so a decoder that met an unfamiliar
type could only stop — returning a dict that looks complete and is short. So
`_map_half` **raises** on an unhandled type and the caller labels the whole
property opaque. Partial is never returned.

The acceptance criterion is the one this module already uses everywhere: the
entry walk must consume **exactly** the tag's declared size, or it is a refusal.
That plus the enclosing walk still ending at 41,416 of 41,420 bytes is what says
a 1,361-byte map in the middle of a 41 KB property list decoded correctly —
a wrong one does not leave the remainder parsing.

### And the cakes are a table, not a rumour

`DA_BreedingItemEffectData` is a **DataAsset**, not a DataTable, and its tagged
walk terminates at 1,053 of 1,057 bytes — the same four-byte tail
`BP_PalGameSetting` leaves. Its `ItemEffectMap` decodes to four entries:

| Cake | Effect |
|---|---|
| `Cake02` | `TalentBonusMin 1`, `TalentBonusMax 5` — the child's IVs get +1 to +5 |
| `Cake03` | `BreedCount 2` — two eggs from one breeding |
| `Cake04` | the IV bonus **and** `MutationRateBonusPercent 2.0` |
| `Cake05` | `bInheritAllActiveSkills true`, `PassiveInheritCountOverride 4` |

**This narrows a refusal recorded under the breeding section**, which says no
file states what produces a mutated egg or at what rate, checked across all 471
server-pak DataTables. That sweep was of *DataTables* and this is a DataAsset —
the same shape as the `WorkSuitabilityMaxRank` correction, where a DataTable
sweep was mistaken for a search of the game. `Cake04` — Extravagant Vegetable
Cake, whose own description already tied it to the Breeding Farm in Pocketpair's
words — carries a **2.0** mutation-rate bonus.

**What is still not stated is the BASE rate.** `MutationRateBonusPercent` is a
bonus, and no file found so far gives the number it is added to, nor what a
mutated egg hatches. So the quote-don't-mechanise rule in `basesupply.py` stands
for everything except this one figure, which is the game's own.

Inheritance itself is **random with a distribution the game ships**, in
`BP_PalGameSetting`:

    Combi_PassiveInheritNum    [4, 3, 2, 1]   -> 40% / 30% / 20% / 10%
    Combi_PassiveRandomAddNum  [4, 3, 2, 1]   -> how many NEW passives roll on
    Combi_TalentInheritNum     [3, 2, 1]      -> 50% / 33% / 17%

These are relative weights, so the percentages are normalised — and **what index
0 counts is an inference from the field name**, not something the file states.
Report them as weights, or as probabilities with that caveat attached; do not
present "40% chance of inheriting one passive" as the game's own claim until
somebody has bred enough eggs to say which end the array starts at.

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
| Pal storage | 2 | 5 | `ModuleMap["…::CharacterContainer"]` |

Eleven bases, eleven worker containers, and every one of them lands somewhere
that is neither 960 (a palbox) nor 5 (a party). **165 of 1,905 Pals are deployed
at a base**; the rest are in palboxes, which is a guild-level thing.

**The specific figures 20/16/13/8 are this world's, not a rule** — the live world
has capacity-25 containers and an earlier snapshot has 10s and 14s. Only the
*neither-960-nor-5* property generalises, and that is what the code checks;
`scripts/verify-figures.py` confirmed the join itself on 44 of 44 bases across
four worlds.

So `palCount` (this base) and `guildPalCount` (this base's guild, repeated on
each of its bases) are both present and named for what they are. Never sum the
second.

### Three caps that look like game rules and are settings

`BaseCampWorkerMaxNum`, `BaseCampMaxNumInGuild` and **`GuildPlayerMaxNum`** are
all INI keys, so the operator's file is the only authority and no bundled table
bounds any of them. The third is the easiest to get wrong: every one of the
game's own difficulty presets ships `GuildPlayerMaxNum = 20`, which makes 20 look
like a rule of the game rather than a default somebody can change.

`gamedata.server_limit()` reads them and returns **`None` when the INI cannot be
read — which is the common deployment**, since most mount only the save path.
None means "not known", never "unlimited" and never "use the game's value": show
no denominator rather than a wrong one.

## The fourth kind of place a Pal can be, and it was called "orphaned"

The row above used to read **Orphaned · 2 · none**, and this file said those two
containers were why Pal `location` has an `other` value. They were never orphans.
Nothing had looked at the module map for them.

`parser.extract_pal_storage` reads the join the game already provides:

    MapObjectSaveData[]
      -> ConcreteModel.ModuleMap["…::CharacterContainer"].RawData.target_container_id
      -> CharacterContainerSaveData[].key.ID

**No byte offsets.** `extract_base_workers` reads `WorkerDirector` at a measured
offset because that blob is opaque; this module's `RawData` decodes to a *named*
`target_container_id`. Where the game gives a name, take the name. The
verification obligation is the same either way: an id that does not resolve in
`CharacterContainerSaveData` is dropped, so a layout change yields nothing rather
than a Pal confidently placed in the wrong guild's store.

Both reference-world hits are `PalBooth` — "Flea Market (Pals)" — and both
attribute to a real base. With them classified, **every character container on
the reference world now belongs to something**, which is the check that the
module type means what it looks like.

**The case that matters most cannot be exercised here, and that is worth saying
rather than hiding.** The world has one `DimensionPalStorage` and three
`GlobalPalStorage` objects, all with an *empty* `ModuleMap`: nobody stored a Pal
in them, so the game has not created their containers yet. A world where someone
has resolves automatically, because the join keys on the **module type** rather
than on a list of structure names — a Pal-holding structure this code has never
heard of still classifies. `other` survives as a real state for a container
nothing references any more; it is now genuinely rare rather than the default
for anything unrecognised.

### An ownerless Pal belongs to the guild, not to nobody

The classification was only half the bug, and the other half was worse because it
was arithmetic. **159 of the reference world's 1,905 Pals carry no
`OwnerPlayerUId` at all** — base workers, and anything in a shared store. Every
Pal query filtered on that field alone, so those Pals were missing from their
owner's My Pals list *and* from the breeding planner, which then insisted a
player did not have species standing in their own base.

They are not unowned. They belong to the **guild**: any member can walk up and
take one out, and a Pal in a base is exactly as breedable as one in a palbox. So
`main._scope_pals` is "your own Pals, plus the ownerless Pals of every guild you
are in", and guild membership is read off the Pals themselves rather than looked
up, which keeps it right for someone in more than one.

A Pal carrying a *different* player's uid is never included, whatever guild it is
in. A shared palbox is not a shared Pal.

**`00000000-…` is a real value, not a sentinel this code invented** — it is what
the parser writes when the field is absent, so "unowned" has to test for it as
well as for the empty string. Treating it as a uid would file every base worker
on the server under one imaginary player.

**There were two copies of this filter and they had already drifted twice.**
`/api/pals` scoped the enriched list inline while the breeding routes called the
shared helper; the comment beside the inline one already recorded it falling
behind on uid normalisation once. `_scope_pals` now takes the list rather than
fetching it, so both callers pass their own copy to one rule. `shared` travels in
the breeding scope payload for the same reason scope itself does: the total
legitimately exceeds the palbox, and an unexplained larger number reads as a
miscount rather than as a fuller answer.

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

### The Guild Chest is not one of them, and that is why it looked empty

**`GuildChest` hangs no `ItemContainer` module off its placed object.** All eight
on the reference world carry `GuildSecurity` and nothing else, so the join above
finds them standing in a base and holding nothing — which reads as a chest nobody
has filled rather than as a chest this code cannot see into.

Its contents are one level up, in `GuildExtraSaveDataMap`, because the chest
belongs to the **guild** rather than to the base it stands in:

```
GuildExtraSaveDataMap[].GuildItemStorage.RawData   # opaque, 20 bytes
  -> container GUID at offset 0
  -> ItemContainerSaveData[].key.ID
```

`parser.extract_guild_storage` reads it. Same measured-offset discipline as
`extract_base_workers`, and the same obligation: the decoded id **must resolve to
a real `ItemContainerSaveData` entry** or it is dropped, so a layout change yields
nothing rather than a confident wrong answer about what a guild is holding.
Measured: **5 of 5** guilds resolve, to 54-slot containers.

**Eight placed chests, five guilds, five containers — the count difference is the
point.** Two chests in one guild are two doors into one box. So "stock each
base's guild chest" is not a thing that can be done, and folding the chest into a
per-base figure would report the same items once per base. It travels at guild
level for the same reason `guildPalCount` does.

### And `breeding` was pointing at the Ranch

`_POI_CATEGORIES` matched `MonsterFarm` for its `breeding` category. `MonsterFarm`
is the **Ranch**; the Breeding Farm is `BreedFarm`, which matched nothing at all —
so all five on the reference world were dropped by `_categorise` and never reached
the map. One category named for a structure it did not match, while the structure
it was named for was invisible.

`structure_name` had the answer the whole time (`MonsterFarm` → "Ranch",
`BreedFarm` → "Breeding Farm"). **A category whose name disagrees with what the
game calls the thing is worth checking rather than trusting** — the same shape as
the tower-boss and `FBOSS` count checks. They are now `breeding` and `ranch`, two
layers.

Note the Breeding Farm's contents were never lost: `extract_container_ownership`
does not filter on category, so its `ItemContainer` was always in per-base
storage. Only the map layer was affected.

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

## The sixteen duplicate durability records were an artifact of one file

**This section used to be titled "A durability record is not one record — it is
sixteen", and it was wrong about the game.** It is worth reading as a warning
about reference data before it is read as a fact about saves.

`backend/dynamicitem.py`. Equipment carries a per-instance record in
`DynamicItemSaveData`, and the container slot points at it by
`dynamic_id.local_id_in_created_world`. Durability lives there, not in the slot.

`refworld` shows **32,446 records against 2,052 distinct ids** — 2,022 ids
appearing exactly 16 times, twelve 6 times, one 5, seventeen once, every copy
byte-identical. That was measured correctly and reasoned about at length: whether
they were orphans (they are not — 1,487 of the sixteen-copy ids are referenced by
live container slots), and what a create path could safely append when the count
is 1, 5, 6 or 16 with no pattern. `can_create()` refused on exactly that basis.

**The count is 1. Always. Measured on the same world's own history**
(2026-08-04):

| Snapshot | records | ids | copies per id |
|---|---:|---:|---|
| server backup 2026.07.22 | 571 | 571 | `{1: 571}` |
| server backup 2026.07.27 | 1,675 | 1,675 | `{1: 1675}` |
| server backup 2026.07.28 ×5 | 2,001–2,051 | same | `{1: n}` |
| the live world | 324 | 324 | `{1: 324}` |
| **`refworld`** | **32,446** | **2,052** | **`{1: 17, 5: 1, 6: 12, 16: 2022}`** |

Nine snapshots in the server's own lineage, one per id, every time. And
`refworld` is **the same world** — identical guild ids (`bfac34db`, `49923822`,
…) — sitting at the same moment as a server backup that has 2,051 ids to its
2,052. So `refworld` is a *processed* copy, and whatever processed it multiplied
the records. The game did not.

**Creation was directly observed, 2,262 times.** Diffing snapshots a week apart
adds 2,017 new ids and the current world another 245; every one arrives as
**exactly one record** — eggs, armour and weapons alike. That is not an inference
from a static count, it is the game creating items and being watched do it.

So a create path is a deep copy of a same-type record plus **one** append.
`scripts/diff-dynamic-items.py` is the tool; it takes a `Level.sav`, a world
directory or a backup `.tar.gz`, and the last of those is why this got settled
without anyone stopping a server: the answer was already sitting in the rotating
backups.

`apply_durability` still writes **every** copy an id resolves to and still
refuses when the count changes between plan and apply. That is now belt and
braces rather than the core defence, and it costs nothing: on a real save the
loop runs once. The first version keyed one id to one record and its own smoke
test caught the consequence in a minute — the plan read one copy, the apply
mutated a different one, and the value appeared not to change. Keep it.

**The transferable lesson is about `refworld`, not about durability.** A single
reference file was treated as ground truth about the format for months, and one
of its properties was an artifact of its own provenance.

`scripts/verify-figures.py` is the response: it re-derives the figures below
across any number of worlds and marks the rows where they disagree. Run it
against `refworld`, a couple of `refs/palworld/.../backup/world/` snapshots in
chronological order, and the live world. The **shape** of a disagreement is what
identifies it — an artifact is a step change at one file with the rest agreeing;
drift is a monotonic trend across time-ordered snapshots.

### What that check confirmed, and what it corrected (2026-08-04)

Four worlds: `refworld`, two server snapshots a week apart, and the live world.

**Confirmed, and now resting on more than one file:**

| Figure | Result |
|---|---|
| `WorkerDirector` blob = 118 bytes, container id at offset 98 | **44 of 44 bases** resolve across all four worlds, including a 16-base one |
| Character slot entries == characters (no empty slots to fill) | true on all four — `palclone` appends, correctly |
| Every item slot carries `slot_index` | 0 missing of 18,728 / 6,065 / 18,490 / 22,215 |
| Slot references resolving to no durability record | **0** on all four |
| One durability record per id | every world except `refworld` |

**Corrected: the 20/16/13/8 worker-container capacities are `refworld`'s, not a
rule.** The live world's character containers include capacity **25**, and the
07-22 snapshot has 10s and 14s. The table further down is a description of one
world at one moment. What generalises is that worker containers are *neither* 960
(palbox) *nor* 5 (party), which is the check `extract_base_workers` actually
performs.

**And `extract_pal_storage` classified a structure it was never told about.** It
keys on the `CharacterContainer` module type rather than a list of names, and on
the live world that picked up two `DismantlingConveyor` ("Pal Disassembly
Conveyor") alongside four `PalBooth` — a kind absent from `refworld` entirely.
That is the design working rather than a lucky guess.

**One trap the script itself fell into, worth more than the figures.** Its first
version used the full `PALWORLD_CUSTOM_PROPERTIES` everywhere and reported
**0 of 11** worker containers on `refworld`, where 11 of 11 is correct. The full
set *decodes* `WorkerDirector.RawData` into a struct instead of leaving it opaque
bytes, so the offset read finds nothing. Nothing was broken — the module logged
its warning and returned nothing, exactly as designed — but a verification script
using the wrong reader **manufactures a regression**, which is worse than having
no script. `load()` now parses each world twice and says why.

`describe()` attaches the item's **factory-fresh durability** from the bundled
data (669 of the 948 items with a dynamic record have one; the rest are
accessories, which do not wear out). The record itself holds only the current
value, so without it the editor asked for a bare number with nothing to measure
against — 1,045 is nearly new or nearly broken depending on an item the operator
was expected to already know.

**Creation lives in `itemclone.py`**, the third module that writes to a save and
the only one that adds a `DynamicItemSaveData` record — separate from
`dynamicitem` for the reason `palclone` is separate from `charedit`. Records are
deep-copied from an existing one of the same type, never constructed:
`CustomVersionData` has three distinct values on one world, and
`leading_bytes`/`trailing_bytes`/`unknown_bytes` are opaque.

**A new item is two things that must agree** — the record, and the container
slot's `dynamic_id.local_id_in_created_world` pointing at it. The verification
after re-reading from disk is the strict one: the array grew by exactly one, the
new id resolves to exactly one record, the slot points at it, and **no other
container changed length**.

Three things that bit while building it:

- **`palsav.archive.UUID(...)` takes raw swizzled bytes, not a string.** Parsing
  a dashed GUID is the separate `from_str`. Passing the string to the constructor
  stores it verbatim and fails much later inside the encoder as *"a bytes-like
  object is required, not 'str'"*. Same family as the `soloexport._write_uid`
  note, one level deeper.
- **`slot_index` 0 is falsy**, so `raw.get("slot_index", -1) or -1` reads the
  first slot of every container as -1. That made "is slot 0 free?" answer yes on
  a full slot, and would have appended a second entry for index 0. All 18,728
  slots on the reference world carry the field, so the default is a guard rather
  than a path; `itemclone._slot_index` is the one correct reader.
- **The catalogue and the save disagree about eggs, and both are right.**
  `gamedata` gives `dynamic.type == "unknown"` — exactly the 56 `PalEgg_*` items,
  the same property `saveedit`'s category sort keys on — while the record itself
  says `type: "egg"`. `_CATALOGUE_TO_RECORD` translates; neither side is "fixed".

**"AN EGG NEEDS A TEMPLATE OF THE SAME ITEM" WAS WRONG, AND IT FAILED IN BOTH
DIRECTIONS.** This paragraph used to say so, reasoning that `character_id`
decides what hatches and the catalogue does not know it — so cloning a
`PalEgg_Dark_01` record for a `PalEgg_Fire_01` request yields a fire egg that
hatches a dark Pal, unnoticed until it hatches. Retracted 2026-08-06.

The premise is exactly right. The conclusion was backwards, because **one egg
item hatches many species**: `PalEgg_Dark_03` covers 18 on one world, 41 items
over 253 distinct (item, species) pairs. So a same-item template handed back
whichever of the eighteen that record happened to hold. **The rule refused the
case it could get exactly right and permitted the case it got by luck.**

And a template was never carrying anything an egg needs. Measured across three
worlds — refworld (30,866 eggs), the live world (180), a 07-22 backup (531) — an
egg record is six fields with nothing opaque in it:

    type            "egg"
    id              { static_id, local_id_in_created_world, created_world_id }
    character_id    what hatches
    object          usually empty; a whole embedded Pal when not
    leading_bytes   ONE distinct value on all three: 4 zero bytes
    trailing_bytes  ONE distinct value on all three: 28 zero bytes

No `CustomVersionData`, no `unknown_bytes`. **The deep-copy rule is measured on
weapons and armour**, where those fields are real and do vary, and it was carried
across to eggs without being checked against one. That is the same shape as the
`IgnoreCombi` and element-variant retractions: a constraint derived for one case,
applied to a neighbouring case that does not share its premise.

`character_id` and `id.static_id` are now **written**, not inherited, and the
post-write verification re-reads the record from disk and refuses if the species
that came back is not the one asked for — the direct check the old rule was a
proxy for. `hatches` is the API parameter; omitting it keeps the old inheriting
behaviour and sets `hatchesFromTemplate`, so an arbitrary species can never read
as a decided one.

Equipment is unchanged: for a weapon every meaningful field is overwritten
(durability, bullets, and passives are cleared so a new item does not inherit the
copied one's), so any template of the type supplies shape.

**The `object` half of the old rule stands.** An egg with a non-empty `object` is
never a template: 172 of 180 are empty, and the 8 that are not embed a whole Pal,
so copying one would duplicate a character wholesale. A world with no
empty-object egg at all is still a refusal.

Audited as `audit.ITEM_CREATE`, not `save.edit` — "who spawned what" is the first
question after a complaint about an unfair advantage, and it should be one filter
rather than a scan.

**Three shapes, and an egg is not equipment.** `armor` is
`type/id/durability/leading/trailing`; `weapon` adds `remaining_bullets`,
`passive_skill_list` and `unknown_bytes` (813 of 814 have the last one, one does
not); `egg` has no durability at all and instead embeds a **whole Pal** under
`object`. So "egg editing" is character editing, which is `palclone`'s problem.
`describe()` never returns that embedded record.

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

**AND THE HAND SWEEP MISSED SIX, WHICH IS WHY IT IS A TEST NOW.**
`backend/tests/test_route_gates.py` enumerates the live FastAPI app and asserts
both gates on every route, so one added tomorrow is covered without anyone
remembering. On its first run it found `/api/bases`, `/api/guilds`,
`/api/players`, `/api/mapobjects`, `/api/bases/storage` and
`/api/bases/{id}/storage` with no `authz.require` — all six resolve an identity
with `_viewer()`, which returns `"guest"` rather than refusing, so they were
open-then-filtered rather than gated.

Writing that test found three bugs **in the test** before it found any in the
code, and each would have been worse than no test: a regex that stopped at the
first escaped slash parsed 20 of 107 patterns and "found" ninety unreachable
routes; a scan for `authz.require` in the endpoint body alone missed every route
that delegates to a helper like `_moderator`, reporting all of moderation as
ungated; and a single `sample_id` probe failed the patterns whose character class
excludes underscores. **A false alarm on a security test is worse than no test,
because the next real one gets waved through.**

`authz.current_user` and `_viewer` are deliberately not counted as gates. They
resolve an identity and return `None`/`"guest"`, which filters but does not
refuse — that is the distinction the whole test rests on.

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

## The pairing rule is derived, and a variant is never a FALLBACK outcome

`scripts/verify-breeding.py`. `backend/breeding.py` ships a precomputed 46,655
pair table from the MIT-licensed tylercamp/palcalc. The game's own rule is now
re-derived from the server pak and agrees with it on **96.92%**:

1. `DT_PalCombiUnique` wins outright — 256 pairs keyed on *tribe*.
2. Otherwise `target = floor((rankA + rankB + 1) / 2)` over `CombiRank`, and the
   child is the eligible species whose rank is nearest.
3. Ties break on **`CombiDuplicatePriority`, highest first** — a column beside
   `CombiRank` named for that job. Ties are the *common* case: 181 species over
   ~130 distinct ranks.

**THE CHILD POOL IS THE WHOLE DIFFICULTY, AND IT SAT AT 67% FOR A DAY BECAUSE OF
IT.** Four tie-breaks were tried and abandoned with a note not to search that
space further. The note was right and the diagnosis was wrong — the tie-break
was never the problem. Every pool criterion is a column the game ships:

    IgnoreCombi == False        breeds at all (226 of 753 say no)
    ZukanIndex > 0              Paldeck-listed; -2 gym, -1 unreleased
    ZukanIndexSuffix != "B"     not an element variant  <- the one that mattered
    OverrideNameTextID == None  not an alias of another entry

### "AN ELEMENT VARIANT IS NOT A BREEDING OUTCOME" WAS WRONG

**This section was titled that, said it in capitals, and the game's own ground
truth contradicts it outright.** Corrected the same day, 2026-08-05.
`DT_PalCombiUnique` names an element variant as the child in **159 of its 256
tribe pairs — 81 distinct variants** — and 86 variant species appear in it as
*parents*. Mossanda Lux is Mossanda x Grizzbolt and always was. What the filter
actually encodes is narrower, and better in both directions:

> The **rank fallback** never produces a variant. A variant comes only from a
> pairing the game names outright.

Better because it is read off ground truth rather than inferred from an item
description, and because it lets the dashboard tell a player *which* pairing
rather than only that a generic rule will not get there. Excluding variants from
the fallback is still worth **70.66% -> 96.92%** and is not in doubt.

**How the wrong version survived a run that looked clean, which is the part to
learn from.** The comparison *skipped every pair whose palcalc child was a
variant* — 1,459 of them, reported in the output as "species with no CombiRank",
which only 303 of them were. That skip discarded 1,300 disagreements and 159
agreements, and **99.72% was what was left of 96.92%**. Excluding a case from
the measurement because the rule excludes it from the answer is circular. A
skip whose printed label does not match its condition is how it stays invisible.

The marker itself is unchanged and still the game's own: `ZukanIndexSuffix ==
"B"`, exactly 90 of 753 forms — the `B` a player already reads on Paldeck entry
#98B. A hand-written `_(Ice|Fire|Water|…)` regex finds 80 and **misses `_Gold`**.

`OverrideNameTextID` is worth its own line. `Quest_Farmer03_SheepBall` is
byte-identical to `SheepBall` on every breeding column — same rank, same
priority, same zukan, same tribe — and differs *only* in borrowing its name. It
was stealing SheepBall's results 30 times.

**The check is membership, not size.** This script already carries a retraction
for claiming "the species set agrees — 299" from a count, so the assertion is
that the derived pool is a strict **subset** of palcalc's list: 181 against 305,
zero strays. And the earlier finding that "palcalc's own pal list as the pool
still gives 64.6%, so the pool is not the variable" was itself the trap —
palcalc's *list* is its parent set and legitimately holds variants and quest
forms, while its *child* set is narrower. Substituting one for the other tested
the wrong thing while looking like it had ruled the pool out.

### 1,427 pairs remain open, with exactly two causes and no scatter

**1,300 are three species: the three variants the game names no pairing for.**
Of the 90 variants, 87 are accounted for — 81 named as a unique-combo child, 6
quest/tower/oilrig/unreleased forms carrying `IgnoreCombi = True`. These three
are Paldeck-listed, `IgnoreCombi = False`, and appear in no unique combo at all:

| Species | Paldeck | Disagreeing pairs |
|---|---:|---:|
| `Kelpie_Fire` — Kelpsea Ignis | 43 | 207 |
| `MushroomDragon_Dark` — Shroomer Noct | 118 | 505 |
| `Yeti_Grass` — Wumpo Botan | 134 | 588 |

palcalc puts exactly those three, and no other variant, into its rank pool —
which is a deliberate choice by somebody rather than an accident, and is why its
child set is 81 + 3 = 84. **No column in `DT_PalMonsterParameter` separates them
from the other 76 breedable variants**; checked column by column, there is none.

**126 are `WhiteDeer`** (Cryolinx, rank 570), which this rule offers for any
target in 565-575 while palcalc offers it for 2 pairs in the whole table.
Nothing distinguishes it either.

palcalc is **not** ground truth — `DT_PalCombiUnique` is, and both pass it 253 of
253 — so with no game column separating the answers, inventing a filter that
happens to exclude one species would be fitting the method to the answer. Both
need somebody to breed one and look at the egg.

`breeding.py` still ships palcalc's table. Nothing is replaced on the strength
of this; the diff is the deliverable.

### And the planner now says which kind of "no" it is

`breeding.obtainability` / `/api/breeding/limits`. Four answers, each read off a
column rather than a hand-written list — `standard`, `named_pairing` (81),
`unverified` (3), `never` (24). The `never` list comes out as exactly the
legendaries and tower bosses a player catches, which is the check that
`IgnoreCombi` means what it looks like.

It exists because **"not reachable within 4 breeding steps from your current
Pals" is a true statement about Frostallion that will stay true however many Pals
you catch**, and on its own it reads as the planner giving up. Same shape as the
Paldeck's empty work-suitability panel.

**A NAMED PAIRING BEATS `IgnoreCombi`, AND CHECKING THE FLAG FIRST GOT THE FOUR
PALS A PLAYER MOST WANTS WRONG.** This is the variant retraction above happening
a second time, one level down, on the same day — so the rule generalises and is
worth stating as one:

> `IgnoreCombi` and `ZukanIndexSuffix == "B"` both constrain **the rank
> fallback**. Neither says anything about a pairing the game names outright, and
> `DT_PalCombiUnique` is consulted first — in `predict`, and now here.

Four species carry `IgnoreCombi` *and* are named in `DT_PalCombiUnique`, and
filing them under "cannot be bred" was a claim about pairings players use daily:

| Pal | The game's own pairing |
|---|---|
| Lyleen Noct | Lyleen + Menasting |
| Faleris Aqua | Faleris + Jormuntide |
| Bellanoir | Bellanoir + Bellanoir Libero |
| Frostallion Noct | Frostallion + Helzephyr |

**And a self-pairing is not a named pairing.** 26 of the 28 `IgnoreCombi`
Paldeck entries breed true — Frostallion + Frostallion yields Frostallion —
which is worth telling somebody who owns one and useless to somebody who does
not. So it never promotes a species out of `never` and travels as `breedsTrue`.

**`IgnoreCombi` does NOT mean "cannot be a parent", and the first note said it
did.** Measured: all 28 are productive parents of **70-100 distinct species
each**, and `IceHorse + IceNarwhal` is Frostallion parenting a Blazamut Ryu. The
flag rules out being *produced*, nothing else. The group is labelled "no pairing
produces these" rather than "cannot be bred" for exactly that reason.

Two further traps, both the read-the-wrong-row family:

- **A row is a PALDECK ENTRY, not a species.** `GrassPanda_Electric_Tower` is
  the tower-boss form of Mossanda Lux — same Paldeck number, same suffix, same
  display name, `IgnoreCombi` true because *that form* is not a breeding
  outcome. Ungrouped, the answer was "Mossanda Lux cannot be bred" about a Pal
  that plainly can. Nine of the eleven Paldeck collisions are this shape
  (`_Oilrig` and `_Tower` forms). Group on `(zukanIndex, zukanSuffix)` and keep
  the **most permissive** answer.
- **`pal_exact` is for stats, not eligibility.** The game sets
  `ZukanIndexSuffix` on the base row only and gives encounter forms
  `zukanIndex = -1`, so `BOSS_GrassPanda_Electric` carries no suffix at all and
  an exact lookup called an alpha Mossanda Lux ordinary. `pal_exact` exists
  because an alpha's *stats* differ; its breeding eligibility does not.

**Reading `DT_PalCombiUnique` directly holds something palcalc's table cannot.**
Katress x Wixen is the game's only gender-dependent pairing and the game states
it as two rows — Wixen Noct from Katress(m) + Wixen(f), Katress Ignis from
Katress(f) + Wixen(m). A flat `pair -> child` table holds one of those. Both now
reach the UI with the genders attached.

A breeds-true self-pairing (`Fuack Ignis + Fuack Ignis`) is labelled **and
sorted last**. The game's own table puts it first for some species and second for
others, so "how do I get a Fuack Ignis" was answered with "breed two Fuack Ignis"
on three of the first four rows.

**The mutated-egg material is quoted, never turned into a mechanic** —
`basesupply.py`'s rule, pinned by a test that rejects any note containing "chance
of", "guaranteed" and friends. Two verbatim strings travel: the egg item's own
description, and `Cake04` (Extravagant Vegetable Cake) — *"Place it in the chest
at a Breeding Farm… Mutations are more likely to occur"* — which is the game
tying mutation to the Breeding Farm in Pocketpair's own words. **No file says
what produces a mutated egg, at what rate, or what it hatches**; checked across
all 471 server-pak DataTables, where `PalEgg_MutationPal` appears only as an
icon, a model, a pickup blueprint and a particle effect. So the payload states
that absence out loud rather than leaving two suggestive quotes to imply a
method.

Also in `BP_PalGameSetting` and unused: **`Combi_BossPalRate = 0.05`** (a bred
Pal is an alpha 5% of the time), `Combi_PassiveInheritNum` and
`Combi_TalentInheritNum`, which give the real inheritance counts.

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

**`MasteredWaza` is editable WHERE IT EXISTS, which narrows an older blanket
refusal rather than overturning it.** This file used to say the learned-move pool
was not editable at all, because the property is absent on most Pals (1,563 of
the reference world's 1,905; 2,225 of the live world's 2,963). That is an
argument against **creating** it — inventing an ArrayProperty means guessing its
`array_type` — and never was an argument against editing the quarter that have
one. The rule is the one every property here follows: write into an existing
shape, never construct one. Both halves are pinned by tests.

## A struct list needs its own writer, and `str()` is the trap

`GotWorkSuitabilityAddRankList` — the work ranks bought with Pal Souls — is an
ArrayProperty of `{WorkSuitability: EnumProperty, Rank: IntProperty}`.
`_write_list_property` coerces every value with `str()`, which is right for
`EquipWaza` and silently wrong here: a struct stringified still serialises.
`charedit._write_work_ranks` is separate for that reason.

**THE CAP IS 10, AND THIS PARAGRAPH USED TO SAY THE GAME SHIPPED NONE.**
Corrected 2026-08-05. `BP_PalGameSetting` carries
**`WorkSuitabilityMaxRank = 10`**, in `backend/data/game_settings.json.gz`.

The old claim was not careless — it was *checked*, and checked in the wrong
place. `DT_GainWorkSuitabilityRankItem` really does hold one ticket item per work
type with **no rank column**, and no other DataTable carries one. But a
DataTable sweep is not a search of the game: the constant lives in the settings
CDO, which nobody looked in for this. **That is the exact failure this file warns
about elsewhere — a documented negative gets trusted and stops the next person
looking.** It was found by someone asking "isn't the max 10?".

Two independent figures agree with it: max `requiredRank` across all 271
structures in `DT_MapObjectAssignData` is **10**, with nothing above; and the
highest natural suitability across 753 species is **8** (`BlueSkyDragon`,
Watering). So the buyable amount is `10 - base`.

Observed spend remains modest — across refworld, the live world and a 07-29
snapshot, **39 Pals carry the property** and the ranks run
`{1: 30, 2: 4, 3: 4, 6: 1}`. Six being the highest anyone reached is a fact about
those players, not the ceiling.

**Condenser stars do not add work suitability, and this was re-asked after the
MapProperty decode rather than trusted.** That matters because the previous
answer rested on a DataTable sweep, which is exactly the kind of negative this
file records getting overturned. With every map in `BP_PalGameSetting` now
readable, the settings mentioning rank are: `CharacterMaxRank` (5),
`CharacterRankUpRequiredNumMap` — **`{1: 4, 2: 8, 3: 12, 4: 24}`, the duplicate
Pals each condenser star costs, 48 for all four** — the Arena rank ladder, and
`WorkSuitabilityMaxRank` (10) with its curves. **Nothing joins the two.** A
suitability-10 Pal is its species base plus work handbooks, and the only thing
that moves work rank is `GotWorkSuitabilityAddRankList`.

### CONDENSING RAISES WORK SUITABILITY — UNVERIFIED, AND THE TEST IS SPECIFIED

**Status 2026-08-07: believed true, not yet confirmed, and nothing implements it.**
Read this before answering "does the condenser affect work suitability" again —
the answer was given as a flat *no* three times before anyone went and looked.

The operator observes a 4-star Jetragon at Gathering **10** against a species
base of **8**, and the same effect on Jormuntide, Jormuntide Ignis, Aegidron and
Verdash. The save cannot show it: across 20 Pals at condenser rank 4 or 5,
**nineteen have no `GotWorkSuitabilityAddRankList` at all**, and one clean case —
a rank-5 Verdash at level 35 with an **empty passive list** — differs from a
rank-1 Verdash in `Rank` and nothing else. So the bonus is **derived at load**,
which is why every save-side and settings-side search came back empty.

A community table (palworld.fandom) states it as:

| Stars | Sacrifices | Stat bonus | Work suitability |
|---|---:|---:|---|
| 1 | 4 | 5% | +1 to its best suitability |
| 2 | 8 | 10% | +1 to its 2nd-best |
| 3 | 12 | 15% | +1 to its 3rd-best |
| 4 | 24 | 20% | **+1 to every** suitability |

**Two of its four columns are exactly reproduced by the game files** —
`CharacterRankUpRequiredNumMap = {1:4, 2:8, 3:12, 4:24}` and
`StatusCalculate_GenkaiToppa_PerAdd = 0.05` — which is real corroboration that
the author was reading data rather than guessing. The suitability column is in
no file found here.

**The fallthrough is the majority case, not an edge case.** A Pal with one
suitability has no "2nd-best", and the operator's reading — that the bonus lands
on the only one it has — is what makes Jormuntide work: Watering 7 -> 8 -> 9 ->
10, then clamped at `WorkSuitabilityMaxRank`. The alternative reading (skip)
predicts 9 and is contradicted by observation. Measured across the 343 base
species:

| Suitabilities | Species | |
|---:|---:|---|
| 0 | 9 | nothing to add to; must not invent one |
| 1 | 89 | fallthrough |
| 2 | 92 | 3-star has no target |
| 3+ | 153 | straightforward |

**181 of 343 — 53% — hit a fallthrough.**

**AND `BestWorkSuitability` IS EDITORIAL, NOT THE MAXIMUM.** The game ships the
column, and it disagrees with the numeric max on 9 species — **8 of them naming
`MonsterFarm`**. Caprity is `{Seeding: 2, MonsterFarm: 1}` and the game calls its
best MonsterFarm, because Caprity is a ranch animal. So "+1 to its best" is
ambiguous: for Caprity that is either Seeding 2->3 or MonsterFarm 1->2, and those
are different Pals afterwards. **133 of 343 species also have a numeric tie for
first place and 169 have a tie somewhere in the ordering**, with no tiebreak
stated anywhere. Half the roster is undetermined by the rule as written.

#### The two readings that settle it, both on Pals the operator already owns

- **A 1-star Anubis** — `{Handcraft: 6, Mining: 6, Transport: 4}`,
  `bestWorkSuitability = Handcraft`. One star, one bonus, and the top two tied.
  Handiwork 7 / Mining 6 means the label breaks ties; 7 / 7 means both sides get
  it; 6 / 7 means the ordering is something else; unchanged means 1-star grants
  nothing. **A 4-star Anubis cannot answer this** — the tied pair converges on 8
  either way, so only the 1-star discriminates.
- **A 4-star Verdash** — `{Seeding: 4, Handcraft: 5, Collection: 5, Deforest: 3,
  Transport: 3}`, nothing clipping the cap, and one specimen in the live world
  carries **no passives at all**. Predicted 7 / 7 / 6 / 4 / 4 under the table.

Until both are read, `optimise.work_level` must keep reporting `base + bought`
and no third term. This belongs in the `elements.py` category when it lands: the
data genuinely is not in the files, so a measured constant with the observation
cited is legitimate — a guess presented as read is not.

### WORK SUITABILITY *IS* RAISED BY PASSIVES, AND I SAID TWICE THAT NOTHING RAISED IT

The operator reported a 4-star Jetragon showing Gathering **10** against a species
base of **8**, and was told twice that condenser stars do not raise work
suitability. The answer to *that* question is still no. It was the wrong
question, and repeating it is the failure — not the first answer.

`passive_effects.json.gz` carries **16 passives whose effect type is
`WorkSuitabilityAddRank_*`**:

| Passive | Effect | Target / invoke |
|---|---|---|
| `..._MonsterFarm_1` — **Farmhand** | Ranch **+1** | `ToSelf` / `InvokeAlways` |
| `..._MonsterFarm_2` — **Ranch Master** | Ranch **+2** | `ToSelf` / `InvokeAlways` |
| 14 × `..._<work>` | that work **+1** | `ToBaseCampPal` / `InvokeInBaseCamp` |

**ONLY THE FIRST TWO ARE PAL PASSIVES.** The fourteen are the effect applied by
the **Applied … Handbook** items (`WorkSuitability_AddTicket_Mining` -> "Applied
Mining Handbook I"), and the rank a handbook grants is written into
`GotWorkSuitabilityAddRankList` — so it is already counted as `bought` and adding
it would double count. `optimise.work_level`'s docstring had this right before
anyone re-derived it wrongly from the effect table; the giveaway is that Farmhand
and Ranch Master carry real display names and prose ("Ranching's work suitability
+2") while the fourteen carry none.

So the genuine gap is two effects, not sixteen — and it is real: **73 Pals on the
live world carry one** (66 Farmhand, 7 Ranch Master) with nothing in their
`GotWorkSuitabilityAddRankList`, and every one is ranked today as though it did
not.

**Why it was invisible is worth more than the finding.**
`palstats.PASSIVE_SELF_INVOKES` excludes `InvokeInBaseCamp` and
`PASSIVE_SELF_TARGETS` excludes `ToBaseCampPal` — **both correct**, because a
base-only buff is not part of the stat block the game prints on a palbox Pal.
Nothing else ever looked. A filter that is right for its own surface becomes a
blind spot the moment it is the only reader, which is the entire argument for
`passiveeffects` being a second module with its own policy rather than a wider
constant.

Measured the same day: of the bundle's 208 effect types, **79 are named anywhere
in `backend/` and 129 are mentioned nowhere.**

### Four progression systems, and the dashboard knew two

Chasing that turned up two more tables nothing reads, which is the count worth
recording — a project that thought there were two ways to improve a Pal was
wrong by half.

| System | Currency | Cap | Where it is |
|---|---|---:|---|
| Level | EXP | 80 | `CharacterMaxLevel` |
| **Condenser** | duplicate Pals | 5 | `CharacterRankUpRequiredNumMap` = `{1:4, 2:8, 3:12, 4:24}` — **48 duplicates for all four stars** |
| **Statue of Power** | Pal Souls | **20** | `DT_CharacterUpgradeMasterDataTable`, never read |
| Work handbooks | tickets | 10 | `GotWorkSuitabilityAddRankList` |

`DT_CharacterUpgradeMasterDataTable` is **the cost side of `soulRanks`**, which
the parser has always read without knowing what a rank cost. `PalUpgradeStone`
1-4 are Small/Medium/Large/**Giant Pal Soul**, and the full climb to rank 20 is
`{Small: 10, Medium: 6, Large: 6, Giant: 30}` plus 128,800 gold to reset. The
table confirms the cap independently: 20 rows, and the live world's most
invested Pal reads `soulRanks {hp: 20, attack: 20, defense: 20, craftSpeed: 20}`.

And the condenser's stat effect is now read rather than assumed:
**`StatusCalculate_GenkaiToppa_PerAdd = 0.05`** — *genkai toppa* is "breaking the
limit" — so 5% per rank, applied to CraftSpeed as well as the combat stats. A
4-star Verdash at level 50 really is better at every job: work speed **70 → 87**.

### The mount MODE is not in the server pak, and the saddle was the best guess

Proposed 2026-08-07 — reasonably, since a flying mount needs a flying saddle,
and Jetragon's gear is called "Jetragon's Missile Launcher" rather than a saddle,
which looks like the item table drawing exactly this distinction.

`DT_ItemDataTable` does carry a gear discriminator, and it is **weapon kind, not
movement mode**. Of the 143 `Essential_PalGear` items, `IconName` splits them
into `SkillUnlock_Saddle` (108), `_Gloves` (10), `_Harness` (5), `_Choker` (4)
and nine weapon kinds. Checked against Pals whose mode is known:

| | Pal | IconName |
|---|---|---|
| flies | Vanwyrm, Shadowbeak, Nitewing | `SkillUnlock_Saddle` |
| ground | Melpaca, Rushoar, Eikthyrdeer, Direhowl | `SkillUnlock_Saddle` |
| swims | Jormuntide Ignis (Orca) | `SkillUnlock_Saddle` |
| flies | **Jetragon** | `SkillUnlock_MultiMissile` |
| swims | **Penguin** | `SkillUnlock_Launcher` |

So the column separates *what the partner skill shoots*, and Jetragon and
Penguin — one flyer, one swimmer — land in different buckets from each other
while every ordinary flyer and ground mount share one. A rule built on it would
have looked convincing on Jetragon and been wrong on Nitewing.

**And Jetragon's launcher IS its saddle** — you ride Jetragon with it; the item
grants the mount *and* the weapon, so the differing icon is the gear's artwork
rather than a second category of item. That kills the reading in which
`_MultiMissile` marks "weapon instead of saddle": there is one gear item per Pal
and `IconName` only ever describes its art.

**Which also means "has PalGear" is not "is a mount", and Galeclaw is the
counterexample.** `SkillUnlock_Eagle` is *Galeclaw's Gloves* — "gloves for
modifying the performance of the equipped glider" — a partner skill you hold,
not a mount. So `RestrictionItems` narrows the field usefully and does not settle
it either.

Last avenue, also empty: `DT_PartnerSkillParameter.ActiveSkill` carries
`bIsOneShotRideAction`, `IsRidingActiveSkillNotWeapon` and
`RidingActiveSkillNotWeaponCondition`, which read like the answer. They are
defaults — **680 of 682 rows are `::None`** and the other two are `WaterJump` —
and non-rideable Lamball is byte-identical to Vanwyrm across all three.

Also checked and empty: no ride/mount/fly column in `DT_PalMonsterParameter`'s
90; `DT_PartnerSkillParameter` gives `RestrictionItems` (which **does** answer
*rideable at all*) and no mode; `DT_PartnerSkill`'s 50 rows are ability kinds;
**no `BP_Pal_*` asset and no `DA_*Ride/Mount/Move/Fly` exists in the server pak
at all**, so the CDO technique has nothing to point at. The Pal blueprints are
client-side, which is the unversioned wall.

`RideSprintSpeed` is populated for **all 753 species**, including Pals that
cannot be ridden, so sorting on it unfiltered produces a leaderboard of mounts
that do not exist. `RestrictionItems` is the best filter available and is not
exact — see Galeclaw above.

Conclusion: fastest **ride** is answerable and fastest **flyer** is not, from
files. A hand-maintained mode list is allowed on `elements.py`'s terms — the data
does not exist, so the obligation is provenance and a visible "unknown", never a
guess derived from a name.

`fullStomach` is still unbounded — that one genuinely has no constant, and the
lesson above is a reason to go and look again rather than to assume it does.

**And the rank is not linear.**
`WorkSuitabilityDefineData_<work>.CommonDefineData.CraftSpeeds` is 11 entries
indexed 0–10: `[0, 50, 70, 100, 140, 190, 260, 370, 510, 720, 1000]`. Rank 3 is
100 and rank 10 is **1000**. `Mining` and `Deforest` additionally gate on
material — rank 2 unlocks Copper, 3 Iron, 4 Platinum — which is a real
eligibility rule, not a speed bonus, and
`TransportItemAbsorbRangeByWorkSuitabilityRank` is **0 below rank 4**. A bare
integer hides all of it.

`backend/workrank.py` reads it and `optimise.work_level` attaches it to every
row, so the ranking tables show what a level buys rather than the level alone.
Two things travel with it and both matter:

- **"THE GAME STATES THE CURVE FOR THREE WORK TYPES" WAS TRUE, AND THE
  INFERENCE DRAWN FROM IT WAS WRONG FOR EIGHT OF THIRTEEN.** This bullet used to
  end here, noting that Collection, Deforest and Mining each carry their own
  identical copy while every other work type sat inside
  `WorkSuitabilityDefineDataMap` — an opaque `<MapProperty 1361B>` — so the
  shared curve travelled as `stated: false`. Three identical copies really is
  good evidence, and labelling the guess really is the right thing to do with
  one. **Neither made the number right.** `uassettable` learned to decode
  MapProperty on 2026-08-07 and the map says:

  | Curve | Work types | Rank 10 |
  |---|---|---:|
  | `0 50 70 100 140 190 260 370 510 720 1000` | Collection, Deforest, Mining, Watering, Seeding, OilExtraction | 1,000 |
  | `0 50 80 140 240 400 680 1100 1900 3200 5400` | EmitFlame, Handcraft, Cool, ProductMedicine | **5,400** |
  | `0 250 325 400 500 750 1000 1500 2000 3000 4000` | GenerateElectricity | 4,000 |
  | `0 2 5 10 20 40 70 120 200 320 500` | Transport | 500 |
  | `10 12 14 16 18 20 22 24 26 28 30` | MonsterFarm (Ranch) | 30 |

  Handcraft was understated **5.4x**, and the Ranch **starts at 10** — a rank-0
  Ranch Pal still produces, which the shared curve erased. A speed figure is now
  only comparable *within* one work type; `optimise.py` ranks one at a time, so
  it is safe there and would not be in a combined table.

  **The decode's verification is that the keys complete a known set.** Eleven
  entries, minus the pseudo-entry `EPalWorkSuitability::Anyone` (a flat 100 at
  every rank), plus the three that ship standalone, are *exactly* the 13 work
  suitabilities the species table uses. A drifted tagged walk does not produce
  eleven valid enum names that fill in the gaps of an independently-known list.
  `stated` survives in the payload and is now true throughout — it means "read
  from the game", and there is no longer anything here that is not.

  The transferable half is the one this file keeps writing down about itself: a
  documented assumption, honestly labelled, still stops the next person looking.
- **The material gate is eligibility, not speed, and is kept out of the sort.** A
  rank-2 miner cannot touch Iron at any speed. Level still orders the ranking
  tables for exactly that reason: speed cannot substitute for a level a Pal does
  not have.

**And `editschema`'s docstring kept the retracted claim after AGENTS.md dropped
it.** This section was corrected when the constant was found; the validator went
on saying "NO MAXIMUM IS ENFORCED, and that is measured rather than lazy" and
citing the DataTable sweep. A correction that lands in the prose and not in the
code is half a correction — the bound is now read from
`gamedata.game_setting("WorkSuitabilityMaxRank")`, never a literal, and an
unreadable bundle drops the bound rather than guessing one.

The *minimum* is real: rank 0 appears on none of the 39, so a zero is
`parser._num`'s default rather than a value the game stores.

Three things it will not do, each for a measured reason:

- **It will not construct an entry.** A new work type deep-copies an existing
  one, which is `palclone`'s rule: the right struct metadata is whatever this
  save already uses.

**"IT WILL NOT CREATE THE PROPERTY" WAS THE FIRST OF THOSE THREE, AND IT WAS
STRICTER THAN ITS OWN REASON.** The reason — never construct a shape — is
unchanged; what was wrong is where the shape was allowed to come from. A
work-rank node carries no `CustomVersionData`, no instance guid and an all-zero
`id`, so two Pals' entries differ only in the enum and the integer and the
writer overwrites both. Any Pal in the save is as good a template, and what the
refusal actually enforced was "you may only edit a Pal that already has a rank"
— not a safety property, just a smaller feature, and handbooks are per work
category so an operator who has spent one anywhere has the shape everywhere.
`charedit.find_work_rank_donor` scans for it once per apply. A save with none
anywhere is still a refusal, now naming the fix.

An **empty array** moves with it and has to: an absent property carries strictly
less information than a present-but-empty one, so accepting the first while
refusing the second is backwards. Only the donor's *entries* are taken there —
this Pal's own array metadata is already correct, and replacing the node would
discard a right answer to import a duplicate of it. Donors are deep-copied,
because a shallow copy edits the Pal we borrowed from, silently, on a Pal the
operator never named.
- **It will not construct an entry.** A new work type deep-copies an existing one
  from the same Pal, which is `palclone`'s rule: the right struct metadata is
  whatever this save already uses.
- **It will not hardcode the enum prefix.** That is read off the template's own
  value, so a rename carries through instead of writing entries the game ignores.

All 39 carry **exactly one entry**, so a multi-entry list is plausible and
unobserved. Adding is allowed — the struct shape is what is risky and that is
copied — but the fact is recorded rather than hidden.

**`_flatten` expanded it and lost it.** That helper turns `{"ivs": {"hp": 1}}`
into `ivs.hp` to match the field names, and did the same to `workRanks` — so the
diff read `before: None`, and since `None` never equals the requested map, an
edit that changed nothing planned as a change every time. It now expands a dict
only when it is *not* itself a declared field. `ivs` is a grouping whose members
are the real fields; `workRanks` is one field that happens to hold a map.

## Ownership history is a list of UUIDs, not a list of strings

`OldOwnerPlayerUIds` is present on **100% of Pals** — the only record of a trade
there is. So unlike every other list field, there is no create-vs-guess problem
here. There is exactly one problem, and it is the value *type*.

palsav decodes a GUID as its own `UUID` class. `_write_list_property` calls
`str()` on everything, so routing this through it produces a tree that reads back
correctly and an encoder that emits wrong bytes — precisely the trap
`soloexport` documents, where an `isinstance(v, str)` test matched nothing and
rewrote **zero of 6,455** uid fields. `charedit._write_uid_list` reconstructs the
class, taking it from whatever is already in the list so a save storing plain
strings keeps storing plain strings.

**Validated on shape, never against the roster.** A player uid is a Steam ID32
followed by zeros (`11a11a01-0000-…`), which is what distinguishes it from the
full-entropy GUIDs used for bases, guilds and character instances — the same
property `soloexport` relies on to match uids by value. A uid this server has
never seen is *accepted*, because the main reason to edit this at all is that a
world export remapped a uid and left entries pointing at a player who no longer
exists anywhere. Checking against the roster would break exactly the case the
feature is for.

Both sides of the change land in the audit log through the ordinary edit diff, so
"who rewrote a Pal's provenance" is answerable without a separate action.

**`DT_GainWorkSuitabilityRankItem` also names one dummy.**
`Dummy_WorkSuitability_AddTicket_OilExtraction` — every other work type has a
real ticket item. Not acted on, but it is the game saying oil extraction rank
cannot be bought.

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

## The phone problem was not the tab bar — there is no tab bar

The task assumed sixteen tabs overflowing a phone's width, and said to **check
before building**. Checking is what found that the nav is not a tab bar at all:
`page.tsx` rendered a fixed **210px `<aside>` with `flexShrink: 0`** inside a
flex row, so a 390px phone had **180px** for the map, the tables and every form.
That is not a layout defect, it is an unusable app, and no amount of fixing the
thing that was assumed to be wrong would have touched it.

Findings at 390px, in the order they matter:

| | |
|---|---|
| 210px sidebar, `flexShrink: 0` | **blocking** — 180px of content |
| `scheduled-announcements` table, no `overflow-x` wrapper | real |
| `backup-manager`'s inner table had `overflowY` and no `overflowX` | real |
| ~30 inline `width: 150…380` form controls that cannot shrink | real |
| the tab bar | **does not exist** |

The sidebar is off-canvas below **900px**, not 640: a 768px tablet in portrait
has the same problem in a milder form, and 210 of 768 is still a quarter of the
screen spent on navigation.

**And the hamburger lost a specificity fight to `.btn`, then won it too hard.**
`.nav-toggle { display: none }` and `.btn { display: inline-flex }` are equal
specificity, so source order decided it and the toggle rendered on desktop —
a control that visibly does nothing, since the class it toggles is only read
inside the media query. Qualifying it as `button.nav-toggle` settles that, and
**a media query adds no specificity**, so the qualified selector then outranked
the bare `.nav-toggle` *inside* the query as well — hiding the only way to open
the nav on the one viewport that needs it. Both ends of a specificity fight have
to move together, and the built bundle is where that was caught rather than the
source.

**THE BREAKPOINT IS CSS, NEVER `window.innerWidth`.** This page is
server-rendered and the server does not know the viewport, so a JS width check
renders the desktop tree and rearranges it after hydration — a mismatch on the
first paint, on the device least able to absorb one. The scrim and the hamburger
are rendered on every viewport and hidden by a media query, for the same reason
plus one more: a scrim that *mounts* cannot animate in, and flashes.

Two leverage points did more than the edits they replaced:

- **`max-width: 100%` on `.input, .select`.** Thirty call sites set an inline
  pixel width, and an inline style beats a stylesheet rule — but `max-width`
  does not compete with `width`, so it caps them all. A fixed-width control
  added tomorrow is covered without anyone remembering. `box-sizing:
  border-box` is what makes the cap honest: under the default `content-box`,
  `max-width: 100%` still overflows its parent by the padding and border.
- **`minWidth: 0` on `<main>`.** A flex child's default `min-width: auto`
  refuses to shrink below its content, so a single wide table pushed the whole
  page sideways instead of scrolling within its own wrapper. The wrappers were
  mostly already there; this is what let them work.

**This was audited statically, not opened on a handset.** The CSS and the markup
are verified — the media query is in the built bundle — but nobody has held a
phone. Touch-target sizes beyond the 40px hamburger, and how Leaflet's own
gesture handling feels on a real screen, are unmeasured.

### And Pocketpair publishes a PvP recipe our own preset contradicts

`docs.palworldgame.com/settings-and-operation/pvp` is not per-key help — it names
six keys only inside prose recipes, which is why `extract-settings-help.py`
deliberately takes nothing from it. But it **is** a set of key/value pairs from
Pocketpair, which is what `settings_ini.PRESETS` holds.

Two presets, split exactly where the page splits: `pvp_official` is the **three**
parameters it says enable PvP (`bIsPvP`, `bEnablePlayerToPlayerDamage`,
`bEnableDefenseOtherGuildPlayer`, all True), and `pvp_official_recommended` adds
the recommendation block. Collapsing them would apply a dozen opinions under a
button labelled "enable PvP".

**THE HAND-MADE `pvp_players_only` SETS ONE OF THOSE THREE TO FALSE.** Its intent
is reasonable — "players fight, bases stay safe" — but whether a partial enable
produces that is a claim about game behaviour no file supports. So the official
pair was *added* rather than the hand-made pair edited, both keep their `source`
tag (`official` vs `dashboard`), and the UI badges them. Same discipline as
`elements.py`: carrying something unverified is fine, presenting it as the game's
word is not. `test_pvp_presets.py` pins the disagreement so it cannot be quietly
resolved in either direction without evidence.

**Two of Pocketpair's own recommendations are deliberately omitted.**
`bEnableAimAssistPad` because the page contradicts itself — heading "Disable
Gamepad Aim Assist", prose "when set to False, aim assist is disabled", code
block `=True`; picking one is a guess wearing an official label.
`DenyTechnologyList` because it disables thirteen technologies including the
Guild Chest, which is a much larger act than the button says, and the page frames
it as something you *can* restrict rather than part of the recipe.

That second omission is a judgement call rather than a limitation, and there is a
test to prove it: the value round-trips through `write_ini` intact —
parentheses, quoted ids and all — without disturbing the setting after it, which
is what a bad `_split_top_level` would break first.

## Settings help comes from Pocketpair, and 19 keys get none

`scripts/extract-settings-help.py` -> `backend/data/settings_help.json.gz`,
served by `backend/settingshelp.py`. The Settings tab showed 119 identifiers like
`PalStomachDecreaceRate` — the game's own misspelling — and explained none of
them.

**Hand-writing 119 sentences was the obvious move and would have been wrong.**
It is the "do not hand-write game data that already exists" rule, and worse: a
sentence I wrote about an unverified mechanic renders identically to one
Pocketpair published, and gets trusted the same way.

Three sources, each tagged in the payload because they carry different authority:

| field | source | coverage |
|---|---|---:|
| `description` | **docs.palworldgame.com**, Pocketpair's own documentation | **93 of 119** |
| `label` | the game's own `WORLDSSETTING_*` UI strings | 50 of 119 |
| `note` | this project's own measurements, tagged `dashboard` | 6 |
| `values` | the game's names for an enum's **values** | `DeathPenalty`, `RandomizerType` |

**`values` is worth more than the key descriptions.** `DeathPenalty` was a free
text field, so setting it meant knowing that the string
`EquipmentAndItemAndRandomPal` exists and is spelled exactly that way — and a
typo is accepted by the file and ignored by the game. It is a dropdown now, but
**only when the value on disk is one the game names**: a select cannot represent
a value it has no option for, so an unrecognised one falls back to the text box
rather than being silently rewritten to whichever option is first.

**19 keys get nothing at all, and that is the feature.** No official description,
no game label, so no tooltip — the operator sees what they saw before. The
extractor prints the list, and the footer says Pocketpair does not document them,
because a missing explanation and a broken bundle look identical otherwise.

**The fetch is a build step.** The container never reaches the network; the page
is parsed once and the result bundled, same rule as `refs/`. And it is parsed
from the table markup rather than read out of a summary — a language model asked
for "verbatim" returns something that *reads* verbatim, and attributing a
paraphrase to Pocketpair is worse than writing our own sentence and saying so.

**`UI_ALIASES` is hand-written and each entry is confirmed by two unrelated
sources.** Nineteen game UI rows are named for something other than their INI key
(`WORLDSSETTING_HatchingEggTime` for `PalEggDefaultHatchingTime`). Matching those
by string similarity is the failure this repo keeps recording. So the acceptance
test is agreement: the pak string reads *"Time (h) to incubate Massive Egg. Note:
Other eggs also require time to incubate"* and the doc row reads *"Time to hatch
a Huge Egg (hours). Note: Other eggs also require time to incubate."* — the same
sentence, reached from a 40 GB pak and from a website. `--show-aliases` prints
every pair for re-checking.

Three things that bit:

- **`<br>` is a sentence boundary.** Stripping tags without substituting for it
  gave "(max 50).Increasing this value" and "Death PenaltyNone : No drops" —
  text that reads as a typo in Pocketpair's docs when it is one in our parser.
- **An aliased row is not always a label.** `HatchingEggTime`'s string is a whole
  explanatory sentence, which is *why* it agreed so well with the doc row — and
  rendering it as the field's name captions a form control with a paragraph.
  Over `MAX_LABEL_CHARS` the label is dropped; the description already covers it.
- **`RANDOMIZER_MODE_NO` is the value `None`.** Title-casing the suffix gets two
  of three and invents `No` for the one that matters, so the three are written
  out and confirmed against the official description, which spells the
  vocabulary out in full.

**`WorldName -> ServerName` is deliberately absent** — the same INI field, but
the game calls it "World Name" on the single-player creation screen and this is a
dedicated-server dashboard. The one case where the game's own words are not
automatically the right ones.

**And the PvP page is a recorded negative.** Six of the 19 are PvP keys that page
names — inside prose recipes ("set these three to True"), never as a description
of any one of them. Splitting that into per-key help means deciding which clause
belongs to which key, which is the guess this whole script avoids. What it *does*
carry is the game's own recommended PvP configuration as key/value pairs, which
belongs in `settings_ini.PRESETS`.

### 92 of the 119 could not be edited here at all

Only the `HIGHLIGHT_GROUPS` keys rendered. That was defensible while the page
could show a key's *name* and nothing else — ninety identifiers in a column is a
hex dump, not a settings screen — and stops being defensible once each one
carries Pocketpair's description. The rest are now behind a collapsed,
filterable "Show the other 92 settings", which is why this shipped **with** the
help rather than before it.

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

## World ACTORS decode too, and that is how the NPCs got their names

**This supersedes the section below for the server pak.** `upackage.py` says a
placed actor's properties cannot be decoded, and that is true — of the *client*
pak. `Pal-LinuxServer.pak`'s `MainGrid_*.umap` cells carry `IntProperty`,
`StructProperty` and `NameProperty` in their name tables, so a spawner actor's
tagged properties walk exactly like a DataTable row's. Nobody had pointed the
tag walk at world cells; it is the same correction `uassettable.py` records for
DataTables, one container over.

    UniqueName    {"Key": "DarkTrader"}   -> DT_UniqueNPC -> "Black Marketeer"
    HumanName     {"Key": "PalDealer"}    -> a character id -> "Pal Merchant"
    Level         45
    RespawnTime   30.0

`scripts/extract-npcs.py` bundles **438 placed NPCs** with role, level and
verified position — 404 with an identity, including 4 Black Marketeers and 4
Medal Merchants. `worldobjects.json.gz`'s "NPCs & camps" layer could only ever
say *someone* stood there: 141 of its 220 points are the generic class
`BP_MonoNPCSpawner`.

**THE ACCEPTANCE CRITERION IS WEAKER THAN `read_table`'s AND MUST SAY SO.** An
actor export cannot prove alignment by ending exactly at the buffer end — 32-43
bytes of component instancing data follow the property terminator, and their
length is not something this reader knows. What replaces it is **resolution**:
every identity must be a `DT_UniqueNPC` row or a known character, because a
drifted tag walk does not produce 400 valid foreign keys. Miss rate above 5%
refuses the build.

**A third place an identity can live, and missing it left the Medal Merchants
anonymous.** `BP_MonoNPCSpawner_MedalTrader` carries neither `UniqueName` nor
`HumanName` — its blueprint already knows. The class-name suffix is used only
when it is a real `DT_UniqueNPC` row, so this cannot invent an id out of a
naming convention.

### Four bad positions, and why they were dropped rather than refused

`extract-spawns.py` refuses outright if any position falls off the cell grid.
Here 4 of 442 did, and the right response was to drop them — because the
coordinate comes from `read_position`'s byte scan for the first plausible triple
of doubles, whose known failure mode is finding *some other* triple. A handful
of misses is that heuristic behaving as documented, not evidence about the
property walk.

They were checked rather than waved through. All four are
`BP_OilrigNPCSpawner_Mono`, and "the grid does not cover the sea" is **ruled
out**: oil rigs pass the same test 185/185, and the four coordinates sit
**60,000 units from the nearest oil rig**. A placement whose position cannot be
trusted must not go on a map; blocking the other 438 over it would be worse. The
extractor still refuses above a 5% drop rate.

### The wandering merchants have no location, and the controls proved it

`DT_RandomIncidentNPC_DarkTrader` (×4) and `_MarchantwithPAL` (×3) name the NPC
and its level and carry `SpawnLocation` **(0,0)** — while **149 of the 195**
incident rows across those tables carry real coordinates. So the merchants are
specifically the ones the game does not place, which is consistent with them
being roaming incidents.

**A naive grid check passes all 195 at every cell size**, including both
controls, because (0,0) is a real occupied cell. That is the check failing to
discriminate rather than succeeding, and it is exactly why
`extract-boss-spawners.py` refuses when a control matches as well.

**The save cannot answer it either.** Merchant NPCs *are* in
`CharacterSaveParameterMap` — `Male_DarkTrader01_04`, `BOSS_Male_Trader01` — and
their `SaveParameter` has no position field at all. There is no "where is the
Black Marketeer right now" to be had.

### The role split is a name rule, and no game table carries one

`DT_UniqueNPC` has appearance and talk-flow columns; `TalkBPClass` is a flavour
label with **58 of its 216 rows set to `None`**. So `_role` sits exactly where
`gamedata.fast_travel_kind` does — a name rule that **fails safe**, since an
unrecognised id becomes a plain `npc`, which is what all 220 were before.
`roleFromName: true` travels in the API payload for the same reason
`hasMultiplier` does: the client is the thing about to draw a legend.

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

## Progression: a count needs a source, and two of them were unknowable

`backend/progresscheck.py`, `/api/progress/detail`. `/api/progress` has counted
these categories since Phase 4 and **nothing ever rendered it** — the relic
statue lines from #61 shipped backend-only. The Progression tab is that, plus
the part that makes a count actionable: *which* ones are left, by name.

A save's flag maps are keyed on ids that resolved to nothing until now:

    towerBosses   BOSS_BATTLE_NAME_GrassBoss     a localisation key
    fieldBosses   81_1_grass_FBOSS_FlameBuffalo  a spawner id
                  BOSS_Hunter_Rifle              …or an NPC id, in the SAME map
    areasFound    Grass_001                      a world-map area row

**`TowerBossDefeatFlag` holds more than towers, and one of its rows is a room.**
The client pak's `BOSS_BATTLE_NAME_*` gives fourteen entries: 8 towers, 3 World
Tree mid-bosses, 2 endgame encounters — and `KingWhaleRoom`, "Eternal Sea",
which is the arena rather than the encounter. A denominator over the whole table
counts a room.

The eight towers are checked against the eight `… Tower Entrance` fast-travel
points, and **that check is worth more than its result** because the two sources
are unrelated: one is the client pak's localisation, the other is extracted from
the world cells. The extractor refuses if they stop agreeing.

**`？？？` is the game's own value.** Two endgame encounters carry full-width
question marks as their name — Pocketpair withholding a spoiler, not a decode
failure. It travels as `hidden` so the UI says "not named yet" rather than
printing it or, worse, humanising the key into a name the game refused to give.

**`FieldBossDefeatFlag` holds two kinds of key and only one is enumerable.**
Measured across the reference world's five players: 82 distinct keys, 59 spawner
ids resolving through `boss_spawners.json.gz` to a species and a level, 23
`BOSS_`-prefixed NPC ids for the human bosses. So the Pal half gets a real total
and the human half gets **none** — the only enumeration available is the
catalogue's 34 `BOSS_` NPCs, and that list contains `BOSS_DarkTrader`, a
merchant, and a quest NPC. `of: null` with `totalSource: "discovered"` is the
honest answer; "124 field bosses" would be the `TowerLockBarrier` mistake again.

**And 89 spawners, not 90 rows.** `remainsIsland_1_GrassGolem_FBOSS` is listed
twice, same species at level 55 and 75. The flag keys on the *spawner*, so that
is one checkbox — taking the row count would leave every player permanently one
short of completion.

**`areasFound` finally has a denominator, through a two-hop join, and it needs a
case-fold.** The save names an area row (`Grass_001`), `progression.areas` maps
it to a localisation key (`REGION_Grass_1`), and `gamedata.regions` maps that to
"Windswept Island". 123 of 123 make the second hop and every observed key makes
the first — but the save writes `BOSS_KingWhale` where the table says
`Boss_KingWhale`. One row in 104, and an exact join drops it silently while
everything else looks right.

**`dungeonsCleared` reports `available: false` with a reason.** No save examined
has ever written a `FixedDungeonClearCount` entry — five players, none with one —
so there is no observed key shape to join dungeon names against, and a checklist
built on a guessed one would be unverifiable. An empty checklist would read as
"you have cleared none of 23".

**The privacy strip is recursive, and it has to be.** `discoveryVisibility`
decides whether the not-yet-found half travels at all, and `main._drop_missing`
walks nested structures because `fieldBosses` holds its two halves one level
down — a filter that only understood the top level would leave the larger list
untouched. Server-side, as always: a UI that received everything and hid some of
it would be handing out the answers in the network tab.

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

## Pal stats are calculated, and the formula is in `refs/`

The save stores only the **inputs** — level, IVs, condenser rank, soul ranks,
trust points — and the game derives HP, Attack, Defense and Work Speed at load.
So there is nothing to read: `backend/palstats.py` runs the same arithmetic.

The formula is **not invented and not scraped from a wiki**. It is in
`refs/PalWorldSaveTools-main.zip`, in two files that must be read together:
`.opencode/skills/pst-stat-formula/SKILL.md` (the derivation, plus a record of
which terms were corrected against in-game breakdowns on maxed test Pals) and
`src/palworld_aio/utils.py` (the constants). `palstats.py` is a transcription;
diff against that implementation if a game update moves a number. Its own
documented tolerance is ±1–2 on the trust and awakening terms at some boundaries,
which is why every figure is labelled `calculated: true` in the payload and the
UI never shows one with the same authority as a level.

    base      = additive_const + floor(scaling × K × level × (1+IV) × (1+condenser))
    subtotal  = base + trust + awakening                    # additive
    final     = floor(subtotal × (1+soul) × (1+passive))    # multiplicative

**This supersedes the earlier note that star/alpha multipliers exist in no
bundled source.** That was true of `resources/game_data/`, and the skills
directory beside it was never checked.

Four things that bite:

- **Rank 1 is *no* stars.** The condenser bonus is `(rank - 1) × 5%`, so four
  stars — the maximum — is rank 5 and +20%. Treating `Rank` as a star count gives
  every Pal in the world a bonus it has not got.
- **The alpha bonus is already in the data**, and must not be applied twice.
  `BOSS_Alpaca` carries hp scaling 108 where `Alpaca` carries 90; that difference
  *is* the alpha bonus. `gamedata.pal()` strips the `BOSS_` prefix — correct for
  naming, since an alpha Lamball is still called Lamball — so stats go through
  **`gamedata.pal_exact()`**, which does not. The reference implementation had
  exactly this bug and removing its separate `lucky_alpha` term is a line in its
  changelog.
- **Attack is *shot* attack.** `meleeAttack` is bundled beside it, is a different
  number on most species (Melpaca: 90 melee, 75 shot), and the game never shows
  it. Reading the wrong one is plausible everywhere and wrong everywhere.
- **Work Speed is flat 70 until the condenser is used at all.** Neither level nor
  craft speed enters below rank 2. A formula that treats it like HP shows work
  speed climbing with level on a Pal whose in-game work speed has not moved —
  wrong in the direction you would expect it to move, so nobody questions it.

## `DT_MapObjectMasterDataTable` says what a structure IS, not what it eats

Checked 2026-08-04 before building a base supply advisor, and recorded because
the negative half is the useful half. The table decodes cleanly — 1,034 rows —
and confirms that the base containers are **five distinct build objects**:

    PalFoodBox        BP_BuildObject_PalFoodBox      bBelongToBaseCamp=True
    CoolerPalFoodBox  BP_BuildObject_PalFoodBoxCool  bBelongToBaseCamp=True
    GuildChest        BP_BuildObject_GuildChest      bBelongToBaseCamp=True
    BreedFarm         BP_BuildObject_BreedFarm       bBelongToBaseCamp=True
    PalMedicineBox    BP_BuildObject_PalMedicineBox  bBelongToBaseCamp=True

**But its columns are HP, Defense, MaterialType, DeteriorationDamage and
ExtinguishBurnWorkAmount — structure and combat.** Nothing about what a
container accepts or what a structure pulls from. So "Pal food must be in a Feed
Box" and "the Breeding Farm consumes Cake" remain *unconfirmed by any game file*,
however obviously true they are in play. A dashboard must not assert them as
rules.

The fix is a better feature anyway: **report facts, not mechanics.** "This base
has a Feed Box and it is empty" needs no rule cited and is worth flagging on its
own; "move your food out of the chest" is a claim about game behaviour and is not
supported. The first framing survives being wrong about the second.

If someone does want the mechanic settled, the place to look is the build-object
Blueprints themselves via the CDO technique below — a container's accepted-item
filter is plausibly a UPROPERTY on `BP_BuildObject_PalFoodBox`'s class default.
That technique is proven and cheap; nobody has pointed it at these.

**`backend/basesupply.py` is what got built on that**, and it holds the line: it
reports where things are and never what to move. A test asserts that no note it
emits contains "move" or "should", so the refusal is pinned rather than merely
intended — the moment someone has a source for the mechanic, that test is the
thing to change, deliberately.

Two figures it needs that are *not* game data, and both say so where they live:

- **The staple material list** is an operator judgement. Nothing in the game
  ranks materials by how often a base needs them, so this is on the same footing
  as `elements.py`'s chart: hand-written is allowed because the data does not
  exist, and the obligation is provenance plus configurability. It is written as
  **ids, never display names** — "Ore" is `CopperOre`, "Ingot" is `CopperIngot`,
  "Refined Ingot" is `IronIngot` and "Paldium Fragment" is `Pal_crystal_S`, so a
  list keyed on what the UI shows would silently miss four basic materials.
- **The floor is not `maxStack`.** Every material in the game stacks to **9,999**,
  so "keep one stack at each base" — the shape the request arrived in — resolves
  to 110,000 Wood across an eleven-base world. The floor is therefore an operator
  setting with `stackSize` carried beside it in the payload, so the UI can show
  the difference instead of letting a chosen threshold read as a game rule.

## A Blueprint's CDO decodes too, and 347 tuning constants were in there

**This supersedes the assumption that only DataTables come out of the pak.** That
was true of the client pak and wrong about the server pak's *Blueprints*:
`BP_PalGameSetting`'s class-default object is tagged the same way, so every
balance constant Pocketpair exposes as a UPROPERTY reads out.
`scripts/extract-game-settings.py` bundles all 347 at 6 KB.

**The decode verifies itself, which is the only reason to trust it without a
second source.** Two constants this project already held from sources that
explicitly could not be checked against the install fall out exactly:

    CharacterMaxLevel = 80    <- editschema.MAX_LEVEL, documented as
                                 "community-sourced, not read from the game files"
    CharacterMaxRank  = 5     <- editschema.MAX_RANK

A drifted tagged walk does not land two independently-known values in the right
places. `--verify` asserts them and is what to run after a game update. The
second criterion is the one `uassettable` already uses: the walk must terminate
at the end of the export — measured, **41,416 of 41,420 bytes**.

**The CDO is found by its `Default__` prefix, never by size.** Picking the
biggest export works today and would silently choose a function body after an
update.

**Unknown value types are skipped by the size in their own tag**, which is the
difference from `read_table`'s outright refusal. There a bad offset makes
everything after it garbage; here each property is self-describing and
independently placed, so an undecodable type costs one property (5 of 352, all
StructProperty) rather than the file.

### Three numbers this project had guessed at were sitting in that file

- **`FriendshipPoint_AutoIncrementRequireSanity = 50`** — the sanity a Pal must
  hold to keep gaining trust. `main.LOW_SANITY` had been *chosen* at 50 as a
  judgement call. It is the game's number, and now comes from the file rather
  than merely agreeing with it.
- **`CharacterMaxLevel = 80`** — see above. The `PALWORLD_MAX_LEVEL` override
  stays, but the constant is no longer unverifiable.
- **`DamageElementMatchRate = 1.2`** — see below. Not 2x.

### What else is in there, unused (candidates for future work)

`BaseCampAreaRange = 3500`, `BaseCampNeighborMinimumDistance = 1500` (`_PVP =
8500`), `PalBoxTimePeriodRecoverySick = 3600` (a sick Pal recovers in an hour in
the box — worth saying on the welfare panel), `HungerParameterRate_Hunger = 10`
and `_Starvation = 20`, `DamageRate_SleepHit = 3.0`, `DamageRate_WealPoint = 1.5`,
`RarePal_AppearanceProbability = 0.1`, `Combi_BossPalRate = 0.05`,
`PlayerHPRateFromRespawn = 0.5`.

### The element chart is the one hand-entered thing here, and it is quarantined

Which element beats which is in **neither** source, and both were searched
exhaustively rather than guessed at. All 480 server-pak DataTables were listed
and read: there is no `Compatibility`, `Effectiveness`, `Weakness`,
`AttributeDamage` or `ElementDamage` asset of any kind, and the only element
DataTable is `DT_PalAwakeningItemElement` (item → element, no multipliers).
Everything else matching "Element" is visual effects, elemental treasure-box
locks and player step-attack statuses. In `refs/PalWorldSaveTools-main.zip`, all
78 matching entries are **icons**. So the chart lives in C++ or in a blueprint's
unversioned properties — the same wall `DT_BossSpawnerLoactionData` hits, and
unlike the passive-effect table it does not come down by switching paks.

`backend/elements.py` therefore ships it as a **documented constant**, on the
same footing as `editschema.MAX_LEVEL`. The rule this project holds is "do not
hand-write game data *that already exists* in `refs/`", so the obligation here is
provenance, not abstinence. Source is named in the module docstring.

**It lives in a module, not in `backend/data/`.** Everything in that directory is
extracted and a script can re-derive it; this cannot. Filing it beside the real
bundles would blur the distinction that makes them trustworthy.

**The game data wins wherever the game has an opinion.** Only the *relation* is
hand-entered. The element vocabulary is read off the bundled Pal data by
`game_elements()`, so the game decides what elements exist; the hardcoded tuple
is a fallback for a missing bundle, not the source. The source's "Ground" is an
alias for the data's `Earth`, and `DT_PassiveSkill_Main`'s third vocabulary
(`Leaf`, `Electricity`, `Normal`) resolves too, because three files disagree
about these names and a caller holding any of them must not silently get "no
effect".

**This is the only thing here that can silently rot**, which is why
`unknown_to_chart()` exists: a content update adding a tenth element would make
every matchup involving it read as a confident "neutral" rather than as a visible
gap. Empty is the healthy state, and a test pins both it and the detector.

Two things checked before trusting the transcription, because a cited source is
not a verified one:

- **The relation is exactly reciprocal** — nine strength pairs, nine weakness
  pairs, identical sets, no orphans either way. A chart copied with an error
  almost certainly breaks this.
- **Every name resolves against the bundled Pal data**, which uses
  `Dark, Dragon, Earth, Electric, Fire, Grass, Ice, Neutral, Water`. Eight of
  nine matched the source's spelling exactly.

**The multiplier is NOT hand-entered, and it is not 2x.** The relation had to be;
the number did not, and looking harder found it —
`BP_PalGameSetting.DamageElementMatchRate = **1.2**`, exposed as
`elements.match_rate()`. The widely repeated figure is 2x dealt and 1/2 taken,
and **the game's settings object contains exactly one element-damage constant**
with no halving or resist counterpart, so neither popular number is reproduced by
the files. A test pins that there is only the one key, so "the other half is not
in there" rests on having looked rather than on not having found it.

`effectiveness()` still returns a *relation* rather than a damage estimate,
because the constant's **semantic is inferred from its name**. The binary also
exports `DamageUpElement_ByElementStatus` and `DamageDownElement_ByElementStatus`,
which are C++ and unread, so whether something stacks on top of 1.2 is not
established. `match_rate()` falls back to **1.0** — no effect — rather than to a
hardcoded 1.2, because a second copy is how the file and the code drift apart.

Neutral is strong against nothing, which is the game's design (Neutral Pals trade
matchups for base work) rather than a hole in the transcription, and Fire is the
only element strong against two.

### The optimiser is where that quarantine could leak, so it is pinned twice

`backend/optimise.py` ranks Pals for work and for combat. The rule it exists to
hold: **a matchup is a badge, never a sort key.** There is no coefficient to rank
by, so folding "strong against Grass" into a score would mean inventing one, and
the resulting order would look more authoritative than anything behind it.

The guard is a *differential* rather than an assertion about the code: rank the
same roster with and without a target and the order must be **identical**. On
refworld's 1,905 characters it is, across the top 50. Pinned on both sides of the
wire — `test_matchup_never_enters_the_ordering` and
`test_a_matchup_does_not_reorder_the_ranking` — because a UI that re-sorted on the
badge would defeat a backend that did not.

`hasMultiplier: false` travels in the payload, not only in a docstring. The client
is the thing about to render a damage figure, so it is the thing that has to be
told there is none.

**Work levels are read; work speed is calculated; the payload says which per
row.** `base` (the species table) and `bought`
(`GotWorkSuitabilityAddRankList`) stay separate as well as summed — "this species
is good at mining" and "somebody spent Pal Souls on this one" are different facts
and one number hides which. A Pal at level 0 for a job is **excluded**, not ranked
last: listing everything that cannot mine under "who should mine" is noise.

Level sorts ahead of speed because speed cannot substitute for it, and refworld
shows why that ordering is not cosmetic — a level-1 Astegon reads work speed 91
against a level-29 Blazamut's 70, since **work speed is flat 70 until the
condenser is used at all**. Sorting on speed would put an unusable Pal first.

Measured on refworld, useful as a regression signal: 412 Pals capable of Mining,
854 of Transporting, **0 of Oil Extraction** (consistent with
`DT_GainWorkSuitabilityRankItem` shipping only a *dummy* ticket for it), and 99
characters with no scaling data excluded from combat — exactly the 99 NPCs
documented above, arrived at independently.

### The passive term was always zero, and 1,352 Pals were understated

`palstats.describe` took `passive_bonus` as a caller-supplied float defaulting to
**0.0**, and `main.py` never passed one. So the `(1+passive)` term in the formula
above contributed nothing, on every Pal, since the feature shipped.

The reason given was sound at the time: the bundled `passives` section carries an
English *sentence* — "Attack +5%" — which is right for showing a player and
impossible to compute with. That was true of `refs/PalWorldSaveTools-main.zip`
and is not true of the game. `DT_PassiveSkill_Main` decodes completely out of the
**server** pak with structured `EffectType/EffectValue/TargetType` columns;
`scripts/extract-passive-effects.py` bundles them to
`backend/data/passive_effects.json.gz` (20 KB, 1,897 skills).

**Verified against the game's own prose**: of the 1,759 passives with a numeric
English description, **1,754 match the extracted numbers exactly**. Four of the
five exceptions are the archive failing to substitute its own `{EffectValue1}`
placeholder — the table is right and the sentence is broken. The fifth
(`FullStomach_Down_1_BossDefeat`, prose "+10.0% slower" against a stored -1.0) is
a real disagreement and is left unresolved rather than explained away.

**A passive's bonus is per stat, not one number.** `Legend` is +20% shot attack
AND +20% defence; `Noukin` is +30% attack and **-50%** craft speed. 175 of the
1,897 touch more than one stat and 77 carry a negative, so a single multiplier is
wrong for hundreds of real Pals in at least one direction. `palstats.passive_bonuses`
returns `{stat: fraction}` and `describe` applies each to its own stat.
`passive_bonus` survives as an **override** — "what would this Pal be without its
passives" is a real question.

Three filters, each measured:

- **`MeleeAttack` passives are deliberately dropped.** Attack here is *shot*
  attack, as everywhere else in this module; folding melee in would inflate it.
- **Worker-only skills do not buff a palbox Pal.** `InvokeWorker` and
  `InvokeInBaseCamp` are excluded from `PASSIVE_SELF_INVOKES`, because a skill
  that fires only at a base is not part of the number the game shows elsewhere.
- **Targets that are not this Pal are dropped.** Across 2,057 effects: ToSelf
  736, ToSelfAndTrainer 341 count; ToTrainer 669, ToOtomo 226, ToBaseCampPal 40,
  ToBuildObject 29, ToActiveOtomo 10, ToTrainerAndOtomo 5 do not.

**Two traps found by the checks rather than by reading.** A declared effect slot
with value `0.0` is not an effect — `GrassMinotaur_PartnerSkill_2` reads "Attack
+12%" and carries a wired-up `Defense 0.0` beside it, which made the skill look
like it touched defence. And `target: None` occurs **exactly once** in the whole
bundle, on `Rare`'s defence, whose own description says "Defense +15%" — so it is
an unset field, not a category. A strict `ToSelf` test silently dropped 15%
defence from every Lucky Pal, and the only thing that surfaced it was stacking
Legend with Rare and watching defence not move.

Measured on the live world: **1,352 of 2,963 characters carry a stat-affecting
passive**, and the largest single attack correction is **+1,515**.

`friendship_*` coefficients are per species and now travel in
`gamedata.json.gz` (`scripts/build-gamedata.py`); without them the trust term
cannot be evaluated at all, and unlike every other term there is no default,
since the whole point is that species differ.

**99 of the reference world's 1,905 characters get no stats, and that is the
answer.** They are hunters, soldiers, merchants and quest NPCs sharing
`CharacterSaveParameterMap` with Pals and carrying IVs exactly like one. There is
no scaling data for them anywhere, so `describe()` returns `None` rather than
zeroes — a breakdown full of zeroes would show confident stats for a merchant.

Level progress, by contrast, is **exact**: `palExpTable` is bundled from the
game's own table. Read `PalNextEXP`/`PalTotalEXP`, not the `NextEXP`/`TotalEXP`
beside them — those are the *player* curve and they differ from level 2 (25 vs 50).

### `OwnedTime` is a TIMESTAMP, and its name says duration

`parser._dotnet_ticks`. The field reads like "how long this Pal has been owned"
and is an absolute **.NET DateTime tick count** — 100-nanosecond intervals since
0001-01-01. As a duration the reference world's values are about two thousand
years; as timestamps they are **2024-04-13 to 2026-07-28**, which is that save's
real lifespan. Checked by converting rather than by trusting the name.

So the conversion is exact and needs nothing from the server: this is wall-clock
time, not game time, so `DayTimeSpeedRate` never enters it. **No timezone is
asserted** — .NET keeps a `DateTimeKind` beside the ticks and this format drops
it, so appending `Z` would be a claim the data does not support.

Present on 1,740 of 1,905. It is the only field that answers "which of these did
I catch first", and the My Pals column sorts on the **raw ticks** rather than the
formatted string, which sorts lexicographically by accident and correctly only by
luck.

### Save fields nobody was reading

`parser.extract_characters` now also carries `soulRanks`, `friendshipPoint` and
`isLucky`. **`Rank_Defence` is spelled the British way and only that one is** —
`Rank_Attack` and `Rank_HP` are not, and `Talent_Defense` sits beside them
American. Reading `Rank_Defense` finds nothing and yields a silent zero, so the
defence souls a player actually spent simply do not appear in the stat.

### And the attack IV was never displayed

`ivs.shot` is the canonical key, from `Talent_Shot`, all the way through the
parser, the API and `charedit`'s field map. `my-pals.tsx` asked for `ivs.attack`
— a key no Pal has — so the Attack column rendered `—` on all 1,905 Pals, the IV
total was short by the attack IV on **every** row, and both the minimum-IV filter
and the Attack sort silently ignored it. Nothing errored; the column read as data
the game had not filled in.

## The item catalogue and the item census are different endpoints

`/api/world/items` is **what the game has** (2,466 entries, bundled, no parsed
world needed). `/api/items` is **what this world holds**, and is privacy-filtered
per guild. They are one letter apart in intent and easy to reach for wrongly.

The slot editor's autocomplete was built on the second, so typing any legitimate
item nobody on the server happened to own showed "not in this world" with no icon
— while the backend, which has always validated against the full catalogue, went
on to accept the very same input at preview. The editor was calling valid entries
wrong and then writing them correctly.

Both id and friendly name travel together on every catalogue row, because the API
speaks `AIcore` and people speak "AI Core", and a catalogue carrying one of them
forces every caller to rebuild the other index. Search boxes accept either
throughout — `SheepBall` and `Lamball` find the same Pals.

`/api/world/items/{id}` (`backend/itemsource.py`) is the catalogue's other half:
where an item comes from, folding six bundled tables into one answer. Its census
counterpart is `/api/bases/craftable` — what a guild's own materials could make —
and the two sit on opposite sides of exactly the line above.

### Keeping ONE recipe per product silently answered a twelfth of the question

`economy.json.gz` used to key recipes by product, which collapsed 1,414 rows to
1,399 and reported *that* as the recipe count. Fifteen rows were discarded, and
they were not evenly spread: **twelve of them are Paldium Fragment**, which has
thirteen recipes because dismantling each kind of Pal Sphere is its own row. The
bundle kept one. Carbon Fibre from Coal *or* Charcoal was invisible the same way.

That is fine for "how do I make X" and wrong for "where does X come from", and
nothing about the shorter answer looked incomplete. Recipes are a list per
product now, each carrying its row id — a product's recipes can be unlocked by
*different* technologies, so the row is the thing a technology joins to, never
the product.

### `DT_PalStaticItemIDRedirectData` IS NOT A RENAME MAP

29 rows of `SourceItemIds -> DestinationItemId` reads exactly like "these old ids
now mean this one", and task #63 asked for it to be wired into `gamedata`'s
lookups so a stale save's items resolved instead of falling back to `humanize()`.

**Every one of the 29 is an accessory tier collapsing onto its own base tier** —
`Accessory_AT_2` and `_AT_3` onto `Accessory_AT_1`, and so on for all seventeen
pendants and twelve whistles. There is not one genuine rename in the table. And
all 58 source ids **already resolve, to distinct names**: the game calls them
"Attack Pendant +1" and "Attack Pendant +2". Applying this map to a lookup would
replace 58 correct names with 29 wrong ones and undo the tier distinction the
L10N join exists to get right.

It is bundled as data with its meaning stated, and **no lookup consults it**.
Same discipline as `elements.py`: carrying something you cannot use is fine;
using it because its name reads right is not.

### The technology join needs a case-fold, and the check is what found that

Two of the 588 rows in `DT_TechnologyRecipeUnlock_Common` spell a recipe
differently from the recipe table — `Bow_triple` against `Bow_Triple`,
`Sakurasaurus` against `SakuraSaurus`. An `FName` compares case-insensitively so
nothing is wrong in the game; a `dict` does not, so an exact join loses two
technologies and reports 586 of 588 as though that were the data.

The extractor refuses on a dangling recipe row or a missing prerequisite, and the
join is on the **row**: reading `UnlockItemRecipes` as *product* ids would resolve
for the handful whose row name matches their product and look like it worked.

### Three things this feature will not say, each for a measured reason

- **Which bench crafts a recipe.** `WorkableAttribute` is on all 1,414 rows and
  is 0 on every one. `basesupply.py`'s rule — report facts, not mechanics — and
  both panels say so out loud rather than leaving a gap that reads as an
  oversight.
- **How often a chest is opened.** `WeightInSlot` is relative within one field's
  slot and nothing says how often a field is rolled. `slotShare` divides by that
  slot's own total, which *is* the chance the item fills the slot given the roll,
  and is a different claim.
- **A rate between drop bands.** The `Level` column holds only 0, 10, 20 … 80.
  The field is named `levelFrom` end to end so nothing downstream reads it as
  exact.

**And the answer is cached as ONE entry, not 2,466.** Keying the finished payload
per item is the obvious move and is wrong twice over: `viewcache`'s file cache is
a shared 128-entry LRU, so browsing the catalogue would evict the Paldeck listing
and everything else in it, and the repeated work is the *scan* rather than the
assembly. `itemsource._build_index` folds the bundle once — 3.5 ms per lookup
becomes 0.2 ms, in one slot.

**Pal-shop rosters shipped stringified dicts for months.** `CharacterIDArray`
decodes as `{"Key": "SheepBall"}` and `str()` on that gives the literal
`"{'Key': 'SheepBall'}"` — id-shaped, serialises perfectly, resolves to nothing.
Nothing read the field, so nothing caught it. `_key()` is the one unwrapper.

## Sorting a chest by category needs `typeA` and `sortId`, and four buckets

`saveedit.sort_containers(order=...)`. `id` is the alphabetical-on-internal-id
ordering everything did before; `category` groups by the game's own `typeA` and
orders within a group by `sortId`, which is the field Palworld itself sorts
inventories with — so a sorted chest matches what the player sees in their own
inventory rather than an order only this dashboard uses.

`order` is deliberately **not** folded into `mode`. `mode` is about what is safe
to move and maps to a capability; `order` is only about what the result looks
like. One enum would have made "sort by category" imply permission to relocate
durability items.

**Pal eggs get their own bucket, because the game's own table does not give them
one.** All 56 `PalEgg_*` items are `typeA: "Material"`, so grouping strictly by
category files a Jormuntide egg between Coal and Wood — every egg scattered
through the ore. They are the one thing in a chest that is not a commodity: each
holds a *distinct Pal*, and someone hunting for one is not looking for a
material. Identified by `dynamic.type == "unknown"`, which is **exactly** those 56
items and nothing else — a property of the data rather than a hand-written id
list or a `PalEgg_` prefix rule a renamed asset would silently break. (They were
already safe from *merging*: each carries a `dynamic_id`, and `_sort_container`
refuses to pool those. This is about where they land.)

**And the third bucket is the subtle one.** 653 of the 2,466 items carry an empty
`typeA` — key items, schematics — but they still carry a `sortId`, so they can be
ordered the way the game orders them even with no category to group under.
Lumping them in with genuinely unknown ids throws that away. Unknown ids sort
last of all: at `sortId` 0 they would sort *first*, which is the most confusing
available place for the items the dashboard understands least.

## Effigies had no fallback, and that is what "not showing" meant

`/api/world/discoveries` serves fast travel and effigies together, calls
`require_user`, and 503s if either bundle fails. The map falls back to
`/api/world/fasttravel` when it is unavailable — and **there was no effigy
counterpart**, so the effigy layer silently vanished for every guest and for any
transient failure, with the toggle still on and nothing drawn.

`/api/world/effigies` is that counterpart, and it applies `discoveryVisibility`
**itself**. That is the lesson from `/api/world/fasttravel`, which for months
returned all 174 points beside a sibling that carefully filtered them: a filter
applied to one of two endpoints serving the same data is not a filter.

The fallback leaves `discovered` **undefined** rather than defaulting it. "We
could not ask" and "you have not collected this" must not share a colour on a
collectathon map. And when *both* endpoints fail the map now says so — a layer
switched on and empty is indistinguishable from a layer that failed to load,
which is exactly how this went undiagnosed.

## `.catch(() => [])` is how a broken layer becomes an empty one

Same lesson, one level up, and it took longer to find because the swallow was in
the *fetch* rather than in a renderer. `page.tsx` polled bases and guilds as

    Promise.all([getBases().catch(() => []), getGuilds().catch(() => [])])

so a 403 from the route allowlist, a 503 from an unparsed world and a backend
that is simply down all arrived as `[]` — and `[]` is a perfectly ordinary
answer. The Bases tab read "no bases" and the map drew **neither base markers nor
their radius circles** on a world with eleven bases, with no error anywhere. The
palbox layer kept rendering throughout, because it comes from `/api/mapobjects`,
which made it look like a base-marker problem rather than a fetch that never
landed.

It is `Promise.allSettled` now, with the reasons kept in `saveDataError` and
shown on the map. Settled independently, so one list failing does not blank the
other.

The general shape to distrust: an empty collection is a legitimate value for
almost every list this dashboard fetches, so a catch that produces one destroys
the distinction between "nothing" and "we could not ask". Let it reject and say
which.

## Scope travels with every breeding answer, not just the palbox

The planner fetches four endpoints and shows one header, so a scope reported on
one of them describes the other three by implication. Below `allPalsVisibility`
the backend pins a request to the caller whatever it asked for — and a route plan
computed from your own box, displayed under "All Pals on the server", reads as a
*wrong answer* rather than a narrower question. `_breeding_scope` is on `/palbox`,
`/reachable` and `/paths` alike.

It carries `pals` — the count the answer was built from — because zero alongside
`linkedToPlayer: false` is the specific state people report as the dashboard
forgetting their account. And the planner's "not linked" banner now reads
`linkedToPlayer` off the **response**, not the cached session: the session object
is refreshed only on page load, so an account linked while its owner was signed
in kept reading as unlinked until they reloaded.

## Two Pals of one species need telling apart

A player usually owns several of the same species at the same level, and
"Lamball · Lv 50" three times over is a list nobody can choose from. The write
path was never at risk — every editor keys on `instanceId` and so does
`palimport` — but a person had no way to see *which* one they had picked, so the
only way to find out was to apply and check.

Editor rows now carry what actually differs between two Pals of one species:
total IVs, condenser stars, alpha, where it is, and a short instance id last,
which is not pretty but is the only thing guaranteed unique when everything else
matches.

## The client pak has no map-icon set — one icon, and it is worse

`scripts/extract-textures.py` reads UTexture2D packages out of
`refs/Pal-Windows.pak` and writes WebP, closing the gap `public/icons/map/PROVENANCE.md`
anticipated. It works: `T_worldmap_icon_fasttravel` decodes at 64x64 PF_DXT5.

**The icons did not change, and that is the result rather than a lack of effort.**
`Blueprint/UI/WorldMap/` holds exactly one icon texture; `Texture/UI/Map/` holds
26 packages of map *furniture* — landmass masks, boss banners, a circle frame, a
stripe pattern. Palworld draws POI markers from widget blueprints with generic
shapes, so **the per-category icon set does not exist as art.** And the one icon
that does exist is a pale plinth with a soft halo — 274 of 4,096 pixels above
alpha 200 — against a wiki stand-in with real silhouette. Same conclusion the
fast-travel marker already reached, now measured against the actual asset.

The mip is located by **anchor, not offset**: every `FTexture2DMipMap` is followed
by its own `SizeX, SizeY, SizeZ`, and the payload precedes it at a length fully
determined by the dimensions and block format. Two independent facts that have to
agree, so a layout change raises instead of writing plausible noise. Same
discipline `upackage.py` uses, for the same reason — there is no version number
in these files to branch on.

Pillow decodes BC1/BC3/BC7 from a DDS container, so the raw mip is wrapped rather
than decoded here. A hand-written BC7 decoder is eight modes and several hundred
lines duplicating a library the project already ships.

## The INI question is answerable by observation

`backend/iniwatch.py`, now wired. The dashboard cannot read the game container's
environment, so it cannot *recognise* an image that regenerates
PalWorldSettings.ini — but it can **observe** one: hash the file when we write it,
hash it again once the server has restarted, and a change we did not make is a
fact about this deployment covering every key rather than a list of the ~15 that
are commonly env-driven.

The baseline is recorded inside `settings_ini.write_ini`, not in the API route —
every writer goes through that function, so a new one cannot forget. Same reason
`guarded_save_write` owns the save-side rules. The observation happens on
`lifecycle._watch_for_return`, the one moment the question is answerable.

`unknown` is a real answer and the honest starting state: it means "not yet
observed", not "safe", and the UI shows nothing for it. Once there *is* a verdict
the old conditional warning is suppressed — a measurement of this deployment beats
a list of what images commonly do, and an operator told "your settings persist"
should not be reading a warning that they might not.

### The file hash answers a question the operator did not ask

Everything above is about the **deployment**. The operator asked something
narrower: *did the setting I just changed survive?* Those come apart in **both**
directions, which is why `verify_written_keys` exists rather than a rewording:

- An image can rewrite the file and leave your key alone. `regenerated` is then
  true and reads as "your change was lost", which it was not.
- An image can leave 126 keys alone and revert the one you cared about — the same
  undifferentiated warning it gives for a cosmetic reformat.

So `record_our_write` also stores **what** was written, per key, and the restart
observation re-reads the INI and compares each one. Verdicts are `verified`,
`reverted`, `missing` and `unchecked`.

**`replacements`, not `changes`.** The comparison is against the string that went
into the file, because `_format` renders `2.0` as `2.000000` — comparing the
caller's request would report every float write as reverted, on every server,
forever. A permanent false alarm is worse than no check, because it teaches the
operator to ignore the panel.

**`missing` is a revert, `unchecked` is not.** A regenerating image that has never
heard of a key writes a file without it and the game falls back to its own
default, so the change is just as gone. An unreadable INI is the other thing
entirely — "we could not look", which this project keeps separate from a negative
everywhere else (the missing ban list, the unreachable server, the unparsed
world).

**Warnings and notes are two lists on purpose**, borrowed from Paladin's
`VerifyResult`. Warnings are actionable; notes are merely true. A change that
applied cleanly on a server whose image also rewrote an unrelated key must render
as success with a note, not as VERIFY FAILED — a single flat list of "findings"
is how a panel gets ignored.

**AND THE SECURITY TRAP IS THE WHOLE REASON THIS NEEDED CARE.** Verifying "what
we wrote is what is on disk" means keeping a copy of what we wrote, and
`AdminPassword` and `ServerPassword` go through this path. `settings_ini` masks
those on read and in the audit log precisely so they never reach a log, a
screenshot or a network tab; a verification record holding the plaintext would
undo that in a *new* place and one that outlives the request.

They are stored as **scrypt hashes** — `accounts.hash_password`, not a second
hashing implementation, because a server password *is* a password and two scrypt
call sites is two places to get `maxmem` wrong. The verdict for a secret is the
comparison result and nothing else: `expected` and `actual` are empty strings in
the payload whatever the outcome, and `verify_written_keys` is the only thing
that ever sees the revealed value. `test_ini_verify.py` asserts the plaintext
appears nowhere in a full `iterdump()` of the database.

**A note on test hygiene this change forced.** `write_ini` now writes to SQLite,
so the ten existing tests that call it started leaving rows — including sealed
password material — in the *development* database, because `real_ini` and `ini`
did not take `fresh_db`. Nothing leaked and nothing was committed, but a test that
mutates shared state outside its `tmp_path` is one refactor away from doing so.
Both fixtures take it now. The trap that produced it is worth naming: a first
version of the new test set `DB_PATH` in the environment, which `db.py` does not
read — the variable is `DASHBOARD_DB` — so all eight tests silently shared one
database and each saw the previous one's rows. **Backend modules capture
environment at import time; monkeypatch the module attribute**, which is what
`fresh_db` does.

## Container capacity: `SlotNum`, not the slot array

The save stores **only occupied slots**; `SlotNum` is the real capacity and the
parser ignored it. One omission, three symptoms:

- no empty rows in the slot editor, so a slot could only be *overwritten*
- **`fillPercent` was ~100% for every base**, so the "nearly full" warning had
  been firing permanently and meaning nothing
- stored `slot_index` values are **sparse**, so the UI's row numbers disagreed
  with the indices the writer uses — the out-of-range errors

`extract_containers` pads to `SlotNum`. The reference world reads 6%–66% full
instead of 100% everywhere.

**One code path, so it covers everything an item container can be.** Verified on
the reference world: a player's six containers — `CommonContainerId` (45),
`DropSlotContainerId` (4), `EssentialContainerId` (230, the key items),
`WeaponLoadOutContainerId` (6), `PlayerEquipArmorContainerId` (9) and
`FoodEquipContainerId` (5) — all come back with contiguous indices and padded
empty slots, exactly as base chests do. There is no separate inventory parser to
keep in step.

**Pal skins are the exception, and they are not an item container at all.**
`SaveData.SkinInventoryInfo.InGameData` is a plain array of
`{SkinName, Num}` — no container id, no slots, nothing in
`ItemContainerSaveData`. See the task notes: all five reference players hold the
identical 22 skins, which is also the total the pak ships, so there is nothing to
grant.

## Work suitability is empty for exactly two released Pals

Panthalus (#203) and Astralym (#204). The other 29 forms with no work are raid,
gym and unreleased entries the Paldeck does not list. Checked against the
reference source for all 753 forms: **zero disagreements**, so an empty set here
is the game's answer and not a gap in the bundle.

The Paldeck panel used to `return null` for them, so the heading vanished along
with the content — indistinguishable from data that failed to load, which is how
it got reported as missing. It now renders the section and says "None — this Pal
cannot be assigned to work at a base."

## A guild move is four structures, and the fifth is not there

`backend/guildedit.py`. Membership is not one field — a player belongs to a guild
through the guild's `players[]`, its `admin_player_uid`, the `group_id` on their
character **and every Pal they own**, and the guild's
`individual_character_handle_ids[]` index of those same characters. Three of four
raises nothing and leaves a world the game reads inconsistently, so the
verification counts all four and runs again after re-reading from disk.

**PST writes a fifth and this does not.** `move_player_to_guild` sets
`SaveData.GroupId` in the player's `.sav`. That key **does not exist** on a
Palworld 1.0 player save — the reference world's 16 `SaveData` keys do not include
it — so writing it would be *creating* a property the game does not store, on the
same reasoning that keeps `MasteredWaza` uneditable. Membership evidently lives
entirely in `GroupSaveDataMap` and in each character's `group_id`, which every
character on the reference world already agrees with: five guild ids account for
all 1,910 of them.

### The solo-guild case is the main one, and PST deletes bases for it

All five guilds on the reference world have **exactly one member**, so "move this
player to their friend's guild" empties the origin guild every time. PST removes
the guild and calls `delete_base_camp` on everything it owned — three fully built
bases destroyed to carry out a request that said nothing about bases.

So it is handled rather than avoided. By default the move is **refused**, naming
what is at stake (`3 base(s) and 54 base-deployed Pal(s)`); `transfer_bases=True`
re-homes those bases to the target guild inside the same all-or-nothing write and
then removes the emptied guild. Nothing is ever deleted. Base deletion is not
implemented and should not be added here — it belongs to a feature that says
"delete this base", where someone can be asked about it.

### Which characters move

Owned Pals carry `OwnerPlayerUId`. **170 of 1,910 carry none at all** — those are
the base-deployed workers, and they belong to a *base* rather than to a person, so
they move only when their base does. The cross-check that this partition is
complete: on "Greed", 560 owned + 54 base workers = **614**, exactly the guild's
`individual_character_handle_ids` count.

Handle entries are **relocated, not rebuilt.** Each carries a `guid` beside the
instance id; reconstructing one means inventing that guid, and the origin guild
already holds the right value.

`MapObjectConcreteInstanceIdAssignedToExpedition` is cleared on every moved
character — expeditions are guild-scoped, and an assignment that survives points
at a map object the new guild does not own.

The emptied guild is removed **last**, after its bases have been re-homed, so a
failure anywhere earlier leaves it still holding them.

## Guild markers are the one map layer that is private by default

`guild_markers` on the guild record. `mine-savefields.py` had listed it as unread
and nothing had looked, because **the reference world has none** — it took a
second save, from a server where somebody had actually dropped pins, and even
there it is 3 markers on one guild and 0 on the other four. A field that is empty
on every world you have is indistinguishable from one that does not exist.

    marker_id         GUID
    icon_location     {x, y, z}   z is always 0.0
    icon_type         int
    owner_player_uid  GUID

**Positions are verified, not assumed.** The three land 1 on Palpagos and 2 on
World Tree against the landmass extents the cell grid gives — real world
coordinates in the same space as everything else on the map. A map-space or
normalised coordinate would have been small, and would have been drawn in the sea.

**THE GAME'S OWN STRINGS SET THE VISIBILITY RULE, WHICH IS NOT A JUDGEMENT
CALL.** `DT_UI_Common_Text` carries `MAP_MARKER_HEAD_GUILD` = "Guild Marker" and
`MAP_MARKER_GUILD_INFO` = **"Shared with Guild Members"**. That is both the
confirmation the field means what its name says and the reason
`/api/world/guildmarkers` scopes to the caller's own guilds.

So this is the **opposite default from base privacy**: a base is visible until
its owner hides it; a marker is hidden unless you share the guild. Staff see all,
through the same `privacy.conceals` rank rule rather than a role list, so a role
added to `roles.py` lands on the right side automatically. An account with no
linked character sees **nothing rather than everything** — "no uid, so no guild,
so no filter" is how a filter becomes a leak.

**`icon_type` IS NOT NAMED, AND THIS IS WHERE THE SEARCH STOPPED** so nobody
repeats it. Values 0 and 6 observed. No marker DataTable in either pak. The
client ships five `MI_UI_MapMarker_*` materials (`00`, `Camp`, `FTTower`,
`Oilrig`, `Tower`) which are the *map's own* markers and cannot be this set,
since the index already exceeds them. The custom-pin sprites live in
`WBP_MapMarker_Button`, a widget blueprint cooked with unversioned properties —
the same wall `elements.py` documents. The integer travels as an integer, the map
draws one shape, and the popup says the game does not name them. Inventing a
legend from a guessed ordering is the `TowerLockBarrier` mistake.

**And the first version put a pin at the world origin.** `location.get("x") or
0.0` turned a marker with no position into a confident (0, 0) — found by the test
that asserts a malformed record is *dropped*, not defaulted. Both coordinates
must actually be numbers.

## Read `docs/SAVE-FIELDS.md` before deciding a field is not in the save

`scripts/mine-savefields.py` walks a save with the **full** custom-property set
and catalogues every field path with its type, occupancy and shape, then
cross-references it against what `backend/*.py` actually mentions. The output is
not "here is the save" but **"here is the part of the save nothing has ever
looked at"** — 547 paths across three worlds, 260 of them unread.

It is `mine-datatables.py` for the save, and it exists for the same reason: the
pak got a systematic index and the save never did, so every field this project
reads was found while chasing one feature. In a single week that cost
`base_camp_level` (found only because a competing tool showed it),
`guild_markers`, `guild_chest_allowed_roles` and `role_permissions` — three more
on the same record, seen in the same glance.

**The `base_camp_level` miss is the rule to take from this.** The check that
"confirmed" it was absent sampled `GroupSaveDataMap[0]`, which is an
`EPalGroupType::Organization` — 7 of the 12 groups, six keys, and it could never
have carried it. The 5 `Guild` records have nineteen. **Sample by variant, never
by index**, which is what the index's `byVariant` buckets are for.

Three things it reports that a naive dump would not:

- **Occupancy, not presence** — `seen`, `nonEmpty` and `nonZero` separately,
  because a key on every Pal populated on 0.1% is a different fact from one
  populated on all.
- **Fixed-width blobs.** `byteLengthConstant` marks the shape that has twice
  turned out to be readable: `WorkerDirector` is 118 bytes with a container id at
  offset 98, `GuildItemStorage` 20 bytes with one at offset 0, and both were
  documented as unavailable first. A constant width is a reason to *look* — the
  decoded value must still resolve against a real entry or be dropped.
- **What differs between worlds.** 26 top-level structures across three saves and
  **no single save has all of them**; `BossSpawnerSaveData` is in the oldest
  backup only. A one-world survey would have called it absent.

**No values from a real world are in the committed index.** `refworld` holds real
Steam IDs and player names, so anything name-shaped is counted and never printed.
`test_savefields.py` pins that against the committed file rather than against the
generator — a test of the extractor would pass beside an index built before the
filter existed.

## Read `docs/GAMEDATA-SOURCES.md` BEFORE designing a feature, not after

`docs/GAMEDATA-SOURCES.md` is the curated map of every source — both paks, the
INI files, the reference archive, the saves, and a section on what has been
searched for and is **confirmed absent**. `scripts/mine-datatables.py` generates
the exhaustive index behind it, cataloguing every DataTable in the server pak —
**471 unique tables, 182,962 rows, 32 refusals** — with row counts and column
names. It is a schema index, not data: it answers "does a table exist that knows
X" so that question stops being answered by concluding it does not.

**It exists because the same mistake shipped twice in a row.** The base supply
advisor recorded that `DT_MapObjectMasterDataTable` carries no consumption
semantics — true — and concluded the structure-to-work mapping did not exist. The
work optimiser then *refused to build base assignment on those grounds*, with a
docstring saying no game file supported it.

`DT_MapObjectAssignData` carries exactly that mapping, in 271 rows, and decodes
cleanly. It was one `ls` away the whole time.

Both refusals were honest about what had been **checked** and wrong about what
was **there**, which is the worse failure: a documented negative gets trusted and
stops the next person looking. Searching per-feature is the root cause, and
`read_table` handles the whole pak at once, so there is no reason for it.

What one sweep answered that had been open as separate tasks:

| Want | Table | Rows |
|---|---|---:|
| Which work a structure needs, min rank, worker cap, sanity drain | `DT_MapObjectAssignData` | 271 |
| Crafting recipes with materials and `WorkableAttribute` | `DT_ItemRecipeDataTable` | 1,414 |
| Build costs, capacity, work amount | `DT_BuildObjectDataTable` | 498 |
| What each Pal drops, per level, with rates | `DT_PalDropItem` | 1,044 |
| Raid bosses: summon items, levels, egg weights | `DT_PalRaidBoss` | 11 |
| Spawner rosters with levels | `DT_PalWildSpawner` | 1,691 |
| **Spawner world positions** | `DT_PalSpawnerPlacement` | 8,253 |
| Dungeon enemies, loot and rewards | `DT_Dungeon*` | 59/32/162 |
| Unique breeding combinations | `DT_PalCombiUnique` | 258 |

`DT_PalSpawnerPlacement` deserves particular note: it carries `Location`, so the
habitat data currently derived by intersecting name tables (97.0% attribution,
and explicitly "references this species", not "spawns here at this rate") has a
**direct source** that supersedes the workaround.

**The element chart survived the sweep, and that is worth recording as a
confirmed negative.** Nothing in 471 tables carries an effectiveness relation —
`TargetElementType` appears only on passives. So `elements.py`'s hand-entered
constant is not a gap in the search; it is the answer, and now on much better
evidence than before.

The 32 refusals are listed in the document with their errors rather than omitted,
because "this exists and we cannot read it" is a different and more useful
statement than silence.

## Display names come from the CLIENT pak, and every row is bound twice

`scripts/l10n.py`. **The server pak's `*Text` tables are Japanese** — that is
Palworld's source language, so the strings that decode there are `メルパカ`, not
Melpaca. English and fourteen other languages are *per-language overrides* of
those same tables under `Pal/Content/L10N/<lang>/`, in the client pak.

Two dead ends were eliminated first, and both looked like the answer:
`Localization/Game/<lang>/Game.locres` exists for 17 languages and **all 17 are
37-byte placeholders with zero entries** — `scripts/locres.py` reads the format
correctly, they are simply empty; and the server pak's source strings decode
perfectly and are the wrong language.

The overrides are in the **client** pak, so properties are unversioned and
`uassettable`'s tag walk cannot run — zero property type names in the name table.
**Do not respond to that by scanning `.uexp` for string-shaped bytes and pairing
them with the name table in order.** That is the unverifiable half-decode this
file refuses elsewhere, and a name is the worst possible place for it: an
off-by-one is invisible until a player reports the wrong Pal.

What makes it decodable is that an `FText` carries its **namespace and key
inline**, so each row is self-identifying and bound by two independent parts of
the file — the row name from the package name table, the key from the value
stream:

    row    PAL_NAME_Alpaca            (name table)
    key    PAL_NAME_Alpaca_TextData   (inside the FText)
    source Melpaca

A one-byte drift breaks that agreement everywhere at once, so the agreement rate
*measures* alignment instead of arguing for it. **235,696 of 235,696 rows**, 16
languages × 27 tables, zero refusals — and every language decodes exactly 14,731
rows, which is its own check, since a language is an override of one table.

The row offset is **searched for, not hardcoded**: the acceptance criterion is
the verification — the walk must end exactly at the end of the export. Same rule
`uassettable` follows, for the same reason.

Three traps, each of which produced plausible output rather than an error:

- **An `FName` is (index, number), and the number is a suffix**, not a duplicate
  marker. `ITEM_NAME_Accessory_NormalResist` with number 2 is the row
  `..._NormalResist_1`. Ignoring it collapsed 784 of 1,994 item rows.
- **History type 255 is a row nobody translated**, and its
  `bHasCultureInvariantString` is an **int32, not one byte**. As a byte it
  desynchronises everything after it — and it cost exactly 3 of 432
  table×language combinations, all non-English, which is how it survived a pass
  that looked complete.
- **The untranslated marker has three spellings** — `en Text`, `en_text` and
  `Unidentified Pal`. Knowing only the first hands `en Text` to the UI as a name.

**The game distinguishes things the bundled archive does not.** `Accessory_AT_2`
is "Attack Pendant **+1**"; the archive calls all three tiers "Attack Pendant",
so the dashboard shows three different items under one name today. Tier variants
otherwise inherit the base name (`AncientArmor_2` → `AncientArmor`), so the rule
is **exact-first, base-fallback** — never base-first, or every `+1` disappears.

**Technology names are rich-text references** — `<itemName id=|AIcore|/>` —
because a technology that unlocks an item is named after it. Same tags appear in
descriptions. A resolver must **refuse an unresolvable id** rather than leak
markup into the UI.

And the archive's `(Boss)` / `(Gym)` suffixes are **its own editorialising, not
game data**. The game calls `BOSS_Alpaca` "Melpaca", which agrees with this
file's own rule that an alpha Lamball is still called Lamball. `isBoss` travels
separately; do not fold it back into the name.

### The swap landed, and the resolver is the load-bearing part

`scripts/gametext.py` joins the strings to the ids and `build-gamedata.py`
overlays them, so `gamedata.json.gz` now carries the game's own names — plus
**1,805 item and 303 Paldeck descriptions**, and two sections that did not exist
before: **140 region names** (`Grass_1` → "Windswept Island", which is what
`extract-progression.py` deliberately left unresolved rather than inventing) and
**33 dungeon names**.

**The evidence the joins are right is the disagreement rate, and it collapsed
once the resolver ran.** Technology showed 410 apparent disagreements against
the archive; with `<itemName id=|X|/>` resolved that fell to **3**, and all three
are the archive shipping a raw id (`Cloth2`, `ItemBooth`, `WallSignboard`) where
the game has a real name. `activeSkills` disagrees **0** times out of 326.

**`Catalogue.name()` is the only entry point callers may use.** `item_name` and
`pal_name` return the *raw* row because the resolver calls them while expanding
a reference and must not recurse through a second resolve — which made them a
trap, and the trap sprang immediately: two items shipped with
`<characterName id=|FlowerPrince|/>'s Petal` as their literal name because the
overlay called the raw form. A missing name is recoverable; a name that is
markup reads as data the game provided.

**A placeholder must never survive as a name, and the archive has them too.**
`Scratch` and `Throw` have shipped as the literal string `en Text` since this
bundle was first built — a pre-existing bug found only because the new test
asserted on the *shipped bundle* rather than on the extractor. The fallback path
now drops to `humanize()` instead. `test_gametext.py` pins that, the markup rule
and the accessory tiers against `gamedata.json.gz` on disk, so those three run
without the pak.

`gamedata.json.gz` finally has a real **`gameBuild`** — it was the only bundle
with `null`, which is why `gameversion.status()` could not say whether it was
stale.

**What the archive still supplies: icon paths, and which ids exist at all.**
Nothing else. That is why `docs/LICENSING.md` does not change — the GPL comes
from `palsav`, and pak-extracted data is Pocketpair's copyright either way.

## The SERVER pak's DataTables are fully decodable — numbers included

**This supersedes the "rates, thresholds and coordinates are locked" conclusion
below, which was measured on the CLIENT pak and is true only of it.**

Palworld ships two paks and they are cooked differently:

- `refs/Pal-Windows.pak` (client, 40.5 GB) — **unversioned properties**. Property
  names are absent from the stream, so only name tables are readable. Everything
  the section below says applies here.
- `refs/palworld/Pal/Content/Paks/Pal-LinuxServer.pak` (server, 4.8 GB) —
  **tagged properties**. Every property carries its name, type and size inline,
  so a DataTable decodes completely.

The tell was a name-table diff. For four tables the client's names are a **strict
subset** of the server's — zero names unique to the client — and what the server
has extra is exactly the schema:

| Table | server | client | server-only |
|---|---:|---:|---|
| `DT_TechnologyRecipeUnlock_Common` | 2,288 | 2,258 | `Cost`, `Description`, `EPalBossType::DesertBoss` |
| `DT_ItemShopCreateData_Common` | 299 | 281 | `IntProperty`, `EPalItemShopProductType::Normal` |
| `DT_PalShopCreateData` | 125 | 111 | `CharacterNum`, `MinCharacterLevel`, `MaxLostPalNum` |
| `DT_ItemLotteryDataTable` | 9,565 | 9,542 | `BonusExpRate`, `EPalMapObjectTreasureGradeType::Grade1` |

Column names, property type names and enum values — precisely what unversioned
cooking strips.

Decoding `DT_PalShopCreateData` from the server pak yields whole rows:

    ROW Desert_00
      MaxLostPalNum     = 5
      CharacterNum      = 5
      CharacterIDArray  = [RaijinDaughter, CactusDoll, DarkCrow, DrillGame, …]
      MinCharacterLevel = 40
      MaxCharacterLevel = 45

**The verification is that the walk ends exactly at the buffer end** — 6,258 of
6,258 bytes across 8 rows. A tagged-property reader that has drifted does not
land on the last byte; it runs off the end or stops early, which is how the first
two attempts failed. Do not trust a partial decode that "looks right".

**`FileVersionUE4` and `FileVersionUE5` are 0 in BOTH paks**, so the version
fields do not distinguish them and are not the thing to check. The tell is
whether the name table contains type names like `IntProperty`.

Two traps in the tag layout, both of which produced plausible garbage:

- The row section does not start immediately after the object properties'
  `None` terminator. On `DT_PalShopCreateData` the terminator ends at byte 37
  and the first row name is at 45 — eight bytes of table header between.
- A `StructProperty` tag carries its struct name **and a 16-byte GUID**; a
  missing `HasPropertyGuid` byte or that GUID misplaces everything after it, and
  the failure surfaces as row names that are real Pal ids with nonsense suffixes
  (`CactusDoll_100`) rather than as an exception.

`scripts/uassettable.py` is the reader. **The row offset is found by trying
candidates and keeping the one whose walk terminates exactly at the end**, rather
than hardcoding the measured 8 bytes — that makes the acceptance criterion the
verification itself.

Confirmed by decoding, and pinned in `test_uassettable.py`:

| Table | What comes out |
|---|---|
| `DT_ItemLotteryDataTable` | `WeightInSlot` — an actual drop **rate** — plus `StaticItemId`, `MinNum`/`MaxNum`, `TreasureBoxGrade`, `BonusExpRate` |
| `DT_TechnologyRecipeUnlock_Common` | `UnlockItemRecipes`, `UnlockBuildObjects`, `Cost`, `Tier`, `LevelCap`, `RequireTechnology`, `RequireDefeatTowerBoss` |
| `DT_ItemShopCreateData_Common` | 38 shops, each with `StaticItemID`, `OverridePrice`, `ProductNum`, `Stock` |
| `DT_FriendshipRankTable` | `RequiredPoint` — the thresholds this file called "**nothing**" |

### That refusal was too broad, and it was hiding the field boss levels

The paragraph this replaces said `DT_BossSpawnerLoactionData` refuses **by
design**, because a natively-serialised struct cannot be walked and "half a
tagged decode reads as real data — coordinates as name indices". The danger was
real. The response was too broad, and it cost **243 of the pak's 912
DataTables** — including that one.

A `StructProperty` tag carries its own **length**, so an unwalkable interior can
be skipped to land exactly on the next tag. Nothing is then read as the wrong
type, every surrounding field stays correctly placed, `read_table`'s
"walk must end at the buffer end" check still proves the row alignment, and the
interior is labelled `{"_opaque": "Vector 24B"}` rather than given a value.

The fear applied to *guessing*; it never applied to *skipping a measured length*.

**Decodable DataTables went from 656 to 899 of 912.** And what came out of the
one that named this rule:

    SpawnerID   yamijima_IceLand_pink_D_BOSS
    CharacterID BOSS_Horus_Water
    Location    {"_opaque": "Vector 24B"}
    Level       66

**89 placed field bosses with species and level**, levels 10–79 across 90
distinct species — which this file and the README both said was unavailable
("Level is not available… do not invent the rest"). It was available; the reader
was refusing the table that held it.

**The table has 159 rows and 69 of them are empty** (`CharacterID: "None"` —
unused spawner slots). Count the populated rows, not the row count. This was
briefly written up as "159 field bosses", which is the same class of error as the
`BP_LevelObject_TowerLockBarrier` mistake recorded above: a category whose size
disagrees with what the game has is wrong however plausible it looks.

**RAID BOSSES ARE NOT IN THIS TABLE AND SHOULD NOT BE EXPECTED IN IT.** Zero of
the 159 rows carry a `RAID_` id, and that is correct rather than a gap: the
bundled data has **19** `RAID_` species (Bellanoir is `NightLady`, Xenolord is
`KingBahamut_Dragon`, plus `DarkMechaDragon` and `LegendDeer`), and they are
**summoned at an altar rather than placed in the world** — so a table of
*locations* has nothing to say about them. The `_2` suffixes are the harder
variants (Bellanoir Libero). They need their own treatment; counting them as
field bosses, or reporting their absence here as missing data, would both be
wrong.

**The `Location` Vector is 24 bytes — three doubles — and the positions are now
VERIFIED, not merely plausible.** `scripts/extract-boss-spawners.py` bundles all
90 with species, level and world position:

| Cell size | Bosses landing on an occupied cell |
|---|---|
| **25,600** (measured) | **90 of 90** |
| 12,800 (control) | 22 of 90 |
| 51,200 (control) | 83 of 90 |

**Both controls doing worse is what makes 90/90 evidence** rather than a
coincidence — the identical test, with the identical controls, that pinned the
cell size against the 174 fast-travel points. A misread byte layout does not put
90 points inside the cells the game ships content for. The script *refuses* if
any position falls off the grid, and refuses again if a control ever matches as
well as the real size, because then the check is not discriminating and proves
nothing.

**The Vector decoder is deliberately local to that script rather than added to
`uassettable`.** That module's contract is tagged properties; a
natively-serialised struct is a different one, trustworthy only where something
checks it. Here the cell grid does. Elsewhere it would not, and a shared decoder
would carry the trust along with the bytes.

Still to check with this: **field boss levels** (AGENTS.md below says the pak was
checked; that check was the client pak) and the **element effectiveness chart**
the party optimiser needs.

## What the client pak does and does not unlock

`refs/Pal-Windows.pak` — 40.5 GB, 185,003 files. The extraction rule is the same
one `upackage.py` documents and it decides everything:

**Name tables are plainly serialised; property values are not.** Palworld's
packages are cooked with unversioned properties, so a DataTable's *rows* cannot
be decoded — but the strings it references can. Measured on four tables:

| Table | Names | What that gives |
|---|---:|---|
| `DT_ItemLotteryDataTable` | 9,542 | which items a drop table references |
| `DT_TechnologyRecipeUnlock_Common` | 2,258 | what each technology unlocks |
| `DT_FriendshipRankTable` | 15 | **nothing** — the thresholds are numbers |
| `DT_BossSpawnerLoactionData` | 344 | **nothing** — the coordinates are numbers |

So: *which things reference which things* is extractable (the habitat and
field-boss trick, 480 non-localised DataTables to mine). *Rates, thresholds,
coordinates and any other numeric column* are not, and no amount of effort here
changes that — those come from `refs/PalWorldSaveTools-main.zip`, which already
decoded them.

Also present and unused: **12 localisation languages** under `L10N/` (de, es, fr,
id, it, ko, pl, ru, th, tr, vi + en) with the game's own item, Pal, skill and
technology name tables — enough to speak a player's language using Pocketpair's
own strings rather than a translation of them. And 21,056 `.ubulk` files, which
is where every texture's pixel data lives.

**It does not unlock durability-record creation** — so "add an egg to a chest"
stays refused. That is a save-format question, not a game-data one; see the
`DynamicItemSaveData` section, where the copy count is now measured rather than
guessed at. `scripts/diff-dynamic-items.py` is the five-minute experiment that
would settle it: diff `Level.sav` before and after crafting **one** item in game,
and the number of records it added is the number the refusal is waiting for.

**Field boss levels are still unavailable, and the pak was checked.** The 94
`FBOSS` spawner packages carry exactly one species name each (`BOSS_PoseidonOrca`,
`BOSS_VioletFairy`) and **zero numeric entries** in their name tables. Level is a
numeric property in the unversioned block — the same wall as
`DT_FriendshipRankTable` and `DT_BossSpawnerLoactionData`. Name, artwork, rarity
and description remain what the data supports; do not invent the rest.

### Icon coverage, actually measured

| Section | Resolve | Note |
|---|---|---|
| items | 2,456 / 2,466 | the 10 are test entries and unreleased ammo |
| pals | 705 / 753 | 48 boss and variant forms have **no art anywhere**, client pak included |
| npcs | 372 / 372 | |
| technology | 0 / 588 | **not installed by design** — `install-icons.py` |
| structures | 3 / 1,088 | same; the map draws its own markers |

The last two are a deliberate 6.4 MB saving for icons nothing renders, and
`build-gamedata.py` now says so per section rather than printing one alarming
total that buries a real regression in items or Pals.

**Two Pal icons were broken on Linux only.** The reference data records
`T_Thunderdog_Ice_icon_normal.webp` for a file that ships as
`T_ThunderDog_Ice_icon_normal.webp`. It resolves on macOS and Windows and 404s in
the container — so it worked on every developer machine and failed only where it
mattered. `resolve_icons()` now fixes case at build time against what is actually
in `public/icons/`, and reports anything still unresolved rather than blanking it:
an empty path and a wrong one both render as no artwork, but only one of them
tells you a regeneration lost something.
