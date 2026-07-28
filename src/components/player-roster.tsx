'use client';

import { useDashboardStore } from '@/lib/store';
import { kickPlayer, banPlayer, unbanPlayer } from '@/lib/api';
import { Users, Search, Shield, Ban, LogOut, MapPin, Eye } from 'lucide-react';
import { useState } from 'react';

export default function PlayerRoster() {
  const { onlinePlayers, setActiveTab } = useDashboardStore();
  const [search, setSearch] = useState('');
  const [feedback, setFeedback] = useState<string | null>(null);
  const [unbanId, setUnbanId] = useState('');

  const filtered = onlinePlayers.filter(p =>
    p.name.toLowerCase().includes(search.toLowerCase())
  );

  const showFeedback = (msg: string) => {
    setFeedback(msg);
    setTimeout(() => setFeedback(null), 3000);
  };

  const handleKick = async (userId: string, name: string) => {
    if (!confirm(`Kick ${name}?`)) return;
    try {
      await kickPlayer(userId);
      showFeedback(`Kicked ${name}`);
    } catch { showFeedback('Kick failed'); }
  };

  const handleBan = async (userId: string, name: string) => {
    if (!confirm(`Ban ${name}? This cannot be easily undone.`)) return;
    try {
      await banPlayer(userId);
      showFeedback(`Banned ${name}`);
    } catch { showFeedback('Ban failed'); }
  };

  const handleUnban = async () => {
    if (!unbanId.trim()) return;
    try {
      await unbanPlayer(unbanId);
      showFeedback(`Unbanned ${unbanId}`);
      setUnbanId('');
    } catch { showFeedback('Unban failed'); }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {feedback && (
        <div className="slide-up" style={{
          position: 'fixed', top: 20, right: 20, zIndex: 1000,
          background: 'var(--bg-card)', border: '1px solid var(--border-accent)',
          borderRadius: 8, padding: '12px 20px', fontSize: 13,
          boxShadow: '0 4px 20px rgba(0,0,0,0.4)'
        }}>
          {feedback}
        </div>
      )}

      {/* Search & Stats */}
      <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
        <div style={{ position: 'relative', flex: 1, maxWidth: 400 }}>
          <Search size={14} style={{
            position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)',
            color: 'var(--text-muted)'
          }} />
          <input
            className="input"
            style={{ paddingLeft: 34 }}
            placeholder="Search players..."
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <span className="badge badge-online">{onlinePlayers.length} Online</span>
      </div>

      {/* Player Table */}
      <div className="glass-card" style={{ overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border-primary)' }}>
              {['Player', 'Level', 'Ping', 'Location', 'Actions'].map(h => (
                <th key={h} style={{
                  padding: '12px 16px', textAlign: 'left',
                  fontSize: 11, fontWeight: 600, color: 'var(--text-muted)',
                  textTransform: 'uppercase', letterSpacing: '0.05em',
                }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={5} style={{
                  padding: 40, textAlign: 'center',
                  color: 'var(--text-muted)', fontSize: 13
                }}>
                  {onlinePlayers.length === 0 ? 'No players online' : 'No matching players'}
                </td>
              </tr>
            ) : (
              filtered.map(player => (
                <tr key={player.userId} style={{
                  borderBottom: '1px solid var(--border-primary)',
                  transition: 'background 0.15s',
                }} onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-card-hover)')}
                   onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                  <td style={{ padding: '12px 16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span className="status-dot online" />
                      <span style={{ fontSize: 13, fontWeight: 500 }}>{player.name}</span>
                    </div>
                  </td>
                  <td style={{ padding: '12px 16px', fontSize: 13, color: 'var(--text-secondary)' }}>
                    {player.level}
                  </td>
                  <td style={{ padding: '12px 16px', fontSize: 13 }}>
                    <span style={{
                      color: player.ping < 100 ? 'var(--accent-emerald)' :
                             player.ping < 200 ? 'var(--accent-amber)' : 'var(--accent-red)'
                    }}>
                      {player.ping}ms
                    </span>
                  </td>
                  <td style={{ padding: '12px 16px' }}>
                    <button className="btn btn-ghost" style={{ padding: '2px 8px', fontSize: 11 }}
                      onClick={() => setActiveTab('map')}>
                      <MapPin size={11} /> View
                    </button>
                  </td>
                  <td style={{ padding: '12px 16px' }}>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button className="btn btn-warning" style={{ padding: '4px 8px', fontSize: 11 }}
                        onClick={() => handleKick(player.userId, player.name)}>
                        <LogOut size={11} /> Kick
                      </button>
                      <button className="btn btn-danger" style={{ padding: '4px 8px', fontSize: 11 }}
                        onClick={() => handleBan(player.userId, player.name)}>
                        <Ban size={11} /> Ban
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Unban Section */}
      <div className="glass-card" style={{ padding: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
          <Shield size={14} style={{ color: 'var(--accent-purple)' }} />
          <span style={{ fontSize: 13, fontWeight: 600 }}>Unban Player</span>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            className="input"
            value={unbanId}
            onChange={e => setUnbanId(e.target.value)}
            placeholder="Enter Steam/User ID to unban..."
            style={{ maxWidth: 400 }}
          />
          <button className="btn btn-primary" onClick={handleUnban}>Unban</button>
        </div>
      </div>
    </div>
  );
}
