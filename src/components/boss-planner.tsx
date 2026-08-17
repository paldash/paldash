'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Swords, RefreshCw, MapPin, FlaskRound, Landmark } from 'lucide-react';
import { getBossEncounters } from '@/lib/save-api';
import { asArray } from '@/lib/arrays';
import GameIcon from '@/components/game-icon';
import type { BossEncounter, BossEncounters } from '@/lib/types';
import { t } from '@/lib/chrome';

/**
 * Every boss, with which elements beat it and which of its own beat you.
 *
 * **The three kinds are shown in separate groups on purpose.** A field boss is
 * placed and has a level; a raid boss is summoned and has no position at all; a
 * tower is a place with no species in the data. Sorting them into one table
 * would invite comparing a level-66 field boss with a raid that has no
 * comparable number, and would make the missing position read as a gap.
 *
 * Two things it refuses, straight from the payload rather than decided here:
 * there is **no recommended level** in any game file, and **no party size**.
 */
export default function BossPlanner() {
  const [data, setData] = useState<BossEncounters | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [kind, setKind] = useState('');
  const [maxLevel, setMaxLevel] = useState('');

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await getBossEncounters({
        kind,
        maxLevel: maxLevel ? Number(maxLevel) : undefined,
      }));
    } catch (e) {
      // Said, not swallowed: an empty boss list is not a plausible answer, so a
      // catch producing one would read as "no bosses" rather than as a failure.
      setError(e instanceof Error ? e.message : 'Could not load the boss list');
    }
  }, [kind, maxLevel]);

  useEffect(() => { load(); }, [load]);

  const bosses = asArray(data?.bosses, 'boss encounters');
  const groups = useMemo(() => ({
    field: bosses.filter((b) => b.kind === 'field'),
    raid: bosses.filter((b) => b.kind === 'raid'),
    tower: bosses.filter((b) => b.kind === 'tower'),
  }), [bosses]);

  return (
    <div className="glass-card" style={{ padding: 14, marginTop: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
                    marginBottom: 8 }}>
        <h3 style={{ display: 'flex', alignItems: 'center', gap: 6, margin: 0,
                     fontSize: 14 }}>
          <Swords size={15} /> Boss planner
        </h3>
        <div style={{ flex: 1 }} />
        <select className="select" value={kind} onChange={(e) => setKind(e.target.value)}
                style={{ fontSize: 12, padding: '3px 6px' }}>
          <option value="">{t('All kinds')}</option>
          <option value="field">{t('Field bosses')}</option>
          <option value="raid">{t('Raid bosses')}</option>
          <option value="tower">{t('Towers')}</option>
        </select>
        <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
          Up to level
          <input className="input" type="number" min={1} max={80} value={maxLevel}
                 placeholder="any"
                 onChange={(e) => setMaxLevel(e.target.value)}
                 style={{ width: 68, fontSize: 12, padding: '2px 6px' }} />
        </label>
        <button className="btn btn-ghost" onClick={load}
                style={{ padding: '3px 10px', fontSize: 11 }}>
          <RefreshCw size={11} /> {t('Refresh')}
        </button>
      </div>

      {error && <div className="notice notice-warn" style={{ fontSize: 12 }}>{error}</div>}

      <Group icon={<MapPin size={13} />} title={t('Field bosses')} rows={groups.field}
             note="Placed in the world, each with its own level." />
      <Group icon={<FlaskRound size={13} />} title={t('Raid bosses')} rows={groups.raid}
             note="Summoned at an altar — these have no location, which is the game rather than missing data." />
      <Group icon={<Landmark size={13} />} title={t('Towers')} rows={groups.tower}
             note="The entrance is what the data names; the boss inside is not in it, so there is no matchup to show." />

      <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 10 }}>
        {/* Both refusals travel in the payload. Rendering them is the point:
            "boss level + 5" is folklore, and this will not print it. */}
        No game file states what level you should be or how many Pals to bring,
        so neither is shown. Element damage uses the game&rsquo;s own ×1.2 — the
        same multiplier both ways, since a disadvantaged defender takes the
        attacker&rsquo;s bonus rather than a separate penalty.
      </p>
    </div>
  );
}

function Group({ icon, title, rows, note }: {
  icon: React.ReactNode; title: string; rows: BossEncounter[]; note: string;
}) {
  if (!rows.length) return null;
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12,
                    fontWeight: 600, color: 'var(--text-primary)', marginBottom: 2 }}>
        {icon} {title} <span style={{ fontWeight: 400, color: 'var(--text-muted)' }}>
          ({rows.length})
        </span>
      </div>
      <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 0 6px' }}>{note}</p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
        {rows.map((boss) => (
          <div key={boss.id} style={{ display: 'flex', alignItems: 'center', gap: 7,
                                      fontSize: 12, flexWrap: 'wrap' }}>
            <GameIcon src={boss.icon} size={20} />
            <span style={{ color: 'var(--text-primary)' }}>{boss.name}</span>
            {/* A level the game states. Absent on towers, and absent is not 0. */}
            {typeof boss.level === 'number' && (
              <span className="mono" style={{ color: 'var(--text-muted)', fontSize: 11 }}>
                Lv {boss.level}
              </span>
            )}
            {boss.elements.length > 0 && (
              <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                {boss.elements.join('/')}
              </span>
            )}
            {boss.counters && (
              <>
                {boss.counters.bringElements.length > 0 && (
                  <span style={{ fontSize: 11, color: 'var(--accent-green)' }}>
                    bring {boss.counters.bringElements.join(', ')}
                  </span>
                )}
                {/* THE HALF A ONE-SIDED PLANNER WOULD DROP, and the one that
                    gets somebody killed. Not the inverse of the line above. */}
                {boss.counters.avoidElements.length > 0 && (
                  <span style={{ fontSize: 11, color: 'var(--accent-red, #d16a6a)' }}>
                    avoid {boss.counters.avoidElements.join(', ')}
                  </span>
                )}
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
