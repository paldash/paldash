# Where every fact about Palworld comes from

A map of every data source this project can read, what is in each, what it is
good for, and — just as important — what has been searched for and **is not
there**.

This document exists because two features shipped with documented claims that
some fact was "not in any game file", and both were wrong. The facts were in
sibling tables nobody had listed. A negative result that was never checked
properly is worse than no result, because it gets trusted and stops the next
person looking.

**Read this before designing a feature.** The exhaustive machine-generated
listing is `DATATABLES.md` (schema only, regenerate with
`scripts/mine-datatables.py`); this document is the curated version that says
what each thing is *for*.

---

## The one rule that decides everything

Palworld ships two paks, cooked differently, and this is the whole story:

| | Server pak | Client pak |
|---|---|---|
| Path | `refs/palworld/Pal/Content/Paks/Pal-LinuxServer.pak` | `refs/Pal-Windows.pak` |
| Size | 4.8 GB | 40.5 GB |
| Properties | **Tagged** — name, type and size inline | **Unversioned** — names stripped |
| DataTables | **Fully decodable, numbers included** | Name tables only |
| Textures | few | all 21,056 `.ubulk` |
| Localisation | Japanese only, as `FText` **source** strings | **16 languages** under `L10N/` — see §"Every display string" |

`FileVersionUE4` and `FileVersionUE5` are **0 in both**, so version fields do not
distinguish them. The tell is whether the name table contains type names like
`IntProperty`.

Neither is encrypted (zero key GUID, `bEncryptedIndex=0`), both are v11 and
Oodle-compressed, and this project already ships an Oodle decompressor for saves.
`scripts/palpak.py` lists entries without extracting.

**Never commit anything from `refs/`.** Beyond size, `refs/palworld/` contains a
`PalWorldSettings.ini` with live server passwords.

---

## 1. Server pak — DataTables

**471 unique tables, 182,962 rows, 32 refusals.** Read with
`scripts/uassettable.py`. Verification is that the property walk terminates
*exactly* at the end of the buffer — a reader that has drifted does not land on
the last byte. Do not trust a partial decode that "looks right".

A `StructProperty` tag carries its own length, so an unwalkable interior is
**skipped** to land on the next tag rather than abandoning the table. That
recovered 243 tables that a previous, over-broad refusal had thrown away.

Tables ending `_Common` are duplicates of the same schema; ignore them.

### 1.1 Pals — what a species *is*

| Table | Rows | What it gives you |
|---|---:|---|
| `DT_PalMonsterParameter` | 753 | The master Pal record: tribe, size, element(s), all stat scaling, work suitabilities, capture rate, food amount, rarity, zukan index, breeding rank, sale price |
| `DT_PalHumanParameter` | 433 | The same shape for humans — merchants, guards, hunters. **Use this to stop classifying NPCs as illegal Pals.** |
| `DT_PalBPClass` | 940 | Species → Blueprint class |
| `DT_PalSizeParameter` | 6 | Size categories, used by work assignment (`WorkableSizeMin/Max`) |
| `DT_PalExpTable` | 100 | The level curve. **Read `PalNextEXP`/`PalTotalEXP`, not `NextEXP`/`TotalEXP`** — those are the *player* curve and differ from level 2 |
| `DT_PalPlayerParameter` | 100 | The player's own stat curve per level |
| `DT_FriendshipRankTable` | 14 | Trust thresholds (`RequiredPoint`) — the numbers an older note called "not extractable" |
| `DT_PalCaptureBonusExpTable` | 4,999 | Capture-count EXP bonus |
| `DT_PaldexDistributionData` | 365 | **`dayTimeLocations` / `nightTimeLocations`** — the game's own habitat map |
| `DT_PalCombiUnique` | 258 | Unique breeding combinations (parent tribes + genders → child) |
| `DT_TalentUpItem` | 3 | What raises an IV |
| `DT_CharacterUpgradeMasterDataTable` | 20 | Condenser costs per rank |
| `DT_GainWorkSuitabilityRankItem` | 13 | The Pal Soul tickets. **Ships a *dummy* for oil extraction**, and carries no rank column — which is why no maximum work rank is enforced anywhere |
| `DT_GainStatusPointsItem` | 11 | Status point items |

`DT_PaldexDistributionData` is worth calling out: the habitat data currently
derived by intersecting spawner name tables (97.0% attribution, and only ever
"references this species") has a **direct, official source here**, split by time
of day.

### 1.2 Skills and passives

| Table | Rows | What it gives you |
|---|---:|---|
| `DT_PassiveSkill_Main` | 1,905 | Structured passive effects: `EffectType/EffectValue/TargetType` ×3, rank, lottery weight, target element. **Bundled** — this is what made stats correct |
| `DT_WazaDataTable` | 384 | Every move: element, power, category, cooldown, range |
| `DT_WazaMasterLevel` | 5,772 | Which move a species learns at which level |
| `DT_WazaMasterTamago` | 7,111 | Moves inherited via eggs |
| `DT_PartnerSkill` / `DT_PartnerSkillParameter` | 50 / 682 | Partner skill mechanics and per-species parameters |
| `DT_PassiveSkillEffectCondition` | 51 | Stacking rules (`bIsHighestOnly`, `bIsFixedValue`) |
| `DT_OperatingTablePassiveSkillDataTable` | 54 | Passive-skill prices at the Operating Table |

### 1.3 Bases, structures and work — the assignment problem

**This is the group that was wrongly declared missing.**

| Table | Rows | What it gives you |
|---|---:|---|
| `DT_MapObjectAssignData` | 271 | **Which work suitability each structure needs**, minimum rank, worker cap, sanity drain per tick, species/size/element restrictions, and up to two extra work slots. Farm plots carry three rows (Seeding, Watering, Collection) |
| `DT_MapObjectMasterDataTable` | 1,034 | Structure identity: HP, defense, material type, deterioration. **Says what a structure IS, not what it eats** |
| `DT_BuildObjectDataTable` | 498 | Build cost, capacity, required work amount, UI category |
| `DT_MapObjectItemProductDataTable` | 16 | What a production structure yields, and how fast |
| `DT_MapObjectFarmCrop` | 18 | Crop, growth time, and separate seeding/watering/harvest work amounts |
| `DT_BaseCampLevelData` | 35 | **Worker cap and base cap per guild, by base level** |
| `DT_BaseCampTask` | 35 | Base-level-up requirements |
| `DT_BaseCampWorkerSickDataTable` | 9 | Illness types, their work/move/satiety penalties, and palbox recovery chance |
| `DT_BaseCampWorkerEventDataTable` | 11 | Worker behaviour triggers, including `TriggerSanity` |
| `DT_LabResearchDataTable` | 168 | Lab research, keyed by required work suitability |
| `DT_PalInvader` / `DT_PalInvaderReward` | 143 / 76 | Base raids: who attacks, at what grade, and what drops |

### 1.4 Items, recipes and the economy

| Table | Rows | What it gives you |
|---|---:|---|
| `DT_ItemDataTable` | 2,466 | The item catalogue — already bundled as `gamedata.json.gz` |
| `DT_ItemRecipeDataTable` | 1,414 | **Full crafting recipes**: product, count, work amount, up to five materials, and `WorkableAttribute` (which work type crafts it) |
| `DT_TechnologyRecipeUnlock` | 588 | Tech tree: unlocks, cost, tier, level cap, prerequisites, boss requirements |
| `DT_ItemLotteryDataTable` | 8,777 | Loot tables with real `WeightInSlot` drop rates, min/max, chest grade |
| `DT_FieldLotteryNameDataTable` | 511 | Per-slot probabilities for field loot |
| `DT_PalDropItem` | 1,044 | **What each Pal drops, by level, with rates and min/max** |
| `DT_ItemShopCreateData` / `DT_ItemShopLotteryData` | 38 / 38 | Merchant stock and rotation |
| `DT_PalShopCreateData` | 8 | Pal merchants: roster, level range |
| `DT_ItemShopSettingData` | 3 | Which item is currency |
| `DT_StatusEffectFood` | 54 | Food buffs: effect type, value, duration |
| `DT_PalStaticItemIDRedirectData` | 29 | Renamed items — needed to read old saves |

### 1.5 The world: spawning, dungeons, bosses, fishing

| Table | Rows | What it gives you |
|---|---:|---|
| `DT_PalSpawnerPlacement` | 8,253 | **Every spawner's world `Location`**, name, type and placement kind |
| `DT_PalWildSpawner` | 1,691 | Spawner rosters: which Pals, level ranges, weights, time and weather conditions |
| `DT_BossSpawnerLoactionData` | 159 | Field bosses — 90 populated, with species, `Location` and `Level` |
| `DT_PalRaidBoss` | 11 | **Raid bosses**: summon item, level, egg weights, rewards. These are *summoned*, not placed, which is why they never appear in the location table |
| `DT_CapturedCagePal` | 139 | Cage spawns by field, weight, level range |
| `DT_Dungeon*` | 15–162 | Dungeon levels, enemy spawns, item lotteries and reward spawners |
| `DT_PalFishingSpotLotteryDataTable` | 1,252 | Fishing: which fish, where, when, difficulty |
| `DT_PalFishShadowDataTable` | 135 | Fish behaviour |
| `DT_PalFishPondLotteryDataTable` | 78 | Fish pond yields |
| `DT_PalRandomizer` | 739 | Randomiser mode's substitution table |
| `DT_RandomIncident*` | 187 tables | Nests, outbreaks, roaming duos, merchant encounters |
| `DT_SupplyIncident_*` | ~30 | Supply drop contents by biome |
| `DT_RespawnPointInfo` | 8 | Starting areas and what they are rich in |
| `DT_WorldMapAreaData` / `DT_WorldMapUIData` | 123 / 2 | Region names and the map's own coordinate mapping |

### 1.6 Progression, quests and players

| Table | Rows | What it gives you |
|---|---:|---|
| `DT_PalQuestData` / `DT_PalQuestLocationData` | 120 / 166 | Quests and their world positions |
| `DT_UniqueNPC` | 216 | Named NPCs |
| `DT_PlayerStatusRankMasterDataTable` | 279 | **Effigy ranks**: relic type, count required, effect rate |
| `DT_PalGameProgressPreset` | 74 | The game's own idea of a "level N player" |
| `DT_CharacterTeamMissionDataTable` | 18 | Team missions and their element requirements |
| `DT_Arena*` | 7–99 | PvP arena ranks, opponents and rewards |
| `DT_AchivementRewardNPC` | 26 | Achievement rewards (the game's own typo) |
| `DT_OptionWorldPresetTable` | 4 | **Difficulty presets** — every rate the INI exposes, as the game sets them |
| `DT_WorldSecurity_CrimeMasterDataTable` | 6 | Crime and bounty values |
| `DT_SkinDataTable` | 29 | Pal and player skins |

### 1.7 Not worth mining

- **46 foliage / test-map tables** (`DT_PL_*`, `DT_PV_*`, `DT_SL_*`,
  `DT_Battle_Royale_*`, `DT_pal_test_*`) — engine data, no game facts.
- **45 text tables** (`*Text`, `*NameText`, `SystemLocalize`) — these decode,
  and their `FText` source strings are **Japanese**, not English. Japanese is
  Palworld's source language. English and 15 other languages are per-language
  *overrides* of these same tables in the client pak's `L10N/`, read by
  `scripts/l10n.py`. Both halves matter: the server pak gives Japanese and the
  localisation keys, the client pak gives everything else.
- **25 icon/UI tables** — asset paths, already resolved by `install-icons.py`.

### 1.8 The 32 refusals

Listed with their errors in `DATATABLES.md`. "This exists and we cannot read it"
is a different and more useful statement than silence.

---

## 2. Server pak — Blueprint class defaults

**This is not limited to DataTables.** A Blueprint's class-default object is
tagged the same way, so every balance constant exposed as a UPROPERTY reads out.
`scripts/extract-game-settings.py` bundles all **347** from `BP_PalGameSetting`
at 6 KB.

The CDO is found by its `Default__` prefix — **never by size**, which would
silently pick a function body after an update.

**The decode verifies itself**, which is the only reason to trust it without a
second source: `CharacterMaxLevel = 80` and `CharacterMaxRank = 5` fall out
exactly, and both were previously held from sources that could not be checked.
`--verify` asserts them.

Constants already in use: `FriendshipPoint_AutoIncrementRequireSanity = 50` (the
welfare threshold), `DamageElementMatchRate = 1.2`.

Unused and available: `BaseCampAreaRange = 3500`,
`BaseCampNeighborMinimumDistance = 1500`, `PalBoxTimePeriodRecoverySick = 3600`,
`HungerParameterRate_Hunger = 10` / `_Starvation = 20`,
`DamageRate_SleepHit = 3.0`, `DamageRate_WealPoint = 1.5`,
`RarePal_AppearanceProbability = 0.1`, `Combi_BossPalRate = 0.05`,
`PlayerHPRateFromRespawn = 0.5`.

**Untried, and the obvious next target:** the build objects' own CDOs
(`BP_BuildObject_PalFoodBox` and friends). A container's accepted-item filter is
plausibly a UPROPERTY there, which is the one thing `DT_MapObjectAssignData` does
*not* answer. See task #58.

---

## 3. Server pak — the world itself

`Pal-LinuxServer.pak` lists **158,444 entries**. The main world is World
Partition: **9,978 streaming cells** named `MainGrid_L0_X<col>_Y<row>`, and those
names *are* coordinates.

**Cell size is 25,600 world units.** Measured, not looked up: at that value all
174 fast-travel points land on an occupied cell (12,800 gets 66, 51,200 gets
157). The same test with the same controls put all 90 field bosses on occupied
cells.

Connected components give one cluster per landmass, which is how the World Tree's
extent was pinned down. A future update's new landmass shows up the same way.

**The cell grid gives extent, not shape.** Occupied cells are not a coastline —
the game ships a cell for anything containing content, including open ocean. On
Palpagos the occupied set fills 51.8% of its bounding box against a 24.4% land
mask. `scripts/fit-worldtree.py` is the recorded negative result.

`scripts/upackage.py` reads `.umap`/`.uasset` **headers** — name table and export
map, giving every object's name, parent and exact byte range. That is enough for
attribution; property lists are not decodable here because these packages are
cooked unversioned. Offsets are measured (96-byte stride, name index at 16,
SerialSize at 28, SerialOffset at 36) and guarded by three assertions so a game
update raises rather than returning nonsense.

**`FPackageIndex` reads the opposite way to how it looks:** positive is an export
(`value - 1`), negative an import (`-value - 1`), 0 is null. Getting it backwards
produces no error — the hierarchy simply appears empty.

---

## 4. Client pak — three things, and only three

### Its DataTables hold nothing the server pak does not — measured, 2026-08-05

The raw count looks alarming: **935 DataTables in the client pak against 471 in
the server pak**, roughly double. It is duplicates. Deduping by filename the way
the server sweep does gives **503 unique**, of which **471 are the same tables**
that decode completely on the server side.

**32 exist only in the client pak, and they are all cosmetic:** 31
`PPSC_Weather_*` post-process settings (Clear, Cloudy, Fog, Overcast, …) and one
`SupplyIncident_NPC_Sakura01`. Nothing of substance.

So the client pak is **not** a second source of game rules, and the gap that
looked like 464 unexamined tables is zero. `scripts/mine-datatables.py --pak client`
regenerates `DATATABLES-CLIENT.md`; run it after a game update alongside the
server sweep, because a *new* client-only table would be the interesting case
this one turned out not to have.

### And its Blueprint CDOs are not usefully tagged

The server pak's `BP_PalGameSetting` class-default object decodes because its
properties are tagged, which is what yielded 347 tuning constants. That does
**not** transfer: `BP_BuildObject_PalFoodBox` in the client pak has 98 names of
which exactly **one** is a property type name (`ObjectProperty`), against the
many a tagged export shows.

So the CDO technique is a server-pak capability, and task #58 stays aimed there.
Checked so nobody re-runs the experiment on the wrong pak.



`refs/Pal-Windows.pak`, 185,003 files. Properties are unversioned, so **no
DataTable row decodes** — and per the section above, there is nothing in its
DataTables worth decoding anyway. What it is genuinely good for:

1. **Name tables** — "which things reference which things" is extractable even
   when values are not. This is how effigies (396, with the instance GUIDs saves
   key on) and the 99 `FBOSS` spawner placements were found.
2. **Textures** — all 21,056 `.ubulk` files. `scripts/extract-textures.py` reads
   UTexture2D packages and writes WebP. The mip is located by **anchor, not
   offset**: every `FTexture2DMipMap` is followed by its own `SizeX/SizeY/SizeZ`,
   and the payload precedes it at a length determined by dimensions and block
   format — two facts that must agree.
3. **Every display string, in sixteen languages — `Pal/Content/L10N/<lang>/`.**
   `scripts/l10n.py`. Settled 2026-08-05; the two eliminations below are worth
   as much as the answer, because each looked like it.

   **`.locres` is NOT the answer.** All 17 of
   `Pal/Content/Localization/Game/<lang>/Game.locres` are **37-byte
   placeholders with zero entries**, English included. Palworld does not ship
   translations that way. `scripts/locres.py` reads the format correctly — the
   archives are simply empty, which is a different and stronger statement than
   "we could not read them".

   **The source strings are Japanese.** They decode out of the *server* pak now
   that `uassettable` handles `FText`, but Japanese is Palworld's source
   language: `メルパカ` for Melpaca, `シラヌイ` for Shiranui. So that path
   yields Japanese and the localisation *keys*, never English.

   **The answer is per-language asset overrides**: 27 text DataTables under
   `L10N/<lang>/Pal/DataTable/Text/`, for de, en, es, es-MX, fr, id, it, ko, pl,
   pt-BR, ru, th, tr, vi, zh-Hans and zh-Hant — plus Japanese from the server
   pak, so seventeen in total.

   These are in the **client** pak, so properties are unversioned and
   `uassettable`'s tag walk cannot run — its tell is negative, zero property
   type names in the name table. **The shortcut of scanning `.uexp` for
   string-shaped bytes and pairing them with the name table in order is exactly
   the unverifiable half-decode this project refuses**, and names are the one
   field where a silent off-by-one stays invisible until a player reports the
   wrong Pal.

   What makes it decodable anyway is that an `FText` carries its **namespace and
   key inline**, so rows are self-identifying and bound *twice* — the row name
   from the package name table, the key from inside the value stream:

       row    PAL_NAME_Alpaca          (name table)
       key    PAL_NAME_Alpaca_TextData (inside the FText)
       source Melpaca

   A one-byte drift breaks that agreement everywhere at once, so the agreement
   rate measures alignment rather than arguing for it. Measured: **235,696 of
   235,696 rows**, 16 languages × 27 tables, zero refusals — and every language
   decodes exactly 14,731 rows, which is its own check, since a language is an
   override of the same table.

   The row offset is **searched for, not hardcoded**, on the principle
   `uassettable` already follows: the acceptance criterion is the verification —
   the walk must end exactly at the end of the export.

   Three traps, each of which produced plausible output rather than an error:

   - **An `FName` is (index, number) and the number is a suffix.**
     `ITEM_NAME_Accessory_NormalResist` with number 2 is the row
     `..._NormalResist_1`. Ignoring it collapsed 784 of 1,994 item rows onto
     their base names.
   - **History type 255 is a row nobody translated**, and its
     `bHasCultureInvariantString` flag is an **int32, not one byte**. Reading it
     as a byte desynchronised everything after it — and cost exactly 3 of 432
     table×language combinations, all non-English, which is how it survived the
     first pass.
   - **Untranslated markers have three spellings** — `en Text`, `en_text` and
     `Unidentified Pal`. A reader knowing only the first hands `en Text` to the
     UI as a display name.

   **This closes the third-party data dependency for names**, the server pak
   already supplying every number (verified 13,836 of 13,836). The joins are
   measured in task #69; `activeSkills` and `passives` agree with the archive
   **100%, zero disagreements**, which is the evidence the conventions are right.

   Two findings from that comparison worth carrying:

   - **The game distinguishes accessory tiers and the archive does not.**
     `Accessory_AT_2` is "Attack Pendant **+1**"; the archive calls all three
     tiers "Attack Pendant", so the dashboard shows three different items under
     one name today. Tier variants otherwise *inherit* the base name
     (`AncientArmor_2` → `AncientArmor`), so the rule is exact-first,
     base-fallback — never base-first.
   - **Technology names are rich-text references** — `<itemName id=|AIcore|/>` —
     because a technology that unlocks an item is named after it. The same tags
     appear in descriptions (`<characterName id=|X|/>`, `<mapObjectName …/>`). A
     resolver for these is the one piece still missing, and it must refuse an
     unresolvable id rather than leaking markup into the UI.

   Also newly available and not previously possible: **Paldeck flavour text**
   (`DT_PalLongDescriptionText`, 307 entries), **region names**
   (`REGION_Grass_1` → "Windswept Island", which resolves the MsgIDs
   `extract-progression.py` deliberately left raw), dungeon names, and the
   game's own **UI vocabulary** (`DT_UI_Common_Text_Common`, 3,099 rows —
   "Work Suitability", "Attack", "SAN", "Sort", "Filter"). If this dashboard is
   ever localised, most of its vocabulary is Pocketpair's own wording rather
   than a paraphrase of it.

**There is no map-icon set.** `Blueprint/UI/WorldMap/` holds exactly one icon
texture and `Texture/UI/Map/` holds 26 packages of map furniture. Palworld draws
POI markers from widget blueprints with generic shapes, so a per-category icon set
**does not exist as art**. The one icon that does exist is a pale plinth — 274 of
4,096 pixels above alpha 200 — worse than the stand-in it would replace.

---

## 5. INI files

`refs/palworld/DefaultPalWorldSettings.ini` is the **authoritative 119-setting
list**. Check presets and highlight groups against it rather than against memory:
`EggDefaultHatchingTime` sat in a highlight group matching nothing for months
because the real key is `PalEggDefaultHatchingTime`.

`DT_OptionWorldPresetTable` (4 rows) is the same settings as the *game* sets them
per difficulty — a cross-check the INI alone cannot give.

**The INI is not the source of truth on a containerised server.**
`thijsvanloef/palworld-server-docker` regenerates it from environment variables on
every start. `jammsen/palworld-dedicated-server` does **not** by default — it
ships `SERVER_SETTINGS_MODE=manual`. Variable names differ too (`REST_API_PORT`
vs `RESTAPI_PORT`). `settings_ini.ENV_MANAGED` names both spellings.

This cannot be *detected* from inside the dashboard container, so it is worded as
a conditional warning — but it can be **observed**: `backend/iniwatch.py` hashes
the file when we write it and again after a restart, and a change we did not make
is a fact about this deployment. `unknown` is a real answer meaning "not yet
observed", not "safe".

`PalWorldSettings.ini` holds live passwords. `settings_ini.SECRET_KEYS` masks them
on read and in the audit log.

---

## 6. The reference archive

`refs/PalWorldSaveTools-main.zip` → `resources/game_data/` — MIT-licensed,
validated against a real save (a player's 117 unlocked fast-travel IDs matched
117/117). Source of the bundled `gamedata.json.gz`: 2,466 items, 753 Pals, 1,905
passives, 588 technologies, 174 fast-travel points with coordinates, 2,468 icons,
both map textures.

Also in it, and easy to miss: `.opencode/skills/pst-stat-formula/SKILL.md` holds
the **stat formula derivation**, with a record of which terms were corrected
against in-game breakdowns. `src/palworld_aio/utils.py` holds the constants.
`backend/palstats.py` is a transcription of these two read together — diff against
that implementation if a game update moves a number.

**Its passives carry an English sentence, not numbers.** That was the stated
reason the passive stat term was zero for the entire life of the feature. The
server pak's `DT_PassiveSkill_Main` supersedes it with structured values.

---

## 7. The saves themselves

Covered in depth in `AGENTS.md`. Summary of what lives where:

| Source | Holds |
|---|---|
| `Level.sav` | Guilds, bases, all characters, all containers, map objects, the guild chest join |
| `<UID>.sav` | Per-player: inventory container ids, palbox/party ids, unlocks, progression flags, skins |
| `<UID>_dps.sav` | Dimensional Pal Storage — **not in `Level.sav` at all** |
| `<world>/backup/` | The server's own rotating snapshots. **Never sweep these into a backup archive** |

The rotating backups are underrated as a *research* tool: the durability-record
copy count was settled by diffing nine snapshots in the server's own lineage,
without stopping a server.

---

## 8. What is confirmed NOT anywhere

Recorded so nobody searches twice — and each of these was checked across all 471
tables, not merely "not found".

- **The element effectiveness chart.** No `Compatibility`, `Effectiveness`,
  `Weakness`, `AttributeDamage` or `ElementDamage` asset exists; the only element
  DataTable is `DT_PalAwakeningItemElement` (item → element, no multipliers), and
  `TargetElementType` appears only on passives. It lives in C++ or in a
  blueprint's unversioned properties. `backend/elements.py` therefore ships the
  relation as a documented constant — with a staleness detector, because it is
  the one thing here that can silently rot.
- **A damage multiplier for that chart.** `DamageElementMatchRate = 1.2` is the
  only element-damage constant in the settings object, with no halving or resist
  counterpart. The widely repeated "2x dealt, ½ taken" is reproduced by no file.
  `DamageUpElement_ByElementStatus` and `DamageDownElement_ByElementStatus` are
  exported by the binary and unread.
- **What a container accepts.** `DT_MapObjectAssignData` says what work a
  structure needs; nothing found so far says which items a Feed Box takes. Build
  object CDOs are untried (#58).
- **An awakened flag on a saved Pal.** No field in any save examined marks one, so
  the awakening term in the stat formula contributes nothing.
- **A maximum work-suitability rank.** `DT_GainWorkSuitabilityRankItem` has no
  rank column and no other table carries one. Asserting a ceiling would be
  inventing a number.

---

## 9. Regenerating

```bash
python3 scripts/mine-datatables.py            # the index — run this FIRST
python3 scripts/mine-datatables.py --grep sanity   # search names and columns

python3 scripts/build-gamedata.py             # -> backend/data/gamedata.json.gz
python3 scripts/extract-passive-effects.py    # -> passive_effects.json.gz
python3 scripts/extract-game-settings.py --verify
python3 scripts/extract-boss-spawners.py --verify
python3 scripts/install-map-assets.py
python3 scripts/install-icons.py
```

Every bundle needs an entry in `backend/data/provenance.json`; `test_gameversion.py`
enforces it. `scripts/jsonout.py`'s `write_json` honours the `.gz` suffix and sets
`mtime=0`, so unchanged input produces byte-identical output and a regeneration
can be **diffed** rather than trusted.

**Every extraction needs a check independent of the extraction itself.** The ones
that exist, as models to copy:

| Extraction | Its independent check |
|---|---|
| Game settings | Two independently-known constants land in the right places |
| Boss spawners | 90/90 on occupied cells at 25,600; both wrong cell sizes do worse |
| Passive effects | 1,754 of 1,759 match the game's own English prose |
| Fast-travel points | 117/117 against a real player's unlock flags |
| Guild chest join | 5/5 resolve to real `ItemContainerSaveData` entries |
| Base worker join | 44/44 bases across four different worlds |
