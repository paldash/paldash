'use client';

import { useEffect, useState } from 'react';
import { Copy, AlertTriangle, ShieldCheck, ArrowRight } from 'lucide-react';
import { previewWorldExport, createWorldExport, getSavePlayers,
  getWorldExportGuilds } from '@/lib/save-api';
import type { WorldExportPlan, WorldExportResult, PlayerSaveData,
  ExportGuild } from '@/lib/types';
import { t } from '@/lib/chrome';

/**
 * Export a playable copy of the world with one player's uid remapped.
 *
 * For carrying a character from the dedicated server into co-op or single-player,
 * or for moving a co-op world onto a server where the host's uid no longer matches.
 *
 * **This is the one save operation that does not need the server stopped**, and the
 * panel says so, because everything else here trains the opposite expectation. It
 * reads the live world and writes a new directory — the source is never modified.
 *
 * The target uid is typed, not picked from a list, because it is a uid that
 * generally does *not* exist in this world yet — it is whatever the destination
 * install will present. Offering a dropdown would imply otherwise.
 */
export default function WorldExport({ canManage }: { canManage: boolean }) {
  const [players, setPlayers] = useState<PlayerSaveData[]>([]);
  const [source, setSource] = useState('');
  const [target, setTarget] = useState('');
  const [plan, setPlan] = useState<WorldExportPlan | null>(null);
  const [result, setResult] = useState<WorldExportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [guilds, setGuilds] = useState<ExportGuild[] | null>(null);
  // `null` means "keep everything", which is what the export did before this
  // option existed. A Set would make "none selected" mean "drop everything",
  // and an empty selection is far too easy to reach by accident.
  const [keep, setKeep] = useState<Set<string> | null>(null);

  useEffect(() => {
    getSavePlayers()
      .then((list) => {
        setPlayers(list);
        if (list.length && !source) setSource(list[0].uid);
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!canManage) return null;

  const loadGuilds = async () => {
    setBusy(true); setError(null);
    try {
      const list = (await getWorldExportGuilds()).guilds;
      setGuilds(list);
      // Everything ticked to begin with. The operator unticks what they want
      // gone, so the destructive direction is always a deliberate act.
      setKeep(new Set(list.map((g) => g.guildId)));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not list guilds');
    } finally {
      setBusy(false);
    }
  };

  const preview = async () => {
    setBusy(true); setError(null); setResult(null); setPlan(null);
    try {
      setPlan(await previewWorldExport(source, target,
        keep ? [...keep] : undefined));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not preview');
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!plan) return;
    setBusy(true); setError(null);
    try {
      setResult(await createWorldExport(source, target, plan.planHash,
        keep ? [...keep] : undefined));
      setPlan(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : t('Export failed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="glass-card" style={{ padding: 16 }}>
      <div className="section-title" style={{ marginBottom: 4 }}>
        <Copy size={14} /> Export a world copy
      </div>
      <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
        Writes a playable copy of this world with one player&apos;s id changed — for
        taking a character into co-op or single-player, or onto another server. The
        copy keeps every player, base and Pal; only the id moves.
        {' '}<strong>{t('Your live world is never modified')}</strong>, so unlike the other
        save tools this does not need the server stopped.
      </p>

      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <div>
          <label style={{ display: 'block', fontSize: 11, color: 'var(--text-muted)', marginBottom: 3 }}>
            Player to remap
          </label>
          <select
            className="select"
            value={source}
            disabled={busy}
            onChange={(e) => { setSource(e.target.value); setPlan(null); setResult(null); }}
          >
            {players.map((p) => (
              <option key={p.uid} value={p.uid}>
                {p.name} — {p.uid.slice(0, 8)}
              </option>
            ))}
          </select>
        </div>

        <ArrowRight size={14} style={{ color: 'var(--text-muted)', marginBottom: 9 }} />

        <div style={{ flex: '1 1 300px' }}>
          <label style={{ display: 'block', fontSize: 11, color: 'var(--text-muted)', marginBottom: 3 }}>
            New player id (the one the destination will use)
          </label>
          <input
            className="input mono"
            style={{ width: '100%' }}
            placeholder="e.g. 22b22b02-0000-0000-0000-000000000000"
            value={target}
            disabled={busy}
            onChange={(e) => { setTarget(e.target.value); setPlan(null); setResult(null); }}
          />
        </div>

        <button className="btn" disabled={busy || !source || !target} onClick={preview}>
          Preview
        </button>
      </div>

      {/* The prune. Opt-in, and the counts arrive before the choice is final —
          a destructive option must never be offered without them. */}
      <div style={{ marginTop: 12 }}>
        {guilds === null ? (
          <button className="btn" disabled={busy} onClick={loadGuilds}>
            Choose which guilds to keep…
          </button>
        ) : (
          <div>
            <div style={{ fontSize: 12, marginBottom: 6 }}>
              Guilds to keep — untick one to remove it and everything it owns.
            </div>
            {guilds.map((g) => {
              const mine = g.adminUid === target || g.playerUids.includes(target);
              const ticked = keep?.has(g.guildId) ?? true;
              return (
                <label
                  key={g.guildId}
                  style={{ display: 'flex', alignItems: 'center', gap: 8,
                           fontSize: 12, padding: '3px 0' }}
                >
                  <input
                    type="checkbox"
                    checked={ticked || mine}
                    /* The exporting player's own guild is kept server-side by
                       `keep_uid` whatever is sent. Disabling it here makes the
                       UI agree with that rather than offering a choice the
                       backend will quietly overrule. */
                    disabled={busy || mine}
                    onChange={(e) => {
                      const next = new Set(keep ?? guilds.map((x) => x.guildId));
                      if (e.target.checked) next.add(g.guildId);
                      else next.delete(g.guildId);
                      setKeep(next); setPlan(null); setResult(null);
                    }}
                  />
                  <span>{g.name || t('Unnamed guild')}</span>
                  <span className="mono" style={{ color: 'var(--text-muted)' }}>
                    {g.guildId.slice(0, 8)} · {g.memberCount} member
                    {g.memberCount === 1 ? '' : 's'}
                  </span>
                  {mine && <span className="badge">yours — always kept</span>}
                </label>
              );
            })}
          </div>
        )}
      </div>

      {error && (
        <div className="notice notice-danger" style={{ fontSize: 12, marginTop: 12 }}>
          <AlertTriangle size={12} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
          {error}
        </div>
      )}

      {plan && (
        <div style={{ marginTop: 14 }}>
          <div
            className={plan.mode === 'swap' ? 'notice notice-warn' : 'notice'}
            style={{ fontSize: 12 }}
          >
            <strong>
              {plan.mode === 'swap'
                ? 'This is a swap, not a rename'
                : 'Rename'}
            </strong>
            <div style={{ marginTop: 4 }}>
              {plan.references.total.toLocaleString()} references across the world
              will be updated
              {plan.references.guildPlayers ? ', including guild membership' : ''}
              {plan.hasDps ? ', plus the dimensional pal storage file' : ''}.
            </div>
            {plan.warnings.map((warning) => (
              <div key={warning} style={{ marginTop: 6, color: 'var(--text-secondary)' }}>
                {warning}
              </div>
            ))}
          </div>

          {plan.prune && plan.prune.dropGuildIds.length > 0 && (
            <div className="notice notice-warn" style={{ fontSize: 12, marginTop: 10 }}>
              <strong>
                Removing {plan.prune.removes.guilds} guild
                {plan.prune.removes.guilds === 1 ? '' : 's'} from the copy
              </strong>
              <div style={{ marginTop: 4 }}>
                {plan.prune.removes.bases.toLocaleString()} bases,{' '}
                {plan.prune.removes.mapObjects.toLocaleString()} structures,{' '}
                {plan.prune.removes.containers.toLocaleString()} containers,{' '}
                {plan.prune.removes.characters.toLocaleString()} Pals and characters
                {plan.prune.removes.ownerlessCharacters
                  ? ` (${plan.prune.removes.ownerlessCharacters.toLocaleString()} of them base workers)`
                  : ''}
                , and {plan.prune.removes.playerSaves} player save
                {plan.prune.removes.playerSaves === 1 ? '' : 's'}.
              </div>
              <div style={{ marginTop: 6, color: 'var(--text-secondary)' }}>
                Your live world is untouched — this only shapes the copy.
              </div>
            </div>
          )}

          <button
            className="btn btn-primary"
            style={{ marginTop: 10 }}
            disabled={busy}
            onClick={apply}
          >
            {busy ? 'Exporting…' : 'Write the copy'}
          </button>
        </div>
      )}

      {result && (
        <div className="notice" style={{ fontSize: 12, marginTop: 14 }}>
          <ShieldCheck size={12} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
          <strong>{t('Copy written and verified.')}</strong>
          <div style={{ marginTop: 5 }}>
            {result.applied.total.toLocaleString()} references remapped ·{' '}
            {(result.archive.sizeBytes / 1024 / 1024).toFixed(1)} MB archive
          </div>

          {/* **A REFUSED PRUNE IS A SUCCESSFUL EXPORT THAT KEPT EVERYTHING.**
              The backend writes the full copy rather than a half-pruned world,
              which is the right outcome and an easy one to hide: reporting
              plain success here would tell the operator their world was pruned
              when it was not. */}
          {result.prune?.requested && !result.prune.pruned && (
            <div className="notice notice-warn" style={{ fontSize: 12, marginTop: 8 }}>
              <strong>{t('Everything was kept.')}</strong> The copy is complete and
              usable, but the guilds you unticked are still in it.
              {result.prune.refused && (
                <div style={{ marginTop: 4, color: 'var(--text-secondary)' }}>
                  {result.prune.refused}
                </div>
              )}
            </div>
          )}

          {result.prune?.pruned && (
            <div style={{ marginTop: 5, color: 'var(--text-secondary)' }}>
              Pruned {result.prune.dropGuildIds?.length ?? 0} guild
              {(result.prune.dropGuildIds?.length ?? 0) === 1 ? '' : 's'}:{' '}
              {(result.prune.removed?.bases ?? 0).toLocaleString()} bases and{' '}
              {(result.prune.removed?.characters ?? 0).toLocaleString()} characters
              removed from the copy.
              {(result.prune.removed?.containerIdsDangling ?? 0) > 0 && (
                <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 3 }}>
                  {result.prune.removed?.containerIdsDangling} container
                  {result.prune.removed?.containerIdsDangling === 1 ? '' : 's'} referenced
                  by those bases did not exist in the save to begin with — a
                  property of the world, not of the export.
                </div>
              )}
            </div>
          )}
          <div className="mono" style={{ fontSize: 11, marginTop: 5, wordBreak: 'break-all' }}>
            {result.archive.path}
          </div>
          <div className="mono" style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 3 }}>
            sha256 {result.archive.sha256.slice(0, 32)}…
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>
            Copy it off the server and unpack it into your own
            {' '}<span className="mono">{t('SaveGames/0/')}</span> directory.
          </div>
        </div>
      )}
    </div>
  );
}
