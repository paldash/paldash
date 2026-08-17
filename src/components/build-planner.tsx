'use client';

import { useCallback, useEffect, useState } from 'react';
import { Gauge, RefreshCw, Swords, Info } from 'lucide-react';
import { getBuildRanking } from '@/lib/save-api';
import { asArray } from '@/lib/arrays';
import { num } from '@/lib/format';
import GameIcon from '@/components/game-icon';
import type { BuildRanking } from '@/lib/types';
import { t } from '@/lib/chrome';

/**
 * Which Pal is fastest, toughest or hardest-hitting at a build you choose.
 *
 * Reference data over the whole species table — this ranks the *game*, while
 * the work and combat rankings under Bases rank the Pals somebody owns.
 *
 * **The build form deliberately greys out for a movement metric**, because
 * level, IVs and soul ranks do not enter a speed (`buildAffectsMetric` comes
 * off the payload, never re-derived here). Condenser stars DO move some
 * speeds — through rank-indexed partner skills, applied per row as
 * `partnerBonus` — and the files say that is the only condenser speed term
 * (`condenserOnSpeedColumns: "absentByEnumeration"`). This paragraph has been
 * corrected twice; the history lives in the notice below and in AGENTS.md.
 *
 * One refusal carried straight through from the backend rather than decided
 * here: **a speed has no unit.** The column is the game's own number. (Mount
 * mode, once refused here too, is now read from the species blueprints —
 * `EPalMonsterMovementType` — so the flyer leaderboard is real.)
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
            <option value="">{t('No target element')}</option>
            {ELEMENTS.map((el) => (
              <option key={el} value={el}>vs {el}</option>
            ))}
          </select>
        )}
        <button className="btn btn-ghost" onClick={load} disabled={loading}
                style={{ padding: '3px 10px', fontSize: 11 }}>
          <RefreshCw size={11} /> {t('Refresh')}
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
          {t('Passives')}
          <input className="input" value={passives} placeholder={t('Legend, MoveSpeed_up_3')}
                 onChange={(e) => setPassives(e.target.value)}
                 style={{ width: 190, fontSize: 12, padding: '2px 6px' }} />
        </label>
      </div>

      {!buildMatters && (
        <div className="notice" style={{ fontSize: 11, marginBottom: 8,
                                         display: 'flex', gap: 6 }}>
          <Info size={12} style={{ flexShrink: 0, marginTop: 1 }} />
          <span>
            {/* THIS PANEL HAS BEEN WRONG TWICE. It first said level and stars
                "do not" change a speed; corrected to "unverified" when the
                operator challenged it; corrected again when they observed
                Direhowl getting faster and the mechanism turned up in the
                files. Stars DO raise some speeds — through the species'
                partner skill, which is a list indexed by condenser rank. */}
            Speed and stamina are flat per-species figures: no level, IV or soul
            bonus applies to them, and this ranking invents none.{' '}
            <strong>{t('Stars do raise the speed of some Pals')}</strong> — 96 species
            have a partner skill that scales with condenser rank, so a 4-star
            Direhowl rides 20% faster while most Pals gain nothing. That term is
            applied here and shown per row.{' '}
            The game&apos;s own files say that is the <em>only</em> condenser
            speed term: its status-operation vocabulary is exactly Attack,
            Defence, HP and Work Speed, and the condenser screen previews no
            speed — so no hidden flat bonus is applied here, and none is
            claimed to exist.
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
              <th style={{ padding: '4px 6px' }}>{t('Pal')}</th>
              <th style={{ padding: '4px 6px', textAlign: 'right' }}>
                {data?.label ?? 'Value'}
              </th>
              {/* The un-multiplied figure stays visible beside the sorted one,
                  so nothing is hidden behind the ordering. */}
              {data?.matchupApplied && (
                <th style={{ padding: '4px 6px', textAlign: 'right' }}>{t('Before matchup')}</th>
              )}
              {data?.against && (
                <th style={{ padding: '4px 6px' }}>{t('Matchup')}</th>
              )}
              <th style={{ padding: '4px 6px' }}>{t('Elements')}</th>
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
                  {/* What the stars bought, when they bought anything. Only 96
                      species have a partner skill that scales with condenser
                      rank, so most rows show nothing here — which is the
                      honest answer rather than a "+0%" implying a shared rule. */}
                  {!!row.partnerBonus && (
                    <span
                      title={`+${Math.round(row.partnerBonus * 100)}% from this Pal's partner skill at the chosen condenser rank — the term the stars buy`}
                      style={{ marginLeft: 5, fontSize: 10,
                               color: 'var(--accent-green)' }}
                    >
                      ★+{Math.round(row.partnerBonus * 100)}%
                    </span>
                  )}
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
