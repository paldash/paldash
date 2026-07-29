import type {
  BaseCamp,
  BaseStorage,
  GuildInfo,
  PlayerSaveData,
  ContainerContents,
  BackupInfo,
  SaveEditRequest,
  ServerState,
  CacheStatus,
  LifecycleStatus,
  MapObject,
  FastTravelPoint,
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
  envCeiling: string;
  levels: { id: string; label: string; description: string }[];
  visibilityKeys: string[];
  allowedCapabilities: string[];
}

export async function getAccessPolicy(): Promise<AccessPolicyInfo> {
  return saveFetch('/policy');
}

export async function setAccessPolicy(update: {
  securityLevel?: string;
  guestVisibility?: Record<string, boolean>;
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

// ─── Per-base storage & reports ─────────────────────────

export type ExportKind = 'world' | 'player' | 'guild' | 'base' | 'container';

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
 * Fast-travel statues, from bundled game data rather than the save.
 *
 * Returns an empty list rather than throwing when the reference data is missing,
 * so a missing bundle degrades the map instead of breaking it.
 */
export async function getFastTravelPoints(): Promise<FastTravelPoint[]> {
  const data = await saveFetch<{ points: FastTravelPoint[] }>('/world/fasttravel');
  return data.points ?? [];
}

export async function getItemTotals(): Promise<ItemTotals> {
  return saveFetch('/items');
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

// ─── Save Editing (Write Mode — server must be offline) ─

export async function editSaveData(request: SaveEditRequest): Promise<{ success: boolean; diff: string }> {
  return saveFetch('/edit', {
    method: 'POST',
    body: JSON.stringify(request),
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
