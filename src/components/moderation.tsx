'use client';

import { useCallback, useEffect, useState } from 'react';
import { Gavel, Megaphone, ShieldCheck, AlertTriangle, UserX, RotateCcw } from 'lucide-react';
import { useDashboardStore } from '@/lib/store';
import { kickPlayer, banPlayer, unbanPlayer, announce, getBanList } from '@/lib/api';
import type { BanList } from '@/lib/types';

/**
 * Kick, ban, unban and broadcast.
 *
 * Every action here goes to the backend rather than the game-REST proxy, so it
 * lands in the audit log with who did it, to whom, why, and whether it worked.
 * That is the reason this panel exists at all — the commands were already
 * reachable, just untraceable.
 *
 * A reason field is offered on kick and ban and is *not* required. Forcing one
 * produces "asdf" rather than insight, but leaving it out entirely means the audit
 * record cannot answer "why" six months later, so it is prominent and optional.
 */
export default function Moderation() {
  const { onlinePlayers } = useDashboardStore();
  const [reason, setReason] = useState('');
  const [message, setMessage] = useState('');
  const [unbanId, setUnbanId] = useState('');
  const [bans, setBans] = useState<BanList | null>(null);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null);

  const loadBans = useCallback(async () => {
    try {
      setBans(await getBanList());
    } catch {
      setBans(null);        // the panel still works without the list
    }
  }, []);

  useEffect(() => {
    void loadBans();
  }, [loadBans]);

  const run = async (label: string, action: () => Promise<unknown>, after?: () => void) => {
    setBusy(true);
    setFeedback(null);
    try {
      await action();
      setFeedback({ ok: true, text: `${label} — recorded in the audit log.` });
      after?.();
    } catch (e) {
      setFeedback({ ok: false, text: e instanceof Error ? e.message : `${label} failed` });
    } finally {
      setBusy(false);
    }
  };

  const nameOf = (uid: string) =>
    onlinePlayers.find((p) => p.userId === uid || p.playerId === uid)?.name ?? uid;

  const doKick = (uid: string) => {
    if (!confirm(`Kick ${nameOf(uid)}? They can rejoin immediately.`)) return;
    void run(`Kicked ${nameOf(uid)}`, () => kickPlayer(uid, reason), () => setReason(''));
  };

  const doBan = (uid: string) => {
    if (!confirm(
      `Ban ${nameOf(uid)}?\n\n` +
      'The game keeps the ban list, not this dashboard, so undoing it means an ' +
      'unban here or editing banlist.txt on the server.'
    )) return;
    void run(`Banned ${nameOf(uid)}`, () => banPlayer(uid, reason), () => {
      setReason('');
      void loadBans();
    });
  };

  return (
    <div className="glass-card" style={{ padding: 16 }}>
      <div className="section-title" style={{ marginBottom: 10 }}>
        <Gavel size={14} /> Moderation
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-muted)' }}>
          every action is audited
        </span>
      </div>

      {feedback && (
        <div
          className={feedback.ok ? 'notice' : 'notice notice-warn'}
          style={{ marginBottom: 10, fontSize: 12 }}
        >
          {feedback.ok
            ? <ShieldCheck size={12} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
            : <AlertTriangle size={12} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />}
          {feedback.text}
        </div>
      )}

      {/* ─── Broadcast ─── */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
        <input
          className="input"
          style={{ flex: 1 }}
          placeholder="Broadcast a message to everyone…"
          value={message}
          maxLength={200}
          onChange={(e) => setMessage(e.target.value)}
        />
        <button
          className="btn"
          disabled={busy || !message.trim()}
          onClick={() => void run('Announced', () => announce(message), () => setMessage(''))}
        >
          <Megaphone size={12} /> Send
        </button>
      </div>

      {/* ─── Reason, shared by kick and ban ─── */}
      <label style={{ display: 'block', fontSize: 11, color: 'var(--text-muted)', marginBottom: 3 }}>
        Reason (optional, but it is what the audit log will show)
      </label>
      <input
        className="input"
        style={{ width: '100%', marginBottom: 12 }}
        placeholder="e.g. destroying other players' bases"
        value={reason}
        maxLength={200}
        onChange={(e) => setReason(e.target.value)}
      />

      {/* ─── Online players ─── */}
      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6 }}>
        Online now
      </div>
      {onlinePlayers.length === 0 ? (
        <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
          Nobody is connected. You can still unban below.
        </div>
      ) : (
        <div style={{ marginBottom: 14 }}>
          {onlinePlayers.map((player, i) => {
            const uid = player.userId || player.playerId;
            return (
              <div
                // Same collision as the Overview list: both ids can come back
                // empty, and a shared key silently renders one row for several
                // players. The index is only ever the last resort.
                key={uid || `${player.name}-${i}`}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8, padding: '5px 0',
                  borderBottom: '1px solid var(--border-primary)',
                }}
              >
                <span style={{ flex: 1, fontSize: 13 }}>{player.name}</span>
                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  Lv {player.level} · {player.ping}ms
                </span>
                <button
                  className="btn btn-ghost"
                  style={{ padding: '2px 8px', fontSize: 11 }}
                  disabled={busy}
                  onClick={() => doKick(uid)}
                >
                  <UserX size={11} /> Kick
                </button>
                <button
                  className="btn btn-ghost"
                  style={{ padding: '2px 8px', fontSize: 11, color: '#f87171' }}
                  disabled={busy}
                  onClick={() => doBan(uid)}
                >
                  <Gavel size={11} /> Ban
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* ─── Ban list ─── */}
      <BanListView bans={bans} busy={busy} onRefresh={() => void loadBans()} />

      <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
        <input
          className="input"
          style={{ flex: 1 }}
          placeholder="Steam ID or uid to unban…"
          value={unbanId}
          onChange={(e) => setUnbanId(e.target.value)}
        />
        <button
          className="btn btn-ghost"
          disabled={busy || !unbanId.trim()}
          onClick={() => void run('Unbanned', () => unbanPlayer(unbanId.trim()), () => {
            setUnbanId('');
            void loadBans();
          })}
        >
          <RotateCcw size={12} /> Unban
        </button>
      </div>
    </div>
  );
}

/**
 * The server's own ban list.
 *
 * "Not found" is rendered differently from "empty" on purpose: an empty list says
 * nobody is banned, and a missing file says we do not know. Showing the second as
 * the first would be a confident lie.
 */
function BanListView({
  bans, busy, onRefresh,
}: {
  bans: BanList | null;
  busy: boolean;
  onRefresh: () => void;
}) {
  if (!bans) return null;

  return (
    <div style={{ marginTop: 4 }}>
      <div style={{
        fontSize: 11, color: 'var(--text-muted)', marginBottom: 6,
        display: 'flex', alignItems: 'center', gap: 8,
      }}>
        Ban list
        <button
          className="btn btn-ghost"
          style={{ padding: '1px 8px', fontSize: 10 }}
          disabled={busy}
          onClick={onRefresh}
        >
          Refresh
        </button>
      </div>

      {!bans.found ? (
        <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>{bans.note}</div>
      ) : bans.bans.length === 0 ? (
        <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          Nobody is banned.
        </div>
      ) : (
        <div style={{ fontSize: 12, fontFamily: 'monospace', lineHeight: 1.8 }}>
          {bans.bans.map((entry) => (
            <div key={entry}>{entry}</div>
          ))}
        </div>
      )}
    </div>
  );
}
