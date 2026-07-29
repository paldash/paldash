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
.venv/bin/python -m pytest                       # backend, everything (~140s)
.venv/bin/python -m pytest -m "not integration"  # backend unit only (~35s)
.venv/bin/python -m pytest -m "not slow"         # skip full-world parses
.venv/bin/python -m pytest backend/tests/test_safety.py -k read_only  # one test
npm test                                          # frontend (vitest)

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

Bundled `maxStack` values are wired into the sorter as of Phase 5. The merge
ceiling is `max(authoritative, observed)`, and the `max()` is the point: raising
a ceiling only ever packs items into fewer slots, while lowering one could
require *more* slots than a container already uses. So a stack larger than the
game's own cap — modded, or from an older version — is preserved rather than
split. `test_saveedit.py` pins all three cases.

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

Remaining gaps are catalogued in `docs/AUDIT.md` §5.

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

`thijsvanloef/palworld-server-docker` and `jammsen/docker-palworld-dedicated-server`
**regenerate PalWorldSettings.ini from environment variables on every start.** A
setting the dashboard writes survives until the next restart and is then
silently reverted — worse than a refusal, because the operator watched it work.

`settings_ini.ENV_MANAGED` lists the keys commonly backed that way and the
variable for each; the settings UI names them and points at `.env`. This cannot
be a *detection* — the dashboard container cannot read the game container's
environment — so it is worded as a warning, not a fact about the user's setup.

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
