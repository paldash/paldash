'use client';

import { useEffect, useState } from 'react';
import { Download, Server } from 'lucide-react';
import { createServerExport, getSavePlayers, previewServerExport, type ServerExportPlan, type ServerExportResult } from '@/lib/save-api';
import type { PlayerSaveData } from '@/lib/types';

export default function ServerExport({ canManage }: { canManage: boolean }) {
  const [players, setPlayers] = useState<PlayerSaveData[]>([]);
  const [remove, setRemove] = useState<string[]>([]);
  const [leaders, setLeaders] = useState<Record<string, string>>({});
  const [plan, setPlan] = useState<ServerExportPlan | null>(null);
  const [stale, setStale] = useState(false);
  const [result, setResult] = useState<ServerExportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    if (canManage) getSavePlayers().then(setPlayers).catch(e => setError(String(e)));
  }, [canManage]);
  const name = (uid: string) => players.find(p => p.uid.replaceAll('-', '').toLowerCase() === uid.replaceAll('-', '').toLowerCase())?.name || uid.slice(0, 8);
  async function preview() {
    setBusy(true); setError(''); setResult(null);
    try { setPlan(await previewServerExport(remove, leaders)); setStale(false); }
    catch (e) { setPlan(null); setError(e instanceof Error ? e.message : 'Preview failed'); }
    finally { setBusy(false); }
  }
  async function create() {
    if (!plan || stale) return;
    setBusy(true); setError('');
    try { setResult(await createServerExport(plan)); setPlan(null); }
    catch (e) { setError(e instanceof Error ? e.message : 'Export failed'); }
    finally { setBusy(false); }
  }
  return <section className="glass-card" style={{ padding: 16, marginTop: 18 }}>
    <h3 className="section-title"><Server size={14} /> Export a pruned server world</h3>
    <p style={{ fontSize: 12, margin: '8px 0' }}>
      Make a copy for another dedicated server with selected players removed. Retained players keep their IDs.
      The receiving server uses its own settings. Your source world stays untouched.
    </p>
    {!canManage ? <p>Backup management permission is required.</p> : <>
      <fieldset disabled={busy} style={{ border: 0, padding: 0 }}>
        <legend style={{ fontSize: 12, marginBottom: 8 }}>Select players to remove from the copy</legend>
        {players.map(p => <label key={p.uid} style={{ display: 'flex', gap: 8, marginBottom: 6, fontSize: 12 }}>
          <input type="checkbox" checked={remove.includes(p.uid)} onChange={e => {
            setRemove(current => e.target.checked ? [...current, p.uid] : current.filter(uid => uid !== p.uid));
            setPlan(null); setResult(null); setLeaders({});
          }} /> {p.name} <span className="mono" style={{ color: 'var(--text-muted)' }}>{p.uid.slice(0, 8)}</span>
        </label>)}
      </fieldset>
      <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>
        Private characters, Pals and inventories are removed. Shared guild assets stay with retained members;
        an empty guild and its assets are removed. Unresolved references stop the export.
      </p>
      <button className="btn" disabled={busy || !remove.length || remove.length >= players.length} onClick={preview}>
        {busy ? 'Working…' : stale ? 'Preview updated choices' : 'Preview server copy'}
      </button>
      {plan && <div style={{ marginTop: 14 }}>
        <p>{plan.retainedPlayers} players retained · {plan.removedPlayers} removed · {plan.removedBases} bases removed</p>
        <p style={{ fontSize: 12 }}>{plan.removedCharacters} characters and {plan.removedContainers} containers removed;
          {' '}{plan.reassignedSharedReferences} shared ownership references reassigned.</p>
        {plan.leaderChanges.map(change => <label key={change.guildId} style={{ display: 'block', margin: '8px 0', fontSize: 12 }}>
          Replacement guild leader{' '}
          <select className="select" disabled={busy} value={leaders[change.guildId] || change.newLeaderUid} onChange={e => {
            setLeaders(current => ({ ...current, [change.guildId]: e.target.value })); setStale(true);
          }}>
            {change.retainedMembers.map(uid => <option key={uid} value={uid}>{name(uid)}</option>)}
          </select>
        </label>)}
        <button className="btn btn-primary" disabled={busy || stale} onClick={create}>Create this server copy</button>
      </div>}
      {result && <div className="notice" style={{ marginTop: 12 }}>
        <p>Server copy created and save files verified. Test this copy on an isolated server before using it as your new world.</p>
        <a className="btn" href={`/api/save/export/server/${result.artifactId}/download`} download>
          <Download size={14} /> Download server world
        </a>
        <p className="mono" style={{ fontSize: 10, overflowWrap: 'anywhere' }}>SHA-256: {result.sha256}</p>
        <p style={{ fontSize: 11 }}>Available to this account for seven days.</p>
      </div>}
      {error && <p className="notice notice-danger" role="alert" style={{ marginTop: 10 }}>{error}</p>}
    </>}
  </section>;
}
