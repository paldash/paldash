# Status

Snapshot: **2026-08-16**. Phases 0–9 complete, plus the post-phase work below.
Previous snapshot (2026-07-30) is in git history; its findings that are still
load-bearing are kept.

Verification for this snapshot is recorded in §6.

---

## 1. What works, end to end

### Reading the world
Parses Palworld 1.0 Oodle (`PlM`) saves that the usual Python tooling cannot
read at all. From the reference world: 1,907 Pals (2 in Dimensional Pal
Storage), 5 players, 11 bases, 5 guilds, 11,639 containers, 8.3 M items, 3,370
placed objects. From the live world it runs beside: 3,018 Pals, 16 bases.

Friendly names for everything — and since #69 they are **the game's own
strings** (client-pak L10N), not a third-party archive's: 1,805 item and 303
Paldeck descriptions, 140 region and 33 dungeon names, with `<itemName id=…/>`
rich-text references resolved and placeholders refused rather than leaked.
**16 languages** ship for the game's nouns (#107); dashboard chrome stays
English, which is the split the data supports (#109 records why).

### Not corrupting the world
The rule the whole project is built around: **never write unless the server is
provably stopped.** Four independent signals (REST API, TCP port, save-file
mtime, process scan) must agree, and anything ambiguous resolves to "running".
An HTTP 401 counts as running — something is listening.

Every mutation goes through `guarded_save_write`: re-check, full verified
backup, re-check, write, **re-read from disk**, verify, automatic rollback on
any mismatch. Sorting additionally proves conservation — every item total
identical before and after, checked twice.

### Editing (Phase 7 + later)
| Feature | Guarantee |
|---|---|
| Container sorting | Item totals conserved, verified after re-read; category order uses the game's own `typeA`/`sortId` |
| Slot editing | Target container matches plan, **every other container unchanged**; empty slots writable (#99) |
| Pal editor | Per-field bounds from game data; absent properties refused, not invented |
| Skills | Passives (≤4) and equipped moves (≤3), ids checked against the bundles |
| Work ranks | Editable to the game's own cap of 10, donor-copied shape (#53, #73) |
| Ownership history | Written as the save's own UUID type, shape-validated (#54) |
| Bulk Pal edits | All-or-nothing across the batch |
| Pal cloning | Both records created and paired; no other container may change length |
| Item creation | Durability record + slot written as one; eggs write `hatches` and verify the species on re-read (#38, #100) |
| Player editor | Spans two files; either mismatch rolls back the whole world |
| Guild move | Four structures updated atomically; solo-guild bases re-homed, never deleted |
| Import / export | Versioned, checksummed; `planHash` refuses a world that moved; prune-other-guilds option (#105, #120) |
| Coordinate teleport | Player `.sav` only; server must be stopped, verified on re-read |

### Understanding Pals (the post-phase layer)
- **Calculated stats** (`palstats`) with per-stat passive terms — 1,352 of the
  live world's 2,963 characters carry a stat-affecting passive (#72, #94).
- **Elemental resistances** (`palresist`): 311 of 1,905 Pals resist something;
  read from effect *types*, never prose, and never folded into an effective-HP
  figure no file states (#104).
- **Partner skills indexed by condenser rank** (#103) — why a condensed
  Direhowl is faster (0/10/12/15/20%) and a 4-star Silvegis shields harder.
- **Condenser → work suitability** (`condenser.py`, #74/#96): confirmed by five
  in-game readings, corroborated by the binary's own
  `GetWorkSuitabilityRankWithCharacterRank`; 262 of 343 species fully
  determined, the rest say `determined: false` rather than guessing.
- **Movement modes** (#110): `EPalMonsterMovementType` off the species
  blueprints — 30 Fly, 12 FlyAndLanding, 10 Swim — so "fastest rideable flyer"
  is answerable (Jetragon, 3,300) and the extractor refuses if its controls
  (Surfent/Surfent Terra, Jormuntide/Ignis) stop disagreeing.
- **Work/combat optimiser** scoped to the caller; matchups are badges, never
  sort keys, pinned on both sides of the wire.
- **Build planner** over the whole species table, with `raw` beside any
  multiplied figure and refusals carried in the payload
  (`condenserOnMovement: "viaPartnerSkill"`, `stackingKnown: false`).
- **Welfare** (#59), **drops** (#97), **habitats with level ranges** (478
  species, #48), **skins** (#41), **breeding planner** with gender-aware
  routes, obtainability classes, mutation passives (#75, #114) and the cakes'
  own table effects.

### The map
World objects: **59,396 across 13 categories** (ore, treasure, fishing, oil
rigs, Pal spawners, dungeons, NPCs, field bosses, skill fruits, junk, lotus,
collectibles, supply drops), viewport-culled, per-kind toggles, admin policy
per category. Plus 396 effigies with the GUIDs saves key on (game's own relic
icons, #70), 90 placed field bosses with levels (#55), 438 named NPCs, guild
markers (#83), live players, bases with radii, and discovery filtering
server-side. Two landmasses, two framings; Palpagos calibrated 157/157, World
Tree provisional and labelled.

### Progression & completion
Progression tab (#47): towers, field bosses (spawner-keyed, 89 not 90 — one
spawner has two levels), areas with a real denominator, effigies via the
non-legacy field (#101), milestone rewards named (#89, #115), NPC request
chains (#116) — the Pal-display half tracked, the item half honestly
`tracked: false`. Completion tracker (#67): every Pal, where to find it, how
to breed it.

### Access control
Seven roles, two independent gates (role capability ∩ security level), a route
allowlist that is not a prefix match, scrypt password hashing, server-side
revocable sessions, per-IP and per-username throttling, and an audit record on
every mutating action. `test_route_gates.py` enumerates the live app and
asserts both gates on every route.

### Server operations
Kick, ban, unban, announce, force-save, graceful shutdown and container
start/stop, all through `moderate.py` so **every one is audited** — including
failures. Metrics every 60 s, 30 days raw, host + game memory split (#76,
#77), gaps are data. Scheduled announcements consume missed windows. Settings
tab: all 119 keys editable, Pocketpair's own per-key help (#80), enum
dropdowns, INI write verification per key (#78) and the regeneration watch
(`iniwatch`) that measures whether this deployment's image rewrites the file.

### Reading the game's own files
Server pak: 473 DataTables catalogued (`docs/DATATABLES.md`), all 8 DataAssets
read (#113), Blueprint CDOs decode (347 tuning constants), world-cell actors
decode (NPCs, effigies, boss spawners), the binary is indexed (100,368
reflection identifiers, #111). Client pak: L10N for 16 languages, textures,
32 client-only tables — all cosmetic (#68). Three indexes exist so negatives
are checkable: `docs/datatables.json`, `docs/savefields.json`,
`docs/binsymbols.json`.

### Staying current with the game
`gameversion.py` reads the install's Steam `appmanifest` `buildid`; the banner
tells the operator "nothing for you to do" and hides maintainer commands
behind a toggle. `scripts/check-game-build.py --extract` diffs re-derived
positions against the bundles; `scripts/regenerate-bundles.py` is the runbook
as a script. **`docs/UPGRADING.md` is the ordered recipe**, and step 0 is
updating `refs/palworld` before diffing anything against it.

### Request cost
`viewcache` memoises per parse generation and per file stamp — and since
2026-08-16 **`per_file` keys on a caller-supplied name as well as the path**,
because two pairs of callers sharing a path was a production 500 (§6.1).

---

## 2. Numbers worth keeping

Every one of these is measured against real data, not quoted.

| Fact | Value | How it was established |
|---|---:|---|
| Streaming cell size | 25,600 | 174/174 fast-travel points land on an occupied cell |
| Effigies | 396 | Package export map; GUIDs verified against saves |
| Static world objects | **59,396** in 13 categories | Pak extraction; re-derived 2026-08-16 with zero diffs |
| Placed field bosses | 90 (89 spawners) | Positions 90/90 on the cell grid; both size controls worse |
| Movement-mode overrides | 31 files → 52 non-ground species | Two swimmer/land-variant pairs are the control |
| Pals working at a base | 165 of 1,905 | `WorkerDirector` join; 44/44 bases across four worlds |
| Tower bosses | 8 | Fast-travel names × client-pak localisation, two unrelated sources |
| Fast-travel points | 174 | Joins to saves 117/117 and four more players clean |
| Level cap | 80 | `CharacterMaxLevel` in the settings CDO — read, no longer community-sourced |
| Work-rank cap | 10 | `WorkSuitabilityMaxRank`, corroborated twice |
| Condenser cost | 4/8/12/24 | `CharacterRankUpRequiredNumMap` |
| Max equipped moves / passives | 3 / 4 | Never exceeded across 1,905 Pals |
| Pals above their EXP band | **0** of 1,905 | Why that rule is one-sided |
| 1.0 server settings | 119 | `DefaultPalWorldSettings.ini` |
| uid references a key-list remap misses | **1,836** | Counted against the reference world |
| Build output after tracing excludes | 73 MB | was 5.8 GB |
| GET routes returning non-500 in a full sweep | **100 of 100** | 2026-08-16, refworld and the live world, worst-case cache order |

### Endpoint latency, 3,018-Pal live world (TestClient, warm)

| Endpoint | Warm | Payload |
|---|---:|---:|
| `/api/pals` | 66 ms | 7.9 MB |
| `/api/optimise/work?work=…` (as the UI calls it) | 60–70 ms | 6 KB |
| `/api/optimise/work` (all 13 types — no UI caller) | ~645 ms | 109 KB |
| `/api/breeding/palbox` | 45 ms | 1.0 MB |
| `/api/mapobjects` | 25 ms | 2.4 MB |
| `/api/bases`, `/api/players`, `/api/items` | 3–5 ms | ≤165 KB |

Headroom: costs scale roughly linearly with Pal count; a 10,000-Pal world
projects to ~200 ms `/api/pals` and a ~26 MB payload — server-side fine, and
the payload is the thing to watch (verify the deployed Next server is sending
`Content-Encoding: gzip`; `compress` is on by default and not disabled).
Parse: ~10 s niced on the 55 MB reference world, ~13 s on the 73 MB live one,
in a separate worker at idle priority with the RAM handed back on exit.

### Corrections this project has made to its own claims
The full ledger lives in `AGENTS.md`; the pattern is recorded there as rules
(sample by variant, never by index; a sweep of one surface is not a search of
the game; enumerate by class, not by name). Highlights that changed shipped
behaviour: effigies 313→396, level cap 100→80, EXP one-sidedness,
`unknown_species` as advisory, field-boss levels "unavailable"→bundled,
"element variants never breed"→81 named pairings, "no `BP_Pal_*` in the
server pak"→1,831 blueprints under `BP_<Species>`.

---

## 3. Known gaps

**Blocking nothing, but worth knowing:**

- **World Tree orientation is unverified.** Extent exact, orientation needs one
  real point on that landmass; `fit-worldtree.py` is the recorded negative.
- **Non-Steam (Xbox/PS5/Mac) players are unverified** — task #33.
  `docs/CROSSPLAY.md` has what is known (now including the community-verified
  operational facts) and the three checks to run the day one joins.
- **Condenser vs movement ratio** — task #106; the instrument
  (`scripts/measure-speed.py`) polls the REST API and needs one observation.
- **Dungeon entrances are not extractable from the pak** — interiors are
  off-grid sub-levels; ~15 overworld entrances, the rest runtime-spawned.
- **Respawn timers have no positions.** The save knows *that* a node is
  respawning, the pak knows *where* nodes are, and no id connects them; the
  unblock is teaching `extract-world-objects.py` to capture instance GUIDs the
  way `extract-effigies.py` does (byte offsets differ per class — real work).
- **Player and technology imports are refused.** Container and Pal imports work.
- **`fieldBosses` and `areasFound` denominators** are honest unions where the
  game enumerates nothing.
- **Dashboard chrome is English** (#109) — localising it means sourcing
  translations, not a framework; `docs/TRANSLATING.md` + `docs/chrome-strings.json`
  are the contributor package (631 strings).
- **No 2FA or password-reset flow.**
- **S7 (CSRF tokens) and S11 (dependency scanning)** remain open in
  `docs/AUDIT.md` §5; mitigated rather than closed.
- **Multi-image Docker validation was done from image metadata, not runs.**

---

## 4. Things deliberately not built

- **Teleport over RCON** — the binary has no coordinate form and both admin
  teleports anchor to the issuing admin's character, which a headless
  dashboard does not have.
- **A mutation *rate*** anywhere in breeding — the model's constants are
  native and unreadable; the game's own quotes travel instead.
- **A day/night flag** — `PalWorldTime_GameStartHour = 5` is unverified as a
  seed or an offset, and a wrong "it is night" reads as a fact about the world.
- **`TransportItemDirector` on the Bases tab** — decoded, verified, and
  unlabelled; an (item, position) pair with no honest caption is not a feature.
- **A `mountMode` from speed-column patterns** — scored 13.5% precision
  against the game's own answer once that answer existed. The real
  `MovementType` shipped instead.

---

## 5. Open items

### Needs the operator
| # | Item |
|---|---|
| — | **World Tree**: build or open anything on that landmass to confirm orientation |
| 33 | **Non-Steam players**: surfaces itself when a console player joins |
| 106 | **Condenser vs movement**: one `measure-speed.py` run on a rank-skill-free Pal |

### Optional hardening, none gating a LAN deployment
| # | Item | Size |
|---|---|---|
| S7 | CSRF tokens (mitigated by `SameSite=Lax` + POST-only mutations) | small |
| S11 | `npm audit` + `pip-audit` in CI | small |
| — | Run-validate the jammsen image (metadata-validated today) | medium |
| 109 | Chrome translations, when a contributor provides one | medium |

---

## 6. Verification for this snapshot

Run on 2026-08-16 against the current tree:

| Check | Result |
|---|---|
| `pytest -m "not integration"` (backend unit) | **all passed, 0 failed** (~3 min) |
| `pytest` (full, incl. integration against a real world) | see §6.1 note |
| `npm test` (vitest) | **153 passed** |
| `npx tsc --noEmit` | clean |
| Full GET sweep, every route, real worlds | **100 requests, 0 failures, 0 skipped** |
| `check-game-build.py --extract` vs installed 24466863 | **13/13 categories, 59,396 objects, zero diffs** |
| `extract-game-settings.py --verify` | 2 known constants match; walk ends 41,416/41,420 |

### 6.1 Findings from this pass

**Two `viewcache.per_file` callers per path, and the cache keyed on the path
alone.** `crafting` and `itemsource` both cache an index built from
`economy.json.gz`; `buildplanner`'s species table shared `gamedata.json.gz`
with the item catalogue. Whichever endpoint ran first seeded the entry and the
other was handed a dict of the wrong shape — a 500 on every crafting tree once
the Items panel had loaded (the reported "sorting by attribute gives error
500"), and in the opposite order a *silently corrupt* item catalogue with a
200. Both passed every test that exercised one module at a time. `per_file`
now takes a required key, mirroring `per_files`, whose docstring had already
recorded this exact failure class. This also retro-explains the earlier
"crafting tree renders empty" reports: before the index restructure the
poisoned lookup returned `None` and the tree was empty rather than broken.

**`/api/backups` 500ed when the backup volume is unmounted or read-only** —
`PermissionError` escaping `os.makedirs`. It now returns `available: false`
with the reason and directory, and the tab says "check the volume is mounted"
— the ban-list rule: "we could not look" and "there are none" are different
answers.

**Provenance:** build 24466863 verified as a no-pak-change build (binary-only
update); server-pak-only artifacts stamped accordingly, mixed-source ones
keep their extraction stamp. `reference_totals.json` and
`server_defaults.json` gained the provenance entries they never had.

**Docs:** `docs/UPGRADING.md` written (the ordered update runbook — step 0 is
updating the reference install first); `docs/CROSSPLAY.md` extended with the
community-verified operational facts (Steam uid = low 32 bits of SteamID64;
console admin actions key on the in-game UID; `CrossplayPlatforms` +
`-publiclobby` + community-list requirements).
