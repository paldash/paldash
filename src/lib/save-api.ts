import type {
  BaseCamp,
  GuildInfo,
  PlayerSaveData,
  ContainerContents,
  BackupInfo,
  SaveEditRequest,
  ServerState,
  CacheStatus,
  LifecycleStatus,
  MapObject,
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
  merge = true
): Promise<SortResult> {
  return saveFetch(`/edit/sort/${mode}`, {
    method: 'POST',
    body: JSON.stringify({ merge }),
  });
}

export async function requestRefresh(): Promise<{ started: boolean; reason: string }> {
  return saveFetch('/refresh', { method: 'POST' });
}

// ─── Map objects & items ────────────────────────────────

export async function getMapObjects(category?: string): Promise<MapObject[]> {
  return saveFetch(`/mapobjects${category ? `?category=${encodeURIComponent(category)}` : ''}`);
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

export async function getBackups(): Promise<BackupInfo[]> {
  return saveFetch<BackupInfo[]>('/backups');
}

export async function createBackup(description?: string): Promise<BackupInfo> {
  return saveFetch<BackupInfo>('/backup', {
    method: 'POST',
    body: JSON.stringify({ description }),
  });
}

export async function restoreBackup(backupId: string): Promise<{ success: boolean }> {
  return saveFetch(`/restore/${backupId}`, { method: 'POST' });
}

// ─── Save Editing (Write Mode — server must be offline) ─

export async function editSaveData(request: SaveEditRequest): Promise<{ success: boolean; diff: string }> {
  return saveFetch('/edit', {
    method: 'POST',
    body: JSON.stringify(request),
  });
}
