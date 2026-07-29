'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Monitor, Map, Users, Building2, Wrench, LogIn, LogOut,
  Server, Shield, Eye, Lock, Unlock, Sliders, Egg, RefreshCw, Package, ShieldCheck,
  UserCog, ScrollText,
} from 'lucide-react';
import { useDashboardStore } from '@/lib/store';
import {
  getServerInfo, getServerMetrics, getPlayers,
  login, loginAsGuest, logout, getSession,
} from '@/lib/api';
import { getBackendHealth, getBases, getGuilds, requestRefresh } from '@/lib/save-api';
import ItemsView from '@/components/items-view';
import AccessSettings from '@/components/access-settings';
import UserManager from '@/components/user-manager';
import AuditLog from '@/components/audit-log';
import ServerOverview from '@/components/server-overview';
import InteractiveMap from '@/components/interactive-map';
import PlayerRoster from '@/components/player-roster';
import BaseViewer from '@/components/base-viewer';
import SaveEditor from '@/components/save-editor';
import ServerSettings from '@/components/server-settings';
import BreedingPlanner from '@/components/breeding-planner';
import { CAPABILITIES } from '@/lib/permissions';
import { ROLE_LABEL, type Role } from '@/lib/auth-types';
import type { DashboardTab } from '@/lib/types';

/**
 * Tabs are gated on capabilities, not on a role name. A Moderator sees the audit
 * log but not save tools; an Administrator sees both; a Player sees neither.
 * Hiding a tab is cosmetic — the backend refuses the request either way.
 */
const TABS: {
  id: DashboardTab;
  label: string;
  icon: React.ReactNode;
  requires?: string;
}[] = [
  { id: 'overview', label: 'Overview', icon: <Monitor size={15} /> },
  { id: 'map', label: 'Map', icon: <Map size={15} /> },
  { id: 'bases', label: 'Bases', icon: <Building2 size={15} /> },
  { id: 'items', label: 'Items', icon: <Package size={15} />, requires: CAPABILITIES.VIEW_DETAIL },
  { id: 'players', label: 'Players', icon: <Users size={15} />, requires: CAPABILITIES.VIEW_DETAIL },
  { id: 'breeding', label: 'Breeding', icon: <Egg size={15} />, requires: CAPABILITIES.VIEW_DETAIL },
  { id: 'settings', label: 'Settings', icon: <Sliders size={15} />, requires: CAPABILITIES.SETTINGS_WRITE },
  { id: 'access', label: 'Access', icon: <ShieldCheck size={15} />, requires: CAPABILITIES.POLICY_MANAGE },
  { id: 'users', label: 'Users', icon: <UserCog size={15} />, requires: CAPABILITIES.USERS_MANAGE },
  { id: 'audit', label: 'Audit log', icon: <ScrollText size={15} />, requires: CAPABILITIES.AUDIT_VIEW },
  { id: 'editor', label: 'Save Tools', icon: <Wrench size={15} />, requires: CAPABILITIES.SAVE_SORT_STACKABLES },
];

export default function Home() {
  const store = useDashboardStore();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loginError, setLoginError] = useState('');
  const [busy, setBusy] = useState(false);
  const [availability, setAvailability] = useState({ anyUsers: true, guestAvailable: true });
  const [refreshing, setRefreshing] = useState(false);
  const [refreshNote, setRefreshNote] = useState<string | null>(null);

  // Zustand's hook returns a new object each render; using it directly as an
  // effect dependency would restart polling on every tick.
  const storeRef = useRef(store);
  storeRef.current = store;

  // ─── Restore an existing session on load ──────────────
  useEffect(() => {
    let cancelled = false;
    getSession()
      .then((session) => {
        if (cancelled) return;
        setAvailability({
          anyUsers: session.anyUsers,
          guestAvailable: session.guestAvailable,
        });
        if (session.user) {
          storeRef.current.setUser(session.user);
          storeRef.current.setAuthenticated(true, session.user.role as Role);
        }
        storeRef.current.setCapabilities(session.capabilities ?? []);
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) storeRef.current.setAuthChecked(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setLoginError('');
    try {
      const { role, user, capabilities } = await login(username, password);
      setPassword('');
      store.setUser(user);
      store.setCapabilities(capabilities ?? []);
      store.setAuthenticated(true, role as Role);
    } catch (err) {
      setLoginError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setBusy(false);
    }
  };

  const handleGuest = async () => {
    setBusy(true);
    setLoginError('');
    try {
      const { role } = await loginAsGuest();
      store.setAuthenticated(true, role);
    } catch (err) {
      setLoginError(err instanceof Error ? err.message : 'Guest login failed');
    } finally {
      setBusy(false);
    }
  };

  const handleLogout = async () => {
    await logout().catch(() => undefined);
    store.reset();
    store.setActiveTab('overview');
  };

  // ─── Polling ──────────────────────────────────────────
  const pollLive = useCallback(async () => {
    const s = storeRef.current;
    try {
      const [info, metrics, players] = await Promise.all([
        getServerInfo().catch(() => null),
        getServerMetrics().catch(() => null),
        getPlayers().catch(() => []),
      ]);

      if (info) s.setServerInfo(info);
      if (metrics) {
        s.setServerMetrics(metrics);
        s.setServerStatus('online');
        s.addFpsPoint({
          timestamp: Date.now(),
          fps: metrics.serverfps,
          frameTime: metrics.frametime,
          playerCount: metrics.currentplayernum,
        });
      } else {
        s.setServerStatus('offline');
      }
      s.setOnlinePlayers(players);
    } catch {
      s.setServerStatus('offline');
    }

    try {
      const health = await getBackendHealth();
      s.setBackendOnline(true);
      s.setServerProcessRunning(health.serverRunning);
      s.setServerState(health.server ?? null);
      s.setCacheStatus(health.cache ?? null);
    } catch {
      s.setBackendOnline(false);
    }
  }, []);

  // Save data is expensive to produce, so it polls far less often than the
  // live REST metrics. The backend caches and rate-limits parsing regardless.
  const pollSave = useCallback(async () => {
    const s = storeRef.current;
    try {
      const [bases, guilds] = await Promise.all([
        getBases().catch(() => []),
        getGuilds().catch(() => []),
      ]);
      s.setBases(bases);
      s.setGuilds(guilds);
    } catch {
      /* save data unavailable */
    }
  }, []);

  useEffect(() => {
    if (!store.isAuthenticated) return;

    pollLive();
    pollSave();

    const live = setInterval(pollLive, 5000);
    const save = setInterval(pollSave, 120000);
    return () => {
      clearInterval(live);
      clearInterval(save);
    };
  }, [store.isAuthenticated, pollLive, pollSave]);

  // ─── Gate ─────────────────────────────────────────────
  if (!store.authChecked) {
    return (
      <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', color: 'var(--text-muted)' }}>
        <RefreshCw size={18} className="fade-in" />
      </div>
    );
  }

  if (!store.isAuthenticated) {
    return <LoginScreen
      username={username}
      setUsername={setUsername}
      password={password}
      setPassword={setPassword}
      onSubmit={handleLogin}
      onGuest={handleGuest}
      error={loginError}
      busy={busy}
      availability={availability}
    />;
  }

  const visibleTabs = TABS.filter(
    (t) => !t.requires || store.capabilities.includes(t.requires)
  );
  const activeTab = visibleTabs.some((t) => t.id === store.activeTab)
    ? store.activeTab
    : 'overview';

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <aside
        style={{
          width: 210,
          padding: '14px 10px',
          borderRight: '1px solid var(--border-primary)',
          background: 'var(--bg-secondary)',
          display: 'flex',
          flexDirection: 'column',
          flexShrink: 0,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '4px 8px 16px' }}>
          <Server size={17} style={{ color: 'var(--accent)' }} />
          <div style={{ fontSize: 14, fontWeight: 600 }}>Palworld</div>
        </div>

        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '8px 10px',
            marginBottom: 14,
            background: 'var(--bg-card)',
            border: '1px solid var(--border-primary)',
            borderRadius: 'var(--radius)',
          }}
        >
          <span className={`status-dot ${store.serverStatus === 'online' ? 'online' : 'offline'}`} />
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            {store.serverStatus === 'online' ? 'Online' : 'Offline'}
          </span>
          {store.serverMetrics && (
            <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 'auto' }} className="mono">
              {store.serverMetrics.currentplayernum}/{store.serverMetrics.maxplayernum}
            </span>
          )}
        </div>

        <nav style={{ display: 'flex', flexDirection: 'column', gap: 1, flex: 1 }}>
          {visibleTabs.map((tab) => (
            <button
              key={tab.id}
              className={`sidebar-btn ${activeTab === tab.id ? 'active' : ''}`}
              onClick={() => store.setActiveTab(tab.id)}
            >
              {tab.icon}
              <span>{tab.label}</span>
            </button>
          ))}
        </nav>

        <div style={{ borderTop: '1px solid var(--border-primary)', paddingTop: 10, marginTop: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 11, color: 'var(--text-muted)', padding: '0 8px 6px' }}>
            {store.serverProcessRunning ? <Lock size={11} /> : <Unlock size={11} style={{ color: 'var(--accent-amber)' }} />}
            <span title={store.serverState?.reason}>
              {store.serverProcessRunning ? 'Saves read-only' : 'Saves editable'}
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 11, color: 'var(--text-muted)', padding: '0 8px 8px' }}>
            {store.userRole === 'guest' ? <Eye size={11} /> : <Shield size={11} />}
            <span title={store.user?.username}>
              {store.user
                ? `${store.user.displayName} · ${ROLE_LABEL[store.userRole]}`
                : ROLE_LABEL.guest}
            </span>
          </div>
          <button className="btn btn-ghost" style={{ width: '100%' }} onClick={handleLogout}>
            <LogOut size={13} /> Sign out
          </button>
        </div>
      </aside>

      <main style={{ flex: 1, padding: 20, overflow: 'auto', minHeight: '100vh' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 18 }}>
          <div>
            <h1 style={{ fontSize: 18, fontWeight: 600 }}>
              {visibleTabs.find((t) => t.id === activeTab)?.label}
            </h1>
            {store.serverInfo && (
              <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
                {store.serverInfo.servername}
              </p>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {!store.backendOnline && (
              <span className="badge badge-warning">Save backend offline</span>
            )}
            {store.backendOnline && store.cacheStatus && (
              <>
                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  {store.cacheStatus.parsing
                    ? 'Parsing save…'
                    : store.cacheStatus.hasData
                      ? `Save data ${formatAge(store.cacheStatus.ageSeconds)}`
                      : 'Save not parsed yet'}
                </span>
                <button
                  className="btn btn-ghost"
                  style={{ padding: '4px 10px', fontSize: 11 }}
                  disabled={refreshing || store.cacheStatus.parsing}
                  onClick={async () => {
                    setRefreshing(true);
                    try {
                      const result = await requestRefresh();
                      if (!result.started) setRefreshNote(result.reason);
                      else setRefreshNote('Parsing started…');
                      setTimeout(() => setRefreshNote(null), 6000);
                    } catch {
                      setRefreshNote('Refresh failed');
                    } finally {
                      setRefreshing(false);
                      pollLive();
                    }
                  }}
                  title="Parse the save file now. Nothing parses on a timer."
                >
                  <RefreshCw size={12} /> Refresh
                </button>
              </>
            )}
          </div>
        </div>

        {refreshNote && (
          <div className="notice" style={{ fontSize: 12, marginBottom: 14 }}>{refreshNote}</div>
        )}

        <div className="fade-in" key={activeTab}>
          {activeTab === 'overview' && <ServerOverview />}
          {activeTab === 'map' && <InteractiveMap />}
          {activeTab === 'bases' && <BaseViewer />}
          {activeTab === 'items' && <ItemsView />}
          {activeTab === 'players' && <PlayerRoster />}
          {activeTab === 'breeding' && <BreedingPlanner />}
          {activeTab === 'settings' && <ServerSettings />}
          {activeTab === 'access' && <AccessSettings />}
          {activeTab === 'users' && <UserManager />}
          {activeTab === 'audit' && <AuditLog />}
          {activeTab === 'editor' && <SaveEditor />}
        </div>
      </main>
    </div>
  );
}

/** "4m ago" / "just now" for the save-data age indicator. */
function formatAge(seconds: number | null): string {
  if (seconds == null) return 'age unknown';
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m old`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h old`;
  return `${Math.floor(seconds / 86400)}d old`;
}

function LoginScreen({
  username, setUsername, password, setPassword, onSubmit, onGuest, error, busy, availability,
}: {
  username: string;
  setUsername: (v: string) => void;
  password: string;
  setPassword: (v: string) => void;
  onSubmit: (e: React.FormEvent) => void;
  onGuest: () => void;
  error: string;
  busy: boolean;
  availability: { anyUsers: boolean; guestAvailable: boolean };
}) {
  return (
    <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 20 }}>
      <div className="glass-card" style={{ width: 360, padding: 28 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 4 }}>
          <Server size={18} style={{ color: 'var(--accent)' }} />
          <h1 style={{ fontSize: 16, fontWeight: 600 }}>Palworld Dashboard</h1>
        </div>
        <p style={{ color: 'var(--text-muted)', fontSize: 12, marginBottom: 20 }}>
          Server administration and save tools
        </p>

        {!availability.anyUsers && (
          <div className="notice notice-warn" style={{ fontSize: 12, marginBottom: 14 }}>
            No accounts exist yet. Set <span className="mono">PANEL_PASSWORD</span> in
            your compose file and restart — the first Owner account is created from
            it automatically.
          </div>
        )}

        <form onSubmit={onSubmit}>
          <label style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 6, display: 'block' }}>
            Username
          </label>
          <input
            className="input"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="admin"
            autoComplete="username"
            disabled={!availability.anyUsers || busy}
            autoFocus
          />
          <label style={{ fontSize: 12, color: 'var(--text-secondary)', margin: '12px 0 6px', display: 'block' }}>
            Password
          </label>
          <input
            type="password"
            className="input"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            disabled={!availability.anyUsers || busy}
          />

          {error && (
            <p style={{ color: 'var(--accent-red)', fontSize: 12, marginTop: 10 }}>{error}</p>
          )}

          <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
            <button
              type="submit"
              className="btn btn-primary"
              style={{ flex: 1 }}
              disabled={!availability.anyUsers || busy}
            >
              <LogIn size={14} /> Sign in
            </button>
            {availability.guestAvailable && (
              <button type="button" className="btn btn-ghost" style={{ flex: 1 }} onClick={onGuest} disabled={busy}>
                <Eye size={14} /> Guest
              </button>
            )}
          </div>
        </form>

        <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 16, lineHeight: 1.5 }}>
          Guests can view the map, server status and base locations, subject to
          what this server exposes. Everything else needs an account — repeated
          failed sign-ins are throttled per user and per address.
        </p>
      </div>
    </div>
  );
}
