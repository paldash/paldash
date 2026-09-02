# Feature inventory

What exists **today**, as of 2026-07-30 (Phases 0–9 complete). Written because the
roadmap in `AUDIT.md` tracks *gaps* — anything already working simply does not appear
there, which makes finished features look missing.

Evidence, re-counted 2026-08-12: **145 backend routes, 67 backend modules,
44 UI components, 34,756 lines of backend Python against 27,236 lines of backend
tests.** The previous figures here (108/41/24, 17,435/11,571) were roughly half
of these — a header labelled *evidence* that had not been re-measured since it
was written. Re-count it when you change it; `grep -c '^@app\.'` and `wc -l` are
the whole job.

Legend: ✅ works · 🟡 works with a caveat · 🔴 not built · ⚪ out of scope

---

## 1. Server monitoring

| Feature | State | Notes |
|---|---|---|
| Live server status | ✅ | REST API + TCP + save-mtime + process scan |
| In-game day & clock | ✅ | 2026-08-12 (task #79). "Day 481 · 12:20" in the status strip, from `GameTimeSaveData` — **from the save**, so it survives the server being off. Units verified against a control: two backups 24 real hours apart put a game day at 29.2 real minutes. The **day** is plain; the **time** is marked, because it is not established whether the counter is seeded with `PalWorldTime_GameStartHour = 5`. No day/night indicator, deliberately — night is a four-hour window and a five-hour error could invert it |
| Player count / online list | ✅ | via Palworld REST API |
| Server settings viewer & editor | ✅ | `PalWorldSettings.ini`, CRLF-preserving |
| Settings presets | ✅ | **Phase 9.** `boosted`, `small_server`, `punishing` and a derived `vanilla` reset, all checked against `DefaultPalWorldSettings.ini`'s 119 keys rather than against memory |
| The game's own difficulties | ✅ | Easy / Hard / Hardcore from `DT_OptionWorldPresetTable`, offered beside the hand-made ones and labelled *from the game*. Only keys that differ from the game's Normal are written |
| Preset cross-check | ✅ | **It found a bug.** The hand-made `hardcore` agreed with the game's on three rates and omitted `bHardcore` and `bPalLost` — player permadeath and Pal loss. Renamed to "Punishing" rather than silently gaining permadeath |
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
| Static world objects | ✅ | **51,921** from the game pak — 24,359 ore, 13,851 Pal spawns, 8,386 chests, 2,163 dungeon objects, 2,757 fishing spots, 220 NPCs/camps, 185 oil. Viewport-culled, capped at 2,000 drawn |
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
| Per-base **Pal** attribution | ✅ | 2026-07-30. Each base's `WorkerDirector` names its worker container; 11/11 resolve on the reference world, 165 of 1,905 Pals deployed. Previously documented as impossible, and shipped as a guild total stamped on every base — which summed to 5,152 against 1,905 Pals |
| Who is **actually** assigned to each job | ✅ | 2026-08-11 (`WorkSaveData`, task #87). The game's own assignment record, as opposed to `baseassign`'s ranking of who *should* be there. 160 jobs on the reference world, every one resolving to a placed structure, and the work type reached two independent ways that agree on all 160. A stale slot pointing at a Pal that no longer exists is **counted, not hidden** |
| Player progression / completion | ✅ | Exact denominators from bundled data |
| Milestone rewards (the game's own, **not Steam**) | ✅ | 2026-08-12 (task #89). The in-game reward NPC's 26 tiers, per player, read from their own save — offline, and for every player rather than only those who handed over a Steam key. Which tiers are *collected* is exact (the save names the row, 26/26); `BossDefeat` shows no progress bar because no save counter is establishable for it |
| Pre-1.0 (`PlZ` / zlib) saves | 🟡 | `palsav` supports it; no sample to verify against |
| Uid remap for co-op / another server | ✅ | **Phase 9** (`soloexport.py`). Writes a *copy*, so it is the one save feature safe to run while the server is up. Matches uids by value, not by key name — a key list misses 1,836 references |
| Self-serve "take my world into single-player" | ✅ | v1.1.0 (`selfexport.py`, My account tab). The player-accessible slice of the remap: source pinned to the caller's linked character, target fixed to the single-player host uid, copy pruned to their own guild — and **solo guilds only**, because a kept guild keeps its members' saves. One archive per account, hourly cooldown, downloads from the same card. `SELF_EXPORT_*` in `CONFIGURATION.md`; player walkthrough in `SINGLE-PLAYER-COPY.md` |
| Xbox / PS5 / Mac players | 🟡 | Platform is parsed and surfaced; no console player has ever been observed. `docs/CROSSPLAY.md`, task #33 |
| Game Pass saves | ⚪ | Out of scope. The extraction tool solves a Windows *file-location* problem, not the crossplay question it gets mistaken for |

## 4. Inventory & items

| Feature | State | Notes |
|---|---|---|
| Server-wide item totals | ✅ | 645 item types |
| Friendly names for items/Pals/NPCs | ✅ | Case-insensitive by necessity |
| Per-base inventory breakdown | ✅ | **Phase 5.** `VIEW_SELF` for your own guild's bases since 2026-07-30 — a Player could see their guild's *total* Wood on the Items tab but not which of their own chests it was in. `baseVisibility` does not widen it: a map pin and an inventory are different disclosures |
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
| **Where an item comes from** | ✅ | Click any row on the Items tab. Every recipe with its materials named, which Pals drop it at what rate, which loot tables hold it, which merchants stock it, which structure produces it, what it is a material *for*, and the technology chain to unlock the recipe. Catalogue data, so it needs no parsed world |
| **Full crafting tree** | ✅ | Under the same panel: every material expanded to raw, with quantities. One Sky Beam Sword is 1,250 Stone, 348 Coal, 345 Ore and eleven more over a five-deep chain. Batches round up and the surplus is shown — a Gold Coin costs 30 Copper Ingots because the recipe makes 20,000 at a time. The four products with two recipes offer the choice, described by their materials |
| Dismantling in the tree | ⚠️ | **Named, never walked.** Sixteen recipes convert rather than produce — a Pal Sphere dismantles back into the Paldium it was made from — so they show as "also from" and are not expanded. Pal Souls therefore read as uncraftable, which is the game |
| **What a guild could craft** | ✅ | On the Base supply panel. Joins the recipe table against the same privacy-scoped base and guild-chest totals. Counts are **alternatives, not a plan** — crafting one consumes what another needs |
| Which workbench crafts a recipe | 🔴 | **No source.** `WorkableAttribute` is present on all 1,414 recipe rows and is 0 on every one. Not inferred, and both panels say so |
| Chest open rate | 🔴 | **No source.** `WeightInSlot` is relative within one field's slot; the share of that slot is shown, which is a different and defensible claim |

## 3b. NPCs on the map

| Feature | State | Notes |
|---|---|---|
| Named NPC layers | ✅ | 438 placed NPCs across seven role layers, replacing the anonymous "NPCs & camps" toggle — 141 of those 220 points were the generic `BP_MonoNPCSpawner` |
| Merchants located | ✅ | 4 Black Marketeers, 4 Medal Merchants, plus Pal Merchants and Wandering Merchants — named by the game, with the level each spawner uses |
| How | ✅ | A world **actor's** tagged properties, which `upackage.py` documented as undecodable. True of the client pak; the server pak's cells are tagged |
| Wandering merchant's live position | 🔴 | **Not in any file.** The incident tables give the NPC and its level with `SpawnLocation` (0,0) while 149 of 195 rows carry real coordinates, and merchants in the save have no position field at all |
| Role of an NPC | ⚠️ | A **name rule**, not a game column — `TalkBPClass` is a flavour label with 58 of 216 rows empty. Fails safe: unrecognised is "Other NPCs" |

## 4c. Progression

| Feature | State | Notes |
|---|---|---|
| Progression tab | ✅ | `/api/progress` had counted these since Phase 4 and **nothing rendered it** — the relic statue lines from #61 shipped backend-only |
| Every denominator says where it came from | ✅ | The game's own count, a published 1.0 figure, or the union of what players here have found — which is a floor that rises as people explore. Mixing those silently invents precision |
| What your effigies bought | ✅ | All 13 statue lines including untouched ones. `CapturePower` shows a rank and never a percentage: it carries 0.0 on all 15 ranks, so its effect lives somewhere other than that column |
| Named checklists | ✅ | *Which* towers, field bosses, regions and fast-travel points are left — the part that makes a count actionable |
| Tower and major bosses | ✅ | The game's own names ("Rayne Syndicate Tower"). 8 towers, checked against the eight `… Tower Entrance` fast-travel points, which come from a different file |
| Regions discovered | ✅ | 123, from `DT_WorldMapAreaData` — `areasFound` had **no denominator at all** before |
| Field bosses (Pals) | ✅ | 89 placed spawners with species and level |
| Field bosses (human) | ⚠️ | Named and counted, **no total.** The only enumeration available is the catalogue's 34 `BOSS_` NPCs and it contains a merchant and a quest NPC |
| Dungeons cleared | 🔴 | `FixedDungeonClearCount` is empty on every save examined, so there is no key shape to join dungeon names against. Reported as unavailable *with the reason* |
| Undiscovered half | ✅ | Dropped **server-side** per `discoveryVisibility`, recursively — `fieldBosses` nests its two halves and a top-level filter would leave the larger list intact |
| Raid bosses | ✅ | 11 summon rows, levels 35-80, with the item that summons each and what it drops. **Not a map layer** — altar-summoned, so no game file gives them a position, and the panel says so |
| Raid egg rewards | 🔴 | `EggPalIDAndWeight` is a `MapProperty` the table reader does not decode. Reported as *unread* rather than as an empty list |
| Base raid reference | ✅ | 44 attacker groups by biome and grade band, their loot, the build-triggered one (`Factory_Money`) and the cancel costs. On the Bases tab beside the supply report |
| Per-base raid forecast | 🔴 | **Two joins missing, neither a matter of effort.** A raid is bounded by a `InvadeGrade` whose meaning in save terms is unestablished — base level is the obvious candidate and is *not in the save at all* — and a base's biome is defined by trigger volumes in the world rather than by any table |

## 4b. Paldeck

| Feature | State | Notes |
|---|---|---|
| Browse every Pal | ✅ | 204 Paldeck entries from bundled data. Needs **no parsed save** — it describes the game, not your server, so it is `VIEW_BASIC` |
| Spawn habitat map | ✅ | 183 of 204 entries. A side map shading where the species is found, at streaming-cell resolution |
| Search | ✅ | Name, Paldeck number, internal id or element |
| Location variants merged | ✅ | `HadesBird` + `HadesBird_Electric` are one Helzephyr entry with the **union** of their ranges |
| Spawn *rates* | 🔴 | Not derivable. A sheet says a species is referenced by spawners in an area, not how often it appears |
| **Boss planner** | ✅ | Every boss with which elements beat it **and which of its own beat you** — not inverses, and the second is the half a one-sided planner drops. Field / raid / tower shown separately: a raid boss has no position, which is the game rather than a gap |
| Recommended level / party size | 🔴 | **No source.** A field boss carries its own level and that is shown; what level *you* should be is in no file, and "boss level + 5" is folklore. The task assumed party size differs by kind — what the data actually has is a `canModeChange` flag on raids, which is not the same thing |
| **Paldeck completion** | ✅ | On the Progression tab: which entries you are missing and how to get each — spawn count, a named pairing, or "no pairing produces this". Denominator is Paldeck **entries** (204), never species forms (753), so 100% is reachable |
| **Build planner** | ✅ | Ranks all 753 species at a level / stars / IV / soul / passive build you choose. Jetragon is the fastest ride at 3,300, which is the check that the column means what it looks like |
| Which Pals can be ridden | ✅ | 149 base species, from the game's own `RestrictionItems`. Incineram reads a 960 ride speed and is **not** a mount; the filter excludes it, and Galeclaw too |
| **Stars raise the speed of 96 species** | ✅ | **This row twice claimed the opposite.** A partner skill is a list indexed by condenser rank, so a 4-star Direhowl rides **20% faster** (0/10/12/15/20 across the stars) while most Pals gain nothing. Which figure moves varies too — Azurobe's is swim. Applied in the ranking, broken out per row as what the stars bought |
| Does the condenser also multiply the species speed column? | ⚠️ | **Still open, and a separate question from the row above.** It would be applied at load and invisible in every file, exactly as the work-suitability bonus was. Never reported as "no" — only a timed 0-star vs 4-star run settles it (#106) |
| Fastest *flyer* | 🔴 | **No source.** Whether a mount flies, swims or walks is in no game file; five avenues checked and recorded. Fastest **ride** is answerable and that is what this ranks |
| Element matchups in the ranking | ⚠️ | Only when you name a target element, and only on damage/bulk — using the game's own ×1.2, the same multiplier both ways. The un-multiplied figure stays in its own column |
| **Partner / ride skills** | ✅ | On the Paldeck, with the game's own name and description **at every condenser rank** — Silvegis "Aegis Shield" cuts your shield damage 65%→80% across the stars; Solmora Lux "Shocking Fish" changes your attack type to Electric while mounted. A **fifth** progression axis; this file previously listed four |
| Partner-skill lines the game fills itself | ⚠️ | 111 of 303 keep a `{ReferenceMsgId_*}` reference this project does not resolve. Shown as the game wrote it, flagged, never with an invented number |
| **Elemental resistance** | ✅ | On My Pals, as badges. `ElementResist_Fire_1` is a flat 15% reduction in incoming Fire damage and **311 of refworld's 1,905 Pals carry a resistance** that nothing here had ever shown. Spread evenly across all nine elements, which is the check the reader is not selecting on something else |
| Ailment immunity | ✅ | Burn, Poison, Freeze, Stun and five more. Every one in the game's data is **100%**, so it is immunity rather than a percentage, and it is shown as a word — not a number comparable with the element figures |
| "Survives 18% longer against Fire" | 🔴 | **No source.** How a 15% resistance composes with the chart's ×1.2 is stated in no file, so the two terms are shown as separate lines and never multiplied. A test asserts the payload contains no such product |
| `DamageRateIfDefender_*` as a resistance | 🔴 | **Not one — it is offensive.** The game's prose reads *"Damage vs Poison +70%"*: damage you *deal* to a poisoned defender. Named here because the field name says the opposite and this doc's own audit table listed it as a Pal buff |

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
| Breeding path search | ✅ | Depth-capped BFS to protect CPU. **Gender-aware** since 2026-07-30: it will not route through a pair you cannot make. The constraint binds on species you *own* only — a bred intermediate can be re-rolled until the gender is right, an owned Pal cannot |
| Scoped to your own palbox | ✅ | A plain Player gets the planner over their own Pals (`VIEW_SELF`); `allPalsVisibility` (default `trusted`) decides who sees everyone's. "Only ones I don't have" is on by default |
| Reachable-with-an-extra-step list | ✅ | Everything obtainable via an intermediate, shortest route each. Counts **breedings**, not BFS generations — a Pal can be two generations deep and need three pairings |
| Unreachable, and *why* | ✅ | "Reachable by species but not with the genders you own" is reported separately from "not reachable at all" — they call for opposite actions |
| What breeding cannot reach | ✅ | **2026-08-05.** Three groups from the game's own columns, not one "unbreedable" list: 24 that no pairing produces (`IgnoreCombi` — the legendaries and tower bosses you catch; they are still parents, and 26 of them breed true), 81 obtainable **only** from a pairing the game names outright (element variants *and* the Noct/Aqua legendary forms — the pairings are listed), and 3 the game names no pairing for while the shipped table offers one. A Pal missing from the planner is usually here rather than missing from the dashboard |
| Gender-dependent pairings | ✅ | Katress × Wixen, the game's only one, shown as both outcomes with the genders — read from `DT_PalCombiUnique` rather than the flat pair table, which can hold one of the two |
| Alpha hatch rate | ✅ | `Combi_BossPalRate = 0.05`, the game's own constant |
| Mutated eggs | ⚠️ quoted only | The game's two descriptions are shown verbatim and the payload states plainly that **no game file says what produces one, at what rate, or which species it hatches**. Report facts, not mechanics |
| Inheritance odds | ✅ | |
| Dataset currency | ✅ | **Merged 2026-07-28**: 305 Pals, +Astralym (#204), +1,803 pairs. See the merge note below |
| Pairing rule verified against the game | ✅ | `scripts/verify-breeding.py` re-derives the rule from the server pak: **96.92%** over all 46,352 comparable pairs. The residual has two named causes and is deliberately not tuned away |

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

**575 of those ids are flagged `bLegalInGame: false`, and 95 of them share their
display name with a legal item** — `Gunpowder` beside `Gunpowder2`, four dead
`Head001_*` tiers beside `Head001`. Typing the name used to pick between them
arbitrarily, and differently in the two editors. It now prefers the live one,
an exact id is still honoured literally, and the creator says which is which.

Nothing is hidden and nothing is refused: the flag is **not** "unobtainable" —
Key Spheres carry it and players hold them — and what it *does* mean is stated
by no game file, so the UI reports the fact and stops there.

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

## 2026-08-18 additions

### Respawning nodes on the map (#141)
The save's respawn clocks joined to bundled world positions: gatherable
actors' instance GUIDs are captured from the pak's L0 streaming cells
(30,708 of refworld's 31,774 spawner keys resolve — 96.6%), and the map's
"Respawning nodes" layer pins every node whose clock is still running, with
the remaining game-hours as of the last parse. Due timers respawn on
approach and are counted, never pinned. `/api/world/respawns`.

### Wild Pal egg spawn points
1,805 placements across 13 biome/grade classes — a category found by the
#141 GUID sweep, not by anyone looking for it. Its own map layer under
"Static world".

### Egg-move pools on the breeding planner (#139)
`DT_WazaMasterTamago` per species, cross-verified against
`DT_WazaDataTable.IgnoreRandomInherit` (47 of 47 pool moves are marked
randomly inheritable). An "Egg moves" button per offspring row shows the
pool — the pool only: no file states how many moves an egg rolls or at what
rate, and the payload says so.

### Random-dungeon guide (#136)
Per biome area: weighted enemy groups with level ranges, chest loot with
per-slot shares, EXP bonus. The areas render as ids because Pocketpair never
named them. On the Progression tab.

### Lifetime counters (#138)
Tower boss defeats, camps conquered, oil rigs cleared, NPC conversations,
items crafted, condenser rank-ups and mutations, per player, from
RecordData. Rendered only when the save carries them — absent is not zero —
and always as counts, never "n of N".

### Player buffs from party passives (#137)
Passives on party Pals that buff the *player* (`ToTrainer` effects), listed
per player on the roster. Per-effect rows, never summed: no file states the
stacking rule.

## Save-editing notes (moved from the README front page)

### Pal welfare
An affliction in Palworld is a **property that exists**: a healthy Pal carries
no `WorkerSick` field at all. Curing is therefore a *deletion* — it produces a
record identical to a Pal that was never ill, rather than a "well" value this
project invented; inflicting one is not offered. Feeding writes **two**
things: `HungerType` is a consequence of `FullStomach`, so clearing the flag
alone leaves fullness where it was and the game sets the flag straight back at
the next tick. The fullness figure offered comes from the highest reading
among the operator's own affected Pals and is shown before anything is
pressed.

### Moving a character between servers
`soloexport.py` is the one save operation that never writes to the live world:
it reads the world and produces a remapped **copy**, so it cannot corrupt
anything and does not require the server stopped. It matches uids **by value,
not by key name** — the four named keys the reference implementation rewrites
miss 1,836 references on a real world, 1,817 of them
`LastNickNameModifierPlayerUid` alone. A key list is also a promise about a
schema this project does not control; a field holding a player's uid *means*
that player whatever it is called.

### Teleport
Coordinate teleport is a **save edit**, so the server must be stopped — it
cannot unstick a player who is stuck right now, and there is no live
alternative. Verified against the shipped server binary rather than community
docs: the only teleport command is `TeleportToPlayerByIndex`, and both admin
teleports anchor to the **issuing admin's in-game character**. A headless
dashboard has no character in the world, so there is no anchor.

### Pal import
Export a Pal (or a whole player, which embeds the team) and import it back.
**Overwrite** writes onto Pals matched by instance id, so re-importing a
world's own export is a restore; **add as new** deep-copies a same-species
record already in the save and applies the file's fields — no template species
means a refusal naming it, because a Pal's record carries values specific to
its save. The preview lists every field it will **not** write (owner,
container, slot, guild — where a Pal *is*, not what it is), so nobody believes
an imported Pal changed hands.
