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

export type { Role } from './auth-types';
import type { Role } from './auth-types';

/** The signed-in account, or null for a guest. */
export interface SessionUser {
  username: string;
  displayName: string;
  role: Role;
  steamUid?: string;
  mustChangePassword?: boolean;
}

interface DashboardState {
  // ─── Auth ──────────────────────────────────
  // `authChecked` distinguishes "not logged in" from "we have not asked the
  // server yet", so a reload does not flash the login screen at a valid user.
  isAuthenticated: boolean;
  authChecked: boolean;
  userRole: Role;
  user: SessionUser | null;
  /** What this session may do. Presentation only — the backend enforces it. */
  capabilities: string[];
  setAuthenticated: (v: boolean, role?: Role) => void;
  setUser: (u: SessionUser | null) => void;
  /** Convenience for the UI. Never a substitute for a server-side check. */
  can: (capability: string) => boolean;
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

  /**
   * Kinds *excluded* per static category, e.g. `{ ore: ['BP_..._RockCoal'] }`.
   *
   * Deliberately stores what is off rather than what is on. An empty entry means
   * "all of them", so a game update that adds a new ore class shows up by default
   * instead of being invisible because it was missing from a saved include list.
   */
  staticKindsOff: Record<string, string[]>;
  toggleStaticKind: (category: string, cls: string) => void;
  setStaticKindsOff: (category: string, kinds: string[]) => void;

  reset: () => void;
}

const MAX_FPS_HISTORY = 360; // 30 min at 5s interval

export const useDashboardStore = create<DashboardState>((set, get) => ({
  isAuthenticated: false,
  authChecked: false,
  userRole: 'guest',
  user: null,
  capabilities: [],
  setAuthenticated: (v, role = 'guest') => set({ isAuthenticated: v, userRole: role }),
  setUser: (u) => set({ user: u, userRole: u?.role ?? 'guest' }),
  can: (capability) => get().capabilities.includes(capability),
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

  // Defaults aim at a readable first view. The high-volume world layers are off
  // because a mature world has ~2,300 chests, ~600 fishing-junk piles and ~500
  // ore nodes, which drown out everything else.
  mapLayers: {
    players: true,
    bases: true,
    fastTravel: true,
    effigies: false,
    palbox: true,
    breeding: true,
    statue: true,
    chest: false,
    oreNode: false,
    oilrigChest: false,
    fishingJunk: false,
    drop: false,
    crafting: false,
    production: false,
    farm: false,
    storage: false,
    comfort: false,
    defense: false,
    egg: false,
    // Static pak-derived layers, off by default and listed explicitly so the
    // default is a decision rather than an omission. Each one is thousands of
    // markers, and switching one on is what makes the map issue a viewport
    // query at all — with them off the map costs exactly what it did before.
    'static:ore': false,
    'static:treasure': false,
    'static:fishing': false,
    'static:oilrig': false,
  },
  toggleMapLayer: (layer) =>
    set((state) => ({
      mapLayers: { ...state.mapLayers, [layer]: !state.mapLayers[layer] },
    })),

  staticKindsOff: {},

  toggleStaticKind: (category, cls) =>
    set((state) => {
      const current = state.staticKindsOff[category] ?? [];
      const next = current.includes(cls)
        ? current.filter((k) => k !== cls)
        : [...current, cls];
      return { staticKindsOff: { ...state.staticKindsOff, [category]: next } };
    }),

  setStaticKindsOff: (category, kinds) =>
    set((state) => ({
      staticKindsOff: { ...state.staticKindsOff, [category]: kinds },
    })),

  reset: () =>
    set({
      isAuthenticated: false,
      userRole: 'guest',
      user: null,
      capabilities: [],
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
