'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Egg, Search, RefreshCw, ArrowRight, Ban } from 'lucide-react';
import {
  getBreedingLimits, getBreedingPath, getOffspring, getPalbox, getReachable,
} from '@/lib/save-api';
import type {
  BreedingLimitRow, BreedingLimits, BreedingPath, BreedingScope, OffspringOption,
  PalboxSummary, ReachableTargets,
} from '@/lib/types';
import { useDashboardStore } from '@/lib/store';
import GameIcon from '@/components/game-icon';

/**
 * Breeding planner driven by the Pals actually present in the save.
 *
 * The pair table is the game's own, so special combinations are correct without
 * reimplementing the CombiRank formula. Single-step results respect gender —
 * a pair needs a male and a female — while route finding treats it as a species
 * reachability question.
 */
export default function BreedingPlanner() {
  const { guilds, user } = useDashboardStore();
  const [owner, setOwner] = useState<string>('');
  const [palbox, setPalbox] = useState<PalboxSummary | null>(null);
  const [offspring, setOffspring] = useState<OffspringOption[]>([]);
  const [reachable, setReachable] = useState<ReachableTargets | null>(null);
  // Reference data, so it is fetched once and never re-fetched when `owner`
  // changes — what the game will not let you breed does not depend on whose
  // palbox is being asked about.
  const [limits, setLimits] = useState<BreedingLimits | null>(null);
  const [limitsError, setLimitsError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  // Defaults to hiding what you already own. The reason to open a breeding
  // planner is to get something you have not got, so a list topped by Pals
  // already in your box is mostly noise.
  const [hideOwned, setHideOwned] = useState(true);
  const [target, setTarget] = useState<string | null>(null);
  const [path, setPath] = useState<BreedingPath | null>(null);

  // Guild membership is the cheapest source of player names/UIDs already loaded.
  const players = useMemo(
    () => guilds.flatMap((g) => g.members.map((m) => ({ uid: m.uid, name: m.name }))),
    [guilds]
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [box, kids, indirect] = await Promise.all([
        getPalbox(owner || undefined),
        getOffspring(owner || undefined),
        getReachable(owner || undefined),
      ]);
      setPalbox(box);
      setOffspring(kids);
      setReachable(indirect);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load breeding data');
    } finally {
      setLoading(false);
    }
  }, [owner]);

  useEffect(() => {
    load();
  }, [load]);

  // Separate from `load` on purpose. This is a fact about Palworld rather than
  // about a save, so it needs no owner, survives an unparsed world, and — most
  // of all — a failure here must not take the planner down with it. It is the
  // one panel that can be absent without anything else being wrong.
  //
  // **The reason is kept rather than swallowed.** Setting `null` on failure
  // would render exactly like "this server has nothing to report", which is the
  // `.catch(() => [])` mistake that hid a world's eleven bases for weeks. An
  // empty limits panel and a limits panel that failed to load must not look the
  // same.
  useEffect(() => {
    let live = true;
    getBreedingLimits()
      .then((data) => { if (live) { setLimits(data); setLimitsError(null); } })
      .catch((e: unknown) => {
        if (live) setLimitsError(e instanceof Error ? e.message : 'Could not load');
      });
    return () => { live = false; };
  }, []);

  const findPath = async (internalName: string) => {
    setTarget(internalName);
    setPath(null);
    try {
      setPath(await getBreedingPath(internalName, owner || undefined));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Path search failed');
    }
  };

  /**
   * `species -> why the game constrains it`, so a "not reachable" answer can
   * say which kind of not-reachable it is.
   *
   * "Not reachable within 4 breeding steps from your current Pals" is true of
   * Frostallion and will stay true however many Pals you catch, because no
   * pairing in the game produces one. Reported as a search limit alone, that
   * reads as a dashboard shortcoming — the same failure the Paldeck's empty
   * work-suitability panel had.
   */
  const limitBySpecies = useMemo(() => {
    const map = new Map<string, BreedingLimitRow>();
    for (const group of [limits?.never, limits?.unverified, limits?.namedPairingOnly]) {
      for (const row of group ?? []) map.set(row.species, row);
    }
    return map;
  }, [limits]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return offspring.filter(
      (o) =>
        (!hideOwned || !o.owned) &&
        (!q || o.name.toLowerCase().includes(q)),
    );
  }, [offspring, query, hideOwned]);

  if (error) {
    return (
      <div className="notice notice-warn">
        <strong>Breeding data unavailable</strong>
        <div style={{ marginTop: 6 }}>{error}</div>
        <button className="btn btn-ghost" style={{ marginTop: 12 }} onClick={load}>
          <RefreshCw size={13} /> Retry
        </button>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Same reason as My Pals: below `allPalsVisibility` the planner is scoped
          to your own palbox, and an account with no linked character resolves to
          nobody — so a correct empty planner and a broken one look identical. */}
      {/* `linkedToPlayer` comes from the response, not from the cached session.
          The session object is only refreshed on page load, so an account linked
          while its owner was signed in kept reading as unlinked until they
          reloaded — which is what "it forgot my account" looks like from the
          player's side. The backend answers for the request being made. */}
      {palbox && !palbox.linkedToPlayer && !palbox.mayScopeToOthers && (
        <div className="notice notice-warn" style={{ fontSize: 12 }}>
          <strong>This account is not linked to a character.</strong> The planner
          works from the Pals you own, so it has nothing to plan with. An
          Administrator links it from the <strong>Players</strong> tab.
        </div>
      )}
      {/* Linked, scoped, and still empty — a different problem with a different
          fix, and previously indistinguishable from the one above. */}
      {palbox?.linkedToPlayer && palbox.pals === 0 && (
        <div className="notice notice-warn" style={{ fontSize: 12 }}>
          <strong>No Pals found for this character.</strong> The account is
          linked, but the parsed world has nothing under that uid — either the
          save has not been parsed since you last played, or the link points at a
          different character.
        </div>
      )}
      {/* Controls */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        {/* The selector only appears for callers who can actually use it.
            Below `allPalsVisibility` the backend pins every request to the
            caller, so this rendered a one-option dropdown reading "All Pals on
            the server" over a Player's own palbox — a control that did nothing,
            labelling the result wrongly. `mayScopeToOthers` comes from the
            response rather than being inferred from the role, because the
            threshold is a server policy the client does not otherwise know. */}
        {palbox?.mayScopeToOthers && players.length > 1 ? (
          <select className="select" style={{ width: 220 }} value={owner} onChange={(e) => setOwner(e.target.value)}>
            <option value="">All Pals on the server</option>
            {players.map((p) => (
              <option key={p.uid} value={p.uid}>{p.name}</option>
            ))}
          </select>
        ) : (
          <span
            className="badge"
            title={
              palbox?.mayScopeToOthers
                ? 'You are the only player with Pals here, so "everyone" and "you" are the same set.'
                : 'This server limits the planner to your own Pals. An Administrator can widen it on the Access tab.'
            }
          >
            {palbox?.mayScopeToOthers ? 'All Pals' : 'Your Pals'}
          </span>
        )}

        <div style={{ position: 'relative', flex: 1, minWidth: 200 }}>
          <Search size={13} style={{ position: 'absolute', left: 10, top: 9, color: 'var(--text-muted)' }} />
          <input
            className="input"
            style={{ paddingLeft: 30 }}
            placeholder="Filter offspring…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>

        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-secondary)' }}>
          <input
            type="checkbox"
            checked={hideOwned}
            onChange={(e) => setHideOwned(e.target.checked)}
          />
          Only ones I don&apos;t have
        </label>

        <button className="btn btn-ghost" onClick={load} disabled={loading}>
          <RefreshCw size={13} /> Refresh
        </button>
      </div>

      {loading && <div style={{ color: 'var(--text-muted)', fontSize: 13 }}>Loading…</div>}

      {palbox && (
        <div className="dashboard-grid grid-4">
          <Stat label="Breedable Pals" value={palbox.totalBreedable} />
          <Stat label="Distinct species" value={palbox.speciesCount} />
          <Stat label="One-step offspring" value={offspring.length} />
          <Stat label="Not breedable" value={palbox.skippedUnbreedable} hint="Bosses and alphas cannot breed" />
        </div>
      )}

      {/* Route finder */}
      {target && path && (
        <div className="glass-card" style={{ padding: 16 }}>
          <div className="section-title" style={{ marginBottom: 10 }}>
            Route to {target}
          </div>
          {/* Which Pals this plan was actually computed from. Every scoped
              breeding endpoint reports it now, not just /palbox — the planner
              shows one header over four requests, so a route found in your own
              box under a header reading "all Pals on the server" reads as a
              wrong answer instead of a narrow one. Especially load-bearing on
              "not reachable", which is a claim about a specific set of Pals. */}
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 8 }}>
            {scopeLabel(path)}
          </div>
          {path.alreadyOwned ? (
            <p style={{ fontSize: 13, color: 'var(--text-secondary)' }}>You already own this Pal.</p>
          ) : !path.reachable ? (
            <>
              <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>{path.reason}</p>
              {/* And *why* it will stay unreachable, when the game says so.
                  "Not reachable within 4 steps from your current Pals" is a true
                  statement about Frostallion that no amount of catching will
                  change, and on its own it reads as the planner giving up. */}
              <WhyLimited row={path.target ? limitBySpecies.get(path.target) : undefined} />
            </>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {path.steps.map((step, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
                  <span style={{ color: 'var(--text-muted)', width: 20 }}>{i + 1}.</span>
                  <GameIcon src={step.parentA.icon} size={20} />
                  <span>{step.parentA.name}</span>
                  <WhereNote species={step.parentA.internalName} palbox={palbox} />
                  <span style={{ color: 'var(--text-muted)' }}>+</span>
                  <GameIcon src={step.parentB.icon} size={20} />
                  <span>{step.parentB.name}</span>
                  <WhereNote species={step.parentB.internalName} palbox={palbox} />
                  <ArrowRight size={13} style={{ color: 'var(--text-muted)' }} />
                  <GameIcon src={step.child.icon} size={20} />
                  <span style={{ color: 'var(--accent)' }}>{step.child.name}</span>
                </div>
              ))}
              {/* Said once, at the end, because it qualifies the whole route.
                  A plan is followed step by step, and finding out at step three
                  that it was never possible is the failure this avoids. */}
              {path.genderAware && (
                <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
                  Every step uses a pair you can actually make — the Pals you own
                  are matched by gender, and bred intermediates can be re-rolled
                  until the gender is right.
                </p>
              )}
            </div>
          )}
        </div>
      )}

      {/* Offspring table */}
      <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
        <table className="table">
          <thead>
            <tr>
              <th>Offspring</th>
              <th>Dex</th>
              <th>Pairs</th>
              <th>Example pairing</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((option) => (
              <tr key={option.internalName}>
                <td style={{ color: 'var(--text-primary)' }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                    <GameIcon src={option.icon} size={22} />
                    <span>
                      {option.name}
                      {option.owned && <span className="badge" style={{ marginLeft: 8 }}>owned</span>}
                    </span>
                  </span>
                </td>
                <td className="mono">{option.dex ?? '—'}</td>
                <td className="mono">{option.pairCount}</td>
                <td style={{ fontSize: 12 }}>
                  {option.fromPairs[0] ? `${option.fromPairs[0].a} + ${option.fromPairs[0].b}` : '—'}
                </td>
                <td>
                  <button
                    className="btn btn-ghost"
                    style={{ padding: '3px 8px', fontSize: 11 }}
                    onClick={() => findPath(option.internalName)}
                  >
                    Route
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {!loading && !filtered.length && (
          <p style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
            <Egg size={16} style={{ display: 'block', margin: '0 auto 8px' }} />
            No offspring available. This needs a parsed save with Pals in it.
          </p>
        )}
      </div>

      {/* Reachable, but not in one step. The offspring table above answers
          "what can I breed right now"; this answers the question after it. */}
      {reachable && reachable.targets.length > 0 && (
        <div className="glass-card" style={{ padding: 16 }}>
          <div className="section-title" style={{ marginBottom: 4 }}>
            Reachable with an extra step ({reachable.targets.length})
          </div>
          <p style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 12 }}>
            Not obtainable directly from what you own, but reachable by breeding an
            intermediate first. Counts are <em>breedings you must perform</em>, which
            can exceed the generation count when both parents need breeding too.
            Searched {reachable.maxDepth} generations deep from{' '}
            {reachable.ownedSpecies} owned species — {scopeLabel(reachable)}.
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {reachable.targets.map((t) => (
              <div key={t.internalName}>
                <button
                  onClick={() => setExpanded(expanded === t.internalName ? null : t.internalName)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 8, width: '100%',
                    background: 'none', border: 'none', padding: '4px 0',
                    cursor: 'pointer', color: 'inherit', font: 'inherit', textAlign: 'left',
                  }}
                >
                  <GameIcon src={t.icon} size={22} />
                  <span style={{ color: 'var(--text-primary)' }}>{t.name}</span>
                  <span className="badge">{t.depth} breedings</span>
                  <span style={{ marginLeft: 'auto', color: 'var(--text-muted)', fontSize: 11 }}>
                    {expanded === t.internalName ? 'hide' : 'route'}
                  </span>
                </button>

                {expanded === t.internalName && (
                  <div style={{ margin: '4px 0 10px 30px', display: 'flex', flexDirection: 'column', gap: 5 }}>
                    {t.steps.map((step, i) => (
                      <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12 }}>
                        <span style={{ color: 'var(--text-muted)', width: 16 }}>{i + 1}.</span>
                        <GameIcon src={step.parentA.icon} size={18} />
                        <span>{step.parentA.name}</span>
                        <span style={{ color: 'var(--text-muted)' }}>+</span>
                        <GameIcon src={step.parentB.icon} size={18} />
                        <span>{step.parentB.name}</span>
                        <ArrowRight size={12} style={{ color: 'var(--text-muted)' }} />
                        <GameIcon src={step.child.icon} size={18} />
                        <span style={{ color: 'var(--accent)' }}>{step.child.name}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* What breeding will never reach. Reference data, so it stands apart
          from everything above it — nothing here depends on whose Pals were
          asked about, and it is still true on a server with no parsed world. */}
      {limitsError ? (
        <div className="notice notice-warn" style={{ fontSize: 12 }}>
          <strong>Breeding limits unavailable.</strong> {limitsError} — the
          planner above is unaffected, but this dashboard cannot currently say
          which Pals the game refuses to breed.
        </div>
      ) : limits ? (
        <BreedingLimitsPanel limits={limits} />
      ) : null}
    </div>
  );
}

/**
 * Which Pals breeding cannot produce, in the game's own terms.
 *
 * Three groups rather than one list, because they call for completely
 * different actions: catch it, use this exact pair, or treat the answer as
 * unconfirmed. Collapsing them into "unbreedable" would be wrong about two of
 * the three — and this project shipped exactly that wrong version for a day,
 * so the distinction is the feature.
 */
function BreedingLimitsPanel({ limits }: { limits: BreedingLimits }) {
  const [open, setOpen] = useState<'never' | 'named' | 'unverified' | null>(null);
  const alpha = limits.alphaChance;

  return (
    <div className="glass-card" style={{ padding: 16 }}>
      <div className="section-title" style={{ marginBottom: 4 }}>
        <Ban size={13} style={{ verticalAlign: -2, marginRight: 6 }} />
        What breeding cannot reach
      </div>
      <p style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 12 }}>
        Read from the game&apos;s own data, not from this server — {limits.paldeckEntries}{' '}
        Paldeck entries checked. A Pal missing from the planner is usually here
        rather than missing from the dashboard.
        {alpha !== undefined && (
          <> Separately, a bred Pal hatches as an alpha{' '}
            <strong>{Math.round(alpha * 100)}%</strong> of the time.</>
        )}
      </p>

      <LimitGroup
        title={`No pairing produces these (${limits.never.length})`}
        /* Not "cannot be bred": every one of them is a productive parent, and
           most breed true. What the game rules out is *producing* one you have
           not already got. */
        blurb="The legendaries and tower bosses — caught or summoned, never hatched
               from a pairing of other Pals. They can still be parents, and most of
               them breed true, so two of one make another."
        rows={limits.never}
        open={open === 'never'}
        onToggle={() => setOpen(open === 'never' ? null : 'never')}
      />

      <LimitGroup
        title={`Only from one exact pairing (${limits.namedPairingOnly.length})`}
        /* Mostly element variants, but not only — the Noct and Aqua forms of
           the legendaries are here too (Frostallion Noct is Frostallion +
           Helzephyr), and calling the group "variants" would have hidden them
           behind a label a player would not think to open. */
        blurb="The game names the exact pairs that produce these and the general rank
               rule never will, so they come from the pairs listed or not at all.
               Element variants, and the Noct/Aqua forms of the legendaries."
        rows={limits.namedPairingOnly}
        open={open === 'named'}
        onToggle={() => setOpen(open === 'named' ? null : 'named')}
        showPairings
      />

      <LimitGroup
        title={`Unconfirmed (${limits.unverified.length})`}
        blurb="Element variants the game's own table names no pairing for, while the
               breeding table this planner runs on offers one. Nothing in the game
               files settles the disagreement, so treat any route to these as
               unconfirmed."
        rows={limits.unverified}
        open={open === 'unverified'}
        onToggle={() => setOpen(open === 'unverified' ? null : 'unverified')}
        showMutation
      />
    </div>
  );
}

function LimitGroup({
  title, blurb, rows, open, onToggle, showPairings, showMutation,
}: {
  title: string;
  blurb: string;
  rows: BreedingLimitRow[];
  open: boolean;
  onToggle: () => void;
  showPairings?: boolean;
  showMutation?: boolean;
}) {
  if (!rows.length) return null;
  // One mutated-egg note for the whole group, not one per row — it is the same
  // three sentences about the same mechanic, and repeating it three times reads
  // as three separate findings.
  const egg = showMutation ? rows.find((r) => r.mutatedEgg)?.mutatedEgg : undefined;

  return (
    <div style={{ borderTop: '1px solid var(--border)', paddingTop: 10, marginTop: 10 }}>
      <button
        onClick={onToggle}
        style={{
          display: 'flex', alignItems: 'center', gap: 8, width: '100%',
          background: 'none', border: 'none', padding: 0, cursor: 'pointer',
          color: 'var(--text-primary)', font: 'inherit', fontSize: 13, textAlign: 'left',
        }}
      >
        <span>{title}</span>
        <span style={{ marginLeft: 'auto', color: 'var(--text-muted)', fontSize: 11 }}>
          {open ? 'hide' : 'show'}
        </span>
      </button>
      <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '4px 0 0' }}>{blurb}</p>

      {open && (
        <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {rows.map((row) => (
            <div key={row.species} style={{ fontSize: 12 }}>
              <span style={{ color: 'var(--text-primary)' }}>{row.name}</span>
              <span className="mono" style={{ color: 'var(--text-muted)', marginLeft: 8 }}>
                #{row.paldeck}{row.suffix}
              </span>
              {showPairings && row.pairings && (
                <div style={{ marginLeft: 14, marginTop: 2, color: 'var(--text-secondary)' }}>
                  {row.pairings.map((p, i) => (
                    <div key={i} style={{ fontSize: 11 }}>
                      {p.aName} + {p.bName}
                      {p.genderA && p.genderB && (
                        <span style={{ color: 'var(--text-muted)' }}>
                          {' '}({p.genderA.toLowerCase()} + {p.genderB.toLowerCase()})
                        </span>
                      )}
                      {/* Labelled, because "breed two of the thing you are
                          trying to get" is not an acquisition route. */}
                      {p.breedsTrue && (
                        <span className="badge" style={{ marginLeft: 6 }}>breeds true</span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}

          {egg && (
            <div style={{
              marginTop: 6, padding: 10, borderRadius: 4,
              background: 'var(--surface-2)', fontSize: 11, color: 'var(--text-secondary)',
            }}>
              <div style={{ color: 'var(--text-muted)', marginBottom: 4 }}>
                The game also ships mutated eggs, in its own words:
              </div>
              <div style={{ fontStyle: 'italic' }}>&ldquo;{egg.quote}&rdquo;</div>
              <div style={{ fontStyle: 'italic', marginTop: 4 }}>&ldquo;{egg.cakeQuote}&rdquo;</div>
              {/* The absence is the point, and it is stated rather than left
                  for the reader to infer from two suggestive quotes. */}
              <div style={{ color: 'var(--text-muted)', marginTop: 6 }}>{egg.note}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * The game's reason a target is out of reach, under the planner's own.
 *
 * Silent for an ordinary Pal: "not reachable from your Pals" is the whole
 * answer there, and adding a second sentence saying nothing would bury the
 * cases where there is something to say.
 */
function WhyLimited({ row }: { row?: BreedingLimitRow }) {
  if (!row?.note) return null;
  return (
    <div style={{
      marginTop: 8, padding: 10, borderRadius: 4,
      background: 'var(--surface-2)', fontSize: 12, color: 'var(--text-secondary)',
    }}>
      <strong style={{ color: 'var(--text-primary)' }}>
        {row.kind === 'never' ? 'And no pairing ever will. ' : 'Why: '}
      </strong>
      {row.note}
      {row.pairings && (
        <div style={{ marginTop: 6 }}>
          {row.pairings.filter((p) => !p.breedsTrue).map((p, i) => (
            <div key={i} className="mono" style={{ fontSize: 11 }}>
              {p.aName} + {p.bName}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * "Computed from N Pals — <whose>", in words a player can act on.
 *
 * `scope` is a machine value (`own`, `server`, `player:<uid>`) and is deliberately
 * not shown raw: a uid tells the reader nothing, and the point of the line is to
 * stop a correctly narrow answer from reading as a wrong one.
 *
 * The shared count is named for the opposite reason. The total legitimately
 * exceeds what is in the palbox — base workers and Pals in a shared store count,
 * because anyone in the guild can take one out and breed it — and an unexplained
 * larger number reads as a miscount rather than as a fuller answer.
 */

/**
 * Where a parent actually is, when it is not in the palbox.
 *
 * A breeding plan is a set of instructions. The parents it names are counted
 * from everything a player can breed with, which correctly includes Pals working
 * at a base and Pals in a guild's shared store — anyone in the guild can take one
 * out. But "pair your two Lamballs" is a bad instruction if one of them is
 * standing in a base three valleys away, and a player looking at their palbox and
 * not finding it there reasonably concludes the planner is wrong about what they
 * own.
 *
 * So: silent when every copy is in the palbox, which is the ordinary case, and
 * explicit the moment it is not. Named by the structure where the game gives one
 * ("Dimensional Pal Storage") rather than by the word `storage`, which does not
 * tell anyone where to walk.
 */
function WhereNote({ species, palbox }: { species?: string; palbox: PalboxSummary | null }) {
  if (!species || !palbox) return null;
  const entry = palbox.species.find((s) => s.internalName === species);
  const locations = entry?.locations;
  if (!locations) return null;

  const elsewhere = Object.entries(locations).filter(([where]) => where !== 'palbox');
  if (elsewhere.length === 0) return null;

  const label = elsewhere
    .map(([where, n]) => `${n} ${where === 'base' ? 'at a base' : `in ${where}`}`)
    .join(', ');

  return (
    <span style={{ fontSize: 11, color: 'var(--text-muted)' }} title="Breedable, but not in your palbox">
      ({label})
    </span>
  );
}

/**
 * The shared count is named for the opposite reason. The total legitimately
 * exceeds what is in the palbox — base workers and Pals in a shared store count,
 * because anyone in the guild can take one out — and an unexplained larger number
 * reads as a miscount rather than as a fuller answer.
 */
function scopeLabel(scope: BreedingScope): string {
  const n = scope.pals;
  const count = n === undefined ? '' : `${n} Pal${n === 1 ? '' : 's'} — `;
  // "only" carries the scope restriction and must survive when nothing is
  // shared; when something is, naming it replaces the restriction rather than
  // stacking on top of it.
  const shared = scope.shared ? `, ${scope.shared} of them your guild's` : ' only';
  if (scope.scope === 'server') return `${count}everyone on this server`;
  if (scope.scope?.startsWith('player:')) return `${count}one player's box${shared}`;
  return `${count}your own Pals${shared}`;
}

function Stat({ label, value, hint }: { label: string; value: number; hint?: string }) {
  return (
    <div className="stat-card" title={hint}>
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={{ marginTop: 6 }}>{value}</div>
    </div>
  );
}
