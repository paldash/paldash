'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Users, ArrowRight, AlertTriangle, ShieldCheck } from 'lucide-react';
import { getGuilds, previewGuildMove, applyGuildMove } from '@/lib/save-api';
import type { GuildInfo, GuildMovePlan } from '@/lib/types';

/**
 * Move a player from one guild to another.
 *
 * Two steps on purpose. "Move this player" sounds like a one-field change and is
 * not: guild membership lives in four structures, and on a solo guild — which is
 * every guild on a small server — the move also decides what happens to that
 * guild's bases. The preview says exactly what will change before anything can
 * be clicked that changes it.
 */
export default function GuildMove({ canEdit }: { canEdit: boolean }) {
  const [guilds, setGuilds] = useState<GuildInfo[]>([]);
  const [playerUid, setPlayerUid] = useState('');
  const [targetGuildId, setTargetGuildId] = useState('');
  const [transferBases, setTransferBases] = useState(false);
  const [plan, setPlan] = useState<GuildMovePlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getGuilds()
      .then((list) => { if (!cancelled) setGuilds(list); })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load guilds');
      });
    return () => { cancelled = true; };
  }, []);

  /** Every guild member in the world, with the guild they are currently in. */
  const players = useMemo(
    () => guilds.flatMap((g) =>
      g.members.map((m) => ({ uid: m.uid, name: m.name, guildId: g.id, guildName: g.name }))
    ),
    [guilds]
  );

  const chosen = players.find((p) => p.uid === playerUid);

  // Their current guild is not a destination. Offering it produces a refusal the
  // moment it is previewed, which is a worse way to learn the same thing.
  const destinations = useMemo(
    () => guilds.filter((g) => g.id !== chosen?.guildId),
    [guilds, chosen]
  );

  // Any change to the inputs invalidates the plan. The apply sends the plan's
  // hash and the backend refuses a stale one, but a preview still on screen
  // describing a different move is how someone confirms the wrong thing.
  const reset = useCallback(() => { setPlan(null); setDone(null); }, []);

  const preview = async () => {
    setBusy(true); setError(null); setDone(null);
    try {
      setPlan(await previewGuildMove(playerUid, targetGuildId, transferBases));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Preview failed');
      setPlan(null);
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!plan?.planHash) return;
    if (!confirm(
      `Move ${plan.playerName} from ${plan.origin.name} to ${plan.target.name}?\n\n` +
      `${plan.movesCharacters} characters` +
      (plan.movesBases ? `, ${plan.movesBases} bases and ${plan.movesBaseWorkers} base Pals` : '') +
      `.\n` +
      (plan.removesOriginGuild
        ? `${plan.origin.name} will be removed once empty — its bases move with the player, nothing is deleted.\n`
        : '') +
      '\nA full backup is taken first and the result is verified. If anything does ' +
      'not add up, the world is rolled back automatically.'
    )) return;

    setBusy(true); setError(null);
    try {
      const result = await applyGuildMove(playerUid, targetGuildId, transferBases, plan.planHash);
      setDone(
        `Moved ${result.playerName} from ${result.fromGuild} to ${result.toGuild} — ` +
        `${result.charactersMoved} characters` +
        (result.basesMoved ? `, ${result.basesMoved} bases` : '') +
        `. Verified. Rollback point: ${result.backupId}.`
      );
      setPlan(null);
      setGuilds(await getGuilds());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Move failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="glass-card" style={{ padding: 16 }}>
      <div className="section-title" style={{ marginBottom: 4 }}>
        <Users size={14} /> Move a player between guilds
      </div>
      <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12, lineHeight: 1.6 }}>
        Takes the player, their character and every Pal they own. Membership, guild
        leadership and both guilds&rsquo; character indexes are rewritten together —
        all of it or none of it.
      </p>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <select
          className="input"
          style={{ flex: '1 1 200px' }}
          value={playerUid}
          onChange={(e) => { setPlayerUid(e.target.value); setTargetGuildId(''); reset(); }}
          disabled={busy}
        >
          <option value="">Pick a player…</option>
          {players.map((p) => (
            <option key={p.uid} value={p.uid}>{p.name} — {p.guildName}</option>
          ))}
        </select>

        <ArrowRight size={14} style={{ color: 'var(--text-muted)' }} />

        <select
          className="input"
          style={{ flex: '1 1 200px' }}
          value={targetGuildId}
          onChange={(e) => { setTargetGuildId(e.target.value); reset(); }}
          disabled={busy || !playerUid}
        >
          <option value="">Pick a destination guild…</option>
          {destinations.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name} ({g.members.length} member{g.members.length === 1 ? '' : 's'})
            </option>
          ))}
        </select>

        <button
          className="btn btn-ghost"
          onClick={preview}
          disabled={busy || !playerUid || !targetGuildId}
        >
          Preview
        </button>
      </div>

      <label style={{
        display: 'flex', alignItems: 'flex-start', gap: 7, marginTop: 12,
        fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6,
      }}>
        <input
          type="checkbox"
          style={{ marginTop: 3 }}
          checked={transferBases}
          onChange={(e) => { setTransferBases(e.target.checked); reset(); }}
          disabled={busy}
        />
        <span>
          <strong>Bring their bases along if the old guild empties.</strong> Needed
          whenever the player is the only member — otherwise the move is refused,
          because it would leave their bases and the Pals working at them in a
          guild with nobody in it. The emptied guild is then removed;{' '}
          <em>nothing is deleted</em>.
        </span>
      </label>

      {error && (
        <div className="notice notice-warn" style={{ marginTop: 12, fontSize: 12 }}>
          <AlertTriangle size={13} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
          {error}
        </div>
      )}

      {done && (
        <div className="notice" style={{ marginTop: 12, fontSize: 12 }}>
          <ShieldCheck size={13} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
          {done}
        </div>
      )}

      {plan && (
        <div style={{
          marginTop: 12, padding: 12, borderRadius: 6,
          border: '1px solid var(--border-primary)', fontSize: 12, lineHeight: 1.8,
        }}>
          {plan.problems?.map((p, i) => (
            <div key={i} style={{ color: 'var(--accent-amber)' }}>{p}</div>
          ))}
          {plan.warnings?.map((w, i) => (
            <div key={`w${i}`} style={{ color: 'var(--text-muted)' }}>{w}</div>
          ))}

          {plan.ok && (
            <>
              <div>
                <strong>{plan.playerName}</strong>: {plan.origin.name} → {plan.target.name}
              </div>
              <div style={{ color: 'var(--text-secondary)' }}>
                {plan.movesCharacters.toLocaleString()} characters move (their own plus
                every Pal they own)
                {plan.movesBases > 0 && (
                  <>, along with {plan.movesBases} base
                    {plan.movesBases === 1 ? '' : 's'} and{' '}
                    {plan.movesBaseWorkers} Pal{plan.movesBaseWorkers === 1 ? '' : 's'} working
                    at them</>
                )}.
              </div>
              {plan.removesOriginGuild && (
                <div style={{ color: 'var(--accent-amber)' }}>
                  {plan.origin.name} will be empty afterwards and is removed. Its bases
                  move to {plan.target.name} rather than being deleted.
                </div>
              )}
              {plan.newLeaderOfOrigin && (
                <div style={{ color: 'var(--text-secondary)' }}>
                  {plan.newLeaderOfOrigin} becomes leader of {plan.origin.name}.
                </div>
              )}
              <button
                className="btn"
                style={{ marginTop: 10 }}
                onClick={apply}
                disabled={busy || !canEdit}
                title={canEdit ? undefined : 'The server must be provably stopped first.'}
              >
                {busy ? 'Moving…' : 'Apply this move'}
              </button>
              {!canEdit && (
                <div style={{ color: 'var(--text-muted)', fontSize: 11 }}>
                  Stop the server to enable this. Guild membership lives in
                  Level.sav, and writing it while the game is running is how a
                  world gets corrupted.
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
