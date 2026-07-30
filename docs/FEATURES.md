# Feature inventory

What exists **today**, as of 2026-07-30 (Phases 0–9 complete). Written because the
roadmap in `AUDIT.md` tracks *gaps* — anything already working simply does not appear
there, which makes finished features look missing.

Evidence: 108 backend routes, 41 backend modules, 24 UI components,
17,435 lines of backend Python against 11,571 lines of backend tests.

Legend: ✅ works · 🟡 works with a caveat · 🔴 not built · ⚪ out of scope

---

## 1. Server monitoring

| Feature | State | Notes |
|---|---|---|
| Live server status | ✅ | REST API + TCP + save-mtime + process scan |
| Player count / online list | ✅ | via Palworld REST API |
| Server settings viewer & editor | ✅ | `PalWorldSettings.ini`, CRLF-preserving |
| Settings presets | ✅ | **Phase 9.** `boosted`, `small_server`, `hardcore` and a derived `vanilla` reset, all checked against `DefaultPalWorldSettings.ini`'s 119 keys rather than against memory |
| Announce & graceful shutdown | ✅ | **Phase 8**, audited. Needs no container control — the game stops its own process |
| Start / stop / restart container | 🟡 | Backend logic works. The `docker` binary is **not in the runtime image**, so a `docker …` command fails; `docs/DEPLOYMENT.md` §4 has working `node`-based commands. Left unconfigured by choice — manual `docker compose stop` works and the UI says so |
| CPU / RAM / disk history | ✅ | **Phase 8**, 60s samples kept 30 days; outages drawn as gaps, not zeroes |
| Broadcast / kick / ban / unban | ✅ | **Phase 8**, through the backend so every action is audited, failures included |
| Force save | ✅ | **Phase 8**, audited |
| Load-aware parse throttling | ✅ | **Phase 8**, defers a parse when server FPS is below the floor |
| Teleport a live player | 🔴 | **Closed, will not build.** Verified in the server binary: the only command is `TeleportToPlayerByIndex`, and both admin teleports anchor to the *issuing admin's* in-game character — which a headless dashboard does not have |
| Teleport by coordinates | 🟡 | **Built as a save edit** (`teleport.py`), with the 174 fast-travel points as presets. Needs the server stopped, so it cannot unstick someone who is online now |
| Scheduled announcements | ✅ | **Phase 8 follow-up.** Rides the existing scheduler tick. An empty server *consumes* its window rather than queueing, so logging in does not fire every overdue message at once |
| Game build / update detection | ✅ | **Phase 9.** Reads the Steam `appmanifest` `buildid` — two file reads, no network. Banners stale bundled data. Reports "cannot tell" when the install directory is not mounted |
| Mod detection | ✅ | **Phase 9.** Exists to qualify a `palcheck` report, not to manage mods. "Cannot see the game directory" never renders as "no mods installed" |

## 2. Map

| Feature | State | Notes |
|---|---|---|
| Interactive map, both landmasses | ✅ | Palpagos + World Tree as separate framings |
| 174 fast-travel points | ✅ | Validated 117/117 against a real player's unlocks |
| Bases, chests, palboxes, farms, production, defences | ✅ | ~3,400 placed objects, layered |
| Static world objects | ✅ | **35,687** from the game pak — 24,359 ore, 8,386 chests, 2,757 fishing spots, 185 oil. Viewport-culled, capped at 2,000 drawn |
| Per-kind layer toggles | ✅ | Every category subdivides by kind (each ore type, each chest type), and an admin policy sets which categories each role may see — including whether they are listed at all |
| Effigies | ✅ | All 396, with the GUIDs saves key on, so the map can show which ones a given player still needs |
| Smooth zoom | ✅ | Continuous (`zoomSnap: 0`). Leaflet's default snaps to whole levels — a 2x jump per wheel notch on a single-image map |
| Game icons | ✅ | 1,409 installed (Pals, items, elements, NPCs). Resolved through a manifest, case-insensitively — a guessed path 404s on exactly the Pals the capitalisation bug already cost us |
| Reload data packs | ✅ | Admin-only. Re-reads the bundled files from disk after you replace them; no container restart. **Reloads, never regenerates** |
| Layer toggles + search | ✅ | |
| World Tree coordinate accuracy | 🟡 | Extent is exact (from the streaming grid); **orientation** is assumed and flagged `calibrated: false` in the UI. Deriving it from the cell silhouette was tried and failed its control — see `scripts/fit-worldtree.py` |
| Live player position | 🟡 | Polled from REST, 15–30 s floor |
| Live player *facing* | 🔴 | **Deliberately not built.** The REST API does not return rotation. Documented rather than faked |
| Fog-of-war / exploration overlay | 🔴 | Data (`FindAreaFlagMap`) is parsed but unused |

## 3. Save parsing

| Feature | State | Notes |
|---|---|---|
| Palworld 1.0 (Oodle / `PlM`) | ✅ | Proven end-to-end on a real 2.1 MB world |
| Guilds, bases, players, Pals | ✅ | |
| Item containers & slot contents | ✅ | 11,639 containers, 8.3 M items on the reference world |
| Per-base storage attribution | ✅ | **Phase 5.** Exact join, not spatial |
| Player progression / completion | ✅ | Exact denominators from bundled data |
| Pre-1.0 (`PlZ` / zlib) saves | 🟡 | `palsav` supports it; no sample to verify against |
| Uid remap for co-op / another server | ✅ | **Phase 9** (`soloexport.py`). Writes a *copy*, so it is the one save feature safe to run while the server is up. Matches uids by value, not by key name — a key list misses 1,836 references |
| Xbox / PS5 / Mac players | 🟡 | Platform is parsed and surfaced; no console player has ever been observed. `docs/CROSSPLAY.md`, task #33 |
| Game Pass saves | ⚪ | Out of scope. The extraction tool solves a Windows *file-location* problem, not the crossplay question it gets mistaken for |

## 4. Inventory & items

| Feature | State | Notes |
|---|---|---|
| Server-wide item totals | ✅ | 645 item types |
| Friendly names for items/Pals/NPCs | ✅ | Case-insensitive by necessity |
| Per-base inventory breakdown | ✅ | **Phase 5** |
| Per-container detail & fill levels | ✅ | **Phase 5** |
| Near-full base warnings | ✅ | **Phase 5**, 90 % threshold |
| Reports: CSV / JSON / TXT | ✅ | **Phase 5**, 4 report types |
| Container sorting (stackables / all) | ✅ | Conservation-verified, auto-rollback |
| Structured export (6 kinds) | ✅ | **Phase 6**, checksummed and verifiable; `pal` added later |
| Container import | ✅ | **Phase 6**, dry-run diff + plan hash + scope-verified write |
| Pal import (`pal` / `player` export) | ✅ | Overwrite existing or add a new Pal; unwritable fields listed, not dropped |
| Pal editor (level, EXP, rank, IVs, nickname) | ✅ | **Phase 7**, preview → apply → verify → rollback. Needs `SECURITY_LEVEL=full` |
| Base-scoped sorting | ✅ | **Phase 5** |
| Real stack limits | ✅ | **Phase 5**, `max(authoritative, observed)` |
| Category rules / priorities / profiles | 🔴 | Deferred — these decide *where an item goes*, which is editor semantics |

## 4b. Paldeck

| Feature | State | Notes |
|---|---|---|
| Browse every Pal | ✅ | 204 Paldeck entries from bundled data. Needs **no parsed save** — it describes the game, not your server, so it is `VIEW_BASIC` |
| Spawn habitat map | ✅ | 183 of 204 entries. A side map shading where the species is found, at streaming-cell resolution |
| Search | ✅ | Name, Paldeck number, internal id or element |
| Location variants merged | ✅ | `HadesBird` + `HadesBird_Electric` are one Helzephyr entry with the **union** of their ranges |
| Spawn *rates* | 🔴 | Not derivable. A sheet says a species is referenced by spawners in an area, not how often it appears |

**Where the habitat data comes from, and why it took a detour.** Spawner actors
name a *sheet*, not a species (`BP_PalSpawner_Sheets_2_1_forest_1`), and the
species list lives in properties that are cooked with unversioned property names
— undecodable. But a package's **name table** is plainly serialised, so reading
the sheet's name table and intersecting it with the known species list yields
its roster. Same trick the effigy extractor uses: attribution without decoding.

Result: **348 species mapped, 13,440 of 13,851 spawners attributed (97.0%)**.

**Encounter-only forms have no habitat, and that is correct.** `_Oilrig` and
`_Tower` variants are placed by encounter logic rather than by world spawners.
Merging variants is what keeps their Paldeck entry mapped anyway.

## 5. Breeding — **built, and often assumed missing**

| Feature | State | Notes |
|---|---|---|
| Breeding calculator | ✅ | 46,655 parent pairs |
| Special combinations | ✅ | Correct by construction (full pair table, not a formula reimplementation) |
| Palbox-driven suggestions | ✅ | Works from what players actually own |
| Breeding path search | ✅ | Depth-capped BFS to protect CPU |
| Reachable-with-an-extra-step list | ✅ | Everything obtainable via an intermediate, shortest route each. Counts **breedings**, not BFS generations — a Pal can be two generations deep and need three pairings |
| Inheritance odds | ✅ | |
| Dataset currency | ✅ | **Merged 2026-07-28**: 305 Pals, +Astralym (#204), +1,803 pairs. See the merge note below |

## 6. Backups

| Feature | State | Notes |
|---|---|---|
| Verified `.tar.gz` archives | ✅ | SHA-256 per file + per archive |
| Scheduled backups | ✅ | Missed windows skipped, never replayed |
| Retention thinning | ✅ | newest N → daily → weekly |
| Restore with preview & rollback point | ✅ | Verifies before touching anything |
| Scoped restore (world/players/config) | ✅ | |
| Download archive | ✅ | |
| Cloud targets | 🔴 | Interface designed (`BackupStore`), no implementation |

## 7. Security & accounts

| Feature | State | Notes |
|---|---|---|
| Accounts, scrypt hashing | ✅ | |
| 7 role presets | ✅ | Guest → Owner |
| Two-gate authorization | ✅ | role capability ∩ security-level ceiling |
| Server-side revocable sessions | ✅ | Stored hashed |
| Rate limiting (per IP + per user) | ✅ | Verified live: 401 on bad password |
| Audit log | ✅ | Every mutating action, **including the failures** — an attempt that did not land still says who tried |
| Proxy route allowlist | ✅ | Not a prefix match; unlisted routes are refused and traversal is rejected before matching |
| Per-player map privacy | ✅ | Four modes, **defaulting to the most private**. `hidden ⟺ viewer_rank <= hider_rank`, so a player can never hide from staff and peers *are* concealed |
| Per-base visibility | ✅ | Gated on the guild master, with a fallback when the master has no account. Fails **closed** when no world has been parsed |
| Undiscovered-content policy | ✅ | `everyone` / `detail` / `nobody`, filtered server-side — a UI that received everything and hid some would hand out the answers in the network tab |
| Non-root container | ✅ | **2026-07-28**, uid 1000 |
| CSRF tokens | 🟡 | Mitigated by `SameSite=Lax`, not eliminated |
| 2FA / password reset flow | 🔴 | |
| Dependency scanning | 🔴 | |

## 8. Not built (and why)

| Item | Reason |
|---|---|
| Player and technology imports | Container and Pal imports ship; these two stay refused with a reason rather than half-validated |
| Solo-world *extraction* | Would delete every other player's characters, Pals and bases — destroying the world it is meant to preserve. The uid remap is the useful half, and it is built |
| Full mod save parsing | Out of scope permanently. Detect-and-warn is achievable; parsing arbitrary mod data is not |
| Cheat mode / arbitrary value injection | **Deliberately never.** Directly opposed to the corruption-safety goal |
| 2FA / password reset | Open |
| Dependency scanning in CI | Open (`npm audit` + `pip-audit`) |
| Multi-server management | Would reshape the data model |
| Postgres / Redis | Wrong scale. SQLite is correct here |

---

## The breeding data merge · ✅ **DONE** (2026-07-28)

`scripts/build-breedingdata.py`. The source data was already local — no download, no
scraping, no new dependency:

```
refs/PalWorldSaveTools-main.zip
  └── palworldsavetools/resources/game_data/breedingdata.json   (7.1 MB, MIT)
```

**A merge, not a regeneration.** `refs/` ships the game's own tables but not a full pair
expansion — `parent_to_children_formula` covers 44 parents. Reconstructing the combi-rank
formula to fill the gap was tried and **rejected**: against the known-good palcalc table
the best reconstruction agreed only **77.5%** of the time, across every plausible
tie-break. Shipping a formula that is wrong one time in four is worse than slightly stale
data. So palcalc's 44,850 pairs stay as the base and `refs/` layers on top.

Result: **44,850 → 46,655 pairs, 299 → 305 Pals.**

Three earlier claims of mine were wrong, and the investigation is what corrected them:

| Claimed | Actually |
|---|---|
| "6 Pals genuinely missing" | **1.** Only `WorldTreeDragon` (Astralym, Paldeck #204) is a released Pal. The other five carry `zukanIndex: -1` — present in the game files, absent from the Paldeck |
| "`CatMage + FoxMage` is wrong and `refs/` fixes it" | **Neither is wrong.** `unique_combos` lists that pair **twice, in the same parent order**, producing `FoxMage_Dark` *and* `CatMage_Fire` — and `child_to_parents_unique` names it as the unique parent pair of both. It genuinely has two outcomes |
| "299 vs 753 Pals" | 753 counts every `BOSS_`/variant form. Breedable species is ~300 either way |

The handling follows from that:

- **Ambiguous pairs are refused, not resolved.** Picking whichever entry came last in the
  file would invent a certainty the data does not have. The base answer stands and the
  build script prints the conflict. Representing "either of two" needs a schema change —
  `pairs` maps one key to one child.
- **Unreleased Pals are flagged, not dropped.** Their pair data is correct if they ever
  ship; `all_pals()` withholds them from the planner's goal list, because offering a
  target nobody can obtain is worse than not listing it.
- **Existing spellings win.** `refs/` says `Blueplatypus`, the save says `BluePlatypus`;
  keeping both would have put a duplicate Fuack in every dropdown. Names are folded onto
  the existing spelling **in the pair keys too** — without that, the merged pairs were
  keyed on a name no lookup could ever produce.

Verified: byte-identical output on re-run, no pair references an unnameable Pal, and the
table cannot shrink below palcalc's original 44,850. 13 tests in `test_breeding.py`.

---

## Phase 7 additions (2026-07-29)

### Bulk Pal editing
One change set applied to many Pals in a single guarded write. Atomic: every Pal
is validated before anything is written, and a verification failure on any one
of them rolls the whole world back. `autoExp` carries EXP along with a level
change — without it a level change leaves each Pal on its old EXP.

### Inventory slot editing
Set, change or clear individual container slots. Routed through the container
import write path, so it inherits every guarantee that already has: unknown item
ids refused, per-item stack ceilings enforced, durability slots refused (writing
over one orphans its `DynamicItemSaveData` record), and after writing the target
container must match while **every other container in the world is unchanged**.

This is also how arbitrary items get added to a world — any id in the bundled
2,466-item database is addressable.

### Illegal-Pal detection and repair
Scans every Pal against the same `editschema` bounds the editor enforces, and
repairs by clamping. Reports **violations** and **advisories** separately:

- **Violations** — IVs outside 0–100, condenser rank outside 1–5, level above the
  cap, EXP beyond its level. The reference world has **zero**.
- **Advisories** — an id the bundled tables do not list. Usually an NPC we simply
  lack, not a mod: 13 of the reference world's own characters are like this, and
  they carry IVs and passives exactly like a Pal, so there is no way to tell them
  apart. Never counted as cheating.

Repair only touches scalars. Passive-skill lists are an ArrayProperty and are
reported untouched; changing a species is not a repair.

---

## Reading the game's own files

`refs/palworld/` (a dedicated server install, gitignored) is now used for two
things beyond reference data:

- **`scripts/palpak.py`** lists all 158,444 entries in
  `Pal-LinuxServer.pak`. The pak is unencrypted, so no key is needed. This is how
  the World Partition cell grid was found — cell names encode coordinates, the
  cell size is 25,600 world units (174/174 fast-travel points land on an occupied
  cell), and that gave the World Tree landmass its true extent.
- **`DefaultPalWorldSettings.ini`** is the authoritative list of the 119 settings
  a 1.0 server accepts. `test_settings_ini.py` checks the parser, the presets and
  the highlight groups against it — which is how a highlight naming a setting
  that does not exist was caught.

### Skill editing
Passive skills and equipped active moves, on the existing Pal editor — they are
ordinary fields now, not a separate feature. At most 4 passives and 3 equipped
moves, no duplicates, every id checked against the bundled tables (1,905
passives, 375 moves). The learned-move pool (`MasteredWaza`) is not editable:
it is absent on most Pals, and creating an ArrayProperty means guessing its type.

### Pal duplication
Copy a Pal into a chosen palbox or party slot, optionally with an edit applied to
each copy (validated exactly like any other edit — a clone is not a way around
the bounds). Up to 50 at a time.

The destination is always explicit. There is no "find room anywhere" mode,
because quietly putting Pals into someone else's palbox is worse than an error.
Cloning a player character is refused outright.

Verification is stricter than an edit's, because this changes the shape of the
save rather than values in it: the character list and the target container must
each grow by exactly the requested count, every new Pal must resolve to its slot,
and no other container may change length.

---

## Discoveries (fast travel + effigies)

`GET /api/world/discoveries` returns all 174 fast-travel points and all 396
effigies, each marked `discovered` or not, folding in the save's per-player
flags. Both datasets are bundled, so the dashboard knows where everything is
regardless of what anyone has found.

Who sees the *undiscovered* half is set by `discoveryVisibility` on the Access
tab (or `DISCOVERY_VISIBILITY` in the environment):

| Level | Effect |
|---|---|
| `everyone` | Anyone who can see the map sees undiscovered markers too |
| `detail` *(default)* | Players see only their own finds; Trusted and above see all |
| `nobody` | Undiscovered markers are never sent to any session |

Filtering is server-side. A Player without `VIEW_DETAIL` can only ask about
their own character, and accounts link to characters through `users.steam_uid`.

Effigy data comes from the game pak via `scripts/extract-effigies.py` — 396,
each with the instance GUID that `RelicObtainForInstanceFlag` uses, which is what
makes the per-player join possible at all.
