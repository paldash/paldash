'use client';

import { useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Landmark } from 'lucide-react';
import { getDungeonGuide } from '@/lib/save-api';
import { asArray } from '@/lib/arrays';
import GameIcon from '@/components/game-icon';
import { t } from '@/lib/chrome';
import type { DungeonGuide as Guide, DungeonArea } from '@/lib/types';

/**
 * What lives inside the random dungeons, per biome area — enemies with level
 * ranges, chest loot with per-slot shares, and the EXP bonus (#136).
 *
 * Two honesty rules carried from the payload rather than decided here:
 *
 * - **The areas render as ids, labelled as such.** Pocketpair never named the
 *   random dungeons (`NAME_Dungeon01` ships the untranslated marker), so
 *   inventing "Verdant Cavern" would be the exact placeholder-as-name failure
 *   this project refuses. The named "Sealed Realms" are the FIXED overworld
 *   dungeons — a different system, already on the map.
 * - **A loot share is per SLOT, an enemy weight is per GROUP.** Neither says
 *   how often a chest spawns or which group fires; both captions say so.
 */
export default function DungeonGuide() {
  const [guide, setGuide] = useState<Guide | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    getDungeonGuide().then(setGuide).catch((e: unknown) =>
      setError(e instanceof Error ? e.message : 'Could not load the dungeon guide'));
  }, []);

  const areas = useMemo(() => asArray(guide?.areas, 'dungeon areas'), [guide]);

  if (error) {
    return (
      <div className="glass-card" style={{ padding: 14, marginTop: 14 }}>
        <div className="notice notice-warn">{error}</div>
      </div>
    );
  }
  if (!guide) return null;

  return (
    <div className="glass-card" style={{ padding: 14, marginTop: 14 }}>
      <div className="section-title" style={{ marginBottom: 4 }}>
        <Landmark size={15} /> {t('Random dungeons')}
      </div>
      <p style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 10 }}>
        {t('What spawns inside each biome’s dungeons, and what the chests roll.')}{' '}
        {t('The game does not name these areas, so they are listed by internal id.')}
      </p>

      {areas.map((a) => (
        <AreaRow key={a.areaId} area={a}
                 open={open === a.areaId}
                 toggle={() => setOpen(open === a.areaId ? null : a.areaId)} />
      ))}
    </div>
  );
}

function AreaRow({ area, open, toggle }: {
  area: DungeonArea; open: boolean; toggle: () => void;
}) {
  const bosses = area.enemies.filter((e) => e.rank !== 'Normal');
  const exp = area.levels[0]?.bonusExpRate;
  return (
    <div style={{ borderTop: '1px solid var(--border-primary)' }}>
      <button
        onClick={toggle}
        className="btn btn-ghost"
        style={{ width: '100%', justifyContent: 'flex-start', gap: 8,
                 padding: '8px 4px', border: 'none', fontSize: 13 }}
      >
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <span className="mono">{area.label}</span>
        <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>
          {area.enemies.length} {t('spawn groups')}
          {exp && exp !== 1 ? ` · EXP ×${exp}` : ''}
          {bosses.length ? ` · ${bosses.length} ${t('boss groups')}` : ''}
        </span>
      </button>

      {open && (
        <div style={{ padding: '0 4px 12px 25px', fontSize: 12 }}>
          {area.enemies.map((g, i) => (
            <div key={i} style={{ marginBottom: 8 }}>
              <div style={{ color: 'var(--text-muted)', fontSize: 11, marginBottom: 2 }}>
                {g.rank === 'Normal' ? t('Enemies') : g.rank}
                {/* Weight is relative within THIS group only — see payload. */}
                <span className="mono" style={{ marginLeft: 6, opacity: 0.6 }}>
                  {g.spawnerName}
                </span>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {g.roster.map((r, j) => (
                  <span key={j}
                        style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                    {r.icon && <GameIcon src={r.icon} title={r.name} size={20} />}
                    <span>
                      {r.name}
                      <span style={{ color: 'var(--text-muted)', marginLeft: 4 }}>
                        Lv {r.levelMin}–{r.levelMax}
                        {r.countMax > 1 ? ` ×${r.countMin}–${r.countMax}` : ''}
                      </span>
                    </span>
                  </span>
                ))}
              </div>
            </div>
          ))}

          {area.loot.map((l, i) => l.items && (
            <div key={i} style={{ marginBottom: 8 }}>
              <div style={{ color: 'var(--text-muted)', fontSize: 11, marginBottom: 2 }}>
                {l.type === 'Normal' ? t('Chest loot') : `${t('Chest loot')} (${l.type})`}
                {' '}
                <span title={t('Share of the slot when it rolls — nothing states how often a chest spawns.')}
                      style={{ opacity: 0.6 }}>
                  · {t('per-slot share')}
                </span>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {l.items.slice(0, 14).map((it, j) => (
                  <span key={j}
                        title={`${it.name} ×${it.min}${it.max !== it.min ? `–${it.max}` : ''} (${it.grade})`}
                        style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                    {it.icon && <GameIcon src={it.icon} title={it.name} size={18} />}
                    <span>{it.name}</span>
                    {it.slotShare != null && (
                      <span style={{ color: 'var(--text-muted)' }}>
                        {Math.round(it.slotShare * 100)}%
                      </span>
                    )}
                  </span>
                ))}
                {l.items.length > 14 && (
                  <span style={{ color: 'var(--text-muted)' }}>
                    +{l.items.length - 14}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
