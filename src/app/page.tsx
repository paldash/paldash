'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { Archive, BookOpen, Building2, Egg, Eye, Lock, LogIn, LogOut, Map, Menu, Monitor, Package, PawPrint, RefreshCw, ScrollText, Server, Shield, ShieldCheck, Sliders, Trophy, Unlock, UserCircle, UserCog, Users, Wrench } from 'lucide-react';
import { useDashboardStore } from '@/lib/store';
import {
  getServerInfo, getServerMetrics, getPlayers,
  login, loginAsGuest, logout, getSession,
} from '@/lib/api';
import { getBackendHealth, getBases, getGuilds, requestRefresh } from '@/lib/save-api';
import { t, useChromePack } from '@/lib/chrome';
import { LanguagePicker } from '@/components/language-picker';
import ItemsView from '@/components/items-view';
import AccessSettings from '@/components/access-settings';
import UserManager from '@/components/user-manager';
import BackupManager from '@/components/backup-manager';
import AuditLog from '@/components/audit-log';
import ServerOverview from '@/components/server-overview';
import InteractiveMap from '@/components/interactive-map';
import PlayerRoster from '@/components/player-roster';
import BaseViewer from '@/components/base-viewer';
import VersionBanner from '@/components/version-banner';
import SaveEditor from '@/components/save-editor';
import ServerSettings from '@/components/server-settings';
import BreedingPlanner from '@/components/breeding-planner';
import Paldeck from '@/components/paldeck';
import Progression from '@/components/progression';
import MyPals from '@/components/my-pals';
import ErrorBoundary from '@/components/error-boundary';
import AccountSettings from '@/components/account-settings';
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
  /** Hidden for a guest session, which has no account to configure. */
  needsAccount?: boolean;
}[] = [
  { id: 'overview', label: 'Overview', icon: <Monitor size={15} /> },
  { id: 'map', label: 'Map', icon: <Map size={15} /> },
  { id: 'bases', label: 'Bases', icon: <Building2 size={15} /> },
  // VIEW_SELF for the same reason as Breeding: `/api/items` scopes to the
  // caller's own guilds below the `serverTotalsVisibility` threshold, so a
  // Player has something real to see here — their guild's storage, not the
  // server's.
  { id: 'items', label: 'Items', icon: <Package size={15} />, requires: CAPABILITIES.VIEW_SELF },
  { id: 'players', label: 'Players', icon: <Users size={15} />, requires: CAPABILITIES.VIEW_DETAIL },
  // VIEW_SELF, matching the backend. `/api/breeding/*` and `/api/pals` moved
  // down to VIEW_SELF so a Player gets a planner over their own palbox, but
  // this gate stayed on VIEW_DETAIL — so the endpoints were reachable and the
  // tab that reaches them was invisible. A UI gate that is stricter than the
  // API it guards is not "safe", it is a feature nobody can find.
  { id: 'breeding', label: 'Breeding', icon: <Egg size={15} />, requires: CAPABILITIES.VIEW_SELF },
  // VIEW_BASIC, unlike Breeding: the Paldeck is reference data about the game
  // rather than a readout of this server's Pals, so it needs no parsed world and
  // discloses nothing a wiki would not.
  { id: 'paldeck', label: 'Paldeck', icon: <BookOpen size={15} />, requires: CAPABILITIES.VIEW_BASIC },
  // VIEW_SELF and an account: progression is read off a player's OWN save, so
  // there is nothing here for a session with no character linked to it. The
  // backend narrows to your own row below VIEW_DETAIL and applies
  // discoveryVisibility on top.
  { id: 'progress', label: 'Progression', icon: <Trophy size={15} />, requires: CAPABILITIES.VIEW_SELF, needsAccount: true },
  // VIEW_SELF: your own palbox is the one Pal view a plain Player must have.
  { id: 'mypals', label: 'My Pals', icon: <PawPrint size={15} />, requires: CAPABILITIES.VIEW_SELF, needsAccount: true },
  { id: 'settings', label: 'Settings', icon: <Sliders size={15} />, requires: CAPABILITIES.SETTINGS_WRITE },
  { id: 'access', label: 'Access', icon: <ShieldCheck size={15} />, requires: CAPABILITIES.POLICY_MANAGE },
  { id: 'backups', label: 'Backups', icon: <Archive size={15} />, requires: CAPABILITIES.BACKUP_MANAGE },
  { id: 'users', label: 'Users', icon: <UserCog size={15} />, requires: CAPABILITIES.USERS_MANAGE },
  { id: 'audit', label: 'Audit log', icon: <ScrollText size={15} />, requires: CAPABILITIES.AUDIT_VIEW },
  { id: 'account', label: 'My account', icon: <UserCircle size={15} />, needsAccount: true },
  { id: 'editor', label: 'Save Tools', icon: <Wrench size={15} />, requires: CAPABILITIES.SAVE_SORT_STACKABLES },
];

export default function Home() {
  const store = useDashboardStore();
  // Chrome-translation subscription — one call, at the root on purpose:
  // `t()` is a plain function, and nothing between here and the leaves is
  // memoized, so this re-render is what re-runs every `t()` in the tree when
  // the pack changes. See src/lib/chrome.ts.
  useChromePack();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loginError, setLoginError] = useState('');
  const [busy, setBusy] = useState(false);
  const [availability, setAvailability] = useState({ anyUsers: true, guestAvailable: true });
  const [refreshing, setRefreshing] = useState(false);
  const [refreshNote, setRefreshNote] = useState<string | null>(null);
  // Drawer state. Meaningless above the 900px breakpoint, where CSS pins the
  // sidebar open and ignores the class entirely.
  const [navOpen, setNavOpen] = useState(false);

  // Zustand's hook returns a new object each render; using it directly as an
  // effect dependency would restart polling on every tick.
  const storeRef = useRef(store);
  // Written in an effect, not during render. The "latest ref" pattern is
  // right — this value is read inside a fetch callback that must not be a
  // dependency — but assigning during render is a real hazard under
  // concurrent rendering, where a render can be thrown away and the ref
  // would keep a value that was never committed.
  useEffect(() => {
    storeRef.current = store;
  });

  // Escape closes the drawer. Cheap, expected of anything modal, and the one
  // exit that does not require finding a small target with a thumb — worth
  // having when the scrim is the only other way out.
  useEffect(() => {
    if (!navOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setNavOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [navOpen]);

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

    // `Promise.allSettled`, not `all` with a per-call `.catch(() => [])`.
    //
    // That catch turned every failure — a 403 from the route allowlist, a 503
    // from an unparsed world, a backend that is simply down — into an empty
    // array, and an empty array is a perfectly ordinary answer. The Bases tab
    // then read "no bases" and the map drew neither base markers nor their
    // radius circles, on a server with eleven bases, with no error anywhere.
    //
    // One list failing must not blank the other, which is why they are settled
    // independently rather than sharing a try block.
    const [bases, guilds] = await Promise.allSettled([getBases(), getGuilds()]);

    if (bases.status === 'fulfilled') s.setBases(bases.value);
    if (guilds.status === 'fulfilled') s.setGuilds(guilds.value);

    const describe = (what: string, result: PromiseSettledResult<unknown>) =>
      result.status === 'rejected'
        ? `${what}: ${
            result.reason instanceof Error ? result.reason.message : String(result.reason)
          }`
        : null;

    const failed = [describe('Bases', bases), describe('Guilds', guilds)].filter(
      (x): x is string => x !== null
    );

    s.setSaveDataError(failed.length === 0 ? null : failed.join(' · '));
  }, []);

  useEffect(() => {
    if (!store.isAuthenticated) return;

    pollLive();
    pollSave();

    // Nothing polls while the tab is hidden.
    //
    // Each live poll is three requests to the *game server's* REST API, and a
    // dashboard left open in a background tab was making them every five
    // seconds indefinitely — per open tab. This project's whole posture is
    // staying out of the game server's way, and polling for a chart nobody is
    // looking at is the clearest possible violation of that.
    //
    // On return the data is refreshed immediately rather than waiting out the
    // remaining interval, so coming back to the tab never shows a stale reading.
    // **The catch-up is per-poller and it was live-only.** Save-derived data —
    // bases and guilds — sat out the remainder of its 120s interval on return,
    // so a tab picked up 119 seconds in showed a reading from two minutes ago
    // for another two minutes. The live half refreshed instantly beside it,
    // which makes the stale half read as broken rather than as merely late.
    //
    // Keyed on when each poller last actually ran, so a return fires exactly the
    // ticks that were skipped and never more: focusing a tab four times in a
    // minute is one live poll per five seconds, not four extra rounds of both.
    const LIVE_MS = 5000;
    const SAVE_MS = 120000;
    const hidden = () => typeof document !== 'undefined' && document.hidden;
    let lastLive = Date.now();
    let lastSave = Date.now();

    const liveTick = () => {
      if (hidden()) return;
      lastLive = Date.now();
      pollLive();
    };
    const saveTick = () => {
      if (hidden()) return;
      lastSave = Date.now();
      pollSave();
    };
    const onVisible = () => {
      if (hidden()) return;
      if (Date.now() - lastLive >= LIVE_MS) liveTick();
      if (Date.now() - lastSave >= SAVE_MS) saveTick();
    };

    // `focus` as well as `visibilitychange`, because they answer different
    // questions: visibilitychange fires on switching tabs inside the browser,
    // focus on switching to the browser from another application. Coming back
    // from the game itself is the second, and on several browsers it does not
    // fire the first — so a player alt-tabbing out of Palworld saw the stale
    // dashboard the guard was supposed to have refreshed.
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);
    const live = setInterval(liveTick, LIVE_MS);
    const save = setInterval(saveTick, SAVE_MS);
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
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
    (t) =>
      (!t.requires || store.capabilities.includes(t.requires)) &&
      (!t.needsAccount || Boolean(store.user))
  );
  const activeTab = visibleTabs.some((t) => t.id === store.activeTab)
    ? store.activeTab
    : 'overview';

  return (
    <div className="app-shell">
      {/* Rendered on every viewport and hidden by CSS above 900px. Mounting it
          conditionally would need a width check in JS, and this page is
          server-rendered — the server does not know the viewport, so that is a
          hydration mismatch on the first paint of the device least able to
          absorb one. */}
      <button
        type="button"
        className={`app-scrim ${navOpen ? 'open' : ''}`}
        aria-label={t('Close navigation')}
        tabIndex={navOpen ? 0 : -1}
        onClick={() => setNavOpen(false)}
      />
      <aside className={`app-sidebar ${navOpen ? 'open' : ''}`}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '4px 8px 16px' }}>
          <Server size={17} style={{ color: 'var(--accent)' }} />
          <div style={{ fontSize: 14, fontWeight: 600 }}>{t('Palworld')}</div>
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
              onClick={() => {
                store.setActiveTab(tab.id);
                // A drawer that stays open after you have chosen covers the
                // thing you chose. Unconditional because above the breakpoint
                // `navOpen` is not read by anything.
                setNavOpen(false);
              }}
            >
              {tab.icon}
              <span>{t(tab.label)}</span>
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

      <main
        className="app-main"
        // `minWidth: 0` is the one that matters and is easy to leave out: a flex
        // child's default `min-width: auto` refuses to shrink below its content,
        // so one wide table inside pushed the whole page sideways instead of
        // scrolling within itself.
        style={{ flex: 1, minWidth: 0, padding: 20, overflow: 'auto', minHeight: '100vh' }}
      >
        <div
          style={{
            display: 'flex', alignItems: 'baseline',
            justifyContent: 'space-between', marginBottom: 18, gap: 10,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
            <button
              type="button"
              className="btn btn-ghost nav-toggle"
              aria-label={t('Open navigation')}
              aria-expanded={navOpen}
              onClick={() => setNavOpen(true)}
            >
              <Menu size={17} />
            </button>
            <div style={{ minWidth: 0 }}>
            <h1 style={{ fontSize: 18, fontWeight: 600 }}>
              {t(visibleTabs.find((v) => v.id === activeTab)?.label ?? '')}
            </h1>
            {store.serverInfo && (
              <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
                {store.serverInfo.servername}
              </p>
            )}
            </div>
          </div>
          {/* Wraps rather than overflowing: these badges are the whole reason
              an operator opens this on a phone — "is it up" — so they must not
              be the thing pushed off the right edge. */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            {/* Renders nothing when the server has no language bundles, so an
                install without them shows no dead control. */}
            <LanguagePicker />
            {!store.backendOnline && (
              <span className="badge badge-warning">{t('Save backend offline')}</span>
            )}
            {store.backendOnline && store.cacheStatus?.worldClock && (
              /* "Day 481 · 12:20" — the most human number here, and it comes
                 from the save rather than the live API, so it stays true while
                 the server is off.

                 The time carries a tooltip because the five-hour epoch offset
                 is unverified; the DAY is not hedged, since the ambiguity can
                 only ever move a boundary. Deliberately no day/night icon — see
                 backend/worldclock.py. */
              <span
                style={{ fontSize: 11, color: 'var(--text-muted)' }}
                title={
                  `${store.cacheStatus.worldClock.gameHours.toLocaleString()} in-game hours` +
                  (store.cacheStatus.worldClock.serverUptimeHours
                    ? ` · server up ${store.cacheStatus.worldClock.serverUptimeHours.toLocaleString()} h`
                    : '') +
                  (store.cacheStatus.worldClock.clockOffsetVerified
                    ? ''
                    : ` · ${store.cacheStatus.worldClock.clockOffsetNote}`)
                }
              >
                Day {store.cacheStatus.worldClock.day.toLocaleString()}
                {' · '}
                {store.cacheStatus.worldClock.timeOfDay}
                {!store.cacheStatus.worldClock.clockOffsetVerified && (
                  <span style={{ opacity: 0.6 }}>?</span>
                )}
              </span>
            )}
            {store.backendOnline && store.cacheStatus && (
              <>
                {/* Three states, not two. "Re-parsing after an update" and
                    "nobody has parsed yet" both show an empty dashboard, and
                    conflating them is what made an upgrade look like data loss:
                    every tab went blank with the status line calmly reporting
                    "Save not parsed yet" on a server that had been running for
                    weeks. */}
                <span
                  style={{
                    fontSize: 11,
                    color: store.cacheStatus.schemaStale && !store.cacheStatus.hasData
                      ? 'var(--accent-amber)'
                      : 'var(--text-muted)',
                  }}
                  title={
                    store.cacheStatus.schemaStale && !store.cacheStatus.hasData
                      ? 'The dashboard was updated and the cached world no longer matches ' +
                        'the shape it expects, so it was discarded. A re-parse starts ' +
                        'automatically; press Refresh if it does not.'
                      : undefined
                  }
                >
                  {store.cacheStatus.parsing
                    ? 'Parsing save…'
                    : store.cacheStatus.hasData
                      ? `Save data ${formatAge(store.cacheStatus.ageSeconds)}`
                      : store.cacheStatus.schemaStale
                        ? 'Re-parsing after update — no world data yet'
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
                  title={t('Parse the save file now. Nothing parses on a timer.')}
                >
                  <RefreshCw size={12} /> {t('Refresh')}
                </button>
              </>
            )}
          </div>
        </div>

        {refreshNote && (
          <div className="notice" style={{ fontSize: 12, marginBottom: 14 }}>{refreshNote}</div>
        )}

        <div className="fade-in" key={activeTab}>
          <VersionBanner />
          <ErrorBoundary key={activeTab} label={activeTab}>
          {activeTab === 'overview' && <ServerOverview />}
          {activeTab === 'map' && <InteractiveMap />}
          {activeTab === 'bases' && <BaseViewer />}
          {activeTab === 'items' && <ItemsView />}
          {activeTab === 'players' && <PlayerRoster />}
          {activeTab === 'breeding' && <BreedingPlanner />}
          {activeTab === 'paldeck' && <Paldeck />}
          {activeTab === 'progress' && <Progression />}
          {activeTab === 'mypals' && <MyPals />}
          {activeTab === 'settings' && <ServerSettings />}
          {activeTab === 'access' && <AccessSettings />}
          {activeTab === 'backups' && <BackupManager />}
          {activeTab === 'users' && <UserManager />}
          {activeTab === 'audit' && <AuditLog />}
          {activeTab === 'account' && <AccountSettings />}
          </ErrorBoundary>
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
          <h1 style={{ fontSize: 16, fontWeight: 600 }}>{t('Palworld Dashboard')}</h1>
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
            {t('Username')}
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
