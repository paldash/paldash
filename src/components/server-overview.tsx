'use client';

import { useDashboardStore } from '@/lib/store';
import { CAPABILITIES } from '@/lib/permissions';
import { announce, forceSave, shutdownServer, stopServer } from '@/lib/api';
import MetricsHistoryPanel from './metrics-history';
import Moderation from './moderation';
import ScheduledAnnouncements from './scheduled-announcements';
import {
  Activity, Clock, Users, Cpu, TrendingUp,
  Megaphone, Save, Power, AlertTriangle,
  Server, Gauge, Swords
} from 'lucide-react';
import { useState } from 'react';
import {
  XAxis, YAxis, ResponsiveContainer, Tooltip, Area, AreaChart
} from 'recharts';
import { t } from '@/lib/chrome';

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export default function ServerOverview() {
  const { serverMetrics, serverInfo, fpsHistory, onlinePlayers, capabilities } =
    useDashboardStore();
  // How many online players this viewer is not allowed to see.
  //
  // The game's own count minus the filtered list. Never negative: the two are
  // polled separately and a player can leave between the calls, which would
  // otherwise render as "-1 more online".
  const hiddenCount = Math.max(
    0,
    (typeof serverMetrics?.currentplayernum === 'number' ? serverMetrics.currentplayernum : 0) -
      onlinePlayers.length
  );

  const [announceText, setAnnounceText] = useState('');
  const [shutdownWait, setShutdownWait] = useState(60);
  const [actionFeedback, setActionFeedback] = useState<string | null>(null);

  const showFeedback = (msg: string) => {
    setActionFeedback(msg);
    setTimeout(() => setActionFeedback(null), 3000);
  };

  const handleAnnounce = async () => {
    if (!announceText.trim()) return;
    try {
      await announce(announceText);
      showFeedback('Announcement sent!');
      setAnnounceText('');
    } catch (e) {
      showFeedback(`Error: ${e instanceof Error ? e.message : 'Failed'}`);
    }
  };

  const handleSave = async () => {
    try {
      await forceSave();
      showFeedback(t('World saved!'));
    } catch (e) {
      showFeedback(`Error: ${e instanceof Error ? e.message : 'Failed'}`);
    }
  };

  // Both of these stop the game process. Neither starts it again — see the
  // Settings tab for what that means for your container setup.
  const handleShutdown = async () => {
    if (!confirm(
      `Shut the server down with a ${shutdownWait}s warning?\n\n` +
      'This stops the game process. It only comes back if your server container restarts it.'
    )) return;
    try {
      // No separate noteShutdown call: the backend route records the shutdown and
      // starts watching for the server's return itself, so doing it here as well
      // would reset that watch with a second, less accurate timestamp.
      await shutdownServer(shutdownWait, 'Server shutting down for maintenance');
      showFeedback(`Shutdown initiated (${shutdownWait}s)`);
    } catch (e) {
      showFeedback(`Error: ${e instanceof Error ? e.message : 'Failed'}`);
    }
  };

  const handleStop = async () => {
    if (!confirm('Force stop the server immediately? Unsaved progress since the last autosave is lost.')) return;
    try {
      await stopServer();
      showFeedback('Server stopped.');
    } catch (e) {
      showFeedback(`Error: ${e instanceof Error ? e.message : 'Failed'}`);
    }
  };

  // PvP toggling lives in the Settings tab, which actually writes
  // PalWorldSettings.ini. The buttons that used to be here only broadcast a
  // message and rebooted — they never changed a single setting.
  const goToSettings = () => useDashboardStore.getState().setActiveTab('settings');

  const chartData = fpsHistory.map(p => ({
    time: new Date(p.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    fps: p.fps,
    frameTime: p.frameTime,
    players: p.playerCount,
  }));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* Feedback Toast */}
      {actionFeedback && (
        <div className="slide-up" style={{
          position: 'fixed', top: 20, right: 20, zIndex: 1000,
          background: 'var(--bg-card)', border: '1px solid var(--border-accent)',
          borderRadius: 8, padding: '12px 20px', fontSize: 13, fontWeight: 500,
          boxShadow: '0 4px 20px rgba(0,0,0,0.4)'
        }}>
          {actionFeedback}
        </div>
      )}

      {/* ─── Stat Cards ─── */}
      <div className="dashboard-grid grid-4">
        <div className="stat-card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <div style={{ color: 'var(--accent-cyan)' }}><Activity size={18} /></div>
            <span className="stat-label" style={{ marginTop: 0 }}>{t('Server FPS')}</span>
          </div>
          <div className="stat-value" style={{ color: serverMetrics && serverMetrics.serverfps < 15 ? 'var(--accent-red)' : 'var(--accent-cyan)' }}>
            {serverMetrics?.serverfps ?? '—'}
          </div>
        </div>

        <div className="stat-card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <div style={{ color: 'var(--accent-purple)' }}><Users size={18} /></div>
            <span className="stat-label" style={{ marginTop: 0 }}>{t('Players Online')}</span>
          </div>
          <div className="stat-value" style={{ color: 'var(--accent-purple)' }}>
            {typeof serverMetrics?.currentplayernum === 'number'
              ? `${serverMetrics.currentplayernum}/${serverMetrics.maxplayernum ?? '?'}`
              : '—'}
          </div>
        </div>

        <div className="stat-card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <div style={{ color: 'var(--accent-emerald)' }}><Clock size={18} /></div>
            <span className="stat-label" style={{ marginTop: 0 }}>{t('Uptime')}</span>
          </div>
          <div className="stat-value" style={{ color: 'var(--accent-emerald)', fontSize: '1.6rem' }}>
            {typeof serverMetrics?.uptime === 'number' ? formatUptime(serverMetrics.uptime) : '—'}
          </div>
        </div>

        <div className="stat-card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <div style={{ color: 'var(--accent-amber)' }}><Gauge size={18} /></div>
            <span className="stat-label" style={{ marginTop: 0 }}>{t('Frame Time')}</span>
          </div>
          <div className="stat-value" style={{ color: 'var(--accent-amber)', fontSize: '1.6rem' }}>
            {typeof serverMetrics?.frametime === 'number'
              ? `${serverMetrics.frametime.toFixed(1)}ms`
              : '—'}
          </div>
        </div>
      </div>

      {/* ─── FPS Chart ─── */}
      <div className="glass-card" style={{ padding: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
          <TrendingUp size={16} style={{ color: 'var(--accent-cyan)' }} />
          <h3 style={{ fontSize: 14, fontWeight: 600 }}>{t('FPS History')}</h3>
          <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 'auto' }}>
            {chartData.length} samples
          </span>
        </div>
        <div style={{ height: 200 }}>
          {chartData.length > 1 ? (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData}>
                <defs>
                  <linearGradient id="fpsGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#00d4ff" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="#00d4ff" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis
                  dataKey="time"
                  stroke="var(--text-muted)"
                  fontSize={10}
                  tickLine={false}
                  axisLine={false}
                />
                <YAxis
                  stroke="var(--text-muted)"
                  fontSize={10}
                  tickLine={false}
                  axisLine={false}
                  domain={[0, 'auto']}
                />
                <Tooltip
                  contentStyle={{
                    background: 'var(--bg-card)',
                    border: '1px solid var(--border-primary)',
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="fps"
                  stroke="#00d4ff"
                  fill="url(#fpsGradient)"
                  strokeWidth={2}
                  dot={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div style={{
              height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: 'var(--text-muted)', fontSize: 13
            }}>
              Collecting FPS data...
            </div>
          )}
        </div>
      </div>

      {/* ─── Server Info + Controls ─── */}
      <div className="dashboard-grid grid-2">
        {/* Server Info */}
        <div className="glass-card" style={{ padding: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
            <Server size={16} style={{ color: 'var(--accent-purple)' }} />
            <h3 style={{ fontSize: 14, fontWeight: 600 }}>{t('Server Information')}</h3>
          </div>
          {serverInfo ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {[
                ['Name', serverInfo.servername],
                ['Version', serverInfo.version],
                ['World GUID', serverInfo.worldguid],
                ['Description', serverInfo.description || '—'],
              ].map(([label, value]) => (
                <div key={label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{label}</span>
                  <span style={{ fontSize: 12, fontFamily: 'JetBrains Mono, monospace', color: 'var(--text-secondary)' }}>
                    {value}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>{t('Server info unavailable')}</p>
          )}
        </div>

        {/* Server controls: Moderator and above. */}
        {capabilities.includes(CAPABILITIES.SERVER_CONTROL) && (
          <div className="glass-card" style={{ padding: 20 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
              <Cpu size={16} style={{ color: 'var(--accent-amber)' }} />
              <h3 style={{ fontSize: 14, fontWeight: 600 }}>{t('Admin Controls')}</h3>
            </div>

            {/* Announce */}
            <div style={{ marginBottom: 16 }}>
              <label style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4, display: 'block' }}>
                Broadcast Announcement
              </label>
              <div style={{ display: 'flex', gap: 8 }}>
                <input
                  className="input"
                  value={announceText}
                  onChange={e => setAnnounceText(e.target.value)}
                  placeholder={t('Message to all players...')}
                  onKeyDown={e => e.key === 'Enter' && handleAnnounce()}
                />
                <button className="btn btn-primary" onClick={handleAnnounce}>
                  <Megaphone size={14} />
                </button>
              </div>
            </div>

            {/* Settings */}
            <div style={{ marginBottom: 16 }}>
              <label style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4, display: 'block' }}>
                Server Settings
              </label>
              <button className="btn btn-ghost" onClick={goToSettings} style={{ width: '100%' }}>
                <Swords size={14} /> PvP &amp; server settings
              </button>
              <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>
                Settings live in PalWorldSettings.ini and apply on restart.
              </p>
            </div>

            {/* Action Buttons */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <button className="btn btn-ghost" onClick={handleSave} style={{ justifyContent: 'flex-start' }}>
                <Save size={14} /> Force Save World
              </button>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <button className="btn btn-warning" onClick={handleShutdown} style={{ flex: 1, justifyContent: 'center' }}>
                  <Power size={14} /> Graceful Shutdown
                </button>
                <input
                  className="input"
                  type="number"
                  value={shutdownWait}
                  onChange={e => setShutdownWait(Number(e.target.value))}
                  style={{ width: 70, textAlign: 'center' }}
                  min={0}
                />
                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>sec</span>
              </div>
              <button className="btn btn-danger" onClick={handleStop} style={{ justifyContent: 'flex-start' }}>
                <AlertTriangle size={14} /> Emergency Stop
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ─── Online Players Quick View ─── */}
      {onlinePlayers.length > 0 && (
        <div className="glass-card" style={{ padding: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
            <Users size={16} style={{ color: 'var(--accent-emerald)' }} />
            <h3 style={{ fontSize: 14, fontWeight: 600 }}>{t('Online Players')}</h3>
            <span className="badge badge-online" style={{ marginLeft: 8 }}>{onlinePlayers.length}</span>
          </div>
          {/* The count above the fold is the game's own `currentplayernum` and
              this list is privacy-filtered, so the two legitimately disagree —
              which read as a broken dashboard until it said so.

              The count is deliberately NOT filtered to match. It is a capacity
              figure, not a roster: anyone who joins the server sees who is on,
              so concealing the number buys no privacy while making the one
              thing it measures wrong. Per-player privacy governs map position
              and roster detail, which is what the list below actually is. */}
          {hiddenCount > 0 && (
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 12 }}>
              {hiddenCount} more online, hidden from you by their privacy settings.
            </div>
          )}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {/* A composite key, not `p.userId`.
                The game's REST API returns an empty `userId` for players in
                some states, and guests have it stripped as PII — so several
                rows shared the key `""`, React reconciled them as one element,
                and the list rendered a single player while the count beside it
                (an array length) correctly said three. The count being right is
                what made it look like a data problem rather than a render one. */}
            {onlinePlayers.map((p, i) => (
              <div key={p.userId || p.playerId || `${p.name}-${i}`} className="glass-card" style={{
                padding: '8px 14px', display: 'flex', alignItems: 'center', gap: 8
              }}>
                <span className="status-dot online" />
                <span style={{ fontSize: 13, fontWeight: 500 }}>{p.name}</span>
                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Lv.{p.level}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* History is a read at the same gate as the live figures above it. */}
      <MetricsHistoryPanel />

      {/* Moderation is its own capability, separate from server control: banning a
          player and shutting the server down are different trusts. */}
      {capabilities.includes(CAPABILITIES.PLAYERS_MODERATE) && <Moderation />}
      {capabilities.includes(CAPABILITIES.PLAYERS_MODERATE) && <ScheduledAnnouncements />}
    </div>
  );
}
