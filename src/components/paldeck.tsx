'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { BookOpen, Search, RefreshCw, MapPin } from 'lucide-react';
import { getPaldeck, getPaldeckEntry } from '@/lib/save-api';
import GameIcon from '@/components/game-icon';
import HabitatMap from '@/components/habitat-map';
import type { PaldeckEntry, PaldeckListing, PaldeckDetail } from '@/lib/types';

/**
 * The Paldeck: every Pal in the game, with where it spawns.
 *
 * Reference data, not a report on this server — it needs no parsed save and
 * works on a fresh install. That is why it is a separate view from the map
 * rather than another layer on it: "where do I find Melpaca" is a question
 * about the game, while the map answers questions about your world.
 *
 * Habitat comes from `backend/data/habitats.json.gz`, derived from the game pak
 * by intersecting each spawner blueprint's name table with the known species
 * list. 183 of 204 entries have one; the rest are tower bosses, raid-only and
 * breeding-only Pals that genuinely have no spawner.
 */
export default function Paldeck() {
  const [listing, setListing] = useState<PaldeckListing | null>(null);
  const [selected, setSelected] = useState<PaldeckDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [onlyWithHabitat, setOnlyWithHabitat] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setListing(await getPaldeck());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the Paldeck');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const select = async (entry: PaldeckEntry) => {
    setDetailLoading(true);
    try {
      setSelected(await getPaldeckEntry(entry.id));
    } catch {
      setSelected(null);
    } finally {
      setDetailLoading(false);
    }
  };

  const filtered = useMemo(() => {
    const all = listing?.pals ?? [];
    const q = query.trim().toLowerCase();
    return all.filter(
      (p) =>
        (!onlyWithHabitat || p.hasHabitat) &&
        (!q ||
          p.name.toLowerCase().includes(q) ||
          p.id.toLowerCase().includes(q) ||
          String(p.paldeckNumber) === q ||
          p.elements.some((e) => e.toLowerCase().includes(q)))
    );
  }, [listing, query, onlyWithHabitat]);

  // A habitat can span both landmasses, and the two map images have separate
  // framings — so they are split and drawn as two maps rather than one wrong one.
  const byLandmass = useMemo(() => {
    const regions = selected?.habitat.regions ?? [];
    return {
      palpagos: regions.filter((r) => r.landmass === 'palpagos'),
      worldtree: regions.filter((r) => r.landmass === 'worldtree'),
    };
  }, [selected]);

  if (error) {
    return (
      <div className="notice notice-warn">
        <strong>Paldeck unavailable</strong>
        <div style={{ marginTop: 6 }}>{error}</div>
        <button className="btn btn-ghost" style={{ marginTop: 12 }} onClick={load}>
          <RefreshCw size={13} /> Retry
        </button>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: 1, minWidth: 200 }}>
          <Search size={13} style={{ position: 'absolute', left: 10, top: 9, color: 'var(--text-muted)' }} />
          <input
            className="input"
            style={{ paddingLeft: 30 }}
            placeholder="Search by name, number, element…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-secondary)' }}>
          <input
            type="checkbox"
            checked={onlyWithHabitat}
            onChange={(e) => setOnlyWithHabitat(e.target.checked)}
          />
          Only ones that spawn in the world
        </label>
        <button className="btn btn-ghost" onClick={load} disabled={loading}>
          <RefreshCw size={13} /> Reload
        </button>
      </div>

      <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        {/* ── The list ── */}
        <div
          className="glass-card"
          style={{ padding: 6, flex: '1 1 320px', maxHeight: 560, overflowY: 'auto' }}
        >
          {filtered.map((p) => (
            <button
              key={p.id}
              onClick={() => select(p)}
              style={{
                display: 'flex', alignItems: 'center', gap: 9, width: '100%',
                padding: '5px 8px', borderRadius: 5, cursor: 'pointer',
                border: 'none', textAlign: 'left', font: 'inherit',
                background: selected?.id === p.id ? 'var(--bg-hover, rgba(255,255,255,0.06))' : 'none',
                color: 'inherit',
              }}
            >
              <span className="mono" style={{ color: 'var(--text-muted)', fontSize: 11, width: 30 }}>
                {p.paldeckNumber}
              </span>
              <GameIcon src={p.icon} size={26} />
              <span style={{ color: 'var(--text-primary)' }}>{p.name}</span>
              {p.hasHabitat ? (
                <span style={{ marginLeft: 'auto', color: 'var(--text-muted)', fontSize: 11 }}>
                  <MapPin size={10} style={{ verticalAlign: '-1px' }} /> {p.habitatCells}
                </span>
              ) : (
                <span style={{ marginLeft: 'auto', color: 'var(--text-muted)', fontSize: 10 }}>
                  no spawn
                </span>
              )}
            </button>
          ))}

          {!loading && !filtered.length && (
            <p style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
              <BookOpen size={16} style={{ display: 'block', margin: '0 auto 8px' }} />
              Nothing matched.
            </p>
          )}
        </div>

        {/* ── The detail, with its habitat map ── */}
        <div className="glass-card" style={{ padding: 16, flex: '1 1 300px', minHeight: 200 }}>
          {!selected ? (
            <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>
              Pick a Pal to see its details and where it spawns.
            </p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <GameIcon src={selected.icon} size={44} />
                <div>
                  <div style={{ color: 'var(--text-primary)', fontSize: 15 }}>
                    <span className="mono" style={{ color: 'var(--text-muted)', marginRight: 8 }}>
                      #{selected.paldeckNumber}
                    </span>
                    {selected.name}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)' }} className="mono">
                    {selected.id}
                  </div>
                </div>
              </div>

              {selected.elements.length > 0 && (
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {selected.elements.map((el) => (
                    <span key={el} className="badge">{el}</span>
                  ))}
                </div>
              )}

              {detailLoading ? (
                <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>Loading…</div>
              ) : selected.habitat.known ? (
                <>
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                    Spawns across <strong>{selected.habitat.cells.length}</strong> areas
                    {' '}({selected.habitat.spawnerCount.toLocaleString()} spawn points)
                  </div>
                  <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                    {byLandmass.palpagos.length > 0 && (
                      <div>
                        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
                          Palpagos Islands
                        </div>
                        <HabitatMap regions={byLandmass.palpagos} region="palpagos" />
                      </div>
                    )}
                    {byLandmass.worldtree.length > 0 && (
                      <div>
                        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
                          World Tree
                        </div>
                        <HabitatMap regions={byLandmass.worldtree} region="worldtree" />
                      </div>
                    )}
                  </div>

                  {selected.speciesIds.length > 1 && (
                    <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      Includes location variants:{' '}
                      <span className="mono">{selected.speciesIds.join(', ')}</span>
                    </div>
                  )}
                </>
              ) : (
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  No spawn points in the world. Tower bosses, raid Pals and
                  breeding-only species have none — this is not missing data.
                </div>
              )}

              {selected.stats && Object.keys(selected.stats).length > 0 && (
                <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', fontSize: 12 }}>
                  {Object.entries(selected.stats).map(([k, v]) => (
                    <span key={k} style={{ color: 'var(--text-secondary)' }}>
                      {k} <span className="mono" style={{ color: 'var(--text-primary)' }}>{v}</span>
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <p style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        {listing
          ? `${listing.pals.length} Paldeck entries, ${listing.habitats.species} with spawn data ` +
            `(${listing.habitats.spawnersMatched.toLocaleString()} of ` +
            `${listing.habitats.spawnersTotal.toLocaleString()} spawn points attributed).`
          : 'Loading…'}{' '}
        Spawn areas are derived from the game files, at the resolution of the
        game&apos;s own streaming grid — a shaded square means &ldquo;found around
        here&rdquo;, not a precise location.
      </p>
    </div>
  );
}
