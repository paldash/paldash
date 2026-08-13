'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Search, RefreshCw, PawPrint, ArrowUpDown, Download } from 'lucide-react';
import { getPals, downloadExport, type PalRecord } from '@/lib/save-api';
import { useDashboardStore } from '@/lib/store';
import { useLanguage } from '@/lib/use-language';
import { localName, matchesQuery } from '@/lib/language';
import { CAPABILITIES } from '@/lib/permissions';
import GameIcon from '@/components/game-icon';
import PalOptimiser from '@/components/pal-optimiser';
import PalWelfare from '@/components/pal-welfare';
import { getWorkTypes, orderedWork, type WorkType } from '@/lib/work-types';
import { asArray } from '@/lib/arrays';
import { loadPassives, describePassive } from '@/lib/passives';
import { num, fixed, count } from '@/lib/format';

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
  | 'level' | 'name' | 'rank' | 'work' | 'ivs' | 'obtained'
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
  // The welfare panel reads on `VIEW_SELF` — the same gate as this tab — so a
  // Player sees their own struggling Pals. Curing needs `SAVE_EDIT_FULL` AND a
  // stopped server, which is every other write path's rule and not a new one.
  const capabilities = useDashboardStore((s) => s.capabilities);
  const serverRunning = useDashboardStore((s) => s.serverProcessRunning);
  const canCure =
    capabilities.includes(CAPABILITIES.SAVE_EDIT_FULL) && !serverRunning;
  const [pals, setPals] = useState<PalRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [query, setQuery] = useState('');
  const [langPack] = useLanguage();

  /**
   * The species name in the chosen language, falling back to English.
   *
   * Used for BOTH display and search below. Keeping them in one helper is the
   * point: a localised list whose filter still tests only the English name
   * loses every query typed in the language the operator selected, and the
   * reverse loses every English one.
   */
  const speciesName = useCallback(
    (p: { speciesId?: string | null; speciesName?: string | null }) =>
      localName(langPack, 'pals', p.speciesId, p.speciesName ?? p.speciesId ?? ''),
    [langPack]
  );
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
  // `{passiveId: tooltip}`. Catalogue data, fetched once for the distinct set
  // across the whole table rather than per row — see `lib/passives`.
  const [passiveTips, setPassiveTips] = useState<Record<string, string>>({});
  const [sort, setSort] = useState<SortKey>('level');
  const [descending, setDescending] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // **Guarded at the source, because `pals` is ITERATED, not mapped.**
      // A non-array here throws "pals is not iterable" out of the `options`
      // memo — before any `.map` is reached — and takes the whole tab with it.
      // `getPals` is typed `PalRecord[]`; that is a claim about a server which
      // may be a container rebuild behind this page.
      setPals(asArray(await getPals(), 'pals'));
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

  // Descriptions for every passive on screen. Deliberately after the Pals load
  // and independent of it: a failure here costs tooltips, never the table.
  useEffect(() => {
    const ids = [...new Set(pals.flatMap((p) => asArray<string>(p.passiveSkills, 'pal passive ids')))];
    if (ids.length === 0) return;
    let live = true;
    loadPassives(ids)
      .then(() => {
        if (!live) return;
        const tips: Record<string, string> = {};
        for (const id of ids) {
          const text = describePassive(id);
          if (text && text !== id) tips[id] = text;
        }
        setPassiveTips(tips);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [pals]);

  // Options come from what you actually own, not from the full 753-species
  // table — a dropdown offering elements none of your Pals have is a list of
  // ways to get an empty result.
  const options = useMemo(() => {
    const elements = new Set<string>();
    const passives = new Set<string>();
    for (const pal of pals) {
      asArray(pal.elements, 'pal elements').forEach((e) => elements.add(e));
      asArray(pal.passiveSkillNames, 'pal passives').forEach((p) => passives.add(p));
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
      // English name, localised name, id AND the player's nickname. Dropping
      // any one of the four loses a query somebody will reasonably type.
      if (q &&
        !matchesQuery(q, p.speciesName, speciesName(p), p.speciesId) &&
        !(p.nickname ?? '').toLowerCase().includes(q)) return false;
      if (p.level < minLevel) return false;
      if (gender && p.gender !== gender) return false;
      if (element && !asArray(p.elements, 'pal elements').includes(element)) return false;
      if (minRank && (p.rank ?? 1) < minRank) return false;
      if (minIv && Math.max(iv(p, 'hp'), iv(p, 'attack'), iv(p, 'defense')) < minIv) return false;
      if (passive && !asArray(p.passiveSkillNames, 'pal passives').includes(passive)) return false;
      if (work && workLevel(p, work) < minWork) return false;
      if (alphaOnly && !p.isBoss) return false;
      if (where && (p.location ?? 'other') !== where) return false;
      return true;
    });

    const key = (p: PalRecord): number | string => {
      switch (sort) {
        // Sorted by what the reader SEES. Sorting a localised list by the
        // English name produces an order that looks arbitrary on screen.
        case 'name': return speciesName(p).toLowerCase();
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
        // Sorted on the raw ticks, never the formatted string: a date rendered
        // for reading sorts lexicographically by accident and correctly only by
        // luck. 0 for a Pal with no timestamp puts it last ascending, which is
        // where "we do not know when you got this" belongs.
        case 'obtained': return p.obtainedAtTicks ?? 0;
        default: return p.level;
      }
    };
    return [...rows].sort((a, b) => {
      const ka = key(a);
      const kb = key(b);
      const cmp = typeof ka === 'string' ? ka.localeCompare(kb as string) : (ka as number) - (kb as number);
      return descending ? -cmp : cmp;
    });
    // `where` is the location dropdown and it was MISSING from this list, so
    // selecting Palbox/Party/Base recomputed nothing and the table did not
    // change — a filter that renders, accepts a click and does nothing. Found
    // by exhaustive-deps, which is the argument for not blanket-silencing it.
    //
    // `speciesName` is here for the same reason and it is the same bug: it
    // closes over the language pack, so without it, switching language would
    // relabel the rows while leaving the filter and the sort on the previous
    // language — visibly reordered wrongly, and searchable only in a language
    // no longer shown.
  }, [pals, query, minLevel, gender, element, minRank, minIv, passive, work, minWork, alphaOnly, where, sort, descending, speciesName]);

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
      {/* Above the table on purpose. A sick Pal is the one thing here that is
          time-sensitive — a base stops producing days before anyone connects
          the two — and it is invisible in a list sorted by level. */}
      <PalWelfare canEdit={canCure} />
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
            {asArray(options.elements, 'element options').map((e) => <option key={e} value={e}>{e}</option>)}
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
            {asArray(options.passives, 'passive options').map((p) => <option key={p} value={p}>{p}</option>)}
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
        {count(filtered)} of {count(pals)} Pals
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
              {/* WHEN YOU GOT IT. `OwnedTime` has been in every save all along
                  and nothing read it; it is also the only field that answers
                  "which of these did I catch first". Date only — the seconds are
                  real but nobody is choosing a Pal by them. */}
              <SortHead label="Obtained" k="obtained" sort={sort} desc={descending} set={setSort} flip={setDescending} />
              <th>Where</th>
              <th>Passives</th>
              {work && <SortHead label={workLabel(work)} k="work" sort={sort} desc={descending} set={setSort} flip={setDescending} />}
              <th />
            </tr>
          </thead>
          <tbody>
            {asArray(filtered, 'filtered pals').slice(0, 500).map((p) => (
              <tr key={p.instanceId}>
                <td>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                    <GameIcon src={p.icon} size={24} />
                    <span>
                      <span style={{ color: 'var(--text-primary)' }}>
                        {p.nickname || speciesName(p)}
                      </span>
                      {p.nickname && speciesName(p) && (
                        <span style={{ color: 'var(--text-muted)', fontSize: 11, marginLeft: 6 }}>
                          {speciesName(p)}
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
                <td
                  className="mono"
                  style={{ fontSize: 11, color: 'var(--text-muted)' }}
                  title={p.obtainedAt ? `${p.obtainedAt} — the server's own clock, timezone not recorded` : undefined}
                >
                  {/* Date only. The full timestamp is in the tooltip, and it
                      carries no timezone because the save does not store one —
                      .NET keeps a kind flag beside the ticks and this format
                      drops it, so appending a Z would be a claim. */}
                  {p.obtainedAt ? p.obtainedAt.slice(0, 10) : '—'}
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
                  {/* One span per passive so each carries its own tooltip.
                      The NAMES are what a player reads; the ids are what the
                      effect table is keyed on, and they arrive in parallel
                      arrays — so an index mismatch would attach the wrong
                      description to the right name, which is worse than none.
                      Guarded below. */}
                  {(() => {
                    const names = asArray<string>(p.passiveSkillNames, 'pal passives');
                    const ids = asArray<string>(p.passiveSkills, 'pal passive ids');
                    if (names.length === 0) return '—';
                    const aligned = ids.length === names.length;
                    return names.map((name, i) => (
                      <span
                        key={`${name}-${i}`}
                        title={aligned ? passiveTips[ids[i]] || name : name}
                        style={{
                          borderBottom:
                            aligned && passiveTips[ids[i]]
                              ? '1px dotted var(--border-primary)'
                              : undefined,
                          cursor: aligned && passiveTips[ids[i]] ? 'help' : undefined,
                        }}
                      >
                        {name}{i < names.length - 1 ? ', ' : ''}
                      </span>
                    ));
                  })()}
                  {p.skin?.label && (
                    /* Derived, not the game's words — see gamedata.skin_label.
                       Shown because an equipped skin changes what the Pal looks
                       like in game and is otherwise invisible here. */
                    <div
                      title={`Skin: ${p.skin.skinId}`}
                      style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}
                    >
                      ✦ {p.skin.label}
                    </div>
                  )}
                  <ResistBadges resist={p.resist} />
                  <StarSpeedBadge moved={p.partnerMovement} />
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

      {/* Rankings over the same scoped list this table shows, so the two cannot
          disagree about whose Pals are in play. */}
      <div style={{ marginTop: 8, paddingTop: 14, borderTop: '1px solid var(--border-primary)' }}>
        <PalOptimiser />
      </div>
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
    `Base ${num(bd.base)}`,
    bd.condenserMultiplier > 1
      ? `Condenser x${fixed(bd.condenserMultiplier)} -> ${num(bd.baseWithCondenser)}`
      : '',
    bd.trust ? `Trust +${num(bd.trust)}` : '',
    bd.awakening ? `Awakening +${num(bd.awakening)}` : '',
    bd.soulMultiplier > 1 ? `Pal Souls x${fixed(bd.soulMultiplier)}` : '',
    `= ${num(bd.final)}`,
    '',
    'Calculated from the game\u2019s formula — the save stores only the inputs.',
  ].filter(Boolean);
  return (
    <td className="mono" title={lines.join('\n')}>
      {num(bd.final)}
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

/**
 * What a Pal resists, as badges — never as a number you could sort on.
 *
 * `optimise.py`'s rule, one surface over: there is no coefficient that combines
 * a 15% element resistance with the type chart's x1.2, so anything that looked
 * like a defensive score would be inventing one. Percentages and immunities are
 * shown as themselves.
 *
 * **`softTo` is deliberately not rendered here.** It is a property of the
 * species, identical on every Lamball in the list, and repeating it on 1,905
 * rows would bury the thing that actually differs between two Pals. It travels
 * in the payload for the boss planner and the Paldeck, where one Pal is in view.
 */
function ResistBadges({ resist }: { resist?: PalRecord['resist'] }) {
  if (!resist?.any) return null;
  const elements = Object.entries(resist.elements).filter(([, v]) => v > 0);
  const immune = Object.entries(resist.ailments).filter(([, v]) => v.immune);
  const other = Object.entries(resist.other).filter(([, v]) => v !== 0);
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 2 }}>
      {elements.map(([element, percent]) => (
        <span
          key={element}
          /* `when` says whether it applies always or only while the Pal is out
             with you — a real difference the number alone would hide. */
          title={
            `${percent}% less incoming ${element} damage`
            + (resist.when[element] === 'deployed' ? ' (only while in your party)' : '')
            + (resist.softToButResists.includes(element)
              ? ` — and ${element} is strong against this Pal`
              : '')
          }
          style={{
            fontSize: 10,
            padding: '0 4px',
            borderRadius: 3,
            border: '1px solid var(--border-primary)',
            color: 'var(--text-muted)',
            /* The one row worth colouring: a resistance to something that beats
               you. Not a score — just where the two facts meet. */
            borderColor: resist.softToButResists.includes(element)
              ? 'var(--accent-green)' : undefined,
            opacity: resist.when[element] === 'deployed' ? 0.7 : 1,
          }}
        >
          🛡 {element} {percent}%
        </span>
      ))}
      {immune.map(([ailment]) => (
        <span
          key={ailment}
          /* Every ailment resistance in the game's data is 100, so this is
             immunity. Shown as a word rather than as "100%", which would read
             as a percentage comparable with the element figures. */
          title={`Immune to ${ailment}`}
          style={{
            fontSize: 10, padding: '0 4px', borderRadius: 3,
            border: '1px solid var(--border-primary)', color: 'var(--text-muted)',
          }}
        >
          ✖ {ailment}
        </span>
      ))}
      {other.map(([kind, percent]) => (
        <span
          key={kind}
          title={`${kind} resistance ${percent}%`}
          style={{
            fontSize: 10, padding: '0 4px', borderRadius: 3,
            border: '1px solid var(--border-primary)', color: 'var(--text-muted)',
          }}
        >
          {kind} {percent}%
        </span>
      ))}
    </div>
  );
}

/**
 * What condensing THIS Pal bought it, in movement.
 *
 * The build planner answers the question for a hypothetical build; this answers
 * it for the Direhowl somebody actually owns, at the rank it is actually at.
 *
 * **Absent on most Pals, and that is the answer rather than a gap** — 96 of the
 * species have a partner skill that scales with condenser rank and the rest gain
 * nothing, so a "+0%" here would imply a shared rule that does not exist.
 */
function StarSpeedBadge({ moved }: { moved?: PalRecord['partnerMovement'] }) {
  if (!moved) return null;
  // Riding and always-on are listed separately because a ride bonus does
  // nothing for a Pal following you around, and one merged figure would say it
  // did. `run` is dropped from the riding set for the same reason.
  const parts: string[] = [];
  for (const [metric, value] of Object.entries(moved.always)) {
    parts.push(`${metric} +${Math.round(value * 100)}%`);
  }
  for (const [metric, value] of Object.entries(moved.riding)) {
    if (metric === 'run') continue;
    parts.push(`${metric} +${Math.round(value * 100)}% while ridden`);
  }
  if (!parts.length) return null;
  return (
    <div
      title={`From this Pal's partner skill at condenser rank ${moved.condenserRank} (${moved.skillIds.join(', ')}). Rank 1 is no stars.`}
      style={{ fontSize: 10, color: 'var(--accent-green)', marginTop: 2 }}
    >
      ★ {parts.join(' · ')}
    </div>
  );
}
