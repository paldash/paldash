'use client';

import { useEffect, useState } from 'react';
import { getReferenceGuides, getReferenceGuide, getRecordedSupply, type ReferenceGuide, type RecordedSupply } from '@/lib/save-api';

export default function ReferenceGuides({ canViewSavedState = false }: { canViewSavedState?: boolean }) {
  const [sections, setSections] = useState<{ id: string; label: string; rows: number }[]>([]);
  const [section, setSection] = useState('fishing');
  const [guide, setGuide] = useState<ReferenceGuide | null>(null);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [limit, setLimit] = useState(50);
  useEffect(() => { let active = true;
    getReferenceGuides().then(r => { if (active) setSections(r.sections); })
      .catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, []);
  useEffect(() => { let active = true;
    getReferenceGuide(section).then(r => { if (active) { setGuide(r); setError(''); } })
      .catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, [section]);
  const rows = guide?.id === section ? guide.rows.filter(r => Object.values(r.cells).some(v => String(v).toLowerCase().includes(query.toLowerCase()))) : [];
  return <section className="glass-card" style={{ padding: 18 }}>
    <h2>Game guides</h2>
    <p>Reference values from the installed game files. Works offline without a world save.</p>
    {canViewSavedState && <RecordedSupplyPanel />}
    <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBlock: 12 }}>
      <select className="select" aria-label="Guide" value={section} onChange={e => { setSection(e.target.value); setQuery(''); setLimit(50); }}>
        {sections.map(s => <option key={s.id} value={s.id}>{s.label} ({s.rows})</option>)}
      </select>
      <input className="input" aria-label="Search guide" placeholder="Search names, groups or requirements" value={query} onChange={e => { setQuery(e.target.value); setLimit(50); }} />
    </div>
    {error && <p role="alert" className="notice notice-warn">{error}</p>}
    {guide?.id === section ? <>
      <h3>{guide.title}</h3><p className="notice">{guide.note}</p>
      <p>{rows.length} matching entries</p>
      <div style={{ overflowX: 'auto' }}><table className="data-table" style={{ width: '100%', fontSize: 13 }}>
        <thead><tr>{guide.columns.map(c => <th key={c} style={{ textAlign: 'left', padding: 8 }}>{c}</th>)}</tr></thead>
        <tbody>{rows.slice(0, limit).map(row => <tr key={row.id}>{guide.columns.map(c => <td key={c} style={{ padding: 8, verticalAlign: 'top', minWidth: c.includes('Reward') ? 280 : 90 }}>{row.cells[c] == null ? 'Not recorded' : String(row.cells[c])}</td>)}</tr>)}</tbody>
      </table></div>
      {rows.length > limit && <button className="btn" onClick={() => setLimit(n => n + 50)}>Show 50 more</button>}
      <details style={{ marginTop: 16 }}><summary>Source and verification</summary>
        <p>Bundled from {guide.source.archive}. Archive SHA-256: <code>{guide.source.sha256}</code>.</p>
        <p>Names resolve through the game catalogue. Missing joins stop extraction; recorded uncertainty remains visible in each guide.</p>
      </details>
    </> : !error && <p>Loading guide…</p>}
  </section>;
}

function RecordedSupplyPanel() {
  const [state, setState] = useState<RecordedSupply | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    getRecordedSupply().then(value => { if (active) setState(value); }).catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, []);
  return <details style={{ marginBlock: 12 }}><summary>Recorded supply events</summary>
    {error && <p role="alert">{error}</p>}
    {state && <><p>{state.note}</p>{state.available ? <>
      <p>Last lottery: {state.LastLotteryTime ?? 'not recorded'} · Last supply: {state.LastSupplyTime ?? 'not recorded'}</p>
      {state.events.length ? <ul>{state.events.map((event, index) => <li key={index}>
        {event.type || 'Unspecified type'} · supply: {event.SupplyTime ?? 'not recorded'} · landed: {event.SupplyLandedTime ?? 'not recorded'}
        {event.bWipedOut_NPC != null && ` · NPCs cleared: ${event.bWipedOut_NPC ? 'yes' : 'no'}`}
        {event.bWipedOut_Pal != null && ` · Pals cleared: ${event.bWipedOut_Pal ? 'yes' : 'no'}`}
      </li>)}</ul> : <p>No events recorded.</p>}
    </> : <p>No supply state is available in the parsed save.</p>}</>}
  </details>;
}
