# Plan: extract everything, then build on it

Written 2026-08-04, after `docs/GAMEDATA-SOURCES.md` established that 471
server-pak DataTables decode and that two shipped features had refused to use
data that was sitting there.

**The ordering rule for this plan comes from that mistake: every extraction lands
before any feature that consumes it.** Building first and discovering the data
afterwards is what produced two wrong "no game file supports this" claims in
consecutive commits.

---

## Phase 0 — Not game data, and it gates the value of everything else

**#66 — tell open tabs a new version shipped.** Out of band from the phases
below, which are organised around game data. It belongs *first* for a reason
that is not about size: every phase after this one ships user-visible change,
and a tab left open never picks any of it up. Next.js content-hashes
`/_next/static/` so a navigation gets the new bundle, but nobody navigates a
dashboard they leave open — so the work lands invisibly for exactly the people
using it most.

Also in scope: `next.config.ts` sets no `headers()` at all, so `public/` assets
(icons, map textures) are unhashed and can serve stale after a regeneration.

Small, independent of everything below, and the only item here that should be
done *before* the next deploy rather than after.

**Check for a reverse proxy first.** A proxy caching HTML produces this exact
symptom and no app-side change fixes it.

---

## Phase 1 — Extraction only. No features.

Nine bundles. Each gets its own script, its own `provenance.json` entry, and its
own **independent verification** — a check that is not the extraction restating
itself. Models to copy are tabulated in `GAMEDATA-SOURCES.md` §9.

**Bundle only what the app reads.** `DT_ItemLotteryDataTable` is 8,777 rows and
`DT_PalSpawnerPlacement` is 8,253; shipping them whole would dwarf every existing
bundle. Where a table is large, extract the projection the feature needs and say
in the provenance note what was dropped.

| # | Bundle | Tables | Verification | Unblocks |
|---|---|---|---|---|
| 1.1 ✅ | `work_assign.json.gz` | `DT_MapObjectAssignData` | The 19 refworld base kinds absent from the table are exactly chests/beds/palbox/spa/walls — assert it | #44, #60 |
| 1.2 ✅ | `basecamp.json.gz` | `BaseCampLevelData`, `BaseCampTask`, `BaseCampWorkerSickDataTable`, `BaseCampWorkerEventDataTable` | Worker caps must be ≥ the largest worker container observed across four worlds (25) | #59, #60 |
| 1.3 ✅ | `economy.json.gz` | `ItemRecipeDataTable`, `PalDropItem`, `ItemLotteryDataTable`, `ItemShopCreateData`, `PalShopCreateData`, `StatusEffectFood` | Every `Product_Id` and `Material*_Id` must resolve in the item catalogue | #63 (was #35+#36) |
| 1.4 ✅ | `spawns.json.gz` | `PaldexDistributionData`, `PalWildSpawner`, `PalSpawnerPlacement` | Cell-grid test at 25,600 with 12,800/51,200 controls; **and** coverage must not regress below the 348 species the name-table trick found | #48 |
| 1.5 ✅ | `moves.json.gz` | `WazaDataTable`, `WazaMasterLevel`, `WazaMasterTamago`, `PalCombiUnique` | Every `PalID` resolves; every `WazaID` resolves after prefix normalisation | #64 |
| 1.6 ✅ | `progression.json.gz` | `PlayerStatusRankMasterDataTable`, `GainStatusPointsItem`, `WorldMapAreaData`, `PalQuestData`, `PalQuestLocationData`, `Dungeon*` | Quest positions land on occupied cells; area count matches the save's `FindAreaFlagMap` key count | #47, #61 |
| 1.7 ✅ | `raidbosses.json.gz` | `PalRaidBoss` | Count `InfoList` **entries**, not rows — 11 rows against 19 bundled `RAID_` species | #56 |
| 1.8 ✅ | `invaders.json.gz` | `PalInvader`, `PalInvaderReward`, `PalInvaderCancelCost`, `PalVisitorNPC` | Every `ItemId` resolves; every `GroupName` in the reward table exists in the invader table | #65 |
| 1.9 ✅ | `worldpresets.json.gz` | `OptionWorldPresetTable`, `OptionWorldModePresetTable` | Every key must exist in `DefaultPalWorldSettings.ini`'s 119 | #62 |

**1.10 — regenerate `gamedata.json.gz` from the game, not from the archive.**
Separate from the above because it is a *replacement*, and because it is the one
with a licensing dimension (see below).

Sources: `DT_ItemDataTable` (2,466), `DT_PalMonsterParameter` (753),
`DT_PalHumanParameter` (433), `DT_TechnologyRecipeUnlock` (588), plus the `*Text`
tables for display names.

**The verification is a diff against the current bundle.** Two independent
derivations of the same catalogue agreeing is far stronger evidence than either
alone, and any disagreement is a real finding about one of them. Do not replace
until the diff is understood.

**The engineering reason outweighs the attribution one:** `gamedata.json.gz` is
the only bundle with `gameBuild: null`, so `gameversion.status()` cannot say
whether it is stale. Re-deriving it from the pak gives it a real build id and
makes that banner work.

---

## Phase 2 — Features that are thinner than their data allowed

Ranked above new work: each is a shipped feature under-delivering, which is worse
than an absent one because it looks finished.

**2.1 · #60 — base worker cap.** Smallest, highest ratio. `palCount` currently
has no denominator, so "11 Pals here" answers nothing. Blocks #44.

**2.2 · #44 — base work assignment.** *The original request.* Look at a base,
work out what it needs from `DT_MapObjectAssignData`, and recommend who to put
there while accounting for Pals already at other bases and in parties. The save
already knows where everyone is; the cap comes from 2.1.

Recommend, never assign — moving Pals between containers is `palclone`/`charedit`
territory with its own verification.

**2.3 · #59 — welfare detail.** "40% work speed, palbox cures it in an hour"
instead of "sick".

**2.4 · #61 — effigy ranks.** The map counts relics without saying what they did.
Presentation over an existing join. Must respect `discoveryVisibility`.

**2.5 · #48 — habitats from real spawn data.** Retires the name-table workaround
and the caveat that it "must not be presented as a spawn-rate table".

**2.6 · #64 — breeding diff.** Deliverable is the *comparison* between palcalc
and the game's own table, not a replacement. Agreement is a result worth
recording; the planner currently rests on a third-party table with nothing
checking it.

---

## Phase 3 — New features

**3.1 · #63 — "where does this item come from"** (absorbs #35 and #36). Item
detail first: drops with rates, recipes, chests, unlocking tech. Pure catalogue
lookup, no world parse, no privacy scope. *Then* "what can this guild craft",
which reads the census and therefore is scoped.

**3.2 · #47 — progression tab.** Dungeons, quests-as-a-map-layer, and
`WorldMapAreaData` turning opaque area flags into "47 of 123". Server-side
discovery filtering, no exceptions.

**3.3 · #56 — raid bosses.** A reference panel, not a map layer — they have no
world position and inventing one repeats the tower-barrier mistake.

**3.4 · #65 — base raid forecast.** Establish what `InvadeGrade` maps to in the
save before making any per-base claim; if it cannot be established, ship the
static reference table and claim nothing about a specific base.

**3.5 · #62 — the game's own difficulty presets.**

---

## Phase 4 — Remaining

**#58** — build-object CDOs, the one thing `DT_MapObjectAssignData` does not
answer (what a container *accepts*). Cheap, proven technique, and it would let
#57's advisor stop hedging.

**#46** — largely answered by the sweep; narrow it to whatever the catalogue did
not cover rather than leaving it open as written.

**#51** — surface unread Pal fields. **#34** — 12 localisations from the client
pak's `L10N/`. **#41** — Pal skins. **#40** — the game's player-arrow marker.
**#33** — non-Steam player handling; blocked on evidence, not on data.

---

## Attribution: what can be dropped, and what cannot

Asked directly, and the answer splits three ways.

### Cannot be removed

| What | Why |
|---|---|
| **`palsav` + `palooz`** (GPL-3.0-or-later) | The save parser. Nothing else reads Palworld 1.0's Oodle `PlM` format — the PyPI `palworld-save-tools` fails outright. **This is the sole reason the project is GPL-3.0, and none of the work below changes that.** |
| **The element chart** (Rock Paper Shotgun) | Searched across all 471 tables: no effectiveness relation exists anywhere, and `TargetElementType` appears only on passives. `backend/elements.py` stays cited. Its staleness detector is the mitigation. |

### Can be removed

| What | Replaced by | Notes |
|---|---|---|
| **PST archive → `gamedata.json.gz` numbers** | `DT_ItemDataTable`, `DT_PalMonsterParameter`, `DT_PalHumanParameter`, `DT_TechnologyRecipeUnlock` | **Verified 2026-08-05: 13,836 of 13,836 values agree.** `scripts/verify-gamedata.py`. |
| **PST archive → names and descriptions** | `Game.locres` in the **client** pak, 17 languages | NOT the server pak's `*Text` tables — those are FText and opaque. Needs a LocRes reader: task #34. |
| **palcalc → breeding combinations** | `DT_PalCombiUnique` + `CombiRank` on `DT_PalMonsterParameter` | Only after #64's diff. |
| **Community "`MAX_LEVEL` = 80"** | `BP_PalGameSetting.CharacterMaxLevel` | **Already superseded**; some docstrings still say "community-sourced, not read from the game files". Needs a sweep. |
| **Community effigy count (313)** | Our own 396 from the relic cell | Already superseded. |
| **`reference_totals.json` web estimates** | The tech and item tables | Mostly already superseded; finish the remaining categories. |

### Can be removed, but the cost is high

| What | Replaced by | Notes |
|---|---|---|
| **PST archive → 2,468 icons** | Client-pak `UTexture2D` via `scripts/extract-textures.py` | Technique is proven. 2,468 textures is a real job for zero user-visible change. Low priority. |
| **PST archive → both map textures** | Same | Two files; cheaper than the icons and could ride along. |
| **PST → the stat formula** | Partially | The species scaling numbers already come from tables and the max constants now come from the CDO. **The formula's *structure* is derived knowledge that lives in C++ and is in no table** — `.opencode/skills/pst-stat-formula/SKILL.md` records which terms were corrected against in-game breakdowns, which is exactly the work we would otherwise be redoing. Keep the citation for the derivation. |

### The thing to be clear about

**Dropping these attributions does not relicense anything, and does not make the
bundled data more "ours".**

- The GPL-3.0 obligation comes from `palsav`, a code dependency, and survives all
  of it.
- Data extracted from the pak is Pocketpair's copyrighted content **either way**.
  Re-deriving it ourselves changes who we owe *credit* to; it does not change the
  copyright position, and `docs/LICENSING.md` should not be edited to imply
  otherwise.

The genuine benefits are narrower and worth doing on their own merits: every
bundle gets a real `gameBuild` so staleness detection works, every bundle becomes
regenerable from one source with our own verification, and the project stops
depending on a third party's release cadence for content updates.

---

## Sequencing

Phase 0 before the next deploy; it is independent of everything else and stops
later work landing invisibly.

Phase 1 is nine independent scripts and can proceed in any order; 1.1 and 1.2
first because Phase 2's top two items block on them. 1.10 last of the extraction
work — it is a replacement rather than an addition and should not be in flight
while other bundles are landing.

Then Phase 2 in the order given (2.1 → 2.2 is a hard dependency), Phase 3 in any
order, Phase 4 as capacity allows.

Run the full suite including integration before each push, not each commit — the
60 integration tests cost ~19 of the 21 minutes.
