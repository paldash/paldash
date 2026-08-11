'use client';

import { useCallback, useEffect, useState } from 'react';
import { Gauge, RefreshCw, Swords, Info } from 'lucide-react';
import { getBuildRanking } from '@/lib/save-api';
import { asArray } from '@/lib/arrays';
import { num } from '@/lib/format';
import GameIcon from '@/components/game-icon';
import type { BuildRanking } from '@/lib/types';

/**
 * Which Pal is fastest, toughest or hardest-hitting at a build you choose.
 *
 * Reference data over the whole species table — this ranks the *game*, while
 * the work and combat rankings under Bases rank the Pals somebody owns.
 *
 * **The build form deliberately greys out for a movement metric**, because
 * level, IVs, condenser stars and soul ranks do not change a speed. That is
 * measured rather than assumed (`buildAffectsMetric` comes off the payload),
 * and it is the single most surprising thing here: a four-star Jetragon is not
 * faster than a one-star one. Passives are the only thing that move it.
 *
 * Two refusals carried straight through from the backend rather than decided
 * here:
 *
 * - **Mode is not known.** Whether a mount flies, swims or walks is in no game
 *   file, so this ranks *rides* and never claims a flyer leaderboard.
 * - **A speed has no unit.** The column is the game's own number.
 */

const METRICS: { id: string; label: string; group: string }[] = [
  { id: 'rideSprint', label: 'Ride speed', group: 'Movement' },
  { id: 'run', label: 'Run speed', group: 'Movement' },
  { id: 'swimDash', label: 'Swim dash', group: 'Movement' },
  { id: 'transport', label: 'Transport speed', group: 'Movement' },
  { id: 'stamina', label: 'Stamina', group: 'Movement' },
  { id: 'attack', label: 'Attack', group: 'Combat' },
  { id: 'hp', label: 'HP', group: 'Combat' },
  { id: 'defense', label: 'Defense', group: 'Combat' },
  { id: 'workSpeed', label: 'Work speed', group: 'Combat' },
];

const ELEMENTS = ['Fire', 'Water', 'Grass', 'Electric', 'Ice', 'Ground',
                  'Dark', 'Dragon', 'Neutral'];

export default function BuildPlanner() {
  const [metric, setMetric] = useState('rideSprint');
  const [level, setLevel] = useState(80);
  const [condenser, setCondenser] = useState(5);
  const [iv, setIv] = useState(100);
  const [souls, setSouls] = useState(20);
  const [passives, setPassives] = useState('');
  const [against, setAgainst] = useState('');
  const [data, setData] = useState<BuildRanking | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await getBuildRanking({
        metric, level, condenser, iv, souls,
        passives: passives.split(',').map((p) => p.trim()).filter(Boolean),
        against,
      }));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the ranking');
    } finally {
      setLoading(false);
    }
  }, [metric, level, condenser, iv, souls, passives, against]);

  useEffect(() => { load(); }, [load]);

  const rows = asArray(data?.rows, 'build ranking rows');
  // Off the payload, never re-derived here — the backend is what knows which
  // metrics a build can move.
  const buildMatters = data?.buildAffectsMetric ?? true;
  const canTarget = metric === 'attack' || metric === 'hp' || metric === 'defense';

  return (
    <div className="glass-card" style={{ padding: 16, marginTop: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
                    marginBottom: 10 }}>
        <h3 style={{ display: 'flex', alignItems: 'center', gap: 6, margin: 0, fontSize: 14 }}>
          <Gauge size={15} /> Build planner
        </h3>
        <div style={{ flex: 1 }} />
        <select className="select" value={metric} onChange={(e) => setMetric(e.target.value)}
                style={{ fontSize: 12, padding: '3px 6px' }}>
          {['Movement', 'Combat'].map((group) => (
            <optgroup key={group} label={group}>
              {METRICS.filter((m) => m.group === group).map((m) => (
                <option key={m.id} value={m.id}>{m.label}</option>
              ))}
            </optgroup>
          ))}
        </select>
        {canTarget && (
          <select className="select" value={against} onChange={(e) => setAgainst(e.target.value)}
                  style={{ fontSize: 12, padding: '3px 6px' }}>
            <option value="">No target element</option>
            {ELEMENTS.map((el) => (
              <option key={el} value={el}>vs {el}</option>
            ))}
          </select>
        )}
        <button className="btn btn-ghost" onClick={load} disabled={loading}
                style={{ padding: '3px 10px', fontSize: 11 }}>
          <RefreshCw size={11} /> Refresh
        </button>
      </div>

      {/* GREYED, NOT HIDDEN. A form that vanishes reads as a missing feature;
          one that dims and says why teaches the thing worth knowing. */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center',
                    fontSize: 12, marginBottom: 8, opacity: buildMatters ? 1 : 0.45 }}>
        <Field label="Level" value={level} set={setLevel} min={1} max={80} />
        <Field label="Stars" value={condenser - 1} set={(v) => setCondenser(v + 1)}
               min={0} max={4} />
        <Field label="IVs" value={iv} set={setIv} min={0} max={100} />
        <Field label="Souls" value={souls} set={setSouls} min={0} max={20} />
        <label style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          Passives
          <input className="input" value={passives} placeholder="Legend, MoveSpeed_up_3"
                 onChange={(e) => setPassives(e.target.value)}
                 style={{ width: 190, fontSize: 12, padding: '2px 6px' }} />
        </label>
      </div>

      {!buildMatters && (
        <div className="notice" style={{ fontSize: 11, marginBottom: 8,
                                         display: 'flex', gap: 6 }}>
          <Info size={12} style={{ flexShrink: 0, marginTop: 1 }} />
          <span>
            Level, stars, IVs and soul ranks do <strong>not</strong> change a
            speed or stamina — those are flat per-species figures and the
            condenser bonus only touches HP, Attack, Defense and Work Speed.
            Passives are the only thing that moves them.
          </span>
        </div>
      )}

      {error && <div className="notice notice-warn" style={{ fontSize: 12 }}>{error}</div>}

      {data?.passiveEffect?.conditional?.length ? (
        <div className="notice" style={{ fontSize: 11, marginBottom: 8 }}>
          Not counted, because nothing here knows the time of day or the ground
          you are on:{' '}
          {data.passiveEffect.conditional
            .map((c) => `${c.passiveId} (${c.type} ${c.value}%)`).join(', ')}
        </div>
      ) : null}

      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ color: 'var(--text-muted)', textAlign: 'left' }}>
              <th style={{ padding: '4px 6px' }}>#</th>
              <th style={{ padding: '4px 6px' }}>Pal</th>
              <th style={{ padding: '4px 6px', textAlign: 'right' }}>
                {data?.label ?? 'Value'}
              </th>
              {/* The un-multiplied figure stays visible beside the sorted one,
                  so nothing is hidden behind the ordering. */}
              {data?.matchupApplied && (
                <th style={{ padding: '4px 6px', textAlign: 'right' }}>Before matchup</th>
              )}
              {data?.against && (
                <th style={{ padding: '4px 6px' }}>Matchup</th>
              )}
              <th style={{ padding: '4px 6px' }}>Elements</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={row.speciesId} style={{ borderTop: '1px solid var(--border-primary)' }}>
                <td style={{ padding: '4px 6px', color: 'var(--text-muted)' }}>{i + 1}</td>
                <td style={{ padding: '4px 6px' }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                    <GameIcon src={row.icon} title={row.name} size={20} />
                    <span style={{ color: 'var(--text-primary)' }}>{row.name}</span>
                    {row.rideable && (
                      <span title="Has mount gear in the game's own data"
                            style={{ fontSize: 10, color: 'var(--text-muted)' }}>ride</span>
                    )}
                  </span>
                </td>
                <td className="mono" style={{ padding: '4px 6px', textAlign: 'right',
                                              color: 'var(--text-primary)' }}>
                  {num(row.value)}
                </td>
                {data?.matchupApplied && (
                  <td className="mono" style={{ padding: '4px 6px', textAlign: 'right',
                                                color: 'var(--text-muted)' }}>
                    {num(row.raw)}
                  </td>
                )}
                {data?.against && (
                  <td style={{ padding: '4px 6px', fontSize: 11 }}>
                    <Matchup label="you" verdict={row.matchup} />
                    {' '}
                    <Matchup label="them" verdict={row.incoming} />
                  </td>
                )}
                <td style={{ padding: '4px 6px', fontSize: 11, color: 'var(--text-muted)' }}>
                  {(row.elements ?? []).join(', ')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 10 }}>
        {data?.ranked ? `Ranked ${num(data.ranked)} species. ` : ''}
        {/* Both refusals, in the payload and therefore said out loud. */}
        Whether a mount flies, swims or walks is not recorded in any game file,
        so this ranks rides rather than flyers. Speeds are the game&rsquo;s own
        unit and do not convert to a distance.
        {data?.against ? ' Element damage uses the game’s own ×1.2 — ' +
          'the same multiplier both ways, since a disadvantaged defender takes ' +
          'the attacker’s bonus rather than a separate penalty.' : ''}
      </p>
    </div>
  );
}

function Field({ label, value, set, min, max }: {
  label: string; value: number; set: (v: number) => void; min: number; max: number;
}) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      {label}
      <input
        className="input"
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(e) => set(Math.max(min, Math.min(max, Number(e.target.value) || min)))}
        style={{ width: 62, fontSize: 12, padding: '2px 6px' }}
      />
    </label>
  );
}

function Matchup({ label, verdict }: { label: string; verdict?: string }) {
  if (!verdict || verdict === 'neutral') {
    return <span style={{ color: 'var(--text-muted)' }}>{label} —</span>;
  }
  return (
    <span style={{
      color: verdict === 'strong' ? 'var(--accent-green)' : 'var(--accent-red, #d16a6a)',
    }}>
      <Swords size={10} /> {label} {verdict}
    </span>
  );
}
