# Feature inventory

What exists **today**, as of 2026-07-28 (Phases 0–5 complete). Written because the
roadmap in `AUDIT.md` tracks *gaps* — anything already working simply does not appear
there, which makes finished features look missing.

Evidence: 58 backend routes, 21 backend modules, 13 UI components, 12 tabs,
379 backend + 39 frontend tests.

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
| Base-scoped sorting | ✅ | **Phase 5** |
| Real stack limits | ✅ | **Phase 5**, `max(authoritative, observed)` |
| Category rules / priorities / profiles | 🔴 | Deferred — these decide *where an item goes*, which is editor semantics |

## 5. Breeding — **built, and often assumed missing**

| Feature | State | Notes |
|---|---|---|
| Breeding calculator | ✅ | 44,850 precomputed parent pairs |
| Special combinations | ✅ | Correct by construction (full pair table, not a formula reimplementation) |
| Palbox-driven suggestions | ✅ | Works from what players actually own |
| Breeding path search | ✅ | Depth-capped BFS to protect CPU |
| Inheritance odds | ✅ | |
| Dataset currency | 🟡 | 299 Pals; `refs/` has 304 — **6 missing 1.0 Pals and 1 wrong combo**. See §8 |

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
| Save import / export | Phase 6. Highest-risk surface in the product |
| Migration (Steam ↔ dedicated ↔ Game Pass) | Phase 9 |
| Mod detection / plugin support | Phase 9; full mod save parsing is out of scope permanently |
| Cheat mode / arbitrary value injection | **Deliberately never.** Directly opposed to the corruption-safety goal |
| Multi-server management | Would reshape the data model |
| Postgres / Redis | Wrong scale. SQLite is correct here |

---

## The breeding data swap

The replacement data is **already local** — no download, no scraping, no new dependency:

```
refs/PalWorldSaveTools-main.zip
  └── palworldsavetools/resources/game_data/breedingdata.json   (7.1 MB, MIT)
```

Measured difference against the current palcalc-derived tables:

| | Current | `refs/` |
|---|---:|---:|
| Pals | 299 | 304 |
| Precomputed pairs | 44,850 | formula + 253 unique combos |
| Special combos agreeing | — | 248 / 253 |

- **6 Pals genuinely missing today**: `BlackFurDragon`, `CandleWitch`, `ElecLion`,
  `Strawhatcat`, `VolcanicTurtle`, `WorldTreeDragon` — 1.0 additions.
- **1 disagreement**: `CatMage + FoxMage` → current says `FoxMage_Dark`, `refs/` says
  `CatMage_Fire`. `refs/` is extracted from the game's own tables and is the authority.
- **1 casing-only difference**: `BluePlatypus` vs `Blueplatypus` — exactly the trap the
  case-insensitive resolver already exists for.
- `PlantSlime_Flower` exists in the current data but not `refs/`; check before dropping it.

Earlier framing of this as "299 vs 753" was wrong: 753 is every entry in the Pal database
including `BOSS_`/variant forms, not breedable species. The real gap is 6 Pals, not 454.

Work required: a `scripts/build-breedingdata.py` alongside the existing
`build-gamedata.py`, plus tests pinning the 6 new Pals and the corrected combo. Small, but
it changes calculator output, so it is a change with tests attached rather than a drop-in.
