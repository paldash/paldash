'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { getWorkTypes, orderedWork, type WorkType } from '@/lib/work-types';
import { BookOpen, Search, RefreshCw, MapPin } from 'lucide-react';
import { getPaldeck, getPaldeckEntry } from '@/lib/save-api';
import GameIcon from '@/components/game-icon';
import { useLanguage } from '@/lib/use-language';
import { localName, matchesQuery } from '@/lib/language';

/**
 * The raw stat keys read as code (`meleeAttack`, `craftSpeed`). These are the
 * names the game shows.
 */
const STAT_LABELS: Record<string, string> = {
  hp: 'HP',
  meleeAttack: 'Melee Attack',
  shotAttack: 'Ranged Attack',
  defense: 'Defense',
  craftSpeed: 'Work Speed',
  atk: 'Attack',
  def: 'Defense',
  craft: 'Work Speed',
};
import HabitatMap from '@/components/habitat-map';
import BuildPlanner from '@/components/build-planner';
import type {
  PaldeckEntry, PaldeckListing, PaldeckDetail, SpeciesMove, SpeciesMoves,
  DropBand,
} from '@/lib/types';
import { asArray } from '@/lib/arrays';
import { t } from '@/lib/chrome';

/**
 * The Paldeck: every Pal in the game, with where it spawns.
 *
 * Reference data, not a report on this server — it needs no parsed save and
 * works on a fresh install. That is why it is a separate view from the map
 * rather than another layer on it: "where do I find Melpaca" is a question
 * about the game, while the map answers questions about your world.
 *
 * Habitat comes from `backend/data/habitats.json.gz`, built from the server
 * pak's own `DT_PalWildSpawner` and `DT_PalSpawnerPlacement` — species, level
 * range, group size and relative weight per spawner. **This comment used to
 * describe the name-table intersection that preceded it**, which could only
 * ever claim "this blueprint references this species"; that script is deleted.
 * See `backend/habitats.py`.
 *
 * A Pal with no habitat is not missing data: tower bosses, raid Pals and
 * encounter-only forms genuinely have no world spawner.
 */
export default function Paldeck() {
  const [listing, setListing] = useState<PaldeckListing | null>(null);
  const [selected, setSelected] = useState<PaldeckDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [onlyWithHabitat, setOnlyWithHabitat] = useState(false);
  const [workTypes, setWorkTypes] = useState<WorkType[]>([]);
  const [langPack] = useLanguage();

  /** The species name in the chosen language. Feeds the list AND the filter. */
  const palName = useCallback(
    (p: { id: string; name: string }) => localName(langPack, 'pals', p.id, p.name),
    [langPack]
  );

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
    queueMicrotask(load);
  }, [load]);

  useEffect(() => {
    getWorkTypes().then(setWorkTypes).catch(() => undefined);
  }, []);

  const [detailError, setDetailError] = useState<string | null>(null);

  const select = async (entry: PaldeckEntry) => {
    setDetailLoading(true);
    setDetailError(null);
    try {
      setSelected(await getPaldeckEntry(entry.id));
    } catch (e) {
      // Say so. Swallowing this made a failed request look identical to a click
      // that did not register — the panel just kept saying "pick a Pal", which
      // is the least useful thing it could do.
      setSelected(null);
      setDetailError(e instanceof Error ? e.message : `Could not load ${entry.name}`);
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
          // English, localised and id together — dropping any one loses a query
          // somebody will reasonably type. The Paldeck NUMBER is the other way
          // in, and is the same in every language.
          matchesQuery(q, p.name, palName(p), p.id) ||
          String(p.paldeckNumber) === q ||
          p.elements.some((e) => e.toLowerCase().includes(q)))
    );
  }, [listing, query, onlyWithHabitat, palName]);

  // A habitat can span both landmasses, and the two map images have separate
  // framings — so they are split and drawn as two maps rather than one wrong one.
  const byLandmass = useMemo(() => {
    const regions = selected?.habitat?.regions ?? [];
    return {
      palpagos: regions.filter((r) => r.landmass === 'palpagos'),
      worldtree: regions.filter((r) => r.landmass === 'worldtree'),
    };
  }, [selected]);

  if (error) {
    return (
      <div className="notice notice-warn">
        <strong>{t('Paldeck unavailable')}</strong>
        <div style={{ marginTop: 6 }}>{error}</div>
        <button className="btn btn-ghost" style={{ marginTop: 12 }} onClick={load}>
          <RefreshCw size={13} /> Retry
        </button>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* Mounted here rather than as a sixteenth tab: this is reference data
          about the game, which is what the Paldeck is, and the mobile nav is
          already long enough that another tab has a real cost. */}
      <BuildPlanner />
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: 1, minWidth: 200 }}>
          <Search size={13} style={{ position: 'absolute', left: 10, top: 9, color: 'var(--text-muted)' }} />
          <input
            className="input"
            style={{ paddingLeft: 30 }}
            placeholder={t('Search by name, number, element…')}
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
              <span style={{ color: 'var(--text-primary)' }}>{palName(p)}</span>
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
          {detailError ? (
            <div className="notice notice-warn" style={{ fontSize: 12 }}>
              <strong>{t('Could not load that Pal')}</strong>
              <div style={{ marginTop: 6 }} className="mono">{detailError}</div>
            </div>
          ) : detailLoading && !selected ? (
            <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>{t('Loading…')}</p>
          ) : !selected ? (
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
                    {palName(selected)}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)' }} className="mono">
                    {selected.id}
                  </div>
                </div>
              </div>

              {asArray(selected.elements, 'paldeck elements').length > 0 && (
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {asArray(selected.elements, 'paldeck elements').map((el) => (
                    <span key={el} className="badge">{el}</span>
                  ))}
                </div>
              )}

              {detailLoading ? (
                <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>{t('Loading…')}</div>
              ) : selected.habitat?.known ? (
                <>
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                    Spawns across <strong>{selected.habitat.cells.length}</strong> areas
                    {' '}({(selected.habitat.spawnerCount ?? 0).toLocaleString()} spawn points)
                  </div>
                  <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                    {byLandmass.palpagos.length > 0 && (
                      <div>
                        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
                          {t('Palpagos Islands')}
                        </div>
                        <HabitatMap regions={byLandmass.palpagos} region="palpagos" />
                      </div>
                    )}
                    {byLandmass.worldtree.length > 0 && (
                      <div>
                        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
                          {t('World Tree')}
                        </div>
                        <HabitatMap regions={byLandmass.worldtree} region="worldtree" />
                      </div>
                    )}
                  </div>

                  {(selected.speciesIds ?? []).length > 1 && (
                    <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      Includes location variants:{' '}
                      <span className="mono">{(selected.speciesIds ?? []).join(', ')}</span>
                    </div>
                  )}
                </>
              ) : (
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  No spawn points in the world. Tower bosses, raid Pals and
                  breeding-only species have none — this is not missing data.
                </div>
              )}

              {/* Work suitabilities, with the game's own icons and in the
                  game's own order — which is the order a player already reads
                  on a Pal's page in game. */}
              {selected.partnerSkill?.name && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
                    Partner skill
                  </div>
                  <div style={{ color: 'var(--text-primary)', fontSize: 13 }}>
                    {selected.partnerSkill.name}
                  </div>
                  <PartnerSkillRanks entry={selected.partnerSkill} />
                </div>
              )}

              {(() => {
                const work = orderedWork(
                  selected.work as Record<string, number> | undefined,
                  workTypes
                );
                return (
                  <div style={{ marginBottom: 10 }}>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
                      Work suitability
                    </div>
                    {/* Rendered even when empty, and this is the fix rather than
                        a nicety. The section used to `return null`, so a Pal
                        with no work suitability showed *nothing where a heading
                        should be* — indistinguishable from data the dashboard
                        had failed to load, which is how it got reported.

                        It is genuinely empty for exactly two released Pals,
                        Panthalus (#203) and Astralym (#204); the other 29 forms
                        with no work are raid, gym and unreleased entries the
                        Paldeck does not list. The bundled table matches the
                        game's own data table for all 753 forms with zero
                        disagreements, so an empty set here is the answer, not a
                        gap. */}
                    {!work.length && (
                      <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                        None — this Pal cannot be assigned to work at a base.
                      </div>
                    )}
                    <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                      {work.map(({ type, level }) => (
                        <span
                          key={type.id}
                          title={`${type.label} — level ${level}`}
                          style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12 }}
                        >
                          <GameIcon src={type.icon} size={18} />
                          <span style={{ color: 'var(--text-secondary)' }}>{type.label}</span>
                          <span className="mono" style={{ color: 'var(--text-primary)' }}>{level}</span>
                        </span>
                      ))}
                    </div>
                  </div>
                );
              })()}

              {/* What this species can learn. Two lists and not one, because
                  they are obtained in completely different ways and the egg
                  half is the only reason to breed for a species you could
                  otherwise just catch. */}
              {selected.moves && (
                <MoveLists moves={selected.moves} />
              )}

              {/* What it drops. Bands are shown separately and never merged:
                  128 species have more than one and the contents differ
                  outright — Anubis at level 0 gives Bone and a Pal Soul, at 80
                  it gives World Tree Relics. Averaging them would invent a
                  table the game does not ship. */}
              {(selected.drops?.length || selected.alphaDrops?.length) ? (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
                    Drops
                  </div>
                  <DropBands bands={selected.drops ?? []} />
                  {selected.alphaDrops?.length ? (
                    <>
                      <div style={{
                        fontSize: 11, color: 'var(--text-muted)', margin: '8px 0 4px',
                      }}>
                        {/* A separate row in the game's table, not a bonus on
                            top of the ordinary one. */}
                        Alpha form
                      </div>
                      <DropBands bands={selected.alphaDrops} />
                    </>
                  ) : null}
                </div>
              ) : null}

              {/* Whether breeding can reach it. Silent for an ordinary Pal —
                  "this can be bred normally" is not worth a line. */}
              {selected.obtainability?.note && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
                    Breeding
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                    {selected.obtainability.note}
                  </div>
                  {selected.obtainability.pairings && (
                    <div style={{ marginTop: 4 }}>
                      {selected.obtainability.pairings
                        .filter((p) => !p.breedsTrue)
                        .map((p, i) => (
                          <div key={i} style={{ fontSize: 12, color: 'var(--text-primary)' }}>
                            {p.aName} + {p.bName}
                            {p.genderA && p.genderB && (
                              <span style={{ color: 'var(--text-muted)' }}>
                                {' '}({p.genderA.toLowerCase()} + {p.genderB.toLowerCase()})
                              </span>
                            )}
                          </div>
                        ))}
                    </div>
                  )}
                </div>
              )}

              {selected.stats && Object.keys(selected.stats).length > 0 && (
                <div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
                    Base stats
                    <span title="The species' base values, before any individual Pal's level, IVs, condenser rank, souls, trust or passives. Those ARE computed for a Pal you own — see the stat breakdown on the My Pals tab — but they need a specific Pal, and this panel describes the species.">
                      {' '}(species)
                    </span>
                  </div>
                  <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', fontSize: 12 }}>
                    {Object.entries(selected.stats).map(([k, v]) => (
                      <span key={k} style={{ color: 'var(--text-secondary)' }}>
                        {STAT_LABELS[k] ?? k}{' '}
                        <span className="mono" style={{ color: 'var(--text-primary)' }}>{v}</span>
                      </span>
                    ))}
                  </div>
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

/**
 * What a species can learn, split by how it is obtained.
 *
 * **The split is the point.** A level-up move arrives on its own and needs
 * nothing but time; an egg move can *only* be inherited by breeding and cannot
 * be taught to a Pal that already exists. Merged into one list, the second fact
 * disappears — and it is the fact that decides whether a breeding target is
 * worth chasing at all.
 *
 * Silent when a species has neither, rather than rendering two empty headings.
 */
function MoveLists({ moves }: { moves: SpeciesMoves }) {
  if (!moves.levelUp.length && !moves.egg.length) return null;

  return (
    <div style={{ marginBottom: 10 }}>
      {moves.levelUp.length > 0 && (
        <>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
            Learns by level ({moves.levelUp.length})
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
            {moves.levelUp.map((m) => (
              <MoveChip key={m.id} move={m} label={`Lv ${m.level}`} />
            ))}
          </div>
        </>
      )}

      {moves.egg.length > 0 && (
        <>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
            Egg moves ({moves.egg.length})
            {/* Said in the heading rather than in a footnote, because someone
                scanning the list will otherwise read these as moves the Pal
                picks up eventually. */}
            <span
              style={{ marginLeft: 6 }}
              title="Inherited from a parent when the Pal hatches. A Pal that already exists cannot learn one, so these are only obtainable by breeding."
            >
              — breeding only
            </span>
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {moves.egg.map((m) => (
              <MoveChip key={m.id} move={m} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function MoveChip({ move, label }: { move: SpeciesMove; label?: string }) {
  return (
    <span
      className="badge"
      title={`${move.element} · ${move.category} · power ${move.power} · ${move.cooldown}s cooldown`}
      style={{ fontSize: 11 }}
    >
      {move.name}
      {label && <span style={{ color: 'var(--text-muted)', marginLeft: 4 }}>{label}</span>}
    </span>
  );
}

/**
 * Drop tables, one block per level band.
 *
 * `levelFrom` is a BAND — the source column holds only 0, 10, 20 … 80 — so it
 * renders as "level 80+", never "level 80". A single band gets no heading at
 * all, because "level 0+" in front of the only table is noise.
 */
function DropBands({ bands }: { bands: DropBand[] }) {
  if (bands.length === 0) return null;
  return (
    <>
      {bands.map((band) => (
        <div key={band.levelFrom} style={{ marginBottom: 6 }}>
          {bands.length > 1 && (
            <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 2 }}>
              Level {band.levelFrom}+
            </div>
          )}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {band.items.map((item) => (
              <span
                key={item.itemId}
                title={`${item.name} — ${item.rate}% chance, ${item.min}-${item.max}`}
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: 3,
                  fontSize: 11, color: 'var(--text-secondary)',
                }}
              >
                <GameIcon src={item.icon} size={14} />
                {item.min === item.max ? item.min : `${item.min}-${item.max}`}
                {'\u00d7 '}
                {item.name}
                {/* Only shown below 100: "100%" on every row is noise, and its
                    absence is not ambiguous once one row carries a figure. */}
                {item.rate < 100 && (
                  <span style={{ color: 'var(--text-muted)' }}>({item.rate}%)</span>
                )}
              </span>
            ))}
          </div>
        </div>
      ))}
    </>
  );
}


/**
 * What a partner skill does, across the condenser ranks.
 *
 * **The rank slider is the feature, not a nicety.** The numbers in the game's
 * own sentence move with the stars — Silvegis cuts shield damage by 65% at one
 * and 80% at five — so a single line would answer a question nobody asked and
 * would hide that condensing a Pal improves this at all.
 *
 * Ranks whose text is identical collapse to one row: 306 species have a partner
 * skill and only 479 forms carry rank-indexed entries, so for the rest the same
 * sentence five times would read as five facts.
 */
function PartnerSkillRanks({ entry }: {
  entry: NonNullable<PaldeckDetail['partnerSkill']>;
}) {
  const [rank, setRank] = useState(1);
  const ranks = asArray(entry.byRank, 'partner skill ranks');
  const current = ranks[Math.min(rank, ranks.length) - 1] ?? ranks[0];
  const varies = new Set(ranks.map((r) => r?.description ?? '')).size > 1;

  if (!current?.description) return null;

  return (
    <div style={{ marginTop: 4 }}>
      <p style={{ fontSize: 12, color: 'var(--text-secondary)', whiteSpace: 'pre-line',
                  margin: 0 }}>
        {current.description}
      </p>
      {varies && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 5,
                      fontSize: 11, color: 'var(--text-muted)' }}>
          <span>{t('Condenser')}</span>
          {[1, 2, 3, 4, 5].map((r) => (
            <button
              key={r}
              onClick={() => setRank(r)}
              style={{
                border: '1px solid var(--border-primary)', borderRadius: 4,
                padding: '1px 7px', cursor: 'pointer', font: 'inherit',
                background: r === rank ? 'var(--bg-input)' : 'none',
                color: r === rank ? 'var(--text-primary)' : 'inherit',
              }}
            >
              {r - 1}★
            </button>
          ))}
        </div>
      )}
      {/* The game's text still holds a reference this project does not resolve.
          Shown as written rather than with a number invented for it. */}
      {current.filled === false && (
        <p style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 3 }}>
          Part of this line is a reference the game fills in itself; it is shown
          as written.
        </p>
      )}
    </div>
  );
}
