import { create } from 'zustand';
import type {
  ServerInfo,
  ServerMetrics,
  Player,
  BaseCamp,
  GuildInfo,
  PlayerSaveData,
  FpsHistoryPoint,
  DashboardTab,
  ServerStatus,
  BackupInfo,
  ServerState,
  CacheStatus,
} from './types';

export type Role = 'admin' | 'guest';

interface DashboardState {
  // ─── Auth ──────────────────────────────────
  // `authChecked` distinguishes "not logged in" from "we have not asked the
  // server yet", so a reload does not flash the login screen at a valid user.
  isAuthenticated: boolean;
  authChecked: boolean;
  userRole: Role;
  /** What this session may do. Presentation only — the proxies enforce it. */
  capabilities: string[];
  setAuthenticated: (v: boolean, role?: Role) => void;
  setAuthChecked: (v: boolean) => void;
  setCapabilities: (c: string[]) => void;

  // ─── Navigation ────────────────────────────
  activeTab: DashboardTab;
  setActiveTab: (tab: DashboardTab) => void;

  // ─── Server status ─────────────────────────
  serverStatus: ServerStatus;
  setServerStatus: (s: ServerStatus) => void;
  serverInfo: ServerInfo | null;
  setServerInfo: (info: ServerInfo | null) => void;
  serverMetrics: ServerMetrics | null;
  setServerMetrics: (m: ServerMetrics | null) => void;

  // ─── FPS history ───────────────────────────
  fpsHistory: FpsHistoryPoint[];
  addFpsPoint: (p: FpsHistoryPoint) => void;
  clearFpsHistory: () => void;

  // ─── Players ───────────────────────────────
  onlinePlayers: Player[];
  setOnlinePlayers: (p: Player[]) => void;
  selectedPlayerId: string | null;
  setSelectedPlayerId: (id: string | null) => void;

  // ─── Save data ─────────────────────────────
  bases: BaseCamp[];
  setBases: (b: BaseCamp[]) => void;
  guilds: GuildInfo[];
  setGuilds: (g: GuildInfo[]) => void;
  playerSaveData: PlayerSaveData[];
  setPlayerSaveData: (p: PlayerSaveData[]) => void;
  backups: BackupInfo[];
  setBackups: (b: BackupInfo[]) => void;

  // ─── Backend status ────────────────────────
  backendOnline: boolean;
  setBackendOnline: (v: boolean) => void;
  serverProcessRunning: boolean;
  setServerProcessRunning: (v: boolean) => void;
  // Full fail-closed verdict, so the UI can explain *why* editing is locked.
  serverState: ServerState | null;
  setServerState: (s: ServerState | null) => void;
  cacheStatus: CacheStatus | null;
  setCacheStatus: (c: CacheStatus | null) => void;

  // ─── Map ───────────────────────────────────
  mapLayers: Record<string, boolean>;
  toggleMapLayer: (layer: string) => void;

  reset: () => void;
}

const MAX_FPS_HISTORY = 360; // 30 min at 5s interval

export const useDashboardStore = create<DashboardState>((set) => ({
  isAuthenticated: false,
  authChecked: false,
  userRole: 'guest',
  capabilities: [],
  setAuthenticated: (v, role = 'guest') => set({ isAuthenticated: v, userRole: role }),
  setAuthChecked: (v) => set({ authChecked: v }),
  setCapabilities: (c) => set({ capabilities: c }),

  activeTab: 'overview',
  setActiveTab: (tab) => set({ activeTab: tab }),

  serverStatus: 'unknown',
  setServerStatus: (s) => set({ serverStatus: s }),
  serverInfo: null,
  setServerInfo: (info) => set({ serverInfo: info }),
  serverMetrics: null,
  setServerMetrics: (m) => set({ serverMetrics: m }),

  fpsHistory: [],
  addFpsPoint: (p) =>
    set((state) => ({
      fpsHistory: [...state.fpsHistory.slice(-(MAX_FPS_HISTORY - 1)), p],
    })),
  clearFpsHistory: () => set({ fpsHistory: [] }),

  onlinePlayers: [],
  setOnlinePlayers: (p) => set({ onlinePlayers: p }),
  selectedPlayerId: null,
  setSelectedPlayerId: (id) => set({ selectedPlayerId: id }),

  bases: [],
  setBases: (b) => set({ bases: b }),
  guilds: [],
  setGuilds: (g) => set({ guilds: g }),
  playerSaveData: [],
  setPlayerSaveData: (p) => set({ playerSaveData: p }),
  backups: [],
  setBackups: (b) => set({ backups: b }),

  backendOnline: false,
  setBackendOnline: (v) => set({ backendOnline: v }),
  // Default to "running" so the editor starts locked, matching the backend's
  // fail-closed stance rather than briefly implying it is safe to write.
  serverProcessRunning: true,
  setServerProcessRunning: (v) => set({ serverProcessRunning: v }),
  serverState: null,
  setServerState: (s) => set({ serverState: s }),
  cacheStatus: null,
  setCacheStatus: (c) => set({ cacheStatus: c }),

  // Chests are off by default: a mature world has thousands and they drown
  // everything else out.
  mapLayers: {
    players: true,
    bases: true,
    palbox: true,
    breeding: true,
    statue: true,
    chest: false,
    crafting: false,
    production: false,
    storage: false,
    comfort: false,
    egg: false,
  },
  toggleMapLayer: (layer) =>
    set((state) => ({
      mapLayers: { ...state.mapLayers, [layer]: !state.mapLayers[layer] },
    })),

  reset: () =>
    set({
      isAuthenticated: false,
      userRole: 'guest',
      serverInfo: null,
      serverMetrics: null,
      onlinePlayers: [],
      bases: [],
      guilds: [],
      playerSaveData: [],
      backups: [],
      fpsHistory: [],
      serverState: null,
      cacheStatus: null,
    }),
}));
