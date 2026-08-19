'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Users, Search, Ban, LogOut, UserPlus, RefreshCw, Copy, Check, MapPin } from 'lucide-react';
import { useDashboardStore } from '@/lib/store';
import { kickPlayer, banPlayer, unbanPlayer } from '@/lib/api';
import { getPlayerRoster, createUser } from '@/lib/save-api';
import { CAPABILITIES } from '@/lib/permissions';
import type { PlayerRoster as Roster, RosterPlayer } from '@/lib/types';
import { SortHead } from '@/components/sort-head';
import { t } from '@/lib/chrome';

/**
 * Everyone who has played here, online or not.
 *
 * This used to list only the live REST players, which made it useless for the
 * thing an operator most often wants from a roster: the person who logged off an
 * hour ago. The save knows everybody, so that is the base list now and online
 * status is an annotation on it.
 *
 * Actions are gated per capability rather than per tab. Kick and ban are
 * `players.moderate`; creating a dashboard account is `users.manage`. A
 * Moderator sees the first two and not the third — which is the two-gate model
 * working, and the reason these belong together rather than split across tabs:
 * each row shows what *you* can do about that person.
 */
export default function PlayerRoster() {
  const { capabilities } = useDashboardStore();
  const [roster, setRoster] = useState<Roster | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [feedback, setFeedback] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [creatingFor, setCreatingFor] = useState<RosterPlayer | null>(null);
  const [unbanId, setUnbanId] = useState('');
  // 'roster' keeps the server's own order, which is the pre-sorting
  // behaviour — a default that silently re-orders the list would make the
  // feature read as a regression to anyone used to the old layout.
  const [sort, setSort] = useState<'roster' | 'name' | 'level'>('roster');
  const [desc, setDesc] = useState(false);

  const mayModerate = capabilities.includes(CAPABILITIES.PLAYERS_MODERATE);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRoster(await getPlayerRoster());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the roster');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    queueMicrotask(load);
  }, [load]);

  const say = (msg: string) => {
    setFeedback(msg);
    setTimeout(() => setFeedback(null), 3500);
  };

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const all = roster?.players ?? [];
    const rows = q
      ? all.filter((p) => (p.name || '').toLowerCase().includes(q) || p.uid.toLowerCase().includes(q))
      : all;
    if (sort === 'roster') return rows;
    const sorted = [...rows].sort((a, b) => {
      const v = sort === 'level'
        ? (a.level ?? 0) - (b.level ?? 0)
        : (a.name || '').localeCompare(b.name || '');
      return desc ? -v : v;
    });
    return sorted;
  }, [roster, search, sort, desc]);

  const copyUid = async (uid: string) => {
    try {
      await navigator.clipboard.writeText(uid);
      setCopied(uid);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      // Clipboard access needs a secure context; plain-http LAN is the normal
      // deployment here, so this is an expected path rather than an error.
      say('Clipboard unavailable over plain HTTP — select the id and copy manually');
    }
  };

  const act = async (label: string, fn: () => Promise<unknown>, done: string) => {
    try {
      await fn();
      say(done);
      load();
    } catch (e) {
      say(`${label} failed: ${e instanceof Error ? e.message : 'unknown error'}`);
    }
  };

  if (error) {
    return (
      <div className="notice notice-warn">
        <strong>{t('Roster unavailable')}</strong>
        <div style={{ marginTop: 6 }}>{error}</div>
        <button className="btn btn-ghost" style={{ marginTop: 12 }} onClick={load}>
          <RefreshCw size={13} /> Retry
        </button>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: 1, minWidth: 200 }}>
          <Search size={13} style={{ position: 'absolute', left: 10, top: 9, color: 'var(--text-muted)' }} />
          <input
            className="input"
            style={{ paddingLeft: 30 }}
            placeholder={t('Search by name or Steam ID…')}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <span className="badge badge-online">{roster?.onlineCount ?? 0} online</span>
        <span className="badge">{roster?.players.length ?? 0} known</span>
        <button className="btn btn-ghost" onClick={load} disabled={loading}>
          <RefreshCw size={13} /> {t('Refresh')}
        </button>
      </div>

      {feedback && <div className="notice" style={{ fontSize: 12 }}>{feedback}</div>}

      {roster && !roster.gameApiReachable && (
        <div className="notice notice-warn" style={{ fontSize: 12 }}>
          The game server is not reachable, so nobody shows as online and kick will
          not work. The roster below still comes from the save.
        </div>
      )}

      <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
        <table className="table">
          <thead>
            <tr>
              <SortHead label={t('Player')} k="name" sort={sort} desc={desc}
                        set={setSort} flip={setDesc} />
              <th>{t('Steam ID')}</th>
              <SortHead label="Level" k="level" sort={sort} desc={desc}
                        set={setSort} flip={setDesc} />
              <th style={{ textAlign: 'right' }}>{t('Actions')}</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((p) => (
              <tr key={p.uid}>
                <td>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                    <span className={p.online ? 'status-dot online' : 'status-dot'} />
                    <span style={{ color: 'var(--text-primary)' }}>{p.name || '(unnamed)'}</span>
                    {p.hasAccount && (
                      <span className="badge" title={`Dashboard account: ${p.accountUsername}`}>
                        {p.accountUsername}
                      </span>
                    )}
                    {p.online && p.ping != null && (
                      <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                        {Math.round(p.ping)}ms
                      </span>
                    )}
                  </span>
                  {(p.trainerBuffs?.length ?? 0) > 0 && (
                    /* What the party grants the player — per effect with its
                       condition and source Pal, never summed: how two buffs
                       stack is stated in no game file, and a riding-only buff
                       is not active on foot. */
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 3 }}>
                      {p.trainerBuffs!.map((b, i) => (
                        <span
                          key={i}
                          title={`${b.skillName} on ${b.palName} — ${b.affectsLabel}, ${b.whenLabel}`}
                          style={{ marginRight: 10, whiteSpace: 'nowrap' }}
                        >
                          {b.label} {b.value > 0 ? '+' : ''}{b.value}
                          {b.unit === 'percent' ? '%' : ''}
                          {b.whenLabel !== 'always' && (
                            <span style={{ opacity: 0.7 }}> ({b.whenLabel})</span>
                          )}
                        </span>
                      ))}
                    </div>
                  )}
                </td>
                <td>
                  <button
                    onClick={() => copyUid(p.uid)}
                    title="Copy"
                    className="mono"
                    style={{
                      background: 'none', border: 'none', padding: 0, cursor: 'pointer',
                      color: 'var(--text-muted)', fontSize: 11,
                      display: 'inline-flex', alignItems: 'center', gap: 5,
                    }}
                  >
                    {p.uid}
                    {copied === p.uid ? <Check size={11} /> : <Copy size={11} />}
                  </button>
                </td>
                <td className="mono">{p.level ?? '—'}</td>
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                  {roster?.canManageAccounts && !p.hasAccount && (
                    <button
                      className="btn btn-ghost"
                      style={{ padding: '3px 8px', fontSize: 11, marginLeft: 6 }}
                      onClick={() => setCreatingFor(p)}
                    >
                      <UserPlus size={11} /> {t('Account')}
                    </button>
                  )}
                  {mayModerate && p.online && (
                    <button
                      className="btn btn-ghost"
                      style={{ padding: '3px 8px', fontSize: 11, marginLeft: 6 }}
                      onClick={() => {
                        if (!confirm(`Kick ${p.name}?`)) return;
                        act('Kick', () => kickPlayer(p.restUserId || p.uid), `Kicked ${p.name}`);
                      }}
                    >
                      <LogOut size={11} /> Kick
                    </button>
                  )}
                  {mayModerate && (
                    <button
                      className="btn btn-ghost"
                      style={{ padding: '3px 8px', fontSize: 11, marginLeft: 6, color: 'var(--accent-red)' }}
                      onClick={() => {
                        if (!confirm(`Ban ${p.name}? They will be disconnected and blocked.`)) return;
                        act('Ban', () => banPlayer(p.restUserId || p.uid), `Banned ${p.name}`);
                      }}
                    >
                      <Ban size={11} /> Ban
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {!loading && !filtered.length && (
          <p style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
            <Users size={16} style={{ display: 'block', margin: '0 auto 8px' }} />
            {roster?.players.length
              ? 'No players matched.'
              : 'No players yet — this needs a parsed save. Press Refresh on the Overview tab.'}
          </p>
        )}
      </div>

      {creatingFor && (
        <CreateAccountForm
          player={creatingFor}
          onClose={() => setCreatingFor(null)}
          onDone={(msg) => {
            setCreatingFor(null);
            say(msg);
            load();
          }}
        />
      )}

      {mayModerate && (
        <div className="glass-card" style={{ padding: 14, display: 'flex', gap: 8, alignItems: 'center' }}>
          {/* Unbanning is by id because a banned player is, by construction, not
              in the online list — and may not be in the save either. */}
          <input
            className="input"
            style={{ flex: 1 }}
            placeholder={t('Steam ID to unban')}
            value={unbanId}
            onChange={(e) => setUnbanId(e.target.value)}
          />
          <button
            className="btn btn-ghost"
            disabled={!unbanId.trim()}
            onClick={() =>
              act('Unban', () => unbanPlayer(unbanId.trim()), `Unbanned ${unbanId.trim()}`)
                .then(() => setUnbanId(''))
            }
          >
            Unban
          </button>
        </div>
      )}

      <p style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        <MapPin size={11} style={{ display: 'inline', verticalAlign: '-1px', marginRight: 4 }} />
        Everyone who has played here, from the save — not only who is connected now.
        Players hiding from you are absent rather than greyed out.
        {roster && !roster.canManageAccounts &&
          ' Creating accounts needs the users.manage capability.'}
      </p>
    </div>
  );
}

/**
 * Create a dashboard account linked to a save character.
 *
 * The uid is fixed and displayed rather than editable. Reaching this from a
 * player row is the whole point — a hand-typed Steam ID that links to nobody is
 * exactly the mistake this is meant to remove, and `users.steam_uid` is how
 * every per-player feature (own progress, privacy, discoveries) finds a
 * character.
 */
function CreateAccountForm({
  player,
  onClose,
  onDone,
}: {
  player: RosterPlayer;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const [username, setUsername] = useState(
    (player.name || '').toLowerCase().replace(/[^a-z0-9._-]/g, '') || 'player'
  );
  const [password, setPassword] = useState('');
  const [role, setRole] = useState('player');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await createUser({
        username,
        password,
        role,
        steamUid: player.uid,
        displayName: player.name,
      });
      onDone(`Created ${username} for ${player.name || player.uid}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not create the account');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="glass-card" style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div className="section-title">Create an account for {player.name || player.uid}</div>
      <div style={{ fontSize: 11, color: 'var(--text-muted)' }} className="mono">
        linked to {player.uid}
      </div>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <input
          className="input"
          style={{ flex: '1 1 160px' }}
          placeholder={t('Username')}
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
        <input
          className="input"
          style={{ flex: '1 1 160px' }}
          type="password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <select
          className="select"
          style={{ width: 170 }}
          value={role}
          onChange={(e) => setRole(e.target.value)}
        >
          <option value="player">{t('Player')}</option>
          <option value="trusted">{t('Trusted player')}</option>
          <option value="readonly">{t('Read only')}</option>
          <option value="moderator">{t('Moderator')}</option>
        </select>
      </div>

      {error && <div className="notice notice-warn" style={{ fontSize: 12 }}>{error}</div>}

      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn" onClick={submit} disabled={busy || !username || !password}>
          <UserPlus size={13} /> {t('Create')}
        </button>
        <button className="btn btn-ghost" onClick={onClose} disabled={busy}>
          Cancel
        </button>
      </div>
      <p style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        Nobody can create an account above their own role — the backend refuses it
        regardless of what this form offers. The password must meet the server&apos;s
        minimum length.
      </p>
    </div>
  );
}
