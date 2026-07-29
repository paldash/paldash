import type { SessionUser } from './store';
import type {
  ServerInfo,
  ServerMetrics,
  Player,
  ServerSettings,
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

// ─── POST endpoints ─────────────────────────────────────

export async function kickPlayer(userId: string, message = 'Kicked by admin') {
  return apiFetch('/kick', {
    method: 'POST',
    body: JSON.stringify({ userid: userId, message }),
  });
}

export async function banPlayer(userId: string, message = 'Banned by admin') {
  return apiFetch('/ban', {
    method: 'POST',
    body: JSON.stringify({ userid: userId, message }),
  });
}

export async function unbanPlayer(userId: string) {
  return apiFetch('/unban', {
    method: 'POST',
    body: JSON.stringify({ userid: userId }),
  });
}

export async function announce(message: string) {
  return apiFetch('/announce', {
    method: 'POST',
    body: JSON.stringify({ message }),
  });
}

export async function forceSave() {
  return apiFetch('/save', { method: 'POST' });
}

export async function shutdownServer(waittime = 60, message = 'Server shutting down') {
  return apiFetch('/shutdown', {
    method: 'POST',
    body: JSON.stringify({ waittime, message }),
  });
}

export async function stopServer() {
  return apiFetch('/stop', { method: 'POST' });
}
