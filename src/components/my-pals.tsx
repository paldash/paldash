'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Search, RefreshCw, PawPrint, ArrowUpDown, Download } from 'lucide-react';
import { getPals, downloadExport, type PalRecord } from '@/lib/save-api';
import { useDashboardStore } from '@/lib/store';
import GameIcon from '@/components/game-icon';

/**
 * A player's own Pals, as a table you can actually work with.
 *
 * This is the view that most justifies a dashboard existing. A palbox holds 960
 * slots and the game shows one Pal at a time, so "which of my Anubis has the
 * best attack IV" is genuinely painful in-game and trivial here. It is also why
 * `/api/pals` is gated on `VIEW_SELF` rather than `VIEW_DETAIL` — a Player
 * could previously see nothing of their own Pals at all.
 *
 * Whose Pals these are is decided by the **backend**: below the
 * `allPalsVisibility` threshold the caller is pinned to their own character
 * whatever they ask for. Nothing here can widen that.
 *
 * All filtering is client-side on purpose. The rows arrive once, already named
 * and enriched, and every filter below is a property of a row — so re-querying
 * the server per keystroke would add latency and cache misses to answer a
 * question the browser can answer instantly.
 */

type SortKey = 'level' | 'name' | 'rank' | 'hp' | 'attack' | 'defense' | 'work';

const WORK_LABELS: Record<string, string> = {
  EmitFlame: 'Kindling',
  Watering: 'Watering',
  Seeding: 'Planting',
  GenerateElectricity: 'Electricity',
  Handcraft: 'Handiwork',
  Collection: 'Gathering',
  Deforest: 'Lumbering',
  Mining: 'Mining',
  OilExtraction: 'Oil',
  ProductMedicine: 'Medicine',
  Cool: 'Cooling',
  Transport: 'Transporting',
  MonsterFarm: 'Farming',
};

/** `location` -> what to show in the table. */
const WHERE_LABELS: Record<string, string> = {
  palbox: 'Palbox',
  party: 'Party',
  base: 'Base',
  other: 'Unassigned',
};

export default function MyPals() {
  // An account with no linked character legitimately owns nothing *here*, and
  // the honest empty list is indistinguishable from a broken one. Saying which
  // it is turns "the dashboard is broken" into a one-line fix an admin can do.
  const linked = !!useDashboardStore((s) => s.user?.steamUid);
  const [pals, setPals] = useState<PalRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [query, setQuery] = useState('');
  const [minLevel, setMinLevel] = useState(0);
  const [gender, setGender] = useState('');
  const [element, setElement] = useState('');
  const [minRank, setMinRank] = useState(0);
  const [minIv, setMinIv] = useState(0);
  const [passive, setPassive] = useState('');
  const [work, setWork] = useState('');
  const [minWork, setMinWork] = useState(1);
  const [alphaOnly, setAlphaOnly] = useState(false);
  const [where, setWhere] = useState('');
  const [exporting, setExporting] = useState('');
  const [sort, setSort] = useState<SortKey>('level');
  const [descending, setDescending] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setPals(await getPals());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load your Pals');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Options come from what you actually own, not from the full 753-species
  // table — a dropdown offering elements none of your Pals have is a list of
  // ways to get an empty result.
  const options = useMemo(() => {
    const elements = new Set<string>();
    const passives = new Set<string>();
    for (const pal of pals) {
      (pal.elements ?? []).forEach((e) => elements.add(e));
      (pal.passiveSkillNames ?? []).forEach((p) => passives.add(p));
    }
    return {
      elements: [...elements].sort(),
      passives: [...passives].sort(),
    };
  }, [pals]);

  const iv = (pal: PalRecord, key: string) => pal.ivs?.[key] ?? 0;
  const workLevel = (pal: PalRecord, key: string) =>
    (pal as PalRecord & { workSuitabilities?: Record<string, number> })
      .workSuitabilities?.[key] ?? 0;

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const rows = pals.filter((p) => {
      if (q &&
        !(p.speciesName ?? '').toLowerCase().includes(q) &&
        !(p.nickname ?? '').toLowerCase().includes(q) &&
        !(p.speciesId ?? '').toLowerCase().includes(q)) return false;
      if (p.level < minLevel) return false;
      if (gender && p.gender !== gender) return false;
      if (element && !(p.elements ?? []).includes(element)) return false;
      if (minRank && (p.rank ?? 1) < minRank) return false;
      if (minIv && Math.max(iv(p, 'hp'), iv(p, 'attack'), iv(p, 'defense')) < minIv) return false;
      if (passive && !(p.passiveSkillNames ?? []).includes(passive)) return false;
      if (work && workLevel(p, work) < minWork) return false;
      if (alphaOnly && !p.isBoss) return false;
      if (where && (p.location ?? 'other') !== where) return false;
      return true;
    });

    const key = (p: PalRecord): number | string => {
      switch (sort) {
        case 'name': return (p.speciesName ?? p.speciesId ?? '').toLowerCase();
        case 'rank': return p.rank ?? 1;
        case 'hp': return iv(p, 'hp');
        case 'attack': return iv(p, 'attack');
        case 'defense': return iv(p, 'defense');
        case 'work': return work ? workLevel(p, work) : p.level;
        default: return p.level;
      }
    };
    return [...rows].sort((a, b) => {
      const ka = key(a);
      const kb = key(b);
      const cmp = typeof ka === 'string' ? ka.localeCompare(kb as string) : (ka as number) - (kb as number);
      return descending ? -cmp : cmp;
    });
  }, [pals, query, minLevel, gender, element, minRank, minIv, passive, work, minWork, alphaOnly, sort, descending]);

  if (error) {
    return (
      <div className="notice notice-warn">
        <strong>Could not load your Pals</strong>
        <div style={{ marginTop: 6 }}>{error}</div>
        <button className="btn btn-ghost" style={{ marginTop: 12 }} onClick={load}>
          <RefreshCw size={13} /> Retry
        </button>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {!linked && (
        <div className="notice notice-warn" style={{ fontSize: 12 }}>
          <strong>This account is not linked to a character.</strong> Everything
          scoped to &ldquo;you&rdquo; — your Pals, your breeding planner, your
          discoveries — has nothing to resolve to, so it comes back empty. An
          Administrator links it from the <strong>Players</strong> tab.
        </div>
      )}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: 1, minWidth: 180 }}>
          <Search size={13} style={{ position: 'absolute', left: 10, top: 9, color: 'var(--text-muted)' }} />
          <input className="input" style={{ paddingLeft: 30 }} placeholder="Species or nickname…"
                 value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <button className="btn btn-ghost" onClick={load} disabled={loading}>
          <RefreshCw size={13} /> Refresh
        </button>
      </div>

      <div className="glass-card" style={{ padding: 12, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <Field label="Min level">
          <input className="input" type="number" min={0} max={80} style={{ width: 70 }}
                 value={minLevel || ''} placeholder="0"
                 onChange={(e) => setMinLevel(Number(e.target.value) || 0)} />
        </Field>

        <Field label="Gender">
          <select className="select" style={{ width: 100 }} value={gender} onChange={(e) => setGender(e.target.value)}>
            <option value="">Any</option>
            <option value="Male">Male</option>
            <option value="Female">Female</option>
          </select>
        </Field>

        <Field label="Element">
          <select className="select" style={{ width: 120 }} value={element} onChange={(e) => setElement(e.target.value)}>
            <option value="">Any</option>
            {options.elements.map((e) => <option key={e} value={e}>{e}</option>)}
          </select>
        </Field>

        <Field label="Min ★">
          <select className="select" style={{ width: 80 }} value={minRank} onChange={(e) => setMinRank(Number(e.target.value))}>
            {[0, 1, 2, 3, 4, 5].map((r) => <option key={r} value={r}>{r || 'Any'}</option>)}
          </select>
        </Field>

        <Field label="Best IV ≥" hint="Highest of HP, Attack or Defense">
          <input className="input" type="number" min={0} max={100} style={{ width: 70 }}
                 value={minIv || ''} placeholder="0"
                 onChange={(e) => setMinIv(Number(e.target.value) || 0)} />
        </Field>

        <Field label="Passive">
          <select className="select" style={{ width: 160 }} value={passive} onChange={(e) => setPassive(e.target.value)}>
            <option value="">Any</option>
            {options.passives.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </Field>

        <Field label="Work" hint="Species work suitability, from bundled game data">
          <span style={{ display: 'inline-flex', gap: 4 }}>
            <select className="select" style={{ width: 130 }} value={work} onChange={(e) => setWork(e.target.value)}>
              <option value="">Any</option>
              {Object.entries(WORK_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <select className="select" style={{ width: 60 }} value={minWork}
                    disabled={!work} onChange={(e) => setMinWork(Number(e.target.value))}>
              {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}+</option>)}
            </select>
          </span>
        </Field>

        <Field label="Where" hint="Palbox, active party, or assigned to work at a base">
          <select className="select" style={{ width: 130 }} value={where} onChange={(e) => setWhere(e.target.value)}>
            <option value="">Anywhere</option>
            <option value="palbox">Palbox</option>
            <option value="party">Party</option>
            <option value="base">Working at a base</option>
            <option value="other">Unassigned</option>
          </select>
        </Field>

        <label style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--text-secondary)' }}>
          <input type="checkbox" checked={alphaOnly} onChange={(e) => setAlphaOnly(e.target.checked)} />
          Alpha only
        </label>

        <button className="btn btn-ghost" style={{ marginLeft: 'auto', fontSize: 11 }}
                onClick={() => {
                  setQuery(''); setMinLevel(0); setGender(''); setElement('');
                  setMinRank(0); setMinIv(0); setPassive(''); setWork('');
                  setAlphaOnly(false); setWhere('');
                }}>
          Clear filters
        </button>
      </div>

      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        {filtered.length.toLocaleString()} of {pals.length.toLocaleString()} Pals
      </div>

      <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
        <table className="table">
          <thead>
            <tr>
              <SortHead label="Pal" k="name" sort={sort} desc={descending} set={setSort} flip={setDescending} />
              <SortHead label="Lv" k="level" sort={sort} desc={descending} set={setSort} flip={setDescending} />
              <SortHead label="★" k="rank" sort={sort} desc={descending} set={setSort} flip={setDescending} />
              <SortHead label="HP" k="hp" sort={sort} desc={descending} set={setSort} flip={setDescending} />
              <SortHead label="Atk" k="attack" sort={sort} desc={descending} set={setSort} flip={setDescending} />
              <SortHead label="Def" k="defense" sort={sort} desc={descending} set={setSort} flip={setDescending} />
              <th>Where</th>
              <th>Passives</th>
              {work && <SortHead label={WORK_LABELS[work]} k="work" sort={sort} desc={descending} set={setSort} flip={setDescending} />}
              <th />
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 500).map((p) => (
              <tr key={p.instanceId}>
                <td>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                    <GameIcon src={p.icon} size={24} />
                    <span>
                      <span style={{ color: 'var(--text-primary)' }}>
                        {p.nickname || p.speciesName || p.speciesId}
                      </span>
                      {p.nickname && p.speciesName && (
                        <span style={{ color: 'var(--text-muted)', fontSize: 11, marginLeft: 6 }}>
                          {p.speciesName}
                        </span>
                      )}
                      {p.isBoss && <span className="badge" style={{ marginLeft: 6 }}>Alpha</span>}
                      <span style={{ color: 'var(--text-muted)', fontSize: 11, marginLeft: 6 }}>
                        {p.gender === 'Female' ? '♀' : '♂'}
                      </span>
                    </span>
                  </span>
                </td>
                <td className="mono">{p.level}</td>
                <td className="mono">{p.rank > 1 ? '★'.repeat(p.rank - 1) : '—'}</td>
                <td className="mono">{p.ivs?.hp ?? '—'}</td>
                <td className="mono">{p.ivs?.attack ?? '—'}</td>
                <td className="mono">{p.ivs?.defense ?? '—'}</td>
                <td style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                  {WHERE_LABELS[p.location ?? 'other'] ?? '—'}
                  {p.location === 'base' && p.baseName && (
                    <span style={{ color: 'var(--text-muted)', marginLeft: 4 }}>{p.baseName}</span>
                  )}
                </td>
                <td style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                  {(p.passiveSkillNames ?? []).join(', ') || '—'}
                </td>
                {work && <td className="mono">{workLevel(p, work) || '—'}</td>}
                <td>
                  {/* One Pal, as the same document `saveexport` already emits
                      inside a player export — so "back this up before I edit
                      it" and "move it to another server" are one file. */}
                  <button
                    className="btn btn-ghost"
                    style={{ padding: '2px 6px', fontSize: 10 }}
                    disabled={exporting === p.instanceId}
                    title="Download this Pal as a checksummed JSON document"
                    onClick={async () => {
                      setExporting(p.instanceId);
                      setError(null);
                      try {
                        await downloadExport('pal', p.instanceId);
                      } catch (e) {
                        setError(e instanceof Error ? e.message : 'Export failed');
                      } finally {
                        setExporting('');
                      }
                    }}
                  >
                    <Download size={10} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {!loading && !filtered.length && (
          <p style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
            <PawPrint size={16} style={{ display: 'block', margin: '0 auto 8px' }} />
            {pals.length
              ? 'No Pals matched those filters.'
              : 'No Pals found. If your account is not linked to a character, ' +
                'an Owner can link it from the Players tab.'}
          </p>
        )}
      </div>

      {filtered.length > 500 && (
        <p style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          Showing the first 500. Narrow the filters to see the rest.
        </p>
      )}
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label title={hint} style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>{label}</span>
      {children}
    </label>
  );
}

function SortHead({
  label, k, sort, desc, set, flip,
}: {
  label: string;
  k: SortKey;
  sort: SortKey;
  desc: boolean;
  set: (k: SortKey) => void;
  flip: (d: boolean) => void;
}) {
  const active = sort === k;
  return (
    <th
      onClick={() => (active ? flip(!desc) : set(k))}
      style={{ cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap' }}
      title={active ? (desc ? 'Descending' : 'Ascending') : `Sort by ${label}`}
    >
      {label}
      <ArrowUpDown
        size={10}
        style={{ marginLeft: 3, opacity: active ? 0.9 : 0.25, verticalAlign: '-1px' }}
      />
    </th>
  );
}
