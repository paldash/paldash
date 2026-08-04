'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Search, RefreshCw, PawPrint, ArrowUpDown, Download } from 'lucide-react';
import { getPals, downloadExport, type PalRecord } from '@/lib/save-api';
import { useDashboardStore } from '@/lib/store';
import GameIcon from '@/components/game-icon';
import { getWorkTypes, orderedWork, type WorkType } from '@/lib/work-types';

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

type SortKey =
  | 'level' | 'name' | 'rank' | 'work' | 'ivs'
  | 'ivHp' | 'ivAttack' | 'ivDefense'
  | 'statHp' | 'statAttack' | 'statDefense' | 'statWork';

/**
 * Fallback only. The real list — names, icons and the game's own ordering —
 * comes from `lib/work-types.ts`, which reads the bundled table.
 *
 * This copy was the whole list, hand-written, alphabetically unrelated to the
 * game's order and with shortened names ("Electricity", "Farming") that do not
 * match what a player reads in game. Kept as a fallback so a clone with no
 * bundled data still has a working filter.
 */
const WORK_LABELS: Record<string, string> = {
  EmitFlame: 'Kindling',
  Watering: 'Watering',
  Seeding: 'Planting',
  GenerateElectricity: 'Generating Electricity',
  Handcraft: 'Handiwork',
  Collection: 'Gathering',
  Deforest: 'Lumbering',
  Mining: 'Mining',
  Transport: 'Transporting',
  MonsterFarm: 'Ranching',
  ProductMedicine: 'Medicine Production',
  OilExtraction: 'Oil Extraction',
  Cool: 'Cooling',
};

/** `location` -> what to show in the table. */
const WHERE_LABELS: Record<string, string> = {
  palbox: 'Palbox',
  party: 'Party',
  base: 'Base',
  // A Pal held by a structure the guild built — a Dimensional Pal Storage, a
  // Global Pal Storage, a Flea Market stand. These used to land in `other` and
  // read as "Unassigned", which is how Pals sitting in plain sight in someone's
  // base looked like a parse failure.
  storage: 'Pal storage',
  // Its own value, not a flavour of `storage`, because it is not even in
  // Level.sav: Dimensional Pal Storage is a separate per-player file
  // (`<UID>_dps.sav`) that this dashboard did not read at all, so these Pals
  // were missing from every count rather than mislabelled.
  dimension: 'Dimensional Pal Storage',
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
  // The game's own work list: display names, icons, and its ordering.
  const [workTypes, setWorkTypes] = useState<WorkType[]>([]);
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

  useEffect(() => {
    getWorkTypes().then(setWorkTypes).catch(() => undefined);
  }, []);

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

  const workLabel = useCallback(
    (id: string) =>
      workTypes.find((w) => w.id === id)?.label ?? WORK_LABELS[id] ?? id,
    [workTypes]
  );

  /**
   * One IV, by the name the *save* uses.
   *
   * **The attack IV is stored as `shot`, not `attack`** — `Talent_Shot` in the
   * save, `ivs.shot` through the parser, the API and `charedit`'s field map.
   * This file asked for `ivs.attack`, which is not a key any Pal has, so the
   * Attack column rendered "—" on all 1,905 Pals, the IV total was short by the
   * attack IV on every row, and both the "minimum IV" filter and the Attack sort
   * silently ignored it. Nothing errored; the column simply looked like the game
   * had not filled it in.
   *
   * The `attack` fallback is kept so an older cached parse still reads.
   */
  const iv = (pal: PalRecord, key: string) =>
    pal.ivs?.[key === 'attack' ? 'shot' : key] ?? pal.ivs?.[key] ?? 0;

  /** A calculated stat's final value, or null when the species is unknown. */
  const stat = (pal: PalRecord, key: 'hp' | 'attack' | 'defense' | 'workSpeed') =>
    pal.stats?.[key]?.final ?? null;
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
        case 'ivHp': return iv(p, 'hp');
        case 'ivAttack': return iv(p, 'attack');
        case 'ivDefense': return iv(p, 'defense');
        // Calculated stats. An unknown species sorts to the bottom rather than
        // to the top, which a plain `?? 0` would do on a descending sort.
        case 'statHp': return stat(p, 'hp') ?? -1;
        case 'statAttack': return stat(p, 'attack') ?? -1;
        case 'statDefense': return stat(p, 'defense') ?? -1;
        case 'statWork': return stat(p, 'workSpeed') ?? -1;
        case 'work': return work ? workLevel(p, work) : p.level;
        // Total IVs, which is the "best overall" question. Deliberately a sum
        // and not a weighted score: weighting HP against Attack depends on what
        // the Pal is *for*, and inventing a weighting would bury that choice in
        // a sort nobody can see the workings of.
        case 'ivs': return iv(p, 'hp') + iv(p, 'attack') + iv(p, 'defense');
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
              {(workTypes.length
                ? workTypes.map((w) => [w.id, w.label] as const)
                : Object.entries(WORK_LABELS)
              ).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
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
            <option value="storage">Pal storage</option>
            <option value="dimension">Dimensional Pal Storage</option>
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
              {/* Calculated stats first — they are what "is this Pal good"
                  actually means. The save holds none of them: it stores level,
                  IVs, condenser rank, souls and trust, and the game derives
                  these at load, so they are computed here from the game's own
                  formula. Marked as calculated in the tooltip rather than shown
                  with the same authority as a level. */}
              <SortHead label="HP" k="statHp" sort={sort} desc={descending} set={setSort} flip={setDescending} />
              <SortHead label="Atk" k="statAttack" sort={sort} desc={descending} set={setSort} flip={setDescending} />
              <SortHead label="Def" k="statDefense" sort={sort} desc={descending} set={setSort} flip={setDescending} />
              <SortHead label="WS" k="statWork" sort={sort} desc={descending} set={setSort} flip={setDescending} />
              {/* The IVs those figures were computed from. Previously the only
                  three columns and labelled HP/Atk/Def, which read as the Pal's
                  actual stats — they are the 0-100 talent rolls. */}
              <SortHead label="ivHP" k="ivHp" sort={sort} desc={descending} set={setSort} flip={setDescending} />
              <SortHead label="ivAtk" k="ivAttack" sort={sort} desc={descending} set={setSort} flip={setDescending} />
              <SortHead label="ivDef" k="ivDefense" sort={sort} desc={descending} set={setSort} flip={setDescending} />
              <SortHead label="ΣIV" k="ivs" sort={sort} desc={descending} set={setSort} flip={setDescending} />
              <th title="Work suitabilities, in the game's own order">Work</th>
              <th>Where</th>
              <th>Passives</th>
              {work && <SortHead label={workLabel(work)} k="work" sort={sort} desc={descending} set={setSort} flip={setDescending} />}
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
                <StatCell pal={p} which="hp" />
                <StatCell pal={p} which="attack" />
                <StatCell pal={p} which="defense" />
                <StatCell pal={p} which="workSpeed" />
                <td className="mono">{iv(p, 'hp') || '—'}</td>
                <td className="mono">{iv(p, 'attack') || '—'}</td>
                <td className="mono">{iv(p, 'defense') || '—'}</td>
                <td className="mono">
                  {iv(p, 'hp') + iv(p, 'attack') + iv(p, 'defense')}
                </td>
                <td>
                  {/* Game order, not strongest-first. Someone scanning several
                      rows compares the same slot in the same place each time,
                      which per-row sorting destroys. Sorting by *strength* is
                      what the column headers do, across Pals. */}
                  <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
                    {orderedWork(p.workSuitabilities, workTypes).map(({ type, level }) => (
                      <span
                        key={type.id}
                        title={`${type.label} ${level}`}
                        style={{ display: 'inline-flex', alignItems: 'center', gap: 1 }}
                      >
                        <GameIcon src={type.icon} size={14} />
                        <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>{level}</span>
                      </span>
                    ))}
                    {orderedWork(p.workSuitabilities, workTypes).length === 0 && (
                      <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>—</span>
                    )}
                  </span>
                </td>
                <td style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                  {WHERE_LABELS[p.location ?? 'other'] ?? '—'}
                  {p.location === 'base' && p.baseName && (
                    <span style={{ color: 'var(--text-muted)', marginLeft: 4 }}>{p.baseName}</span>
                  )}
                  {p.location === 'storage' && (p.storageKind || p.baseName) && (
                    <span style={{ color: 'var(--text-muted)', marginLeft: 4 }}>
                      {[p.storageKind, p.baseName].filter(Boolean).join(' · ')}
                    </span>
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

/**
 * One calculated stat, with its whole derivation in the tooltip.
 *
 * The breakdown is not decoration. These figures are computed rather than read
 * from the save, and a bare number nobody can take apart is unfalsifiable — a
 * player who believes it is wrong has no way to say *which term* is wrong, and
 * neither has anyone reading the bug report. Hovering shows base, condenser,
 * trust, souls and the total, which is the same breakdown the game itself shows.
 *
 * An em dash means the species has no entry in the bundled tables. That is the
 * honest answer for the humans and NPCs sharing the character map with Pals:
 * they carry IVs exactly like a Pal, so there is no structural way to tell them
 * apart, and inventing scaling numbers would show confident stats for a
 * merchant.
 */
function StatCell({
  pal, which,
}: {
  pal: PalRecord;
  which: 'hp' | 'attack' | 'defense' | 'workSpeed';
}) {
  const bd = pal.stats?.[which];
  if (!bd) {
    return (
      <td className="mono" style={{ color: 'var(--text-muted)' }}
          title="No stat scaling for this species — humans and NPCs share the character map with Pals.">
        —
      </td>
    );
  }
  const lines = [
    `Base ${bd.base.toLocaleString()}`,
    bd.condenserMultiplier > 1
      ? `Condenser x${bd.condenserMultiplier.toFixed(2)} -> ${bd.baseWithCondenser.toLocaleString()}`
      : '',
    bd.trust ? `Trust +${bd.trust.toLocaleString()}` : '',
    bd.awakening ? `Awakening +${bd.awakening.toLocaleString()}` : '',
    bd.soulMultiplier > 1 ? `Pal Souls x${bd.soulMultiplier.toFixed(2)}` : '',
    `= ${bd.final.toLocaleString()}`,
    '',
    'Calculated from the game\u2019s formula — the save stores only the inputs.',
  ].filter(Boolean);
  return (
    <td className="mono" title={lines.join('\n')}>
      {bd.final.toLocaleString()}
    </td>
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
