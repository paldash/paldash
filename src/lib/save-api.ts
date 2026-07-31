import type {
  BaseCamp,
  BaseStorage,
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
  PaldeckListing,
  PaldeckDetail,
  PlayerRoster,
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
  baseId?: string
): Promise<SortResult> {
  return saveFetch(`/edit/sort/${mode}`, {
    method: 'POST',
    body: JSON.stringify({ merge, baseId: baseId ?? null }),
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
   * `parser.extract_base_workers`. `other` is a real state: the reference world
   * has orphaned containers belonging to no live player or base.
   */
  location?: 'palbox' | 'party' | 'base' | 'other';
  /** The base it works at, when `location` is `base`. */
  baseId?: string;
  /** That base's display name, joined at request time. */
  baseName?: string;
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

export async function getBaseStorage(): Promise<BaseStorage[]> {
  return saveFetch('/bases/storage');
}

export async function getOneBaseStorage(baseId: string): Promise<BaseStorage> {
  return saveFetch(`/bases/${encodeURIComponent(baseId)}/storage`);
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

export async function previewWorldExport(
  sourceUid: string,
  targetUid: string
): Promise<WorldExportPlan> {
  return saveFetch('/export/world-copy/preview', {
    method: 'POST',
    body: JSON.stringify({ sourceUid, targetUid }),
  });
}

export async function createWorldExport(
  sourceUid: string,
  targetUid: string,
  planHash: string
): Promise<WorldExportResult> {
  return saveFetch('/export/world-copy', {
    method: 'POST',
    body: JSON.stringify({ sourceUid, targetUid, planHash }),
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

export async function getPaldeck(): Promise<PaldeckListing> {
  return saveFetch('/world/paldeck');
}

export async function getPaldeckEntry(speciesId: string): Promise<PaldeckDetail> {
  return saveFetch(`/world/paldeck/${encodeURIComponent(speciesId)}`);
}

export async function getBreedingPath(target: string, owner?: string) {
  const params = new URLSearchParams({ target });
  if (owner) params.set('owner', owner);
  return saveFetch<{
    target: string;
    reachable: boolean;
    alreadyOwned?: boolean;
    reason?: string;
    steps: { parentA: PalSummary; parentB: PalSummary; child: PalSummary }[];
  }>(`/breeding/paths?${params}`);
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
