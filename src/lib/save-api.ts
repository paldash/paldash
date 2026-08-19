import type {
  BaseCamp,
  BaseStorage,
  DiscoveryPoint,
  DungeonGuide,
  EditPlan,
  EditResult,
  EditSchema,
  GuildInfo,
  PlayerSaveData,
  ContainerContents,
  BackupInfo,
  BulkEditPlan,
  BulkEditResult,
  PalContainer,
  ClonePlan,
  CloneResult,
  PalImportPlan,
  PalImportResult,
  SlotPatch,
  SlotEditPlan,
  SlotEditResult,
  PalCheckScan,
  PalRepairPlan,
  PalRepairResult,
  ServerState,
  CacheStatus,
  LifecycleStatus,
  MapObject,
  FastTravelPoint,
  Discoveries,
  ManagedUser,
  RolePreset,
  AuditPage,
  BackupListing,
  BackupDetail,
  BackupVerification,
  RestorePreview,
  PruneResult,
  BackupSchedule,
  ItemTotals,
  IniSettings,
  PalboxSummary,
  OffspringOption,
  PalSummary,
  MyPrivacy,
  ManageableBases,
  StaticWorldObjects,
  StaticWorldSummary,
  GameBuildStatus,
  WorldPackReload,
  ReachableTargets,
  BreedingLimits,
  CatalogueItem,
  CatalogueStructure,
  CraftableReport,
  NpcPlacements,
  BossEncounters,
  PaldeckCompletion,
  ProgressDetailReport,
  InvaderReport,
  RaidBossReport,
  BuildRanking,
  CraftTree,
  ItemSources,
  GuildMovePlan,
  GuildMoveResult,
  PalStats,
  BreedingPath,
  PaldeckListing,
  PaldeckDetail,
  PlayerRoster,
  ExportGuild,
  LanguageNames,
  WorldExportPlan,
  WorldExportResult,
  AnnouncementList,
  ScheduledAnnouncement,
} from './types';

const BASE = '/api/save';

async function saveFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`Save API ${res.status}: ${text}`);
  }
  return res.json();
}

// ─── Health & Status ────────────────────────────────────

export async function getBackendHealth(): Promise<{
  status: string;
  serverRunning: boolean;
  saveDir: string;
  worldGuids: string[];
  server?: ServerState;
  cache?: CacheStatus;
  breedingData?: boolean;
  lifecycle?: LifecycleStatus;
}> {
  return saveFetch('/health');
}

// ─── Server lifecycle ───────────────────────────────────

/**
 * Tell the backend a shutdown was just issued so it can watch for the server
 * coming back. The game's REST API only stops the process; whether anything
 * restarts it depends on how the server container is supervised.
 */
export async function noteShutdown(reason: string): Promise<LifecycleStatus> {
  return saveFetch('/server/note-shutdown', {
    method: 'POST',
    body: JSON.stringify({ reason }),
  });
}

export async function restartServer(): Promise<{ ok: boolean }> {
  return saveFetch('/server/restart', { method: 'POST' });
}

export async function stopContainer(): Promise<{ ok: boolean }> {
  return saveFetch('/server/stop-container', { method: 'POST' });
}

export async function startContainer(): Promise<{ ok: boolean }> {
  return saveFetch('/server/start-container', { method: 'POST' });
}

// ─── Access policy ──────────────────────────────────────

export interface AccessPolicyInfo {
  securityLevel: 'readonly' | 'safe' | 'full';
  guestVisibility: Record<string, boolean>;
  /** Who sees locations nobody has found: `everyone`, a role name, or `nobody`. */
  discoveryVisibility: string;
  /** Who sees other guilds' bases: `everyone`, a role name, or `own`. */
  baseVisibility: string;
  /** Who sees server-wide item totals rather than their own guilds'. */
  serverTotalsVisibility: string;
  /** Who sees everyone's Pals rather than their own. */
  allPalsVisibility: string;
  /** Per static-object category, same threshold vocabulary. */
  worldObjectVisibility: Record<string, string>;
  envCeiling: string;
  levels: { id: string; label: string; description: string }[];
  visibilityKeys: string[];
  discoveryLevels: { id: string; label: string; description: string }[];
  /** The same three values described for world objects, not for discoveries. */
  worldObjectLevels?: { id: string; label: string; description: string }[];
  /**
   * Per-category discovery dials, resolved to the level actually in force.
   *
   * `inherited` means no override is set and the category follows
   * `discoveryVisibility` — sent so the UI can show what applies rather than a
   * blank that looks unset.
   */
  discoveryCategories?: {
    id: string;
    label: string;
    level: string;
    inherited: boolean;
  }[];
  baseVisibilityLevels: { id: string; label: string; description: string }[];
  scopeLevels: { id: string; label: string; description: string }[];
  worldObjectCategories: { id: string; label: string; count: number }[];
  /** Named starting points for the four visibility thresholds. */
  visibilityPresets?: {
    id: string;
    label: string;
    description: string;
    values: Record<string, string>;
    /** Computed, not stored — it is true when every value still matches. */
    active: boolean;
  }[];
  allowedCapabilities: string[];
}

export async function getAccessPolicy(): Promise<AccessPolicyInfo> {
  return saveFetch('/policy');
}

export async function setAccessPolicy(update: {
  securityLevel?: string;
  guestVisibility?: Record<string, boolean>;
  discoveryVisibility?: string;
  baseVisibility?: string;
  serverTotalsVisibility?: string;
  allPalsVisibility?: string;
  worldObjectVisibility?: Record<string, string>;
  discoveryCategoryVisibility?: Record<string, string>;
  visibilityPreset?: string;
}): Promise<AccessPolicyInfo> {
  return saveFetch('/policy', { method: 'POST', body: JSON.stringify(update) });
}

// ─── Save editing ───────────────────────────────────────

export interface SortResult {
  ok: boolean;
  mode: string;
  /** Which ordering was applied: `id` or `category`. */
  order?: string;
  merged: boolean;
  baseId: string;
  scope: 'world' | 'base';
  containersInScope: number;
  containersTouched: number;
  slotsChanged: number;
  backupId: string;
  verified: boolean;
}

/**
 * Tidy containers. `mode` maps to a distinct capability, so the two are
 * separately grantable.
 */
export async function sortContainers(
  mode: 'stackables' | 'all',
  merge = true,
  baseId?: string,
  order: 'id' | 'category' = 'id'
): Promise<SortResult> {
  return saveFetch(`/edit/sort/${mode}`, {
    method: 'POST',
    body: JSON.stringify({ merge, baseId: baseId ?? null, order }),
  });
}

// ─── Character editing ──────────────────────────────────

/**
 * A Pal as `/api/pals` returns it — the parsed record plus name/icon
 * enrichment. Deliberately narrower than `PalInfo`, which describes a fuller
 * shape the save parser does not currently produce.
 */
export interface PalRecord {
  instanceId: string;
  ownerUid: string;
  characterId: string;
  speciesId: string;
  speciesName?: string;
  icon?: string;
  elements?: string[];
  paldeckNumber?: number;
  nickname: string;
  gender: string;
  level: number;
  exp: number;
  rank: number;
  isBoss: boolean;
  ivs: Record<string, number>;
  passiveSkills: string[];
  passiveSkillNames?: string[];
  /**
   * An equipped Pal skin, labelled. `label` is DERIVED — the game ships no
   * display name for a skin, only the id and a `TargetPalName` — so it is
   * this project's phrasing, which is what `derived` says.
   */
  skin?: {
    skinId: string;
    speciesId: string;
    palName: string;
    variant: number;
    label: string;
    derived: boolean;
  } | null;
  /**
   * What this Pal RESISTS, from its passives — the defensive half `stats` does
   * not cover, because the four figures the game prints are HP, Attack, Defense
   * and Work Speed and a 35% Fire reduction is none of them.
   *
   * `elements` is a percentage reduction in incoming damage; `ailments` is
   * immunity (every one in the game's data is 100). `softTo` is the *type
   * chart* read from this Pal's side and is independent of its passives — it
   * does not move when a resistance is added.
   *
   * **The two never combine.** `stackingKnown` is false because no game file
   * states how a resistance composes with the chart's x1.2, so a client shows
   * them as separate lines and never as an effective-HP figure.
   */
  resist?: {
    elements: Record<string, number>;
    ailments: Record<string, { percent: number; immune: boolean }>;
    other: Record<string, number>;
    conditional: Record<string, number>;
    when: Record<string, string>;
    softTo: string[];
    softToButResists: string[];
    any: boolean;
    stackingKnown: boolean;
    matchRate: number;
  } | null;
  /**
   * Movement this Pal's partner skill grants **at its own condenser rank** —
   * i.e. whether condensing it actually made it faster.
   *
   * Null when there is nothing to report, which is most Pals: only 96 species
   * have a partner skill that scales with rank. `riding` applies only while you
   * are riding it, and is kept apart from `always` for that reason.
   */
  partnerMovement?: {
    always: Record<string, number>;
    riding: Record<string, number>;
    skillIds: string[];
    condenserRank: number;
  } | null;
  /** Equipped moves, prefix stripped. Null when the Pal stores no EquipWaza. */
  activeSkills?: string[] | null;
  activeSkillNames?: string[];
  /** Species work levels from bundled game data, e.g. `{Mining: 3}`. */
  workSuitabilities?: Record<string, number>;
  rarity?: number;
  /**
   * Where the Pal physically is.
   *
   * `base` means assigned to a base's worker container — see
   * `parser.extract_base_workers`. `storage` means held by a structure the
   * guild built for the purpose (Dimensional Pal Storage, Global Pal Storage,
   * a Flea Market stand) — see `parser.extract_pal_storage`. `other` is what is
   * left: a container nothing references any more.
   *
   * `dimension` does not come from `Level.sav` at all — it is a per-player
   * `<UID>_dps.sav`, which nothing here opened until 7ece5fd. A Pal moved into
   * one was missing from every count rather than merely mislabelled.
   */
  location?: 'palbox' | 'party' | 'base' | 'storage' | 'dimension' | 'other';
  /** The base it works at or is stored at, when known. */
  baseId?: string;
  /** Which kind of store holds it, when `location` is `storage`. */
  storageKind?: string;
  /**
   * Calculated HP / Attack / Defense / Work Speed, and level progress.
   *
   * Null for humans and NPCs, which share the character map with Pals and carry
   * IVs exactly like one but have no stat scaling anywhere in the game data.
   *
   * These are **computed**, not read: the save stores only the inputs (level,
   * IVs, condenser rank, soul ranks, trust) and the game derives the rest at
   * load. See `backend/palstats.py` for the formula and where it comes from.
   */
  stats?: PalStats | null;
  /** Pal Soul upgrades, `Rank_*` in the save. Separate from the condenser. */
  soulRanks?: Record<string, number>;
  /** Trust points — the heart meter. */
  friendshipPoint?: number;
  /** The gold "lucky" variant. Distinct from `isBoss`, which is the alpha form. */
  isLucky?: boolean;
  /** That base's display name, joined at request time. */
  baseName?: string;

  // ── Condition ──────────────────────────────────────────────────
  //
  // AN AFFLICTION IS A PROPERTY THAT EXISTS, so every one of these is null on a
  // healthy Pal rather than carrying some "fine" value. That is what makes them
  // safe to test for truthiness, and it is why curing one is a deletion — see
  // `charedit.PAL_CLEARABLE`.

  /** Mood, 0–100. Below ~50 a Pal starts refusing to work. */
  sanity?: number | null;
  /** Fullness. See `maxFullStomach` for the ceiling. */
  fullStomach?: number | null;
  /**
   * The species' fullness ceiling — the game's own `MaxFullStomach`, 100 to 730.
   *
   * This comment used to say the ceiling "is not stored". It is: a column on
   * all 753 species, verified as a real cap (1,635 of the reference world's
   * Pals inside theirs, zero over, maximum ratio exactly 1.000).
   *
   * **Absent, not zero, for NPCs** — 99 of the reference world's characters
   * have no species row, and a zero denominator renders as infinitely hungry.
   */
  maxFullStomach?: number | null;
  /** `Hunger` or `Starvation`. Null when fed. */
  hungerType?: string | null;
  /** Depression, sprain, fracture, weakness, bulimia. Null when well. */
  workerSick?: string | null;
  /** `Minor` or `Severe`. Null when unhurt. */
  physicalHealth?: string | null;
  /** What it is doing at a base right now. */
  currentWork?: string | null;

  // ── Identity and history ───────────────────────────────────────

  /** The applied skin, when one is. */
  skinName?: string | null;
  /**
   * Null means the save has no such property, which is different from `false`
   * and the difference is load-bearing: `charedit` will not write a property
   * this Pal does not carry, so a field seeded from a flat `false` renders an
   * input that can only ever be rejected.
   */
  isImported?: boolean | null;
  isAwakened?: boolean | null;
  favoriteIndex?: number | null;
  /** Every previous owner, oldest first — the only record of a trade there is. */
  previousOwners?: string[];
  /** Who last renamed it. The other half of "where did this come from". */
  lastRenamedBy?: string | null;
  /**
   * When this Pal was obtained, as an ISO string with **no timezone claimed**.
   *
   * The save field is `OwnedTime` and its name misleads: it reads like a
   * duration and is an absolute .NET DateTime tick count. As a duration the
   * reference world's values would be two thousand years; as a timestamp they
   * are 2024-04-13 to 2026-07-28, which is that save's real lifespan.
   *
   * Wall-clock, not game time, so no server setting enters the conversion.
   */
  obtainedAt?: string | null;
  /** The raw ticks, for sorting without reparsing the string. */
  obtainedAtTicks?: number | null;
  /** Seconds of trust accrued at a base — the clock behind the heart meter. */
  basecampTrustSeconds?: number | null;
  /** A named story encounter rather than an ordinary spawn. */
  uniqueNpcId?: string | null;
  /** The learned-move pool, as opposed to the three equipped. */
  masteredSkills?: string[] | null;
  /**
   * Work ranks bought with Pal Souls, e.g. `{Handcraft: 2}`.
   *
   * `null` when the save has no `GotWorkSuitabilityAddRankList` at all — which
   * is not the same as `{}`. There is no entry to copy a struct shape from, so
   * the field is not editable on such a Pal; see `charedit._write_work_ranks`.
   */
  workRanks?: Record<string, number> | null;
  /** Work types the player switched off for this Pal. */
  workDisabled?: string[];
}

/**
 * Pals that need attention, and how many have each problem.
 *
 * The counts sum higher than `pals.length` on purpose — a Pal that is sick AND
 * starving is counted under both, because "how many are sick" is the question
 * being asked and deduplicating it would answer a different one.
 */
export interface WelfareReport {
  counts: Partial<Record<WelfareProblem, number>>;
  pals: (PalRecord & { problems: WelfareProblem[] })[];
  /** How many Pals were examined — the denominator for every count above. */
  scanned: number;
  /** The sanity threshold the backend used, so the UI never hardcodes it. */
  lowSanityBelow: number;
  /**
   * What each condition actually present costs. Only the ones on this roster —
   * a reference table of all eight beside two sick Pals is noise.
   */
  illnesses?: Illness[];
  /** The palbox cure chance is rolled once per this many seconds (3600). */
  palboxCurePeriodSeconds?: number | null;
  /** Sanity levels at which a worker misbehaves, highest first. */
  sanityThresholds?: SanityThreshold[];
  scope?: string;
  linkedToPlayer?: boolean;
}

/**
 * One condition, and what it costs.
 *
 * **`name` is the game's, and it can disagree with the id** — `Cold` displays as
 * "Sick". `nameIsInternal` is true only when the client pak was absent at build
 * time and the id had to stand in.
 *
 * `effectiveItemRank` is deliberately NOT resolved to a medicine item: which
 * item clears which rank is unverified, and naming one would be a mechanic
 * claim nothing here can back.
 */
export interface Illness {
  id: string;
  name: string;
  nameIsInternal: boolean;
  description: string;
  /** Signed percentages, as the game stores them. Cold is -5 work speed. */
  workSpeed: number;
  moveSpeed: number;
  satietyDecrease: number;
  /** Chance the palbox clears it per `palboxCurePeriodSeconds`. */
  palboxRecoveryPercent: number;
  effectiveItemRank: number;
}

export interface SanityThreshold {
  id: string;
  triggerSanity: number;
  assignableWork: boolean;
  assignableFixedWork: boolean;
}

export type WelfareProblem =
  | 'sick'
  /**
   * A leftover base-camp sickness flag on a Pal that is not at a base.
   *
   * `EPalBaseCampWorkerSickType` is a base-camp worker state, so it does not
   * describe a Pal in a box — every Pal one live server flagged as sick was
   * healthy in game, and the flags sit unchanged for far longer than any cure
   * window (an earlier version called this `sickRecovering`, which claimed a
   * process nothing observes). Reported separately rather than dropped: the
   * record really carries the field, and clearing it is how it gets tidied.
   */
  | 'staleSick'
  | 'injured'
  | 'hungry'
  | 'starving'
  | 'lowSanity';

export async function getWelfare(owner?: string): Promise<WelfareReport> {
  return saveFetch(`/welfare${owner ? `?owner=${encodeURIComponent(owner)}` : ''}`);
}

/**
 * Pals. Whose, is the backend's decision — below `allPalsVisibility` the
 * caller is pinned to their own character whatever `owner` says.
 */
export async function getPals(owner?: string): Promise<PalRecord[]> {
  return saveFetch(`/pals${owner ? `?owner=${encodeURIComponent(owner)}` : ''}`);
}

export async function getEditSchema(target: 'pal' | 'player'): Promise<EditSchema> {
  return saveFetch(`/edit/schema/${target}`);
}

export async function previewPalEdit(
  instanceId: string,
  changes: Record<string, unknown>
): Promise<EditPlan> {
  return saveFetch(`/edit/pal/${encodeURIComponent(instanceId)}/preview`, {
    method: 'POST',
    body: JSON.stringify({ changes }),
  });
}

/**
 * Apply an edit. `planHash` must come from the preview the user actually saw —
 * the backend re-plans and refuses if it no longer matches.
 */
export async function applyPalEdit(
  instanceId: string,
  changes: Record<string, unknown>,
  planHash: string
): Promise<EditResult> {
  return saveFetch(
    `/edit/pal/${encodeURIComponent(instanceId)}?planHash=${encodeURIComponent(planHash)}`,
    { method: 'POST', body: JSON.stringify({ changes }) }
  );
}

export async function previewPlayerEdit(
  uid: string,
  changes: Record<string, unknown>
): Promise<EditPlan> {
  return saveFetch(`/edit/player/${encodeURIComponent(uid)}/preview`, {
    method: 'POST',
    body: JSON.stringify({ changes }),
  });
}

export async function applyPlayerEdit(
  uid: string,
  changes: Record<string, unknown>,
  planHash: string
): Promise<EditResult> {
  return saveFetch(
    `/edit/player/${encodeURIComponent(uid)}?planHash=${encodeURIComponent(planHash)}`,
    { method: 'POST', body: JSON.stringify({ changes }) }
  );
}

// ─── Pal duplication ─────────────────────────────────────

export async function getPalContainers(): Promise<{ containers: PalContainer[] }> {
  return saveFetch('/edit/pal-containers');
}

export async function previewClone(
  instanceId: string,
  containerId: string,
  count: number,
  changes?: Record<string, unknown>
): Promise<ClonePlan> {
  return saveFetch('/edit/pal/clone/preview', {
    method: 'POST',
    body: JSON.stringify({ instanceId, containerId, count, changes: changes ?? null }),
  });
}

export async function applyClone(
  instanceId: string,
  containerId: string,
  count: number,
  planHash: string,
  changes?: Record<string, unknown>
): Promise<CloneResult> {
  return saveFetch(`/edit/pal/clone?planHash=${encodeURIComponent(planHash)}`, {
    method: 'POST',
    body: JSON.stringify({ instanceId, containerId, count, changes: changes ?? null }),
  });
}

// ─── Pal import ──────────────────────────────────────────
//
// The document is passed through untouched: it is a `saveexport` envelope whose
// checksum covers the payload, so anything this client "helpfully" normalised
// would fail verification on the backend.

export async function previewPalImport(
  document: unknown,
  mode: 'overwrite' | 'create',
  target: { instanceId?: string; containerId?: string } = {}
): Promise<PalImportPlan> {
  return saveFetch('/edit/pal/import/preview', {
    method: 'POST',
    body: JSON.stringify({
      document,
      mode,
      instanceId: target.instanceId ?? '',
      containerId: target.containerId ?? '',
    }),
  });
}

export async function applyPalImport(
  document: unknown,
  mode: 'overwrite' | 'create',
  planHash: string,
  target: { instanceId?: string; containerId?: string; templateInstanceId?: string } = {}
): Promise<PalImportResult> {
  return saveFetch(`/edit/pal/import?planHash=${encodeURIComponent(planHash)}`, {
    method: 'POST',
    body: JSON.stringify({
      document,
      mode,
      instanceId: target.instanceId ?? '',
      containerId: target.containerId ?? '',
      templateInstanceId: target.templateInstanceId ?? '',
    }),
  });
}

// ─── Bulk Pal editing ────────────────────────────────────

export async function previewBulkPalEdit(
  instanceIds: string[],
  changes: Record<string, unknown>,
  autoExp = true
): Promise<BulkEditPlan> {
  return saveFetch('/edit/pals/bulk/preview', {
    method: 'POST',
    body: JSON.stringify({ instanceIds, changes, autoExp }),
  });
}

export async function applyBulkPalEdit(
  instanceIds: string[],
  changes: Record<string, unknown>,
  planHash: string,
  autoExp = true
): Promise<BulkEditResult> {
  return saveFetch(`/edit/pals/bulk?planHash=${encodeURIComponent(planHash)}`, {
    method: 'POST',
    body: JSON.stringify({ instanceIds, changes, autoExp }),
  });
}

// ─── Inventory slot editing ──────────────────────────────

export async function previewSlotEdit(
  containerId: string,
  patches: SlotPatch[]
): Promise<SlotEditPlan> {
  return saveFetch(`/edit/container/${encodeURIComponent(containerId)}/slots/preview`, {
    method: 'POST',
    body: JSON.stringify({ patches }),
  });
}

export async function applySlotEdit(
  containerId: string,
  patches: SlotPatch[],
  planHash: string
): Promise<SlotEditResult> {
  return saveFetch(
    `/edit/container/${encodeURIComponent(containerId)}/slots?planHash=${encodeURIComponent(planHash)}`,
    { method: 'POST', body: JSON.stringify({ patches }) }
  );
}

/**
 * Creating equipment or an egg — an item that never existed in this world.
 *
 * A separate pair of calls from the slot editor's, mirroring the backend split.
 * The slot editor moves and stacks items that are already there and cannot touch
 * anything with a durability record; this brings one into being, and it is
 * audited under its own action for that reason.
 */
export interface ItemCreatePlan {
  ok: boolean;
  problems?: string[];
  planHash: string;
  containerId?: string;
  slotIndex?: number;
  staticId?: string;
  itemName?: string;
  icon?: string;
  /** `weapon`, `armor` or `egg` — the save's own name for the record type. */
  type?: string;
  durability?: number;
  maxDurability?: number;
  /**
   * The species an egg will hatch.
   *
   * **A CHOICE, as of 2026-08-06** — this comment used to say it was not. The
   * item id does not fix the species: `PalEgg_Dark_03` hatches 18 different
   * Pals, so a value inherited from a template is arbitrary rather than
   * determined. Pass `hatches` to decide it.
   */
  hatchesInto?: string;
  /** True when no species was asked for, so this one came from the template. */
  hatchesFromTemplate?: boolean;
  /** `hatchesInto` resolved to what a player reads. */
  hatchesName?: string;
}

export interface ItemCreateResult {
  ok: boolean;
  localId: string;
  containerId: string;
  slotIndex: number;
  staticId: string;
  itemName: string;
  type: string;
  durability: number;
  hatchesInto: string;
  backupId: string;
}

export async function previewItemCreate(
  containerId: string,
  slotIndex: number,
  itemId: string,
  durability?: number,
  hatches?: string
): Promise<ItemCreatePlan> {
  return saveFetch(`/edit/container/${encodeURIComponent(containerId)}/create/preview`, {
    method: 'POST',
    body: JSON.stringify({ slotIndex, itemId, durability, hatches }),
  });
}

export async function applyItemCreate(
  containerId: string,
  slotIndex: number,
  itemId: string,
  planHash: string,
  durability?: number,
  hatches?: string
): Promise<ItemCreateResult> {
  return saveFetch(
    `/edit/container/${encodeURIComponent(containerId)}/create?planHash=${encodeURIComponent(planHash)}`,
    { method: 'POST', body: JSON.stringify({ slotIndex, itemId, durability, hatches }) }
  );
}

// ─── Illegal-Pal detection ───────────────────────────────

export async function scanIllegalPals(): Promise<PalCheckScan> {
  return saveFetch('/palcheck/scan');
}

export async function previewPalRepair(instanceIds?: string[]): Promise<PalRepairPlan> {
  return saveFetch('/palcheck/repair/preview', {
    method: 'POST',
    body: JSON.stringify({ instanceIds: instanceIds ?? null }),
  });
}

export async function applyPalRepair(
  planHash: string,
  instanceIds?: string[]
): Promise<PalRepairResult> {
  return saveFetch(`/palcheck/repair?planHash=${encodeURIComponent(planHash)}`, {
    method: 'POST',
    body: JSON.stringify({ instanceIds: instanceIds ?? null }),
  });
}

export type ExportKind = 'world' | 'player' | 'guild' | 'base' | 'container' | 'pal';

/**
 * Download a structured export. Same download-via-fetch approach as reports, so
 * a 403 surfaces as an error rather than saving the error body as a .json.
 */
export async function downloadExport(kind: ExportKind, id?: string): Promise<void> {
  const query = id ? `?id=${encodeURIComponent(id)}` : '';
  const res = await fetch(`${BASE}/export/${kind}${query}`);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.error || detail.detail || `Export failed (${res.status})`);
  }

  const disposition = res.headers.get('content-disposition') ?? '';
  const named = /filename="([^"]+)"/.exec(disposition);

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement('a');
    link.href = url;
    link.download = named?.[1] ?? `palworld-${kind}.json`;
    link.click();
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** Check an export file without importing it. */
export async function verifyExport(file: File): Promise<{
  ok: boolean;
  problems: string[];
  kind: string | null;
  schemaVersion?: number;
  worldGuid?: string;
  exportedAt?: string;
}> {
  return saveFetch('/export/verify', { method: 'POST', body: await file.text() });
}

// ─── Per-base storage & reports ─────────────────────────

/** One player's item containers, with fill and how much of each is editable. */
export interface PlayerContainer {
  field: string;
  label: string;
  note: string;
  containerId: string;
  decoded: boolean;
  totalSlots?: number;
  usedSlots?: number;
  itemCount?: number;
  /** Slots holding a durability item; the writer refuses these. */
  lockedSlots?: number;
  editableSlots?: number;
}

export async function getPlayerContainers(
  uid: string
): Promise<{ uid: string; name: string; containers: PlayerContainer[] }> {
  return saveFetch(`/players/${encodeURIComponent(uid)}/containers`);
}

export async function getBaseStorage(): Promise<BaseStorage[]> {
  return saveFetch('/bases/storage');
}

export async function getOneBaseStorage(baseId: string): Promise<BaseStorage> {
  return saveFetch(`/bases/${encodeURIComponent(baseId)}/storage`);
}

export interface SupplyItem {
  itemId: string;
  itemName: string;
  /** Straight from the bundled catalogue — never derived from the id. */
  icon: string;
  count: number;
}

/** One container at a base, with what is in it. */
export interface SupplyContainer {
  containerId: string;
  kind: string;
  kindName: string;
  usedSlots: number;
  totalSlots: number;
  itemCount: number;
  items: SupplyItem[];
}

export interface SupplyStaple extends SupplyItem {
  floor: number;
  /** The game's own stack ceiling — 9999 for every material. Not the floor. */
  stackSize: number;
  below: boolean;
}

export interface BaseSupply {
  baseId: string;
  baseName: string;
  guildId: string;
  guildName: string;
  palCount: number;
  hungryPals: number;
  feedBoxes: SupplyContainer[];
  breedingFarms: SupplyContainer[];
  medicineBoxes: SupplyContainer[];
  staples: SupplyStaple[];
  /**
   * Observations, never instructions. The backend deliberately does not claim
   * what a structure consumes — no game file this project can read says so.
   */
  notes: { kind: string; text: string }[];
}

export interface GuildChest {
  guildId: string;
  guildName: string;
  containerId: string;
  usedSlots: number;
  totalSlots: number;
  itemCount: number;
  items: SupplyItem[];
  staples: SupplyItem[];
}

export interface SupplyReport {
  bases: BaseSupply[];
  /** One per guild, shared by all its bases — never a per-base figure. */
  guildChests: GuildChest[];
  materials: string[];
  floor: number;
  floorIsOperatorSetting: boolean;
  cakeItems: string[];
}

/** A Pal in a ranking. Enough fields to tell two of a species apart. */
export interface RankedPal {
  instanceId: string;
  name: string;
  speciesId: string;
  speciesName: string;
  icon: string;
  level: number;
  rank: number;
  gender: string;
  isBoss: boolean;
  elements: string[];
  location: string;
  baseId: string;
}

export interface WorkRankedPal extends RankedPal {
  /** `base` is the species table, `bought` is Pal Souls. Kept apart on purpose. */
  work: { base: number; bought: number; level: number };
  workSpeed: number;
  workSpeedCalculated: boolean;
}

export interface CombatRankedPal extends RankedPal {
  attack: number;
  defense: number;
  hp: number;
  score: number;
  /** The composite exists to give the list a default order, nothing more. */
  scoreIsArbitrary: boolean;
  calculated: boolean;
  /** Qualitative only — attached when `against` is set, never sorted on. */
  matchup?: 'strong' | 'weak' | 'neutral';
}

export interface WorkRankingReport {
  workTypes: { id: string; display_name: string; icon: string; index: number }[];
  rankings: { workId: string; workName: string; pals: WorkRankedPal[] }[];
  scope: string;
  mayScopeToOthers: boolean;
  linkedToPlayer: boolean;
  pals: number;
}

export interface CombatRankingReport {
  /** Named `ranking`, not `pals` — the scope block already owns `pals`. */
  ranking: CombatRankedPal[];
  against: string[];
  counters: {
    target: string[];
    strong: RankedPal[];
    weak: RankedPal[];
    neutral: RankedPal[];
    hasMultiplier: false;
  } | null;
  /**
   * Always false. The element chart carries a relation and no coefficient, so
   * there is no damage figure to render — see `backend/elements.py`.
   */
  hasMultiplier: false;
  elements: string[];
  /** False once the game ships an element the bundled chart has never seen. */
  chartIsCurrent: boolean;
  unknownElements: string[];
  scope: string;
  mayScopeToOthers: boolean;
  linkedToPlayer: boolean;
  pals: number;
}

export async function getWorkRanking(work?: string, limit = 10): Promise<WorkRankingReport> {
  const q = new URLSearchParams({ limit: String(limit) });
  if (work) q.set('work', work);
  return saveFetch(`/optimise/work?${q}`);
}

export async function getCombatRanking(
  against?: string[], limit = 20
): Promise<CombatRankingReport> {
  const q = new URLSearchParams({ limit: String(limit) });
  if (against?.length) q.set('against', against.join(','));
  return saveFetch(`/optimise/combat?${q}`);
}

export async function getBaseSupply(floor?: number): Promise<SupplyReport> {
  const query = floor === undefined ? '' : `?floor=${encodeURIComponent(floor)}`;
  return saveFetch(`/bases/supply${query}`);
}

export type ReportFormat = 'csv' | 'json' | 'txt';

export async function listReports(): Promise<{
  formats: ReportFormat[];
  reports: { id: string; title: string }[];
}> {
  return saveFetch('/reports');
}

/**
 * Download a report.
 *
 * Goes through fetch rather than a plain link so an auth failure surfaces as an
 * error instead of silently saving the JSON error body as a .csv, and so the
 * filename the backend chose is preserved.
 */
export async function downloadReport(
  report: string,
  format: ReportFormat,
  baseId?: string
): Promise<void> {
  const params = new URLSearchParams({ format });
  if (baseId) params.set('baseId', baseId);

  const res = await fetch(`${BASE}/reports/${report}?${params}`);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.error || detail.detail || `Export failed (${res.status})`);
  }

  const disposition = res.headers.get('content-disposition') ?? '';
  const named = /filename="([^"]+)"/.exec(disposition);

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement('a');
    link.href = url;
    link.download = named?.[1] ?? `${report}.${format}`;
    link.click();
  } finally {
    URL.revokeObjectURL(url);
  }
}

export async function requestRefresh(): Promise<{ started: boolean; reason: string }> {
  return saveFetch('/refresh', { method: 'POST' });
}

// ─── Map objects & items ────────────────────────────────

export async function getMapObjects(category?: string): Promise<MapObject[]> {
  return saveFetch(`/mapobjects${category ? `?category=${encodeURIComponent(category)}` : ''}`);
}

/**
 * Static pak-derived world objects inside a bounding box.
 *
 * The box is required in practice: there are 51,921 of these, and asking for all
 * of them is asking the browser to draw 51,921 markers. The response reports
 * `inView` and `truncated` so the caller can say what it is not showing.
 */
export async function getStaticWorldObjects(box: {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
  categories?: string[];
  /**
   * Per-category class selection, `{ ore: ['BP_..._RockCoal'] }`. A category
   * absent from this map is unfiltered; a category present with an empty array
   * asks for none of it.
   */
  kinds?: Record<string, string[]>;
  limit?: number;
}): Promise<StaticWorldObjects> {
  const query = new URLSearchParams({
    minX: String(Math.round(box.minX)),
    minY: String(Math.round(box.minY)),
    maxX: String(Math.round(box.maxX)),
    maxY: String(Math.round(box.maxY)),
  });
  // One category per request would mean four requests per pan. The backend
  // returns every category when none is named, and the layer toggles decide what
  // is drawn — so a request is only re-issued when the viewport moves.
  if (box.categories?.length === 1) query.set('category', box.categories[0]);

  // `category:class` pairs, not bare class names: a bare list would apply to
  // every category, so narrowing ore to coal would filter chests to nothing.
  // Only categories with an actual selection are sent, keeping the URL short.
  if (box.kinds) {
    const pairs: string[] = [];
    for (const [category, classes] of Object.entries(box.kinds)) {
      if (classes.length === 0) pairs.push(`${category}:`);
      else pairs.push(...classes.map((cls) => `${category}:${cls}`));
    }
    if (pairs.length) query.set('kinds', pairs.join(','));
  }

  if (box.limit) query.set('limit', String(box.limit));
  return saveFetch(`/world/objects?${query.toString()}`);
}

export async function getStaticWorldSummary(): Promise<StaticWorldSummary> {
  return saveFetch('/world/objects/categories');
}

// ─── World copy with a uid remap ─────────────────────────
//
// Reads the live world and writes a new directory, so unlike every other save
// operation here it does not need the server stopped.

/** Language codes with a bundle on this server. */
export async function getLanguages(): Promise<{ languages: string[]; default: string }> {
  return saveFetch('/world/languages');
}

/**
 * The game's own display names for one language, keyed by id.
 *
 * An overlay, not a replacement: the English `name` on every payload stays, and
 * search must test both — see `src/lib/language.ts`.
 */
export async function getLanguagePack(
  code: string
): Promise<{ lang: string; names: LanguageNames; searchNeedsEnglishToo: boolean }> {
  return saveFetch(`/world/language/${encodeURIComponent(code)}`);
}

/** The guilds an export could keep or drop, with what each owns. */
export async function getWorldExportGuilds(): Promise<{ guilds: ExportGuild[] }> {
  return saveFetch('/export/world-copy/guilds');
}

/**
 * `keepGuilds` is guild ids to KEEP. **Undefined means keep everything** — the
 * behaviour before the option existed, and the only safe reading of an absent
 * field for something destructive.
 */
export async function previewWorldExport(
  sourceUid: string,
  targetUid: string,
  keepGuilds?: string[]
): Promise<WorldExportPlan> {
  return saveFetch('/export/world-copy/preview', {
    method: 'POST',
    body: JSON.stringify({ sourceUid, targetUid, keepGuilds }),
  });
}

export async function createWorldExport(
  sourceUid: string,
  targetUid: string,
  planHash: string,
  keepGuilds?: string[]
): Promise<WorldExportResult> {
  return saveFetch('/export/world-copy', {
    method: 'POST',
    body: JSON.stringify({ sourceUid, targetUid, planHash, keepGuilds }),
  });
}

/** Whether the bundled positions still match the installed game build. */
export async function getGameBuildStatus(): Promise<GameBuildStatus> {
  return saveFetch('/world/build');
}

export async function acknowledgeGameBuild(buildId: string): Promise<GameBuildStatus> {
  return saveFetch('/world/build/acknowledge', {
    method: 'POST',
    body: JSON.stringify({ buildId }),
  });
}

/**
 * Re-read the bundled data packs from disk.
 *
 * Reloads; does not regenerate. Extraction needs the game pak and walks ~9,900
 * cell packages, which is not something to start from a web page beside a live
 * game server — and it could not persist anyway, since `backend/data/` is in the
 * image layer.
 */
export async function reloadWorldPacks(): Promise<WorldPackReload> {
  return saveFetch('/world/packs/reload', { method: 'POST' });
}

/**
 * Fast-travel statues, from bundled game data rather than the save.
 *
 * Returns an empty list rather than throwing when the reference data is missing,
 * so a missing bundle degrades the map instead of breaking it.
 */
export async function getFastTravelPoints(): Promise<FastTravelPoint[]> {
  const data = await saveFetch<{ points: FastTravelPoint[] }>('/world/fasttravel');
  return data.points ?? [];
}

/**
 * Fast-travel points and effigies, marked found/not-found.
 *
 * `uid` narrows to one player; omitted, an admin gets everyone's discoveries
 * folded together and a Player gets their own. Whether undiscovered locations
 * are included at all is decided server-side by the discovery policy — this
 * call cannot ask for more than the caller's role allows.
 */
export async function getDiscoveries(uid?: string): Promise<Discoveries> {
  return saveFetch(`/world/discoveries${uid ? `?uid=${encodeURIComponent(uid)}` : ''}`);
}

/**
 * Effigies alone, without the found/not-found join.
 *
 * The fallback for when `getDiscoveries` is unavailable — which is not an edge
 * case: that route requires a real account, so it fails for every guest, and it
 * serves both categories at once so either bundle failing takes both down. Fast
 * travel already had `getFastTravelPoints` to fall back to and effigies had
 * nothing, so the layer disappeared silently.
 *
 * `discovered` is absent from this shape, so the caller marks them unknown
 * rather than guessing. The undiscovered half is still withheld server-side when
 * the policy says so.
 */
export async function getEffigyPoints(): Promise<DiscoveryPoint[]> {
  const data = await saveFetch<{ points: DiscoveryPoint[] }>('/world/effigies');
  return data.points ?? [];
}

/** One pin a guild has dropped on its shared map. */
export interface GuildMarker {
  id: string;
  x: number;
  y: number;
  /**
   * **An integer, and deliberately unnamed.** The game's custom-pin sprites live
   * in a widget blueprint cooked with unversioned properties, so there is no
   * vocabulary to resolve it against — see `parser._guild_markers`. Do not
   * invent labels for these; draw one shape.
   */
  iconType: number;
  ownerUid: string;
  guildId: string;
  guildName?: string;
}

/**
 * Guild markers the caller is allowed to see.
 *
 * **Scoped server-side to the caller's own guilds** (staff excepted), because
 * the game itself calls these "Shared with Guild Members". `scope` says which
 * answer this is: an empty list because nobody has placed one, and an empty list
 * because you are in no guild, are different things and the second must not read
 * as a broken layer.
 */
export async function getGuildMarkers(): Promise<{
  points: GuildMarker[];
  scope: 'all' | 'guild' | 'none';
  guildsVisible: number;
  linkedToPlayer: boolean;
}> {
  const data = await saveFetch<{
    points: GuildMarker[];
    scope: 'all' | 'guild' | 'none';
    guildsVisible: number;
    linkedToPlayer: boolean;
  }>('/world/guildmarkers');
  return {
    points: data.points ?? [],
    scope: data.scope ?? 'none',
    guildsVisible: data.guildsVisible ?? 0,
    linkedToPlayer: Boolean(data.linkedToPlayer),
  };
}

/** One placed field boss: species, level and a verified world position. */
export interface BossSpawner {
  id: string;
  spawnerId: string;
  speciesId: string;
  name: string;
  icon?: string;
  elements?: string[];
  /** The level the game spawns it at. Unavailable until the pak reader was fixed. */
  level: number;
  x: number;
  y: number;
  z: number;
}

/**
 * The 90 placed field bosses.
 *
 * Not discovery-filtered, unlike effigies and fast travel: a field boss
 * respawns and is never collected, so the save has no per-player record to
 * filter against and inventing one would be worse than showing them all.
 */
export async function getBossSpawners(): Promise<BossSpawner[]> {
  const data = await saveFetch<{ bosses: BossSpawner[] }>('/world/bosses');
  return data.bosses ?? [];
}

/** Which item scopes this caller may ask for. Decided by the backend. */
export async function getItemScopes(): Promise<{
  guilds: { id: string; name: string }[];
  serverWide: boolean;
  bases: boolean;
}> {
  return saveFetch('/items/scopes');
}

export async function getItemTotals(guild?: string): Promise<ItemTotals> {
  return saveFetch(`/items${guild ? `?guild=${encodeURIComponent(guild)}` : ''}`);
}

/**
 * Every item **in the game**, by id and by friendly name.
 *
 * Not `getItemTotals`, which reports what this *world* holds. The slot editor
 * was built on that one, so any legitimate item nobody on the server happened to
 * own rendered as "not in this world" with no icon — the editor calling valid
 * input wrong while the backend, which validates against this catalogue, went on
 * to accept it.
 */
export async function getItemCatalogue(): Promise<{
  items: CatalogueItem[];
  total: number;
}> {
  return saveFetch('/world/items');
}

/**
 * Every buildable structure and what it costs to place.
 *
 * **A structure is not an item.** Palboxes, furnaces and breeding farms live in
 * their own game table with their own material columns, so the item catalogue
 * has never listed one — which is why the crafting tree came back empty for
 * every structure until the table was extracted.
 */
export async function getStructureCatalogue(): Promise<{
  structures: CatalogueStructure[];
  total: number;
}> {
  return saveFetch('/world/structures');
}

/**
 * Where one item comes from — recipes, drops, chests, merchants, production.
 *
 * The catalogue again, so it needs no parsed world. `known: false` in the reply
 * means there is no such item, which is a different answer from an item nothing
 * produces (`hasSource: false`).
 */
export async function getItemSources(itemId: string): Promise<ItemSources> {
  return saveFetch(`/world/items/${encodeURIComponent(itemId)}`);
}

/**
 * Rank every species at a chosen build.
 *
 * Catalogue data — this describes the game, so it needs no parsed world. The
 * roster-scoped version is `/optimise/*`, which ranks the Pals somebody owns
 * and is privacy-filtered accordingly.
 *
 * `against` names an ELEMENT, not a species: it is the one thing the game gives
 * a coefficient for.
 */
export async function getBuildRanking(options: {
  metric: string;
  level?: number;
  condenser?: number;
  iv?: number;
  souls?: number;
  passives?: string[];
  against?: string;
  rideable?: boolean;
  limit?: number;
}): Promise<BuildRanking> {
  const query = new URLSearchParams({ metric: options.metric });
  if (options.level) query.set('level', String(options.level));
  if (options.condenser) query.set('condenser', String(options.condenser));
  if (options.iv) query.set('iv', String(options.iv));
  if (options.souls) query.set('souls', String(options.souls));
  if (options.passives?.length) query.set('passives', options.passives.join(','));
  if (options.against) query.set('against', options.against);
  if (options.rideable) query.set('rideable', 'true');
  if (options.limit) query.set('limit', String(options.limit));
  return saveFetch(`/world/builds?${query.toString()}`);
}

/**
 * The same answer taken all the way down: every material, to raw, with totals.
 *
 * `prefer` names recipe rows for the four products with more than one way to
 * make them, and applies wherever that product appears rather than only at the
 * root — "make Carbon Fibre from Charcoal" is a statement about how you play.
 */
export async function getCraftingTree(
  itemId: string,
  count = 1,
  prefer: string[] = []
): Promise<CraftTree> {
  const query = new URLSearchParams({ count: String(count) });
  if (prefer.length) query.set('prefer', prefer.join(','));
  return saveFetch(
    `/world/items/${encodeURIComponent(itemId)}/tree?${query.toString()}`
  );
}

/**
 * What the materials in a guild's bases and chest could make.
 *
 * The census half, and therefore privacy-scoped. Each recipe is costed against
 * the whole pile on its own, so the counts are alternatives rather than a plan —
 * `simultaneous: false` says so in the payload.
 */
export async function getCraftable(guild?: string): Promise<CraftableReport> {
  const query = guild ? `?guild=${encodeURIComponent(guild)}` : '';
  return saveFetch(`/bases/craftable${query}`);
}

// ─── Server settings (PalWorldSettings.ini) ─────────────

export async function getIniSettings(): Promise<IniSettings> {
  return saveFetch('/settings/ini');
}

export async function writeIniSettings(
  changes: Record<string, string | number | boolean>
): Promise<{ applied: { key: string; from: string; to: string }[]; changed: boolean; restartRequired: boolean }> {
  return saveFetch('/settings/ini', {
    method: 'POST',
    body: JSON.stringify({ changes }),
  });
}

export async function applySettingsPreset(presetId: string) {
  return saveFetch<{
    applied: { key: string; from: string; to: string }[];
    changed: boolean;
    restartRequired: boolean;
    skippedKeys: string[];
  }>(`/settings/preset/${presetId}`, { method: 'POST' });
}

// ─── Guild membership ───────────────────────────────────

/**
 * Dry-run a guild move. Reads only; returns exactly what would change.
 *
 * Separate from the apply because the interesting part is what it *reports*: on
 * a solo guild the move also decides the fate of that guild's bases, and a
 * confirmation dialog that cannot name the number is asking for trust rather
 * than agreement.
 */
export async function previewGuildMove(
  playerUid: string,
  targetGuildId: string,
  transferBases: boolean
): Promise<GuildMovePlan> {
  return saveFetch('/edit/guild/move/preview', {
    method: 'POST',
    body: JSON.stringify({ playerUid, targetGuildId, transferBases }),
  });
}

/** Apply a previewed move. The hash is required — a stale plan is refused. */
export async function applyGuildMove(
  playerUid: string,
  targetGuildId: string,
  transferBases: boolean,
  planHash: string
): Promise<GuildMoveResult> {
  return saveFetch(`/edit/guild/move?planHash=${encodeURIComponent(planHash)}`, {
    method: 'POST',
    body: JSON.stringify({ playerUid, targetGuildId, transferBases }),
  });
}

// ─── Breeding ───────────────────────────────────────────

export async function getPalbox(owner?: string): Promise<PalboxSummary> {
  return saveFetch(`/breeding/palbox${owner ? `?owner=${encodeURIComponent(owner)}` : ''}`);
}

export async function getOffspring(owner?: string): Promise<OffspringOption[]> {
  return saveFetch(`/breeding/offspring${owner ? `?owner=${encodeURIComponent(owner)}` : ''}`);
}

/**
 * Pals that need an intermediate breeding step, with the shortest route to each.
 *
 * One request covers every reachable species — the backend runs a single BFS
 * rather than a route lookup per Pal.
 */
export async function getReachable(owner?: string): Promise<ReachableTargets> {
  return saveFetch(`/breeding/reachable${owner ? `?owner=${encodeURIComponent(owner)}` : ''}`);
}

/**
 * What breeding cannot reach, and why — reference data, no world needed.
 *
 * Deliberately **not** merged into the scoped planner requests. "Not reachable
 * from your Pals" changes with the palbox; "the game names no pairing for this"
 * does not, and serving them together would make the second look conditional on
 * the first.
 */
export async function getBreedingLimits(): Promise<BreedingLimits> {
  return saveFetch('/breeding/limits');
}

/**
 * The egg-move POOL for one species — the game's own DT_WazaMasterTamago
 * checked against DT_WazaDataTable.IgnoreRandomInherit. `poolOnly` is the
 * honesty flag: no file states how many moves an egg rolls or at what rate,
 * so the UI must present a pool, never a prediction. A species with no pool
 * 404s — a real answer, distinct from an empty list.
 */
export interface EggMovePool {
  species: string;
  name: string;
  moves: { id: string; name: string; element: string; power: number | null }[];
  poolOnly: boolean;
  note: string;
}

/** One regrowing gatherable (#141): where, what, and how long in GAME hours. */
export interface RespawnPin {
  x: number;
  y: number;
  category: string;
  cls: string;
  inGameHours: number | null;
}

export interface RespawnReport {
  pins: RespawnPin[];
  /** Pending timers with no bundled position — counted, never guessed onto the map. */
  pendingUnmapped: number;
  counts: Record<string, number>;
  clockTicks: number | null;
  note: string;
}

export async function getRespawns(): Promise<RespawnReport> {
  return saveFetch('/world/respawns');
}

export async function getEggMovePool(species: string): Promise<EggMovePool> {
  return saveFetch(`/breeding/eggmoves?species=${encodeURIComponent(species)}`);
}

/**
 * Every Pal in the game, from bundled data — a reference view, not a report on
 * your server, so it works with no parsed world.
 */
/**
 * Everyone who has played here, online or not.
 *
 * Merged server-side rather than in the browser: the backend already holds the
 * save roster, the live list and the account table, and privacy filtering has
 * to happen there anyway.
 */
export async function getPlayerRoster(): Promise<PlayerRoster> {
  return saveFetch('/players/roster');
}

export async function getDungeonGuide(): Promise<DungeonGuide> {
  return saveFetch('/world/dungeons');
}

export async function getPaldeck(): Promise<PaldeckListing> {
  return saveFetch('/world/paldeck');
}

export async function getPaldeckEntry(speciesId: string): Promise<PaldeckDetail> {
  return saveFetch(`/world/paldeck/${encodeURIComponent(speciesId)}`);
}

/**
 * A route to one target.
 *
 * Carries the same scope fields `/breeding/palbox` does. "Not reachable" is a
 * claim about a *specific set of Pals*, and the planner shows one header over
 * four endpoints — so a plan computed from your own box under a header reading
 * "all Pals on the server" reads as a wrong answer rather than a narrow one.
 */
export async function getBreedingPath(
  target: string,
  owner?: string
): Promise<BreedingPath> {
  const params = new URLSearchParams({ target });
  if (owner) params.set('owner', owner);
  return saveFetch(`/breeding/paths?${params}`);
}

export async function getAllPals(): Promise<PalSummary[]> {
  return saveFetch('/breeding/pals');
}

export async function isServerRunning(): Promise<boolean> {
  try {
    const data = await getBackendHealth();
    return data.serverRunning;
  } catch {
    return false;
  }
}

// ─── Save Data (Read-Only) ─────────────────────────────

export async function getBases(): Promise<BaseCamp[]> {
  return saveFetch<BaseCamp[]>('/bases');
}

export async function getGuilds(): Promise<GuildInfo[]> {
  return saveFetch<GuildInfo[]>('/guilds');
}

export async function getSavePlayers(): Promise<PlayerSaveData[]> {
  return saveFetch<PlayerSaveData[]>('/players');
}

export async function getPlayerSaveData(uid: string): Promise<PlayerSaveData> {
  return saveFetch<PlayerSaveData>(`/players/${uid}`);
}

export async function getContainerContents(containerId: string): Promise<ContainerContents> {
  return saveFetch<ContainerContents>(`/inventory/${containerId}`);
}

// ─── Backups ────────────────────────────────────────────

export async function getBackups(): Promise<BackupListing> {
  return saveFetch<BackupListing>('/backups');
}

export async function getBackupDetail(backupId: string): Promise<BackupDetail> {
  return saveFetch(`/backups/${backupId}`);
}

export async function createBackup(description?: string): Promise<BackupInfo> {
  return saveFetch<BackupInfo>('/backup', {
    method: 'POST',
    body: JSON.stringify({ description }),
  });
}

export async function verifyBackup(backupId: string): Promise<BackupVerification> {
  return saveFetch(`/backups/${backupId}/verify`, { method: 'POST' });
}

export async function renameBackup(backupId: string, description: string): Promise<BackupInfo> {
  return saveFetch(`/backups/${backupId}`, {
    method: 'PATCH',
    body: JSON.stringify({ description }),
  });
}

export async function deleteBackup(backupId: string): Promise<{ success: boolean }> {
  return saveFetch(`/backups/${backupId}`, { method: 'DELETE' });
}

export async function previewRestore(
  backupId: string,
  scope = 'world'
): Promise<RestorePreview> {
  return saveFetch(`/backups/${backupId}/preview?scope=${encodeURIComponent(scope)}`);
}

export async function restoreBackup(
  backupId: string,
  scope = 'world'
): Promise<{ success: boolean; rollbackId: string; restoredFiles: string[] }> {
  return saveFetch(`/restore/${backupId}`, {
    method: 'POST',
    body: JSON.stringify({ scope }),
  });
}

export async function pruneBackups(dryRun = true): Promise<PruneResult> {
  return saveFetch('/backups/prune', {
    method: 'POST',
    body: JSON.stringify({ dryRun }),
  });
}

export function backupDownloadUrl(backupId: string): string {
  return `${BASE}/backups/${backupId}/download`;
}

export async function getBackupSchedule(): Promise<BackupSchedule> {
  return saveFetch('/backups/schedule/config');
}

export async function setBackupSchedule(
  changes: Partial<{ enabled: boolean; frequency: string; pruneAfter: boolean }>
): Promise<BackupSchedule> {
  return saveFetch('/backups/schedule/config', {
    method: 'POST',
    body: JSON.stringify(changes),
  });
}

// ─── Accounts ────────────────────────────────────────────

export async function getRolePresets(): Promise<RolePreset[]> {
  return saveFetch('/roles');
}

export async function getUsers(): Promise<ManagedUser[]> {
  return saveFetch('/users');
}

export async function createUser(payload: {
  username: string;
  password: string;
  role: string;
  steamUid?: string;
  displayName?: string;
  mustChangePassword?: boolean;
}): Promise<ManagedUser> {
  return saveFetch('/users', { method: 'POST', body: JSON.stringify(payload) });
}

export async function updateUser(
  username: string,
  changes: Partial<{
    role: string;
    steamUid: string;
    displayName: string;
    disabled: boolean;
    password: string;
  }>
): Promise<ManagedUser> {
  return saveFetch(`/users/${encodeURIComponent(username)}`, {
    method: 'PATCH',
    body: JSON.stringify(changes),
  });
}

export async function deleteUser(username: string): Promise<{ ok: boolean }> {
  return saveFetch(`/users/${encodeURIComponent(username)}`, { method: 'DELETE' });
}

// ─── Recurring announcements ─────────────────────────────

export async function getAnnouncements(): Promise<AnnouncementList> {
  return saveFetch('/announcements');
}

export async function createAnnouncement(body: {
  message: string;
  interval: string;
  enabled?: boolean;
  onlyWhenOnline?: boolean;
}): Promise<ScheduledAnnouncement> {
  return saveFetch('/announcements', { method: 'POST', body: JSON.stringify(body) });
}

export async function updateAnnouncement(
  id: number,
  changes: Partial<{
    message: string;
    interval: string;
    enabled: boolean;
    onlyWhenOnline: boolean;
  }>
): Promise<ScheduledAnnouncement> {
  return saveFetch(`/announcements/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(changes),
  });
}

export async function deleteAnnouncement(id: number): Promise<{ ok: boolean }> {
  return saveFetch(`/announcements/${id}`, { method: 'DELETE' });
}

/** Sends now, attributed to you, and resets the interval. */
export async function sendAnnouncementNow(id: number): Promise<{ ok: boolean }> {
  return saveFetch(`/announcements/${id}/send`, { method: 'POST' });
}

// ─── Your own map privacy ────────────────────────────────
//
// There is deliberately no "read someone else's" or "set someone else's" call
// here, because the backend has no such route: an Owner switching a player's
// privacy off would defeat the point of the setting, and staff already see
// everyone below them regardless of it.

export async function getMyPrivacy(): Promise<MyPrivacy> {
  return saveFetch('/privacy/me');
}

export async function setMyPrivacy(mode: string): Promise<{ mode: string; ok: boolean }> {
  return saveFetch('/privacy/me', {
    method: 'POST',
    body: JSON.stringify({ mode }),
  });
}

/** Bases you may hide — your guild's, if you are its master. */
export async function getManageableBases(): Promise<ManageableBases> {
  return saveFetch('/privacy/bases');
}

export async function setBaseHidden(
  baseId: string,
  hidden: boolean
): Promise<{ baseId: string; hidden: boolean }> {
  return saveFetch(`/privacy/bases/${encodeURIComponent(baseId)}`, {
    method: 'POST',
    body: JSON.stringify({ hidden }),
  });
}

export async function changeOwnPassword(
  currentPassword: string,
  newPassword: string
): Promise<{ ok: boolean }> {
  return saveFetch('/auth/password', {
    method: 'POST',
    body: JSON.stringify({ currentPassword, newPassword }),
  });
}

// ─── Audit log ───────────────────────────────────────────

export async function getAuditLog(params: {
  limit?: number;
  offset?: number;
  action?: string;
  username?: string;
  result?: string;
} = {}): Promise<AuditPage> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') query.set(key, String(value));
  }
  const suffix = query.toString();
  return saveFetch(`/audit${suffix ? `?${suffix}` : ''}`);
}

/**
 * What work each base needs, who covers it, and who could fill the gaps.
 *
 * **`minRank` and `maxRank` are both real and mean different things.** A station
 * list can be tiered — the research lab has ten slots at ranks 1..10, so a
 * rank-1 Pal can start on it — while the Ancient Multi Product Mining rig has
 * ten slots all at rank 6. Coverage tests the minimum; `topStationStaffed` says
 * whether the hardest one is manned.
 *
 * **Advisory only.** Nothing here writes; `advisoryOnly` travels in the payload
 * so the UI cannot forget.
 */
export interface AssignCandidate {
  instanceId: string;
  name: string;
  nickname?: string;
  level: number;
  work: { base: number; bought: number; level: number };
  workSpeed: number;
  workSpeedCalculated: boolean;
  /** free | party | base | committed — what taking this Pal would cost. */
  availability: 'free' | 'party' | 'base' | 'committed';
  /** Where it is now, named. "Ore Outpost", "In a party", "Palbox / storage". */
  where: string;
}

/** One Pal the game has put on a job, with its level for that job. */
export interface ActualWorker {
  instanceId: string;
  name: string;
  speciesId: string;
  level: number;
  /**
   * Per work type this structure needs. **`null` is not zero** — it means the
   * character has no work table at all, which is what an NPC looks like, and
   * reporting it as rank 0 would call a merchant unfit for a job.
   */
  workLevels: Record<string, number | null>;
  /** The game's own integer. It names these nowhere; do not build a legend. */
  state: number;
}

export interface ActualJob {
  workId: string;
  structureId: string;
  structureName: string;
  defineId: string;
  workableType: string;
  /** Ranch and walk-around jobs, which have no fixed standing positions. */
  wanders: boolean;
  needs: string[];
  /** The structure asks for `Anyone` — any Pal qualifies, no suitability. */
  anyPalQualifies: boolean;
  assigned: ActualWorker[];
  /** Slots pointing at a Pal that no longer exists. Real, and worth showing. */
  staleAssignments: number;
  /** Standing positions. **NOT a capacity** — a Ranch with two Pals reads 0. */
  fixedPositions: number;
}

export interface ActualWorkBase {
  baseId: string;
  baseName: string;
  jobs: ActualJob[];
  workersAssigned: number;
  staleAssignments: number;
}

export interface WorkMismatch {
  kind: 'empty' | 'unsuitable';
  baseId: string;
  baseName: string;
  structureName: string;
  needs: string[];
  instanceId?: string;
  name?: string;
  speciesId?: string;
}

export interface ActualWorkReport {
  bases: ActualWorkBase[];
  /** Jobs outside any base — a world-placed structure being repaired. */
  unbased: ActualJob[];
  totalJobs: number;
  /**
   * Every assignment in the world, including Pals the caller may not see. A
   * job's own `assigned` is scoped; this is not, so a base does not read as
   * empty because of somebody else's privacy setting.
   */
  totalAssigned: number;
  staleAssignments: number;
  /** The game does not name the state integers. Do not invent a legend. */
  stateIsUnnamed: boolean;
  mismatches: WorkMismatch[];
  scope?: string;
  linkedToPlayer?: boolean;
  pals?: number;
}

export interface AssignNeed {
  work: string;
  workName: string;
  /** Lowest rank any station of this work accepts — what coverage tests. */
  minRank: number;
  /** Highest — what it takes to staff every station. */
  maxRank: number;
  /** Worker positions across the base. Not `workerMax`, which is usually unset. */
  slots: number;
  structures: string[];
  sanityPerTick: number;
  covered: boolean;
  coveredBy: { instanceId: string; name: string; level: number }[];
  /** Highest rank standing here now. Against `maxRank`, says if the top station is idle. */
  bestRank: number;
  topStationStaffed: boolean;
  candidates: AssignCandidate[];
  candidateCount: number;
}

export interface BaseAssignment {
  baseId: string;
  baseName: string;
  guildId: string;
  guildName: string;
  workerCount: number;
  /** Absent when the worker container did not resolve — never render `n/0`. */
  workerCapacity?: number | null;
  /** null means "capacity unknown", not "full". */
  freeSlots: number | null;
  needs: AssignNeed[];
  uncovered: number;
  structuresWithoutWork: number;
  advisoryOnly: boolean;
}

export async function getBaseAssignments(
  base?: string
): Promise<{ bases: BaseAssignment[]; scope?: string; linkedToPlayer?: boolean; pals?: number }> {
  const query = base ? `?base=${encodeURIComponent(base)}` : '';
  return saveFetch(`/bases/assign${query}`);
}

/**
 * Who the game has ACTUALLY assigned — the sibling of `getBaseAssignments`.
 *
 * That one ranks who *should* work at a base; this reports who *is*, from
 * `WorkSaveData`. Easy to reach for wrongly, hence the two names.
 */
export async function getActualWork(
  base?: string
): Promise<ActualWorkReport> {
  const query = base ? `?base=${encodeURIComponent(base)}` : '';
  return saveFetch(`/bases/working${query}`);
}

/**
 * One Lifmunk-effigy statue line, and what a player's relics bought on it.
 *
 * From `/api/progress`. Every line is returned including untouched ones —
 * "nothing spent on Endurance" is what someone deciding where the next effigy
 * goes actually needs.
 *
 * **`hasEffectRate` must be honoured.** `CapturePower` carries 0.0 on all 15 of
 * its ranks while the other twelve carry real values, so its effect lives
 * somewhere other than that column. Rendering "+0%" for it would be a confident
 * wrong number rather than a missing one.
 *
 * **`requiredRelics` on a rank is the cost OF THAT RANK, not a running total**,
 * and `effectRate` is already cumulative — adjacent columns meaning opposite
 * things. The backend has summed the former; do not sum it again.
 */
export interface RelicLine {
  type: string;
  /** The game's own name — `HungerReduction` is "Satiety Duration". */
  name: string;
  /** True only when the client pak was absent at build time. */
  nameIsInternal: boolean;
  description: string;
  /** Relics this player has put into this line. */
  spent: number;
  rank: number;
  /** Cumulative effect at the current rank, as a percentage. */
  effectRate: number;
  /** False for CapturePower — show the rank, never a percentage. */
  hasEffectRate: boolean;
  /**
   * The next rank, or null at max. This — not a `nextCost` field that never
   * existed — is the maxed test: an earlier version keyed "maxed" on a name
   * the backend never emitted, so every line read as maxed.
   */
  nextRank: number | null;
  /** Relics needed to reach `nextRank`; 0 at max. */
  relicsToNext: number;
  /** Total relics to max this line out. */
  totalToMax: number;
}

/** One player's counted progress, from `/api/progress`. */
export interface PlayerProgress {
  uid: string;
  name: string;
  level: number;
  technologyPoints: number;
  ancientTechnologyPoints: number;
  technologiesUnlocked: number;
  relicLines: RelicLine[];
  remaining: Record<string, number>;
  [category: string]:
    | string
    | number
    | RelicLine[]
    | Record<string, number>
    | { obtained: number; of: number; source: string }
    // `distinct` is null when the save stores a plain int — a scalar carries
    // no per-key breakdown, and the counter is ABSENT (not 0/0) until the
    // game first has something to count.
    | { total: number; distinct: number | null }
    | undefined;
}

export async function getProgress(): Promise<{
  players: PlayerProgress[];
  knownTotals: Record<string, { total: number; source: string }>;
  note: string;
}> {
  return saveFetch('/progress');
}

/**
 * Named checklists — *which* bosses and regions are left, not how many.
 *
 * A sibling of `getProgress` rather than part of it: the lists are large (174
 * fast-travel points, 396 effigies) and the counts are what most callers want.
 *
 * `showsMissing: false` means the operator's `discoveryVisibility` hides the
 * unfound half from this viewer, and the backend has already dropped it — the
 * lists are not merely unrendered.
 */
/**
 * Which Pals each player still needs, with a route to each.
 *
 * Split from `/progress/detail` because it is 204 rows with a route attached.
 * The not-yet-caught half is filtered server-side by `discoveryVisibility`;
 * `showsMissing` says whether it was.
 */
/**
 * Every boss with its counters. Catalogue data, no parsed world.
 *
 * `element` filters on the boss's OWN element, not on what beats it.
 */
export async function getBossEncounters(options: {
  kind?: string; element?: string; maxLevel?: number;
} = {}): Promise<BossEncounters> {
  const query = new URLSearchParams();
  if (options.kind) query.set('kind', options.kind);
  if (options.element) query.set('element', options.element);
  if (options.maxLevel !== undefined) query.set('maxLevel', String(options.maxLevel));
  const suffix = query.toString();
  return saveFetch(`/world/encounters${suffix ? `?${suffix}` : ''}`);
}

export async function getPaldeckCompletion(uid?: string): Promise<{
  players: PaldeckCompletion[];
  showsMissing: boolean;
}> {
  const query = uid ? `?uid=${encodeURIComponent(uid)}` : '';
  return saveFetch(`/progress/paldeck${query}`);
}

export async function getProgressDetail(uid?: string): Promise<ProgressDetailReport> {
  const query = uid ? `?uid=${encodeURIComponent(uid)}` : '';
  return saveFetch(`/progress/detail${query}`);
}

/**
 * Placed NPCs by role — merchants, villagers, hunters, police, quest givers.
 *
 * The map drew these as one anonymous "NPCs & camps" layer until a spawner
 * actor's tagged properties turned out to be readable in the *server* pak. Not
 * discovery-filtered: a spawn point is not a collectable, so there is no
 * per-player state to hide.
 */
export async function getNpcPlacements(role?: string): Promise<NpcPlacements> {
  const query = role ? `?role=${encodeURIComponent(role)}` : '';
  return saveFetch(`/world/npcs${query}`);
}

/**
 * Raid bosses: what summons each, at what level, and what it drops.
 *
 * A separate category from field bosses and deliberately not a map layer — they
 * are altar-summoned, so no game file gives them a world position.
 */
export async function getRaidBosses(): Promise<RaidBossReport> {
  return saveFetch('/world/raidbosses');
}

/**
 * Base raid groups: who attacks, in which biome, and what they drop.
 *
 * **A reference table, never a per-base forecast.** Neither of the two joins
 * that would make it one is available — see the backend route.
 */
export async function getInvaders(): Promise<InvaderReport> {
  return saveFetch('/world/invaders');
}
