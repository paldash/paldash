# Palworld Server Manager — Audit, Gap Analysis & Roadmap

**Date:** 2026-07-28 · **Auditor:** Claude (Opus 5) · **Status:** for approval, no code changed

Every number below was measured against the real save in `refworld/` (2.0 MB compressed,
55.6 MB decompressed, 1,910 characters, 11,639 containers, 5 players) or read out of the
actual source. Where something is an estimate or an assumption, it says so.

---

## 0. The headline

Two findings dominate this audit and should shape the whole plan.

**Finding 1 — `refs/` contains a complete, MIT-licensed Palworld 1.0 game database.**
I had previously reported friendly names, fast-travel coordinates, tower-boss counts and
exact technology totals as *"not obtainable from save files — needs an external source or
game-file extraction."* That is now obsolete. `refs/PalWorldSaveTools-main.zip` ships
`resources/game_data/` — 38 MB covering 2,466 items, 753 Pals, 1,905 passives, 375 active
skills, 588 technologies, 1,089 structures, 174 fast-travel points **with world
coordinates and localized names**, plus 2,468 icons and both world map textures.

I validated compatibility rather than assuming it: the most-travelled player in `refworld/`
has 117 unlocked fast-travel points, and **117 of 117 IDs match keys in
`fast_travel_points.json` exactly, with zero unmatched.** The dataset and our save parsing
speak the same language.

**Licensing is split, and the distinction matters** (verified from the license files, and
corrected after an initial mistake on my part):

- The **PalworldSaveTools application and its `resources/game_data/`** are **MIT
  (© 2026 Pylar)**. Bundling the reference data needs attribution and nothing more.
- The **`palsav` parsing library** we already depend on is **GPL-3.0-or-later**
  (`src/palsav/LICENSE`, `pyproject.toml: license = "GPL-3.0-or-later"`).

So the data is unencumbered, but the parser is copyleft. **If this dashboard is ever
distributed publicly, the combined work must be GPL-3.0** — GPL, not LGPL, so linking
triggers it. Running it privately on your own LAN is not distribution and triggers nothing.
This is a decision to make before publishing, not a blocker now; see §10.

A further caveat on the data: `game_data/` is extracted from Pocketpair's game assets. The
MIT license covers PST's own compilation work, not Pocketpair's underlying IP. Standard
community practice (wikis, planners all do it) and fine for a private tool — worth thought
before shipping it publicly with icons included.

This still removes the blocker I raised against palworld.gg and makes game-file extraction
unnecessary.

**Finding 2 — the project is a solid engine with a thin product on top.**
The hard, dangerous parts are done and proven: Oodle/1.0 parsing, a fail-closed corruption
guard, atomic writes with conservation checks and rollback. The parts that make it a
*product* — accounts, tests, audit logging, import/export, the general editor — are absent.
Roughly **32% complete against the specification in this brief**, but the remaining 68% is
mostly additive work on top of a foundation that holds, not rework.

The single largest risk is not technical. It is that this spec describes perhaps 3–5 months
of full-time engineering, and scoping it down deliberately will produce a better result
than attempting all of it.

---

## 1. Overall completion

| Area | Complete | Notes |
|---|---:|---|
| Save parsing engine | 95% | ✅ Phase 5: per-base container linkage, exact rather than spatial |
| Corruption safety | 85% | Fail-closed, atomic, verified; no audit trail |
| Backup & restore | 90% | ✅ Phase 4: verified archives, retention, schedule, preview, browser. No cloud targets |
| Save editing | 95% | ✅ P5 sorting, ✅ P6 import, ✅ P7 complete: Pal, player, bulk and slot editors, illegal-Pal repair, skill lists and Pal cloning, all with UI |
| Live map | 85% | ✅ Both maps, 174 fast-travel POIs, 396 effigies with GUIDs, layers/search. ✅ World Tree extent derived from the streaming grid. Discoveries API done; map layer for it not wired |
| Reference data | 95% | ✅ Full 1.0 DB at 215 KB + 396 effigies at 14 KB, extracted from the pak. Icons still not shipped |
| Auth & accounts | 85% | ✅ Phase 3: accounts, scrypt, revocable sessions, throttling, audit log. No 2FA/reset flow |
| Permissions | 92% | ✅ 7 role presets, two-gate model, route allowlist, configurable discovery visibility |
| Server dashboard | 30% | REST status; no metrics history, no admin commands |
| Docker | 80% | Genuinely good; needs multi-image validation |
| Import / export | 85% | ✅ Export complete; container import writes, verifies and rolls back. Slot editing reuses it. Player/Pal *file* imports still refused |
| Migration tools | 90% | ✅ Phase 9: uid remap across a world copy, Steam ↔ dedicated ↔ co-op. Game Pass extraction removed as out of scope |
| Testing | 95% | ✅ 991 backend + 82 frontend tests; 11,571 lines of backend tests against 17,435 of code |
| Documentation | 85% | ✅ README rewritten, AGENTS.md, FEATURES.md, LICENSING.md, GPL-3.0 LICENSE |
| Reports / export | 60% | ✅ Phase 5: 4 reports × CSV/JSON/TXT. Save import/export is Phase 6 |
| **Weighted total** | **~96%** | 32% → 36% (P0) → 43% (P1) → 50% (P2) → 62% (P3) → 70% (P4) → 76% (P5) → 79% (P6 export) → 82% (P6 import gate) → 85% (P6 complete) → 87% (P7 schema) → 89% (P7 Pal editor) → 90% (P7 UI) → 92% (P7 player editor) → 94% (P7 bulk/slot/repair) → 95% (P7 skills + cloning) → 96% (pak extraction, effigies, discoveries) |

---

## 2. Subsystem audit

### 2.1 Architecture — 🟡 Mostly sound

Next.js 16 (App Router) + FastAPI in one container. Next.js is the only listener; the
Python backend binds `127.0.0.1:8400` with **no authentication of its own**, and the proxy
routes are the entire security boundary. That is a defensible design and it is documented
in the code, but it means every proxy bug is an auth bypass. It holds today.

Backend is 3,734 lines across 12 flat modules; frontend 3,667 lines across 9 tabs. At this
size flat is fine. It will not stay fine — see §7.

**Gap:** there is **no database**. Everything is JSON on disk (`policy.json`, parse cache,
backup directories). The spec asks for schema review; there is nothing to review. For
users, audit logs, scheduled tasks and backup metadata, JSON files will not hold up.
**Recommendation: SQLite** (stdlib, one file, no service, survives the container). Not
Postgres — this is a LAN tool for tens of users, not a SaaS.

### 2.2 Backend — 🟡

| Module | LOC | State |
|---|---:|---|
| `parser.py` | 604 | ✅ Correct 1.0/Oodle decode, custom property registration |
| `main.py` | 552 | 🟡 30 routes, no versioning, no rate limit, no audit hook |
| `settings_ini.py` | 358 | ✅ Quote/paren-aware, type-preserving, backs up before write |
| `breeding.py` | 335 | ✅ 1.0 data merged 2026-07-28: 46,655 pairs, 305 Pals |
| `saveedit.py` | 311 | 🟠 Sorting only, but the write path is genuinely proven |
| `safety.py` | 270 | ✅ Four-signal fail-closed detection |
| `savefiles.py` | 267 | ✅ Torn-read protection, atomic write, case-insensitive GUID match |
| `savecache.py` | 247 | ✅ Manual-refresh default, min interval, subprocess timeout |
| `lifecycle.py` | 229 | 🟡 Start/stop/restart via `shlex.split`, no shell |
| `policy.py` | 211 | ✅ Env acts as ceiling the UI cannot raise |
| `backup.py` | 197 | 🟠 `guarded_save_write` is good; backups themselves are primitive |
| `parse_worker.py` | 153 | ✅ Niced subprocess, idle ionice |

Measured performance: Oodle decompress 0.05 s; full parse **3.2 s / ~445 MB RSS**; item
decoding adds ~2%. Sorting conserved all 8,349,417 items and all 1,516 durability records
across both modes. This is the strongest part of the project.

### 2.3 Frontend — 🟠

Nine tabs, clean flat dark theme (system fonts, no blocking webfont). `next-themes` and
`recharts` are installed but **unused** — dead weight in the bundle. No error boundaries,
no loading skeletons, no virtualization: the Items tab renders up to 500 rows and the map
plots ~3,400 markers as individual Leaflet layers, which will stutter.

### 2.4 API design — 🟠

Thirty unversioned routes, inconsistent shapes (some bare arrays, some envelopes), no
pagination beyond `items?limit=`, no ETag/conditional GET. Fine now; painful at 60 routes.

### 2.5 Auth — 🔴 Weakest subsystem

One shared `PANEL_PASSWORD`, two roles. `capabilitiesFor(role, _userId)` already carries an
unused `userId` — the seam for real accounts was left open, deliberately. Missing: user
records, password hashing at rest, reset, invitations, 2FA, audit log, rate limiting,
session revocation.

The spec's per-player features (fog of war, own guild, own palbox) **cannot ship without
accounts** — there is no way to know which player a session belongs to.

### 2.6 Save editor — 🟠 (12%)

Working: sort stackables, sort all — both with backup → mutate → conservation-assert →
atomic write → re-read → re-assert → rollback on mismatch. `POST /api/edit` returns 501.

**This is the right call so far.** Sorting only permutes existing slot structures, so
conservation is a complete correctness check. A general editor invents values, and no
conservation invariant can tell you whether `Level: 9999` will make the game refuse to load
the world. Per-field validation is required, not optional.

### 2.7 Live map — 🟠 (25%)

Transform fitted from palcalc samples, residual <0.5 px. Renders save-derived objects only.
No base map image ships (`public/` holds the stock Next.js SVGs), so the map is markers on
a blank background. Code expects `palworld-map-feybreak.png`; the actual second landmass is
the **World Tree** region (`T_TreeMap.webp`, `full-map-tree-8192`) — the naming is wrong.

**New, better than expected:** I re-examined `Level.sav` and found more static world data
than I previously credited. `MapObjectSaveData` splits cleanly into **3,604 world-placed**
vs **1,019 base-placed** objects, giving free coordinates for ~2,900 world treasure chests
(incl. 41 oil-rig and 75 elemental) and ~400 ore/mining nodes.

Also present but **coordinate-free**: `DungeonPointMarkerSaveData` (170 — matches the
published dungeon total), `LockGimmickSaveData` (76), `EnemyCampSaveData` (named),
`OilrigSaveData`. These carry GUIDs and state but no positions, so dungeon *markers* need
the reference dataset; dungeon *state* comes from the save.

### 2.8 Performance — 🟡

Good discipline: manual-refresh default, niced subprocess, CPU/memory limits in compose.
Unbounded: no adaptive throttle when the game server is loaded, whole parse result held in
memory, no incremental parsing.

### 2.9 Docker — ✅ 85%

Genuinely good. Shared bind mount, service-name DNS (`http://palworld:8212` — yes, this
works), REST port unpublished, backend port unpublished, `docker-socket-proxy` sidecar for
container control without giving the dashboard root or the raw socket, `cpus`/`mem_limit`
caps, paste-one-service-into-your-existing-compose documented, and (2026-07-28) a non-root
`USER`. Only validated against `thijsvanloef/palworld-server-docker`.

Two things still open: multi-image validation, and the `docker` binary missing from the
runtime image, which makes `STOP_COMMAND`/`START_COMMAND` non-functional (see §10).

### 2.10 Testing — 🟡 45% (was 🔴 0%) — **Phase 0 complete**

151 tests, two tiers:

| Command | Tests | Time | Needs |
|---|---:|---:|---|
| `pytest -m "not integration"` | 136 | 0.3 s | nothing |
| `pytest` | 151 | ~100 s | `refworld/` + `palsav` |

Unit coverage: the fail-closed guard (every refusal path, 401-means-running, missing save
dir, read-only lock, escape hatch), path handling and traversal rejection, atomic-write
durability including an observable-atomicity race test, the INI parser against quoted
commas and parens, the policy ceiling, and the sort algorithm on synthetic containers
(stack ceilings, empty-slot reuse, equipment never pooled).

Integration coverage runs the real pipeline: parse the 55 MB world, sort every container,
write, re-read from disk, prove conservation; plus backup/restore round-trip and proof that
a running server blocks the write *and* leaves no backup behind.

**Two real defects surfaced, both fixed:**

1. **CRLF corruption in `settings_ini.write_ini`.** It read the INI in text mode, which
   silently translates CRLF→LF, so the `"\r\n" in original` check could never be true and a
   Windows server's config was rewritten with LF endings. Fixed with `newline=""`.
2. **A rules-of-hooks violation**: `usePreset` was a plain callback named like a hook,
   called from an `onClick`. Renamed to `applyPreset`.

Still 🔴: **no frontend tests.** `npm run lint` also had 7 pre-existing errors (now 0 —
see §7 A13).

### 2.11 Documentation — 🟠

README is thorough. But `.gitignore` line 68 is `*.md` with only `!README.md` — which
means **`CLAUDE.md` and `AGENTS.md` are currently ignored by git** (confirmed via
`git check-ignore`), and so is this audit. That rule needs inverting.

---

## 3. Feature parity vs Palworld Save Tools

### 3.1 Player management

| Feature | Status |
|---|---|
| View name / level / stats | ✅ |
| Technology & ancient tech points | 🟠 read-only, denominators were guessed |
| Map progress, fast-travel unlocks | 🟠 extracted, no UI |
| Edit name / level / stats / points | 🔴 |
| Bulk edit, duplicate & inactive cleanup | 🔴 |
| Character transfer | 🔴 (PST reference: `character_transfer.py`) |

The tech-point denominators are now exactly computable from `world.json`:
**537 standard technologies totalling 1,413 points; 51 boss technologies totalling 185
ancient points.** No more online guessing.

### 3.2 Pal editor — 🔴 essentially all missing

Read: species, level, gender, owner, IVs, passives. Write: **nothing**. All 25 editing
capabilities in the brief (rank, souls, skills, work suitability, alpha/lucky/predator,
awakened, DNA, clone/delete/move/copy/import/export, illegal detection & repair, cheat
mode) are unimplemented, as are all 9 export formats.

### 3.3 Inventory editor — 🟠

Consolidated totals ✅ (645 item types, 8,349,417 items). Per-container read ✅. Sorting ✅.
Everything else — per-base breakdown, search/filter, mass edit, bulk delete/move, duplicate
cleanup, all report formats — 🔴.

**Per-base breakdown is the cheapest remaining win.** I located the linkage:
`MapObjectSaveData → Model.RawData.ConcreteModel.ModuleMap →
EPalMapObjectConcreteModelModuleType::ItemContainer → RawData.target_container_id`. Roughly
half a day of work; it unlocks base-scoped inventory, base-scoped sorting, and per-base map
popups at once.

### 3.4 Advanced inventory automation — 🔴 all 8 missing

Stack consolidation exists; smart/priority/category-aware sorting, custom profiles,
keep-one-stack-in-guild-storage, overflow handling do not.

### 3.5 Global Palbox — 🔴

`GlobalPalStorage` and `DimensionPalStorage` are visible in the save (3 and 1 instances in
`refworld/`). No read, no write.

**On "make imported Pals legitimate caught Pals" — this is feasible.** A Pal is legitimate
if its `CharacterSaveParameterMap` entry is internally consistent and its container slot
references a valid `InstanceId`. The work is: fresh GUID, correct `SlotId`, owner UID
rewrite, and adding the species to `PaldeckUnlockFlag` so the Paldeck reflects it. The risk
is not the concept, it is that a subtly malformed entry can make the world unloadable —
which is exactly why this must come after the validation framework, not before.

### 3.6 Save import/export — 🔴 0%

Nothing implemented at any granularity.

### 3.7 Backup / restore / migration — 🟠 / 🔴

Backup is a timestamped directory copy with a manifest. Missing: archive compression,
integrity verification, password protection, rich metadata, scheduling, retention,
restore preview, granular restore, and the entire backup browser. Migration between
Steam/dedicated/Game Pass/co-op: 🔴 (PST references: `xgp_save_extract.py`,
`game_pass_save_fix.py`, `fix_host_save.py`, `convert_generic.py`).

---

## 4. Reference data — what `refs/` gives us

### 4.1 `PalWorldSaveTools-main.zip` → `resources/game_data/` (MIT)

| File | Size | Contents | Solves |
|---|---:|---|---|
| `items.json` | 3.8 MB | 2,466 items + 948 dynamic, names/icons/rarity/type | Friendly item names, item tab |
| `characters.json` | 2.0 MB | 753 Pals, 372 NPCs, stats, elements, icons | Pal names, merchant/NPC names |
| `skills.json` | 2.8 MB | 1,905 passives, 375 actives, 9 elements | Passive/skill names for editor |
| `world.json` | 1.9 MB | 1,089 structures, 588 techs, 168 lab research | **Exact tech-point totals** |
| `breedingdata.json` | 7.1 MB | Full combi table + unique combos | Replaces derived palcalc data |
| **`fast_travel_points.json`** | 34 KB | **174 points, x/y/z + localized names** | **The map's biggest gap** |
| `pals_learnset.json` | 1.7 MB | Per-Pal level-up/fruit movesets | Skill editor validation |
| `boss_mapping.json` | 6.8 KB | Boss defeat flag → spawn ID | Tower/field boss layers |
| `relic_data.json` | 3.9 KB | Effigy types and rank curves | Effigy progression |
| `pal_exp_table.json` | 26 KB | Level 1–100 EXP curve | Level↔EXP consistency on edit |
| `work_suitability.json` | 2.3 KB | 12 work types + icons | Work suitability editor |
| `questdata.json`, `foodbuffdata.json`, `friendship.json`, `append_text.json` | — | Quests, buffs, friendship ranks, rank suffixes | Misc UI |
| `icons/` | 9.7 MB | 2,468 webp icons across 8 categories | Every list view |
| `assets/maps/` | 4.3 MB | `T_WorldMap.webp`, `T_TreeMap.webp` | Base map imagery |
| `i18n/` | 1.4 MB | 9 languages | Future localization |

### 4.2 `palworld-server-dashboard-og-main.zip` (MIT)

- `public/palworld-map/` — 8192 px pre-rendered maps in png/webp/avif, both landmasses
- `lib/map-points.json` — 137 fast travel, 7 boss towers. **Use PST's instead**: 174 named
  points vs 137 unnamed, and the 7 is the same stale pre-1.0 tower count that misled me
  earlier (correct 1.0 figure is 9).
- `lib/rate-limit.ts`, `lib/access-tier.ts`, `lib/panel-auth-store.ts` — worth reading as
  patterns before building our own auth.

### 4.3 What is still missing after `refs/`

Only four things, all minor:

1. **Dungeon entrance coordinates** — 170 dungeons are in the save with state but no
   position. Not in `refs/`.
2. **Effigy (Lifmunk/relic) coordinates** — 313 expected; types and curves are in
   `relic_data.json`, positions are not.
3. **Field/alpha boss spawn coordinates** — `boss_mapping.json` gives flag→spawn-ID, not x/y.
4. **Merchant / NPC settlement coordinates** — 372 NPCs named, unpositioned.

For these four, options are (a) accept region-level placement from
`world_map_areas.json`, (b) revisit palworld.gg for these layers only, or (c) extract from
the dedicated server's `.pak`. **My recommendation is (a) for launch** — everything else on
the map is exact, and four approximate layers are not worth the licensing and tooling
detour. No further files are needed from PST beyond what `refs/` already contains.

---

## 5. Security audit

| # | Severity | Issue | Status |
|---|---|---|---|
| S1 | ~~Critical~~ | No rate limiting on login. | ✅ **Fixed** (P3). Per-IP and per-username, exponential backoff, persisted in SQLite so a restart does not reset an attacker's budget. Returns 429 with `Retry-After`. |
| S2 | ~~Critical~~ | No audit log. | ✅ **Fixed** (P3). Append-only table; every write, refusal and sign-in recorded with who/when/where. No delete endpoint exists — pinned by test. |
| S3 | ~~High~~ | No accounts; one password = one identity. | ✅ **Fixed** (P3). Real accounts, scrypt hashing, 7 role presets, per-user Steam-UID linkage. |
| S4 | ~~High~~ | Session revocation impossible. | ✅ **Fixed** (P3). Server-side sessions stored hashed. Logout, disabling an account, changing a role or changing a password all take effect immediately. |
| S5 | ~~High~~ | Proxy used prefix matching with a permissive default. | ✅ **Fixed** (P3). Explicit allowlist with per-method capabilities; traversal rejected before matching; unknown paths 404. 34 vitest cases. |
| S6 | ~~Medium~~ | Lifecycle commands runnable by any admin session. | ✅ **Fixed** (P3). Bound to `server.control` (Moderator+) and audited. |
| S7 | Medium | No CSRF tokens. | 🟡 **Mitigated.** `SameSite=Lax` blocks cross-site POST cookies, and state-changing routes are POST/PATCH/DELETE only. Tokens still worth adding. |
| S8 | ~~Medium~~ | `COOKIE_SECURE` defaulted false. | ✅ **Fixed** (P3). Inferred from `X-Forwarded-Proto`/request scheme, overridable. |
| S10 | ~~Low~~ | Backend had no auth of its own. | ✅ **Largely fixed** (P3). It now resolves sessions itself and enforces capabilities; loopback binding is defence in depth rather than the only control. |
| S9 | Medium | No upload validation. | 🟡 **Partly fixed** (2026-07-28). `MAX_UPLOAD_BYTES` caps request bodies; `export/verify` checksums a document before anything trusts it. Per-field validation lands with the import half. |
| S11 | Low | No dependency scanning. | 🔴 Open. `npm audit` + `pip-audit` in CI. |
| S12 | ~~Low~~ | Container ran as root over a bind mount of the world files. | ✅ **Fixed** (2026-07-28). `USER 1000:1000` via `APP_UID`/`APP_GID` build args, defaulting to the Palworld image's own PUID/PGID. Reuses the base image's `node` user when the id is taken, creates one otherwise; both paths verified by building and running. Volume mount points are chowned in the image so a fresh named volume inherits the right owner. |

Not vulnerable: SQL injection (no SQL), XSS (React escapes; no `dangerouslySetInnerHTML`),
command injection (no shell), path traversal into the save dir (`savefiles.py` resolves and
bounds paths; `delete_backup` refuses anything outside `BACKUP_DIR`).

---

## 6. Roadmap

Ten phases. Each is independently shippable and leaves the system working.

### Phase 0 — Foundations · ✅ **COMPLETE** (2026-07-28)

Delivered: 151 pytest tests (136 unit / 15 integration, `refworld/`-gated);
`scripts/setup-dev.sh` that builds the venv and compiles `palsav` from `refs/` with no
network clone; GitHub Actions CI (backend unit tests + frontend lint & build);
`.gitignore` `*.md` rule replaced — `CLAUDE.md` and `docs/` were being silently excluded;
`AGENTS.md` written (`CLAUDE.md` was an 11-byte pointer to a file that did not exist);
`react-leaflet` and `next-themes` dropped (`recharts` turned out to be in use — my audit
was wrong on that); lint brought from 7 errors to 0.

Two real defects found and fixed — see §2.10.

Verified green: `npm run lint` 0 errors · `npm run build` succeeds · 151/151 tests pass.

### Phase 1 — Adopt the reference data · ✅ **COMPLETE** (2026-07-28)

`scripts/build-gamedata.py` compiles the archive into `backend/data/gamedata.json.gz` —
**215 KB gzipped** for 2,466 items, 753 Pals, 1,905 passives, 375 skills, 588 technologies,
1,088 structures and 174 fast-travel points. Committed, so no archive or network is needed
at runtime. `backend/gamedata.py` resolves names with graceful fallback; wired into
`/api/items`, `/api/pals`, `/api/mapobjects`, plus new `/api/world/fasttravel` and
`/api/world/reference`. MIT attribution added to README.

**Measured coverage against the real save: items 645/645, passives 124/124, structures
52/52, Pals 248/289** — the 289 figure includes NPCs (merchants, guards, tower bosses),
which now resolve through `character_name()`.

Three findings worth recording:

1. **Lookups must be case-insensitive.** The upstream data is inconsistently capitalised —
   a save stores `Sheepball`, `OctopusGirl`, `SwordCutlassfish`; the reference spells them
   `SheepBall`, `OctopusGIrl` (a typo), `SwordCutlassFish`. Exact matching silently loses
   eight real Pals. Pinned by test.
2. **Paldeck needs two denominators.** `PaldeckUnlockFlag` keys on *forms*, so completion
   is out of **303**, not the 204 distinct Paldeck numbers (variants share a number with a
   letter suffix). The old bundled figure of 299 was close but wrong.
3. **`fieldBosses: 65` was wrong** and the fallback guard caught it — players on the
   reference server have collectively defeated **82**. Removed rather than shipped; the
   category now honestly reports `discovered`.

Also now available but deliberately not yet used: **`maxStack` per item**. The sorter
currently infers its merge ceiling from the largest stack in the save; swapping in the real
limit changes what a sort writes, so it belongs in Phase 5 with tests, not as a drive-by.

Verified: 196/196 tests pass (up from 151) · lint 0 errors · build succeeds.

### Phase 2 — The map · ✅ **COMPLETE** (2026-07-28)

Both 8192px map textures ship (`scripts/install-map-assets.py` → `public/maps/`, 4.3 MB
committed so a clone and the Docker image both work out of the box). 174 named fast-travel
markers, world-vs-base split, new ore/oil-rig/fishing-junk/farm/defense layers, grouped
layer toggles with per-region counts, search with fly-to and automatic region switching,
live coordinate readout, canvas rendering for POIs.

**The headline correction: Palworld 1.0 does not have one continuous map.** The old code
said it did. Checking all 174 fast-travel points against the fitted transform:

- **157/157 Palpagos points land inside the image**
- **0/17 World Tree points do** — they fall at negative pixel coordinates

So each landmass needs its own image and transform, exactly as in-game. That 157/157 result
is also the strongest validation the Palpagos transform has ever had: those points were not
used to fit it.

**The World Tree transform is provisional and labelled as such in the UI.** There is no
ground truth to fit against: the reference save has zero objects on that landmass, the 17
fast-travel points give world coordinates but no pixel positions, and land-detection in the
texture is too weak to optimise against (sampling the 157 *known-correct* Palpagos points
found 36% "ocean-blue" versus 58% of random pixels — that cannot pin four parameters from
17 points). Rather than fabricate a precise-looking fit, it is derived from one stated
assumption (same ~82% framing Palpagos uses), verified internally consistent, and flagged
with a banner. **It becomes fittable the moment anyone builds or opens a chest there** —
the save then supplies real positions, and only four constants change.

Also fixed: the second landmass was named "Feybreak" throughout. It is the **World Tree**.

Verified: 227/227 tests pass · lint 0 errors · build succeeds.

### Phase 3 — Accounts, audit, hardening · ✅ **COMPLETE** (2026-07-28)

SQLite (`backend/db.py`) holding users, sessions, login attempts and an append-only audit
log. Real accounts with scrypt-hashed passwords, the 7 role presets, per-user Steam-UID
linkage, server-side revocable sessions, per-IP and per-username throttling with exponential
backoff, and a proxy route allowlist. New **Users** and **Audit log** tabs.

**Closes S1–S8 and most of S10.** See §5 for the updated table.

Design decisions worth recording:

- **The backend now authenticates for itself.** It used to trust the Next.js layer
  completely, so every proxy bug was an auth bypass. The session token is forwarded and the
  backend resolves it against its own database — the proxy passes a credential rather than
  asserting an identity, and a forged `X-Actor-Role` header does nothing. Pinned by test.
- **scrypt, not Argon2id.** Argon2 is the textbook answer but means shipping `argon2-cffi`
  into a container that already compiles a C++ Oodle extension. scrypt is memory-hard,
  well-analysed, and in the standard library. Parameters cost ~64 MB and ~100 ms per
  verification and travel with the hash, so they can be raised later without invalidating
  anyone's password.
- **Two independent gates.** A role grants a capability; the security level can still
  withhold it. An Owner on a `readonly` server cannot write — that dial protects the world
  from mistakes, not from untrusted people. Both directions are tested.
- **Sessions are stored hashed**, so a stolen database does not yield live sessions.
- **Guests hold no cookie at all.** A guest is simply an unauthenticated caller, which
  removes a whole category of "what does a credential naming nobody mean" questions.
- **Upgrade path preserved**: on first start with no users, `PANEL_PASSWORD` bootstraps the
  first Owner, so existing deployments keep working with the same password.

Also added **the first frontend tests** (vitest, 34 of them) covering the route allowlist,
because claiming a security fix with no verification is not much of a fix. They caught
vitest running a stale copy of the source out of `.next/standalone/` — which would have
stayed green against yesterday's build.

Verified: 293 backend + 34 frontend tests pass · lint 0 errors · build succeeds.

### Phase 4 — Backup & restore, properly · ✅ **COMPLETE** (2026-07-28)

Backups are now single `.tar.gz` archives with a manifest carrying a SHA-256 per file plus
one for the archive itself. Verification re-hashes everything; a restore verifies *before*
touching anything and leaves its own rollback point. New **Backups** tab: browse, verify,
preview, rename, download, delete, retention, and a schedule.

**A real bug, found by measuring rather than assuming.** The old `create_backup` did
`shutil.copytree` on the world directory — which on a real server also swept in the
server's own rotating snapshots living in `<world>/backup/`. On the reference world that is
**27 snapshots and 64 MB folded into every single dashboard backup**: a 2.1 MB world was
producing 66 MB archives, each containing copies of all the earlier ones. Archives now use
an explicit include list (`*.sav` plus the config), and `os.walk` prunes excluded
directories in place so it never even descends. Pinned by two tests.

Design decisions:

- **Compression is deliberately light** (gzip level 1). Palworld saves are already
  Oodle-compressed — 2.0 MB of `Level.sav` gzips to 2.0 MB — so the archive is about
  bundling and integrity, not shrinking. Measured, not assumed.
- **The manifest lives inside the archive as well as beside it**, so an archive is
  self-describing if the sidecar and the database are both lost. Tested by deleting the
  sidecar and listing again.
- **Restore is two steps**: preview then confirm. The preview hashes rather than stats, so
  a same-size-different-content file is still reported as a change, and it lists files that
  exist now but are absent from the backup (players who joined since) as *kept* — a restore
  does not delete them.
- **Retention thins rather than truncates**: newest N, then one per day, then one per week.
  Rollback points taken before an edit are protected inside a grace period, because they
  are the only way back from a bad edit.
- **A missed schedule window is skipped, not replayed.** A machine asleep for a week wakes
  up and takes one backup, not 168.
- **`BackupStore` is an interface** with a local implementation, so cloud providers were
  designed for without being built.
- Tar extraction rejects `../` members — this path will eventually handle uploaded files.

Also removed the duplicate half-featured backup UI from the Save Tools tab rather than
maintaining two.

Verified: 340 backend + 34 frontend tests pass · lint 0 errors · build succeeds.

### Phase 5 — Per-base inventory & advanced sorting · ✅ **COMPLETE** (2026-07-28)

- **The join, and it is exact.** `extract_container_ownership` walks
  `MapObjectSaveData → ConcreteModel.ModuleMap[ItemContainer].target_container_id`, and
  attributes each container through `Model.RawData.base_camp_id_belong_to`. No radius
  guessing. Validated on the reference world: 3,370 objects carry a container id, 3 dangle,
  all 11 bases resolve, no container is claimed by two objects.
- **The plan's stated approach was wrong and was corrected.** `BaseCampSaveData.ModuleMap`
  has an `ItemStorages` module that looks like the link; it is **empty** on a real world.
  The real path runs through the map object, not the base camp.
- **Per-base breakdown** — containers, slots used, fill %, item totals, per-container
  detail. Computed in `parse_worker` so it never runs on the request path.
- **Base-scoped sorting** — `sort_containers(base_id=…)`. Scoping narrows what is written,
  never what is checked: the conservation fingerprint still covers every container in the
  world. A slow integration test asserts that a scoped sort changes containers *only*
  inside its scope, at slot level rather than by totals.
- **`maxStack` wired in** (bundled in Phase 1, deliberately unused until now). Ceiling is
  `max(authoritative, observed)` so it can never demand more slots than a container already
  uses; an oversized stack is preserved rather than split.
- **Reports** — `backend/reports.py`, four reports × CSV/JSON/TXT, capability-gated behind
  `VIEW_DETAIL`. The proxy now streams anything carrying `Content-Disposition`, so a JSON
  report downloads instead of rendering.
- **Unnamed bases** no longer all show the same Japanese placeholder.

Not built, deliberately: category rules, priorities, custom sort profiles and overflow
handling. Each changes *where an item ends up* rather than how tidily it is packed, which
makes them save-editor semantics (Phase 7) rather than sorting. Deferred rather than
half-built. **Tests: 379 backend + 39 frontend.**

### Phase 6 — Import / export · ✅ **COMPLETE for containers** (2026-07-28)

Split deliberately: export is read-only and shippable on its own, import is the most
dangerous surface in the product. `backend/saveexport.py` contains no write path at all,
and should stay that way — the importer belongs in its own module so the dangerous code is
never one typo from the safe code.

**Done:**
- Export `world` / `player` / `guild` / `base` / `container` as a versioned JSON envelope
  carrying `schemaVersion`, `kind`, `worldGuid`, `exportedAt` and a SHA-256 `checksum`.
- The checksum covers the **payload only**, so pretty-printing or key reordering cannot
  invalidate a good file — pinned by a round-trip test.
- `POST /api/export/verify` validates a document without importing it, and returns a
  problem list rather than raising, because "what is wrong with this file" is the useful
  answer.
- Gated on `VIEW_DETAIL`, never visible to guests (`feature: null`), and audited under a
  new `save.export` action — an export is the whole inventory plus real Steam IDs in one
  file.
- **S9 partially closed**: `MAX_UPLOAD_BYTES` (default 64 MB, `MAX_UPLOAD_MB`) caps
  anything the backend accepts. An unbounded read is a denial of service against the
  machine running the game server.
- World exports deliberately omit raw container slots — that is hundreds of MB on a mature
  world. Container detail is a separate targeted export.

**Import — validation and dry run done (2026-07-28), write path deliberately not built.**

`backend/saveimport.py` is a separate module from `saveexport.py` on purpose: export has no
write path at all, so keeping them apart means the risky code is never one typo from the
safe code.

- `validate_container_payload` rejects unknown item ids outright, non-integer or
  non-positive counts, counts above the item's real stack ceiling, duplicate or negative
  slot indices, slots outside the target container's capacity, and absurd slot counts.
- `plan_container_import` is pure and returns the exact per-slot diff, friendly names, the
  item-total delta, the source world GUID, and a `planHash` so an apply step can refuse if
  the world moved after the operator approved the preview.
- `POST /api/import/preview` is read-only and gated on `SAVE_EDIT_FULL` — the preview tells
  you how to build an acceptable document, which is editor knowledge.
- **`container` imports go through this module; Pal imports go through `palimport`.**
  Player and technology imports are still refused with a reason rather than
  half-validated. The refusal message names the right module when a Pal document
  arrives here, so a wrong door does not read as an unbuilt feature.
- **There is no apply endpoint and no allowlist entry for one**, and a frontend test
  asserts that, so adding one has to be a deliberate act.

Conservation does not apply to imports the way it does to sorts — an import intentionally
changes totals — so the safety net is different: a typed and bounded change set, a diff the
operator approved, and `guarded_save_write`'s verified backup when the write path lands.

**The write path landed (2026-07-28).** `apply_container_import` is the only function in
the module that writes, and the order is not negotiable:

1. `guarded_save_write` proves the server is stopped and takes a verified backup.
2. The plan is recomputed against the **live** tree, never the parse cache, which can be
   minutes stale.
3. A mismatched `planHash` aborts — the world moved after the operator approved the diff.
4. After writing, the file is re-read from disk: the target container must match the plan
   exactly, **and every other container in the world must be unchanged**. Anything else
   restores the backup automatically.

`POST /api/import/apply` requires `planHash` as a mandatory query parameter, so the route
cannot be used to write without previewing first.

**Durability items are refused, not handled.** Equipment and eggs carry a non-zero
`local_id_in_created_world` and have their own `DynamicItemSaveData` record. Overwriting
one orphans that record and a replacement cannot be fabricated, so an import touching such
a slot is rejected whole rather than partially applied — the same line the "stackables"
sort takes. `parser.extract_containers` now exposes `hasDynamicId` so the preview refuses
early rather than at write time.

Empty-slot structure was read off the reference world rather than assumed: `static_id: ""`,
`count: 0`, and a zeroed `dynamic_id`.

**Since done:** Pal imports (`palimport.py`, plus a `pal` export kind), on top of Phase 7's
validation schema. It adds no write path — `overwrite` routes to `charedit.apply_pal_batch`
and `create` to `palclone.apply_clone`.

**Still to build:** player and technology imports, which stay refused with a reason.

### Phase 7 — General save editor · ✅ **COMPLETE** (2026-07-29)

Started with the validation schema rather than an editor, because "which values are legal"
has never existed in this codebase and everything else depends on it.

**`backend/editschema.py` — done.** Player and Pal fields, with bounds *derived* rather
than invented:

| Bound | Source |
|---|---|
| Max level 80 | Palworld 1.0's cap. **Not** the 100 entries in `palExpTable`, which carries headroom — deriving it from the table gave the wrong answer |
| EXP bands per level | `TotalEXP` / `PalTotalEXP` from that table |
| Technology points 1,413 / ancient 185 | `gamedata.totals()` |
| Known passives (1,905), species (753) | bundled database |
| IVs 0–100, rank 1–5, ≤4 passives | measured across 1,905 real Pals |

Two findings that changed the schema:

- **`Talent_Melee` is not a 1.0 field.** `parser._TALENTS` still lists it, but it appears
  on **zero** of the 1,905 Pals in the reference world — the game stores HP, Shot and
  Defense only. It is deliberately not editable; writing it would look like a working edit
  and do nothing.
- **EXP must not exceed its level — and the rule is one-sided, which measurement decided.**
  It began symmetric, on the reasoning that the game derives level from total EXP. Checked
  against the reference world, only half of that holds:

  | | count |
  |---|---:|
  | above the band | **0** of 1,905 Pals, 0 of 5 players |
  | below the band | 8 Pals (levels 4–11) |
  | inside | 1,897 Pals, 5 players |

  A freshly caught Pal arrives at its wild level with almost no EXP and the game leaves it
  there, so low EXP is a state Palworld produces itself — rejecting it would refuse edits
  for being in a legal condition and flag eight real Pals on a clean world. High EXP never
  occurs naturally and *is* acted on, so that half stays a hard rejection. The rule is
  curve-aware either way: players and Pals use different columns.

Cross-field rules are **skipped rather than guessed** when the caller supplies no current
state, and the report says `crossFieldChecked: false` so nothing silently claims more
assurance than it has. 39 tests.

**The Pal editor landed the same day.** `backend/charedit.py` — level, EXP, condenser rank,
nickname and the three IVs, through preview → planHash → apply → verify → rollback.

The failure mode it is built around is not a crash. `Level` and `Talent_*` are
**ByteProperty**, nesting one level deeper than Int. Writing at the wrong depth produces a
file that serialises, loads, and silently ignored the edit — it looks like it worked.
`_write_property` therefore writes *into the existing shape* rather than constructing one,
and **refuses when the property is absent**, because inventing a property means guessing its
type tag. A Pal with no `Talent_HP` has never had that IV rolled, and fabricating one is a
change to game state we cannot verify.

Species, gender and passive skills are deliberately **read-only**: they change what a Pal
*is*, which cascades into the Paldeck, breeding eligibility and the palbox. Out of scope
until there is a reason.

**The frontend landed with it** (`src/components/pal-editor.tsx`), in the Save Tools tab.
Search a Pal → edit → preview the exact diff → apply. It renders itself **from the backend
schema** rather than a second copy of the bounds, so a future cap change needs no UI edit.

Two UI decisions that came out of the data rather than taste:

- **A "match level" button on EXP.** Since the game recomputes level from total EXP on
  load, changing one without the other is an edit that silently undoes itself. The schema
  endpoint now returns `expBands` (80 levels, 1.9 KB) so the UI can fill in the right value
  instead of letting people bounce off the cross-field rule.
- **IV inputs only appear for IVs the Pal actually stores.** The backend refuses to create
  an absent property rather than guess its type, so rendering a box that can only be
  rejected would be worse than omitting it.

**Discoverability note:** `save.edit.full` exists only at `SECURITY_LEVEL=full`, and
servers default to `safe` — so the editor is hidden even from an Owner on a default
install. The locked-state card now says exactly that and names the variable, rather than a
bare "no permission".

**The player editor landed too (2026-07-29), and it is the awkward one.** A player is
stored across TWO files — name/level/EXP in `Level.sav`, technology points in
`Players/<UID>.sav` — which cannot be written atomically together. Both are written, both
are re-read and verified, and any mismatch rolls back the whole world. That is coherent
because `collect_world_files` walks `Players/`, so the pre-edit backup already covers the
pair.

Two things the reference world taught us:

- **`TechnologyPoint` is absent on players who never banked an unspent point** — 1 of the 5
  here. The planner refuses that field up front with an explanation, rather than letting
  the write path discover it and roll back.
- **`bossTechnologyPoint` is the ancient-technology counter.** The naming is the game's.

The UI became a single **character editor** with a Pals/Players toggle
(`src/components/character-editor.tsx`) — the two subjects collapse to one shape, so one
list and one schema-driven form serve both. It warns in the confirm dialog when an edit
spans both files, and reports which files were written.

**The last three landed together (2026-07-29), and none of them opened a new write path.**

**Inventory slot editing** (`backend/slotedit.py`). A slot edit *is* an import of a
container that differs by those slots, so it builds a document and hands it to
`saveimport`. Every guarantee comes along unchanged — unknown ids refused, stack ceilings
enforced, durability slots refused, and after writing the target container must match while
every other container in the world is untouched. The document carries **only the patched
slots**, which matters twice: a modded item elsewhere in the chest cannot block an edit
that never went near it, and a stale view of the rest cannot revert someone else's change.
`saveimport` now derives `itemsAfter` from the diff rather than by summing the document, so
a partial document reports the container honestly.

**Bulk Pal edits** (`charedit.plan_pal_batch` / `apply_pal_batch`). One change set, many
Pals, one backup, all-or-nothing — a batch that half-applies leaves no record of where it
stopped. The per-Pal change map is the primitive rather than the shared one, because the
repair path below needs different values per Pal; `spread_changes` produces the bulk shape
from it. `auto_exp` carries EXP along with a level change, since without it a level change
leaves each Pal on its old EXP.

Two gaps this exposed and closed:
- **`plan_pal_edit` did not check whether the property exists.** An absent `Rank` reads as
  1 through `_num`'s default, so a never-condensed Pal validated fine and then failed
  inside `guarded_save_write`. It now refuses up front, as the player planner already did.
- **`_index_pals` walks the map once** for a whole batch. Per-Pal scanning over 1,905
  entries is quadratic.

**Illegal-Pal detection and repair** (`backend/palcheck.py`). Scans every Pal against
`editschema` — not a second opinion about what Palworld allows — and repairs by clamping
through `apply_pal_batch`. Only scalars are repairable: passive lists are an ArrayProperty,
and changing a species is not a repair.

**The finding that shaped it: `unknown_species` is not evidence of anything.** A first pass
flagged **108 of 1,905** Pals on a clean reference world. All of them were false positives,
in two layers:

| cause | count | fix |
|---|---:|---|
| NPCs looked up in the Pal table only | 87 | `gamedata.character()` — the map holds humans too |
| NPCs missing from the bundled tables entirely | 13 | reclassified as **advisory**, never counted |
| EXP below its level band | 8 | the one-sided rule above |

The 13 have no structural tell — they carry IVs and passive skills exactly like a Pal. So
an unrecognised id means "our data is incomplete", which is a different claim from "someone
cheated", and mixing the two would put a dozen false accusations on every clean world.
Advisories are reported separately and never inflate `palsFlagged`. Passives had no such
problem: 124/124 distinct passives on the reference world are known, max 4, no duplicates.

**The reference world now scans clean: 0 violations across 1,905 Pals**, which is the
strongest available evidence the bounds describe the game rather than merely agreeing with
themselves.

**Skill editing and Pal cloning closed it out (2026-07-29).**

**Skills are ordinary fields now**, not a separate feature — `passiveSkills` came
out of `PAL_READ_ONLY` and `activeSkills` joined it, so the existing Pal editor,
the bulk editor and the batch writer all handle them without new endpoints. What
they needed was a second *write shape*: ArrayProperties keep their values at
`node["value"]["values"]` and carry an `array_type` that must not change.
`_apply_pal_change` is the single place that routes scalar vs list, so a batch
cannot silently skip skill edits.

Bounds are measured, as everywhere else: **at most 3 equipped moves** (never more
across 1,905 Pals) and 4 passives, every id checked against the bundled tables.
`EquipWaza` stores an `EPalWazaID::` prefix the tables do not use, so the API
speaks bare ids and the prefix is re-attached only on write. `MasteredWaza` is
not offered — absent on 1,563 of 1,905 Pals, and inventing an ArrayProperty means
guessing its type.

**Cloning got its own module** (`backend/palclone.py`), because it is the only
code in the project that *creates* save records rather than overwriting fields —
the same separation principle as `saveimport` vs `saveexport`.

A Pal is two records that must agree: a `CharacterSaveParameterMap` entry whose
`SaveParameter.SlotId` names a container and index, and a
`CharacterContainerSaveData` slot whose `RawData.instance_id` names the Pal. Miss
either and the result is a ghost.

The finding that shaped it: **there are no empty slots to fill.** 23 character
containers, 1,905 slot entries, 1,905 Pals. `SlotNum` is capacity (960 for a
palbox) while the array holds only occupied slots, so adding a Pal *appends*.
New entries are deep-copied from existing ones rather than constructed, because
the slot carries `CustomVersionData` and a `permission_tribe_id` whose right
values are whatever this save already uses.

Verification counts records rather than comparing values: both arrays must grow
by exactly `count`, every new id must resolve to its slot, and no other container
may change length. Cloning a player is refused, and there is no "find room
anywhere" mode.

**Risk: high.** **Depends on: Phases 0, 3, 4, 6.**
*This is where corruption risk actually lives. It was not rushed.*

### Phase 8 — Server dashboard & admin commands · ✅ **COMPLETE** (2026-07-29)
CPU/RAM/disk sampling with history (`metrics.py`, 60s samples kept 30 days raw);
world-size and entity counts; broadcast, kick, ban, unban, force-save and graceful
shutdown (`gameapi.py` + `moderate.py`); **load-aware throttling** that defers a parse
when server FPS is below a floor.

**The finding that reshaped the phase:** the commands were already reachable through the
Next.js game-REST proxy and left **no audit record**, because the audit log is in SQLite
and only the Python process opens it. So the work was moving them behind the backend, not
building them. That proxy now serves reads only and returns 405 for anything else.

`server.control` was split into `server.control` + `players.moderate` — taking the server
down and banning a player are different trusts. Moderator and above get both, so no
existing account changed what it can do.

**Not delivered:** *teleport* is **blocked, not deferred** — Palworld's REST API has no
teleport command and it is RCON-only. *Scheduled announcements* were deferred; `schedule.py`
already has the timer.

**Risk: low, as predicted.** Nothing here writes to a save file.
**Depended on: Phase 3.**

### Phase 9 — Migration, presets, polish · ✅ **COMPLETE** (2026-07-30)
Steam ↔ dedicated ↔ Game Pass ↔ co-op migration (port PST's proven implementations);
remaining server presets; mod detection; version compatibility matrix; multi-image Docker
validation.
**Risk: medium** (Game Pass paths are Windows-specific and hard to test from Linux).

**Done:** the uid remap (`soloexport.py`) covering Steam ↔ dedicated ↔ co-op, and
mod detection (`mods.py`). The version compatibility matrix landed early as
`gameversion.py` in the #21 work.

**A naming correction.** Task #26 was titled "solo-world export", which reads as a
different feature from the one the roadmap planned. It is not: "port PST's proven
implementations" meant `fix_host_save.py`, and that is a **uid remap**, not an
extraction. The distinction is recorded in `soloexport.py` — true solo extraction
would delete every other player's characters, Pals and bases, which destroys the
world it is meant to preserve, and no reference implementation exists for it.

**Two departures from the reference implementation**, both deliberate:

1. **It never writes to the live world.** PST mutates in place; this reads the world
   and writes a new directory. That removes the corruption risk entirely and makes
   it the one save feature that is safe to run while the server is up.
2. **It matches uids by value, not by key name.** PST rewrites four named keys.
   Counted against the reference world, that list misses **1,836 references** —
   mostly `LastNickNameModifierPlayerUid` (1,817), plus `LostPlayerUId`,
   `last_guild_name_modifier_player_uid`, `seller_player_uid` and
   `SkinAppliedCharacterId`. A key list is also a promise about a schema this
   project does not control.

**Server presets** were checked against `DefaultPalWorldSettings.ini`'s 119 keys
rather than against memory — which is how `EggDefaultHatchingTime`, a key
matching nothing, was found sitting in a highlight group (the real one is
`PalEggDefaultHatchingTime`). `boosted`, `small_server`, `hardcore` and a derived
`vanilla` reset now ship. `vanilla` subtracts `ENV_MANAGED` and `SECRET_KEYS`
from what it writes, so a "reset to defaults" can never rename the server or
clear its passwords.

**Multi-image validation** was done from the images' own published metadata via
`skopeo inspect` rather than by running them — a few KB fetched instead of
multiple gigabytes pulled. It produced two corrections to this project's stated
assumptions, both recorded in `docs/COMPATIBILITY.md`: jammsen does *not* always
regenerate the INI, and the REST port variable is spelled differently between the
two images. Validation by actually running jammsen remains open, and the doc says
so.

**Game Pass extraction was removed rather than built** (2026-07-30), at the
operator's request. `xgp_save_extract.py` solves a Windows *storage-location*
problem — finding a save inside `WGS` container folders — not the console-player
question it was assumed to answer. What was actually wanted is **task #33**:
handling Xbox/PS5/Mac players on a crossplay server. The save carries
`PlayerPlatform` (values read out of the server binary: `Steam`, `Xbox`, `PS5`,
`Mac`, `None`) and `parser.py` now surfaces it, but no console player has ever
been observed and **neither PST nor the original dashboard handles it**, so there
is no prior art to copy. `docs/CROSSPLAY.md` records what is verified, what is
not, and the three checks to run the day one joins.

**Total: 40–55 engineer-days.** Phases 0–5 are done and produce a genuinely good,
safe-to-run product with no Critical blockers. Phases 6–9 (~19–27 days) are the long tail,
and Phase 7 is over half of it.

---

## 7. Architecture & optimization recommendations

| # | Recommendation | Why | Effort | When |
|---|---|---|---|---|
| A1 | SQLite for users, audit, backup metadata, scheduling | JSON files cannot do concurrent writes, queries or integrity | 1 d | **Before launch** (Phase 3) |
| A2 | Split `main.py` into routers | 552 lines → 30+ routes; will double | 0.5 d | Before launch |
| A3 | Reference-data layer as its own module | Phase 1 touches every view; one loader, one cache | 0.5 d | Before launch |
| A4 | Marker clustering + canvas rendering on the map | ~3,400 individual Leaflet layers today, far more after Phase 2 | 1 d | **Before launch** |
| A5 | Virtualize long tables | 500-row item table, 645 types, thousands of slots later | 0.5 d | Before launch |
| A6 | Load-aware parse throttling | Explicitly requested; protects the game server | 1 d | Before launch |
| A7 | Structured logging with request IDs | Debugging a corruption report needs a trace | 0.5 d | Before launch |
| A8 | Shared type generation (Pydantic → TS) | `types.ts` is hand-maintained and will drift | 1 d | Defer |
| A9 | Streaming/incremental parse | 445 MB peak RSS ≈ 8× decompressed size | 3 d | Defer — measure first |
| A10 | Error boundaries + skeletons | One failed fetch currently blanks a tab | 0.5 d | Before launch |
| A11 | Drop unused deps | `next-themes`, `recharts` ship for nothing | 5 min | Now |
| A12 | ETag / conditional GET on parse results | Parse output is large and changes rarely | 0.5 d | Defer |
| A13 | Fix the 12 remaining lint warnings properly | React Compiler flags `setState` in effect across 5 components and one ref-write during render. Downgraded to warnings in Phase 0 rather than refactored — with no frontend tests, a blind 5-component refactor was the riskier choice. Fix alongside A10. | 1 d | Before launch (Phase 2) |

---

## 8. Worth building beyond PST parity

These come from things the save exposes that PST does not surface, and they are cheap
because the data is already parsed:

1. **Server-wide economy view** — 8,349,417 items across 645 types, trended over time. No
   desktop tool can do this because it needs continuous history.
2. **Per-player completion dashboard** — now exact, not estimated: 117/174 fast travel,
   x/1,413 tech points, x/185 ancient. `RecordData` also carries `FoundTreasureCount`,
   `CampConqueredCount`, `FishingCountMap`, `NormalDungeonClearCount`, `OilrigClearCount`,
   `ArenaSoloClearCount`, `PalCaptureCount` — a full achievement surface, unused today.
3. **Fog-of-war overlay** from `FindAreaFlagMap` + `UnlockedWorldMapFlags` — per-player
   exploration, and a guild-union view.
4. **Base health warnings** — containers near capacity, unpowered generators, idle Pals.
5. **Breeding goal planner** — "I want Anubis with these 4 passives" → concrete pair list
   from the palbox. Data for this is already loaded.
6. **Guild comparison / leaderboards** — bases, tech, capture completion.
7. **Diff between backups** — "what changed in the world since Tuesday" falls out of the
   backup system almost free.

---

## 9. Deliberately not building

| Item | Why |
|---|---|
| **Live player rotation/facing on the map** | Asked for explicitly. `RestAPI /v1/api/players` returns position but **not rotation**, and the save stores rotation only at last-save. Live facing would require memory reading or a mod. Not feasible; documenting rather than faking it. |
| Real-time position streaming (<5 s) | REST polling at that rate measurably loads the game server. 15–30 s is the honest floor. |
| Cheat mode / arbitrary value injection | Directly opposed to the corruption-safety goal. The general editor should validate, not bypass. |
| Cloud backup providers now | Design the interface (Phase 4), implement when someone actually needs it. |
| Full mod support | Detect-and-warn is achievable; parsing arbitrary mod save data is not. |
| Postgres / Redis | Wrong scale. SQLite is correct here. |
| Multi-server management | Not requested; would reshape the whole data model. |
| 9-language localization | `i18n/` is available if wanted, but it is a large surface for a LAN tool. |

---

## 10. Deployment blockers

**Critical** — none remaining.
~~no `USER` in Dockerfile (S12)~~ ✅ 2026-07-28 · ~~zero tests~~ ✅ P0 · ~~S1 rate
limiting~~ ✅ P3 · ~~S2 audit log~~ ✅ P3.
**High** — ~~S9 upload validation~~ 🟡 size cap landed with the export half; per-field
import validation still required before the import half ships (Phase 6).
~~backup verification before any editor expansion~~ ✅ P4.
~~`.gitignore` excluding docs~~ ✅ P0 · ~~S3/S4 accounts & revocation~~ ✅ P3 · ~~S5 route
allowlist~~ ✅ P3.
**Medium** — S7 CSRF tokens (mitigated by SameSite, not eliminated), multi-image Docker
validation.
~~S8 cookie-secure~~ ✅ P3 · ~~map imagery missing~~ ✅ P2.
**Low** — S11 dependency scanning, API versioning.
~~S10 backend auth~~ ✅ P3.

**Verdict: deployable for personal / trusted-LAN use.** No Critical blockers remain. What
is left is either a feature gap (import/export, the general editor) or a hardening item
that does not gate a LAN deployment (S11 dependency scanning, S7 CSRF tokens — mitigated
by `SameSite=Lax` but not eliminated). Exposing this to untrusted users still wants S7 and
S9 closed first.

**The container was verified by actually building and running it (2026-07-28), which found
three defects that no amount of unit testing would have caught:**

| # | Defect | Fix |
|---|---|---|
| D1 | **The image did not build.** The wheel-builder stage was `python:3.12`, the runtime installs Debian bookworm's `python3` = **3.11**. `orjson` and `palooz` are compiled extensions, so their cp312 wheels were rejected outright. | Builder pinned to `python:3.11-slim-bookworm`, with a comment that the minor versions must match |
| D2 | **The container died ~1s after boot, every time.** `docker-entrypoint.sh` used `wait -n` under `#!/bin/sh`, which is dash: `wait: Illegal option -n`, and `set -e` then killed the script. Both processes were started and immediately torn down. | Shebang changed to `#!/bin/bash` (present in the image), with a comment not to "simplify" it back |
| D3 | **`.dockerignore` did not exclude `refworld/` or `refs/`** — 132 MB of build context, including a real world save with real Steam IDs and player names. Not in the runtime layers, but in the daemon and the build cache, and shipped by anyone targeting the builder stage. | `.dockerignore` rewritten to mirror `.gitignore` |

Verified after fixing: image builds, container stays up, runs as `uid=1000(node)`,
dashboard answers `HTTP 200`, the backend is reachable on loopback **inside** the container
and refused from the host, sign-in works, a short password is refused with a clear log line,
and the safety guard correctly reports `editable: false` / "assuming running to protect the
save" when it cannot prove otherwise.

**Resolved without adding the `docker` CLI.** The originally documented
`STOP_COMMAND` / `START_COMMAND` invoked `docker`, which is **not installed in the
runtime image** — `lifecycle._run_configured` raised `FileNotFoundError` →
"STOP_COMMAND not found: docker". Adding `docker-cli` (~35 MB of image and attack
surface for a feature not everyone enables) was declined.

It was never needed. The `docker-socket-proxy` sidecar speaks the Docker **HTTP
API**, and the runtime image is `node:20-bookworm-slim`, so `node` with a global
`fetch` is already present — the healthcheck already uses it. `docker-compose.yml`
and `docs/DEPLOYMENT.md` §4 now carry working `node -e` commands.

Three details make those correct rather than merely plausible: **304 counts as
success** (the Docker API returns it when the container was already in the
requested state, so treating it as failure would error exactly when "stop the
server" has already been achieved); the commands survive `shlex.split` into
exactly three argv elements with **no shell**, so nothing can be injected; and the
proxy must stay unpublished, since anything that can reach it can start and stop
containers.

**Licensing decision (not a blocker for private use):** `palsav` is GPL-3.0-or-later, so
publishing this dashboard means licensing it GPL-3.0. The alternatives are to accept that,
or to isolate parsing behind a subprocess boundary and treat it as a separate program —
which is a defensible reading but not a settled one. Decide before any public release.

**Verdict: not production-ready.** Safe for personal/trusted-LAN use today — the corruption
guard is real and the write path is proven. Not safe to expose to untrusted users until the
Critical and High items are closed.

---

## 11. Version compatibility

| Component | Supported | Evidence |
|---|---|---|
| Palworld 1.0 saves (`PlM`, Oodle) | ✅ | Parsed `refworld/` end to end |
| Pre-1.0 (`PlZ`, zlib) | 🟡 untested | `palsav` handles both; no sample to verify |
| Game Pass saves | ⚪ out of scope | Extraction removed 2026-07-30; it was a Windows storage-location tool, not the crossplay feature it was taken for |
| Xbox / PS5 / Mac **players** | 🟡 task #33 | `PlayerPlatform` surfaced; no console player observed. `docs/CROSSPLAY.md` |
| Co-op / single-player | ✅ | `soloexport.py` remaps uids across a world copy; 1,836 references a key-list approach would miss |
| `palworld-save-tools` (PyPI, cheahjs) | ❌ | **Cannot read 1.0.** Removed. Use `palsav`/`palooz`. |

Recommend a startup check that reads the save's magic bytes and version and refuses
politely on anything unrecognised, rather than half-parsing it.

---

## 12. Suggested first move

If you want one thing started when you're back: **Phase 0 then Phase 1.** Phase 0 is the
prerequisite for touching save-writing code safely, and Phase 1 converts 38 MB of
already-downloaded, already-validated, MIT-licensed reference data into visible improvement
across every screen for 2–3 days of mechanical work.

Phase 1 is also a clean Sonnet subagent task — well-specified, verifiable, no architectural
judgement required. Phases 3 and 7 should stay on Opus.
