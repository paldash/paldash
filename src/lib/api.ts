import type { SessionUser } from './store';
import type {
  ServerInfo,
  ServerMetrics,
  Player,
  ServerSettings,
  BanList,
  MetricsHistory,
  MetricsSummary,
} from './types';

const BASE = '/api/palworld';

// ─── Auth ───────────────────────────────────────────────

export async function login(
  username: string,
  password: string
): Promise<{ role: string; user: SessionUser; capabilities: string[] }> {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: 'Login failed' }));
    throw new Error(body.error || `Login failed (${res.status})`);
  }
  return res.json();
}

export async function loginAsGuest(): Promise<{ role: 'guest' }> {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ guest: true }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: 'Guest login failed' }));
    throw new Error(body.error || `Guest login failed (${res.status})`);
  }
  return res.json();
}

export async function logout(): Promise<void> {
  await fetch('/api/auth/logout', { method: 'POST' });
}

export async function getSession(): Promise<{
  role: string;
  user: SessionUser | null;
  capabilities: string[];
  guestAvailable: boolean;
  anyUsers: boolean;
}> {
  const res = await fetch('/api/auth/session');
  if (!res.ok) {
    return { role: 'guest', user: null, capabilities: [], guestAvailable: true, anyUsers: false };
  }
  return res.json();
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json();
}

// ─── GET endpoints ──────────────────────────────────────

export async function getServerInfo(): Promise<ServerInfo> {
  return apiFetch<ServerInfo>('/info');
}

export async function getServerMetrics(): Promise<ServerMetrics> {
  return apiFetch<ServerMetrics>('/metrics');
}

export async function getPlayers(): Promise<Player[]> {
  const data = await apiFetch<{ players: Player[] }>('/players');
  return data.players ?? [];
}

export async function getServerSettings(): Promise<ServerSettings> {
  return apiFetch<ServerSettings>('/settings');
}

// ─── Commands ───────────────────────────────────────────
//
// These go to the **backend** (`/api/save/...`), not to the game-REST proxy above.
// The proxy forwards reads only and returns 405 for anything else, because it
// cannot write an audit record — the audit log is in SQLite and only the Python
// process opens that file. Every command below is recorded with the actor, the
// target, the reason and the outcome.

async function commandFetch<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api/save${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json();
}

export async function kickPlayer(userId: string, reason = '') {
  return commandFetch('/moderate/kick', { userid: userId, reason });
}

export async function banPlayer(userId: string, reason = '') {
  return commandFetch('/moderate/ban', { userid: userId, reason });
}

export async function unbanPlayer(userId: string) {
  return commandFetch('/moderate/unban', { userid: userId });
}

export async function announce(message: string) {
  return commandFetch('/moderate/announce', { message });
}

export async function getBanList(): Promise<BanList> {
  const res = await fetch('/api/save/moderate/bans');
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}

export async function forceSave() {
  return commandFetch('/server/save');
}

export async function shutdownServer(seconds = 60, message = 'Server shutting down') {
  return commandFetch('/server/shutdown', { seconds, message });
}

/** No countdown, no announcement. Loses everything since the last autosave. */
export async function stopServer() {
  return commandFetch('/server/force-stop');
}

// ─── Metrics history ────────────────────────────────────

export async function getMetricsHistory(hours = 24, buckets = 120): Promise<MetricsHistory> {
  const res = await fetch(`/api/save/metrics/history?hours=${hours}&buckets=${buckets}`);
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}

export async function getMetricsSummary(): Promise<MetricsSummary> {
  const res = await fetch('/api/save/metrics/summary');
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}
