# Mined-data review — 28 September 2026, corrected 29 September

This is a fresh review of the local game archives and the existing extraction code, following the security/project audit. It separates newly verified facts from feature ideas. No live world was edited. Private save values, player names and account identifiers are excluded from this report.

## Coverage and limits

| Surface checked | Fresh result | What this establishes |
|---|---:|---|
| Server DataTable/CompositeDataTable/CurveTable assets | 475 unique names: 472 decoded, 3 refused; 183,228 rows in the decoded catalog | Actual export classes were checked, rather than assuming every `DT_` filename is a table. Interior fields can still be opaque. |
| Client tables, selected by those same classes | 475 unique names; zero missing from the server's decoded **or refused** lists | There is no additional client-only table family in these archives. Client name tables do not supply decoded numerical values. |
| Server package headers | 66,973 assets; zero header-read failures | Checked class coverage outside the existing asset miner's naming prefixes. |
| Non-table asset miner | 7,993 selected assets; 7,141 with decoded properties; 252 refusals | Re-ran the existing Blueprint/DataAsset/struct scan. A complete outer property walk does not make opaque inner values decoded. |
| Native binary reflection index | 100,368 identifiers, 1,800 enums / 11,554 values; unchanged from committed index | Names and types can be searched; native numeric defaults are still not recovered by this technique. |
| Reference save plus all six player save files | 873 field paths across seven files; 476 paths marked unread by the literal-name heuristic, 348 populated | Includes player RecordData and quest records, not only Level.sav. This is occupancy in one reference world, not the complete save schema. |
| Existing extraction code and bundles | Compared source mentions, inspected promising rows, and regenerated the loot projection in memory | A source-name match is a discovery aid, not proof a table is consumed or a feature is missing. |

The existing three-world save index remains useful and should not be replaced by the narrower fresh seven-file reference scan. Some historical fields occur only in another world or an older save. Current scratch outputs are under `/tmp/paldash-implementation-20260928/`; they are local audit evidence, not runtime data or publication artifacts.

## Strongest new findings

### 1. The game provides the map framing that World Tree was missing

`DT_WorldMapUIData` has two rows, `MainMap` and `Tree`. Its coordinate bounds were labeled opaque because the generic tagged-property reader does not decode native vectors. An inspection decoder using the same 24-byte, three-double Vector layout already verified by the boss-spawner extractor, plus the exact 16-byte Vector2D shape, reads:

| Region | World X bounds | World Y bounds | Referenced texture |
|---|---|---|---|
| MainMap | −1,099,400 to 349,400 | −724,400 to 724,400 | `/Game/Pal/Texture/UI/Map/T_WorldMap.T_WorldMap` |
| Tree | 347,351.5 to 689,148.5 | −818,197 to −476,400 | `/Game/Pal/Texture/UI/Map/T_TreeMap.T_TreeMap` |

Both rows specify an 8,192-pixel texture block and one block on each axis. The texture names match the installed map assets. The map values consume their declared byte widths; the surrounding table still decodes completely.

**Independent control:** applying the dashboard's established axis convention to MainMap gives scale `0.00282716731`, X offset `2048`, and Y offset `987.81225842` in its 4,096-unit display space. Compared with the independently fitted Palpagos transform, scale differences are 0.6745% and 0.0132%, and offset differences are 2.575 and 0.277 display pixels. This strongly supports the interpretation that these are image bounds. It does not independently establish a new orientation; the existing object-position control supplies that evidence.

**Implemented:** dedicated verified `extract-map-framing.py`; both regions' source bounds and texture references are bundled; all 174 travel points are checked; World Tree uses explicit game bounds. The UI describes source-derived framing. Do not silently label either the old estimate or a newly inferred orientation as ground-truth calibration. Keep the generic parser conservative rather than spreading a layout assumption to unrelated structs.

### 2. Field loot has slot probabilities in addition to relative item weights

`DT_FieldLotteryNameDataTable` contains 511 groups with 15 `ItemSlotN_ProbabilityPercent` columns. There are 1,129 nonzero slot values; 149 lie strictly between 0 and 100. The current economy bundle retains `WeightInSlot` but does not consume these slot-selection probabilities.

The item lottery table has 8,782 rows in 500 named groups. **498 of those groups join exactly** to the slot-probability table. Two do not: `SkyIsland_Treasure_Fishpond` and `Treasure_Grass_Grade_01`. Missing joins must stay unknown; they must not become 100%.

**Implemented:** economy extraction includes a separate slot-probability section, shown alongside relative item weights in item-source and dungeon panels. Only expose a computed unconditional item probability after the selection semantics and number of draws are independently verified. Neither table establishes chest respawn timing.

The old catalog's 8,777-row count is stale, but the current `_lottery` projection is **identical to the committed economy bundle**. A changed catalog count does not by itself prove the shipped loot list is outdated.

### 3. Several complete systems are cataloged but not exposed through existing extractors

These are verified table families, not promises that a whole new screen can be generated from a name alone:

| Candidate | Verified source | Useful addition and remaining check |
|---|---|---|
| Fishing guide | 1,252 fishing-lottery rows; 115 named groups; 135 fish-shadow rows; 78 fish-pond rows; 9 bait rows | Encounter weights, time restrictions, level bands, difficulty and reward-lottery links. Join fishing placement classes to group keys and validate bait/pond semantics before presenting rates. |
| Expeditions | 18 `DT_CharacterTeamMissionDataTable` rows | Duration, recommended strength, required element/count, maximum team size, unlock/challenge conditions and reward-group IDs. Combine reference requirements with actual saved mission progress; do not infer success probability from strength. |
| Operating Table | 54 `DT_OperatingTablePassiveSkillDataTable` rows | Which passive can be obtained, its stated money price and any required item. Verify all IDs against the passive and item catalogs and distinguish acquisition from breeding inheritance. |
| Crop planning | 18 `DT_MapObjectFarmCrop` rows | Product/count, growth time and distinct seeding, watering and harvesting work amounts. Growth time alone is not total farm throughput; worker suitability and server modifiers still matter. |
| Caged Pals | 139 `DT_CapturedCagePal` rows | Regional roster, relative weights and level bands. Match the correct cage-region group before adding a map popup; do not describe relative weight as a global encounter chance. |
| Passive stacking | 51 `DT_PassiveSkillEffectCondition` rows | `bIsHighestOnly` and `bIsFixedValue` can improve effect explanations and audit existing stat aggregation. Their scope needs a checked join to effect IDs; applying the flags globally by a partial string match would be unsafe. |
| Recruitment | Biome/rank roster tables and 75 appeal-passive rows | A recruitment reference can explain eligible rosters and appeal properties. Evaluate all table joins and conditions before claiming a recruitment probability. |
| Base-level tasks | `DT_BaseCampTask` | Show actual construction/worker requirements for the next level. `BuildObject1` is now decoded as a list, so an extractor must preserve alternatives instead of stringifying or selecting one. |

These should be added as focused bundle/backend/UI changes with coverage checks. They should not be mixed into the urgent security patch merely because the data is available.

### 4. “Condenser costs” is a misleading source label

The curated source document labels `DT_CharacterUpgradeMasterDataTable` as condenser costs. Its 20 rows instead carry `RequiredStaticItemId`, `RequiredItemNum` and `ResetRequiredMoney`, with `PalUpgradeStone` in the inspected first row. This is the Pal Soul/stat-upgrade system, not a valid basis for the number of Pals consumed in condensation.

**Plan:** correct the source label; verify all material IDs before building a Soul-cost/reset-cost guide. Keep condenser rank and Soul enhancements distinct in comparisons and calculators.

## Corrections to the cataloging process

1. **The 32 “client-only tables” conclusion was wrong.** Thirty-one `DT_PPSC_Weather_*` assets are `PPSkyCreatorWeatherPreset`, not DataTables. `DT_SupplyIncident_NPC_Sakura01` exists on the server but is one of its refused row decodes. Comparing client names against only successful server decodes mislabeled failure as absence. The miner now compares both successful and refused entries.
2. **Three server assets remain refused, not 32.** They are the Sakura supply-incident table, `DefaultReverbAssignmentTable`, and the `CT_AmmoMesh` CurveTable that the old decoder misidentified as a successful DataTable. The latter is audio metadata. No usable gameplay numbers should be invented from the former's name table.
3. **A schema catalog is not a value-drift check.** The server scan found five row-count changes and two sample changes relative to the committed index: `DT_ItemLotteryDataTable` 8,777 → 8,782, both `DT_MapObjectNameText` variants 616 → 617, both `DT_UI_Common_Text` variants 3,143 → 3,175, and changed samples in `DT_LabResearchText` and `DT_BaseCampTask`. Its `--check` primarily compares row counts and columns. A value can change without either moving. Catalog format 2 now includes stable full-row digests, archive provenance and explicit interior-opacity counts. Its `--check` re-run passed against the regenerated catalog.
4. **Deduplication currently picks the first filename in sorted path order.** For localized tables this can select an L10N copy. The current reader prefers the nonlocalized canonical path and catalogs language variants separately. `_Common` means shared/localization-related content in many cases; equal schemas do not establish equal values.
5. **The non-table miner is narrower than “all assets.”** Its selected prefixes are `BP_`, `PA_`, `DA_`, `ST_`. I checked every server asset header outside those prefixes too. The additional setting/preset classes found were weather, animation-compression and audio presets; no missed gameplay DataAsset family was established by that class check. Widget bytecode, native code, maps and arbitrary embedded exports remain different surfaces.
6. **The binary index has not changed.** It remains evidence for named mechanisms, including the breeding mutation model, not numerical values for their native defaults. This review did not discover a defensible base mutation rate or per-species mutation probability.

## Save fields worth a second, focused feature pass

The fresh scan confirms populated paths that no backend source mentions literally, including fishing counts, solo-arena clear counts, full-release quest records, supply-event timestamps/state, crop growth state and stored energy. Some may be consumed through generic traversal, so the 348 populated heuristic matches are not 348 missing features.

Prioritize:

- Fishing history and arena completion linked to the new reference guides.
- Full-release quest completion and active quest blocks, with exact localization joins.
- Supply/drop state based on recorded events, with honest timestamps rather than invented schedules.
- Farm growth and energy-storage panels that distinguish live saved state from reference maxima.
- Ownership/reference fields needed by the dedicated-server pruner: seller identity, builder identity, nickname modifier, old-owner lists, lock ownership, work assignments, worker containers and character storage. Deleting only a player file or guild member is insufficient.

Do not turn opaque byte runs, unknown flag dictionaries, cosmetic appearance fields or low-occupancy fields into editors simply because their names look useful. Preserve the three-world historical index; add another representative save when a feature is absent from this reference world.

## Recommended order

1. Finish and verify the security and save-safety fixes in the dated audit.
2. Implement the separate dedicated-server export against an immutable snapshot, with explicit shared-guild handling and refusal when pruning cannot be verified.
3. Add the map-framing extractor and loot-slot probabilities, because they correct existing claims or incomplete displays.
4. Add fishing, expedition and Operating Table references; then connect saved progress to them.
5. Add crop/recruitment/stacking enhancements after their joins and mechanics have independent checks.
6. Refresh catalog provenance and coverage so a future audit can detect changed values, not only changed names.

The local implementation now contains all twelve reference-guide sections, the map/loot changes, recorded fishing/arena/quest and crop/energy/supply state, and catalog provenance. See [the current implementation report](IMPLEMENTATION-2026-09-29.md) for verification. Unknown fishing/cage placement joins, quest-title translations, expedition saved progress and native mutation rates remain explicit unknowns rather than invented features.

## Additional decoder correction — 29 September

A buffer-end check accepted a wrong row offset whose supposed property type was `None`. Requiring property type tags, valid GUID flags, bounded field sizes, unique rows and the header’s declared row count corrected sixteen table decodes. This recovered four fishing groups (115 total), **97 invader entries** (240 total, 76 groups), and further arena, item, randomizer and outbreak rows. All 76 invader groups now match all 76 reward groups. The previous assertion that 32 mainland rewards were unused was a decoder failure, not a game-design fact.

Regenerated the invader, world-preset and economy bundles, plus the new guides. The catalog now has 472 successful decodes and three honest refusals; a lower successful-table count can mean improved correctness. Canonical-source selection and full-row digests prevent future value-only changes from passing a schema-only check.

The 12 guide sections contain 1,252 fishing encounters, 78 pond entries, 9 baits, 18 expeditions, 54 Operating Table choices, 20 Soul ranks, 18 crops, 139 cage entries, 901 recruitment rows, 75 appeals, 51 stacking-effect records and 35 base-level tasks. Requirements preserve construction alternatives. Expedition titles use the exact English mission-text join in the same server archive. Recruitment rank rows resolve their package object references to the actual roster tables.
