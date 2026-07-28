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
| Save parsing engine | 90% | 1.0/Oodle proven; missing per-base container linkage |
| Corruption safety | 85% | Fail-closed, atomic, verified; no audit trail |
| Backup & restore | 30% | Directory copy only; no archive/verify/schedule/browse |
| Save editing | 12% | Two sort modes work; everything else 501 |
| Live map | 25% | Transform fitted, save POIs only; no static POIs, no tiles |
| Reference data | 15% | Small derived subset; the authoritative DB sits unused in `refs/` |
| Auth & accounts | 20% | Shared password, 2 roles, no users/audit/rate-limit |
| Permissions | 45% | Capability model is sound; only 2 roles bound to it |
| Server dashboard | 30% | REST status; no metrics history, no admin commands |
| Docker | 80% | Genuinely good; needs multi-image validation |
| Import / export | 0% | Not started |
| Migration tools | 0% | Not started |
| Testing | 45% | ✅ Phase 0: 151 backend tests. Frontend still 0 |
| Documentation | 70% | ✅ Phase 0: `.gitignore` fixed, AGENTS.md written |
| **Weighted total** | **~36%** | was 32% before Phase 0 |

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
| `breeding.py` | 335 | 🟡 Works; built on a derived dataset that `refs/` supersedes |
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

### 2.9 Docker — ✅ 80%

Genuinely good. Shared bind mount, service-name DNS (`http://palworld:8212` — yes, this
works), REST port unpublished, backend port unpublished, `docker-socket-proxy` sidecar for
container control without giving the dashboard root or the raw socket, `cpus`/`mem_limit`
caps, paste-one-service-into-your-existing-compose documented. Only validated against
`thijsvanloef/palworld-server-docker`.

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

| # | Severity | Issue | Fix |
|---|---|---|---|
| S1 | **Critical** | No rate limiting on `/api/auth/login`. Single shared password, brute-forceable at network speed. | Per-IP limit + exponential backoff + lockout. `refs/` has `rate-limit.ts` to model. |
| S2 | **Critical** | No audit log. Sorting rewrites `Level.sav`; nothing records who or when. | Append-only log for every write; required by the brief. |
| S3 | **High** | No accounts. One password = one identity; cannot revoke one person's access or attribute an action. | SQLite users + Argon2id. |
| S4 | **High** | Session revocation impossible. Logout clears the cookie; the token stays valid to `exp` (12 h). | Server-side session table or token version counter. |
| S5 | **High** | Proxy path handling uses regex prefixes on a joined catch-all. Unmatched paths correctly fail closed to admin-only, but an admin can reach arbitrary backend paths. | Explicit route allowlist; reject `..` and encoded separators. |
| S6 | **Medium** | `RESTART/STOP/START_COMMAND` are operator-set and run via `shlex.split` (no shell) — but any admin-session holder can trigger them. | Bind to a capability; log every invocation. |
| S7 | **Medium** | No CSRF tokens. `SameSite=Lax` blocks cross-site POST cookies, so exposure is limited today. | Add tokens with the accounts work. |
| S8 | **Medium** | `COOKIE_SECURE` defaults false (correct for LAN http, wrong behind a proxy). | Auto-enable on `X-Forwarded-Proto: https`. |
| S9 | **Medium** | No upload validation — because uploads don't exist yet. Import is a top request. | Magic-byte check, size cap, sandboxed parse, never trust filenames. |
| S10 | **Low** | Backend has no auth; safe only while loopback-bound. | Shared-secret header as defence in depth. |
| S11 | **Low** | No dependency scanning. | `npm audit` + `pip-audit` in CI. |
| S12 | **Low** | Container runs as root unless the image says otherwise. | Explicit `USER` in Dockerfile. |

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

### Phase 1 — Adopt the reference data (2–3 days) · **highest value/effort ratio**
Bundle `game_data/` as gzipped assets + icons; friendly-name resolver across item/Pal/
passive/skill/tech/structure; replace derived breeding data and `reference_totals.json`
with authoritative figures; MIT attribution in README.
*Immediate effect:* every list stops showing `AIcore` and starts showing "AI Core"; tech
totals become exact (1,413 / 185).
**Risk: low. Dependencies: none.** Good first Sonnet subagent task — mechanical, verifiable.

### Phase 2 — The map (4–5 days)
Ship map imagery; fix the `feybreak` → `tree` naming; 174 named fast-travel markers;
world-vs-base object split (3,604/1,019); ore and chest layers; layer toggles, search,
fly-to, coordinate readout; marker clustering for performance.
**Risk: low-medium** (coordinate validation for each new layer). **Depends on: Phase 1.**

### Phase 3 — Accounts, audit, hardening (5–7 days) · **security-critical**
SQLite; users with Argon2id; the 7 permission presets bound to the existing capability map;
per-user Steam-ID linkage (this is what unlocks every per-player feature); append-only audit
log; rate limiting; session revocation; route allowlist. Fixes S1–S8.
**Risk: medium** (auth is easy to get subtly wrong). **Depends on: Phase 0.**

### Phase 4 — Backup & restore, properly (4–5 days)
Compressed archives with manifest + checksums; integrity verify; retention; scheduling
(manual/hourly/daily/pre-edit/pre-restart); backup browser; restore preview; granular
restore; automatic rollback points. Storage behind an interface so cloud backends slot in
later without redesign.
**Risk: low.** **Depends on: Phase 0, Phase 3 (audit log).**

### Phase 5 — Per-base inventory & advanced sorting (3–4 days)
Wire `ModuleMap → target_container_id`; per-base breakdown; base-scoped sorting; category
rules, priorities, custom profiles, overflow handling; CSV/JSON/TXT reports.
**Risk: low-medium.** **Depends on: Phase 1, Phase 4.**

### Phase 6 — Import / export (4–5 days)
Export whole save, player, guild, base, palbox, container as JSON/archive. Import with
validation, dry-run diff preview, mandatory pre-import backup.
**Risk: high** — import is the most dangerous surface in the product. Every import must be
dry-run-previewed and backed up first, no exceptions.
**Depends on: Phase 4.**

### Phase 7 — General save editor (8–12 days) · **the big one**
Per-field validation schema (type, range, enum, cross-field) covering player fields, Pal
fields, container slots. Then editors: player → Pal → inventory → bulk. Illegal-Pal
detection and repair. Every write through `guarded_save_write` with a preview diff.
**Risk: high.** **Depends on: Phases 0, 3, 4, 6.**
*This is where corruption risk actually lives. It must not be rushed and must not start
before the test harness and backup system are real.*

### Phase 8 — Server dashboard & admin commands (3–4 days)
CPU/RAM/disk/network sampling with history; player/entity counts; broadcast, kick, ban,
teleport, force-save, scheduled announcements via REST; **load-aware throttling that pauses
dashboard work when the game server is under pressure** (explicitly requested — gameplay
wins over dashboard responsiveness).
**Risk: low.** **Depends on: Phase 3.**

### Phase 9 — Migration, presets, polish (4–6 days)
Steam ↔ dedicated ↔ Game Pass ↔ co-op migration (port PST's proven implementations);
remaining server presets; mod detection; version compatibility matrix; multi-image Docker
validation.
**Risk: medium** (Game Pass paths are Windows-specific and hard to test from Linux).

**Total: 40–55 engineer-days.** Phases 0–4 (~19–24 days) produce a genuinely good,
safe-to-run product. Phases 5–9 are the long tail.

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

**Critical** — S1 (login rate limiting), S2 (audit log), no `USER` in Dockerfile.
~~zero tests~~ ✅ closed in Phase 0.
**High** — S3/S4 (accounts, revocation), backup verification before any editor expansion.
~~`.gitignore` excluding docs~~ ✅ closed in Phase 0.
**Medium** — S5 route allowlist, S7 CSRF, S8 cookie-secure detection, map imagery missing,
multi-image Docker validation.
**Low** — S10 backend shared secret, S11 dependency scanning, API versioning.

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
| Game Pass saves | 🔴 | Needs PST's `xgp_save_extract.py` |
| Co-op / single-player | 🟡 | Same format; host-GUID handling differs |
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
