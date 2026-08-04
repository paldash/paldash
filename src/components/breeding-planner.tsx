'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Egg, Search, RefreshCw, ArrowRight } from 'lucide-react';
import { getBreedingPath, getOffspring, getPalbox, getReachable } from '@/lib/save-api';
import type {
  BreedingPath, BreedingScope, OffspringOption, PalboxSummary, ReachableTargets,
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

  const findPath = async (internalName: string) => {
    setTarget(internalName);
    setPath(null);
    try {
      setPath(await getBreedingPath(internalName, owner || undefined));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Path search failed');
    }
  };

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
            <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>{path.reason}</p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {path.steps.map((step, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
                  <span style={{ color: 'var(--text-muted)', width: 20 }}>{i + 1}.</span>
                  <GameIcon src={step.parentA.icon} size={20} />
                  <span>{step.parentA.name}</span>
                  <span style={{ color: 'var(--text-muted)' }}>+</span>
                  <GameIcon src={step.parentB.icon} size={20} />
                  <span>{step.parentB.name}</span>
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
    </div>
  );
}

/**
 * "Computed from N Pals — <whose>", in words a player can act on.
 *
 * `scope` is a machine value (`own`, `server`, `player:<uid>`) and is deliberately
 * not shown raw: a uid tells the reader nothing, and the point of the line is to
 * stop a correctly narrow answer from reading as a wrong one.
 */
function scopeLabel(scope: BreedingScope): string {
  const n = scope.pals;
  const count = n === undefined ? '' : `${n} Pal${n === 1 ? '' : 's'} — `;
  if (scope.scope === 'server') return `${count}everyone on this server`;
  if (scope.scope?.startsWith('player:')) return `${count}one player's box`;
  return `${count}your own Pals only`;
}

function Stat({ label, value, hint }: { label: string; value: number; hint?: string }) {
  return (
    <div className="stat-card" title={hint}>
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={{ marginTop: 6 }}>{value}</div>
    </div>
  );
}
