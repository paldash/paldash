# Feature inventory

What exists **today**, as of 2026-07-28 (Phases 0–5 complete). Written because the
roadmap in `AUDIT.md` tracks *gaps* — anything already working simply does not appear
there, which makes finished features look missing.

Evidence: 64 backend routes, 24 backend modules, 13 UI components, 12 tabs,
449 backend + 46 frontend tests.

Legend: ✅ works · 🟡 works with a caveat · 🔴 not built

---

## 1. Server monitoring

| Feature | State | Notes |
|---|---|---|
| Live server status | ✅ | REST API + TCP + save-mtime + process scan |
| Player count / online list | ✅ | via Palworld REST API |
| Server settings viewer & editor | ✅ | `PalWorldSettings.ini`, CRLF-preserving |
| Settings presets | 🟡 | Applies presets; the full preset library is Phase 9 |
| Start / stop / restart container | 🟡 | Backend logic works, but the `docker` binary is **not in the runtime image**, so `STOP_COMMAND` fails. Manual `docker compose stop` works and is documented in the UI |
| CPU / RAM / disk history | 🔴 | Phase 8 |
| Broadcast / kick / ban / teleport | 🔴 | Phase 8 |

## 2. Map

| Feature | State | Notes |
|---|---|---|
| Interactive map, both landmasses | ✅ | Palpagos + World Tree as separate framings |
| 174 fast-travel points | ✅ | Validated 117/117 against a real player's unlocks |
| Bases, chests, palboxes, farms, production, defences | ✅ | ~3,400 placed objects, layered |
| Layer toggles + search | ✅ | |
| World Tree coordinate accuracy | 🟡 | `calibrated: false`, stated in the UI. No ground truth exists yet — the reference world has zero objects there |
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
| Game Pass saves | 🔴 | Phase 9 |

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
| Structured export (5 kinds) | ✅ | **Phase 6**, checksummed and verifiable |
| Container import | ✅ | **Phase 6**, dry-run diff + plan hash + scope-verified write |
| Base-scoped sorting | ✅ | **Phase 5** |
| Real stack limits | ✅ | **Phase 5**, `max(authoritative, observed)` |
| Category rules / priorities / profiles | 🔴 | Deferred — these decide *where an item goes*, which is editor semantics |

## 5. Breeding — **built, and often assumed missing**

| Feature | State | Notes |
|---|---|---|
| Breeding calculator | ✅ | 46,655 parent pairs |
| Special combinations | ✅ | Correct by construction (full pair table, not a formula reimplementation) |
| Palbox-driven suggestions | ✅ | Works from what players actually own |
| Breeding path search | ✅ | Depth-capped BFS to protect CPU |
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
| Audit log | ✅ | Every mutating action |
| Proxy route allowlist | ✅ | 39 frontend tests |
| Non-root container | ✅ | **2026-07-28**, uid 1000 |
| CSRF tokens | 🟡 | Mitigated by `SameSite=Lax`, not eliminated |
| 2FA / password reset flow | 🔴 | |
| Dependency scanning | 🔴 | |

## 8. Not built (and why)

| Item | Reason |
|---|---|
| General save editor | Phase 7. Returns 501. The write path is proven; per-field validation is not |
| Save import beyond containers | Phase 7. Container import ships; player/Pal/technology imports refused until the per-field schema exists |
| Migration (Steam ↔ dedicated ↔ Game Pass) | Phase 9 |
| Mod detection / plugin support | Phase 9; full mod save parsing is out of scope permanently |
| Cheat mode / arbitrary value injection | **Deliberately never.** Directly opposed to the corruption-safety goal |
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
