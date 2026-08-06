'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  Trophy, RefreshCw, MapPin, Sparkles, Compass, Swords, EyeOff, Info,
} from 'lucide-react';
import {
  getProgress, getProgressDetail, type PlayerProgress, type RelicLine,
} from '@/lib/save-api';
import type { Checklist, ChecklistEntry, ProgressDetailReport } from '@/lib/types';

/**
 * How far through the game each player is, and *what is left* by name.
 *
 * `/api/progress` has counted these categories since Phase 4 and nothing has
 * ever rendered it — the relic statue lines shipped backend-only. This is that
 * tab, plus the checklists, which are the part that makes a count actionable:
 * "92 of 123 regions" tells you where you are, the list of 31 tells you where
 * to go.
 *
 * TWO THINGS IT IS CAREFUL ABOUT
 *
 * **A denominator's source travels with it.** Some totals are the game's own
 * (174 fast-travel points, 123 regions), some are published community figures,
 * and some are just the union of what players here have found — which is a
 * floor, and rises as people explore. A progress bar that mixes those without
 * saying so invents precision.
 *
 * **The unfound half may legitimately be missing.** `discoveryVisibility` is an
 * operator setting and the backend drops that half server-side, so
 * `missingHidden` means "you are not allowed to see this", not "there is
 * nothing left". Rendering an empty list for it would be a lie in the more
 * discouraging direction.
 */
export default function Progression() {
  const [summary, setSummary] = useState<PlayerProgress[] | null>(null);
  const [totals, setTotals] = useState<Record<string, { total: number; source: string }>>({});
  const [detail, setDetail] = useState<ProgressDetailReport | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [who, setWho] = useState<string>('');

  const load = useCallback(async () => {
    setLoading(true);
    // Settled independently: the counts and the checklists come from different
    // endpoints, and one failing must not blank the other. A Promise.all with a
    // catch returning [] is how the base markers vanished from the map.
    const [a, b] = await Promise.allSettled([getProgress(), getProgressDetail()]);
    const problems: string[] = [];
    if (a.status === 'fulfilled') {
      setSummary(a.value.players);
      setTotals(a.value.knownTotals || {});
    } else {
      problems.push(`Progress counts: ${String(a.reason)}`);
    }
    if (b.status === 'fulfilled') setDetail(b.value);
    else problems.push(`Checklists: ${String(b.reason)}`);
    setErrors(problems);
    setLoading(false);
  }, []);

  useEffect(() => { void load(); }, [load]);

  const players = summary ?? [];
  const selected = who || players[0]?.uid || '';
  const player = players.find((p) => p.uid === selected);
  const lists = detail?.players.find((p) => p.uid === selected);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <h3 style={{ display: 'flex', alignItems: 'center', gap: 7, margin: 0, fontSize: 15, color: 'var(--text-primary)' }}>
          <Trophy size={16} /> Progression
        </h3>
        <div style={{ flex: 1 }} />
        {players.length > 1 && (
          <select className="select" style={{ width: 200 }} value={selected} onChange={(e) => setWho(e.target.value)}>
            {players.map((p) => (
              <option key={p.uid} value={p.uid}>{p.name} · Lv {p.level}</option>
            ))}
          </select>
        )}
        <button className="btn btn-ghost" onClick={() => void load()} disabled={loading}>
          <RefreshCw size={13} /> Reload
        </button>
      </div>

      {errors.map((e) => (
        <div key={e} className="notice notice-warn" style={{ fontSize: 12 }}>{e}</div>
      ))}

      {!loading && !players.length && !errors.length && (
        <div className="notice" style={{ fontSize: 12 }}>
          No parsed progress yet. Press Refresh in the header, or sign in with an
          account linked to a character.
        </div>
      )}

      {player && <Counts player={player} totals={totals} />}
      {player && <Relics lines={player.relicLines || []} />}

      {detail && !detail.showsMissing && (
        <div className="notice" style={{ fontSize: 12, display: 'flex', gap: 7, alignItems: 'flex-start' }}>
          <EyeOff size={13} style={{ flexShrink: 0, marginTop: 2 }} />
          <span>
            This server hides undiscovered locations. You can see what you have
            found; the rest is filtered out before it reaches your browser.
          </span>
        </div>
      )}

      {lists && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 10 }}>
          <ChecklistCard
            icon={<Swords size={14} />}
            title="Tower and major bosses"
            list={lists.towerBosses}
            note="Eight towers, three World Tree roots and two encounters the game does not name yet."
          />
          <ChecklistCard
            icon={<Swords size={14} />}
            title="Field bosses (Pals)"
            list={lists.fieldBosses.pals}
            note="Placed alpha spawners, with the level each one is."
            label={(e) => (e.level && e.levelMax && e.level !== e.levelMax
              ? `${e.name} · Lv ${e.level}-${e.levelMax}`
              : `${e.name}${e.level ? ` · Lv ${e.level}` : ''}`)}
          />
          <HumanBosses bosses={lists.fieldBosses.humans} />
          <ChecklistCard
            icon={<Compass size={14} />}
            title="Regions discovered"
            list={lists.areasFound}
            note="From the game's own world-map area table."
          />
          <ChecklistCard
            icon={<MapPin size={14} />}
            title="Fast travel"
            list={lists.fastTravel}
            label={(e) => (e.kind && e.kind !== 'travel' ? `${e.name} (${e.kind})` : e.name)}
          />
          <ChecklistCard
            icon={<Sparkles size={14} />}
            title="Effigies"
            list={lists.effigies}
            note="Positions only — a relic has no name of its own. The map draws them."
          />
          <Unavailable detail={lists.dungeonsCleared} />
        </div>
      )}
    </div>
  );
}

function Counts({
  player,
  totals,
}: {
  player: PlayerProgress;
  totals: Record<string, { total: number; source: string }>;
}) {
  const rows: [string, string][] = [
    ['towerBosses', 'Tower bosses'],
    ['fieldBosses', 'Field bosses'],
    ['fastTravel', 'Fast travel'],
    ['paldeck', 'Paldeck'],
    ['effigies', 'Effigies'],
    ['areasFound', 'Regions'],
  ];
  return (
    <div className="dashboard-grid grid-3">
      {rows.map(([key, label]) => {
        const entry = player[key] as { obtained: number; of: number; source: string } | undefined;
        if (!entry || typeof entry.obtained !== 'number') return null;
        const source = entry.source || totals[key]?.source;
        return (
          <div key={key} className="stat-card">
            <div className="stat-label">{label}</div>
            <div className="stat-value" style={{ marginTop: 6 }}>
              {entry.obtained}
              <span style={{ fontSize: 13, color: 'var(--text-muted)' }}> / {entry.of}</span>
            </div>
            {/* WHERE THE DENOMINATOR CAME FROM, always. 'discovered' is the
                union of what players here have found — a floor that rises as
                people explore, not the game's true total. */}
            <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 3 }}>
              {source === 'gamedata'
                ? "the game's own count"
                : source === 'reference'
                  ? 'published 1.0 figure'
                  : 'found so far on this server — a floor, not a total'}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Relics({ lines }: { lines: RelicLine[] }) {
  if (!lines.length) return null;
  return (
    <div className="glass-card" style={{ padding: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 8 }}>
        <Sparkles size={14} /> What your effigies bought
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 8 }}>
        {lines.map((line) => (
          <div key={line.type} style={{ fontSize: 12 }} title={line.description}>
            <div style={{ color: 'var(--text-primary)' }}>
              {line.name}
              {line.nameIsInternal && (
                <span style={{ color: 'var(--text-muted)' }}> (internal id)</span>
              )}
            </div>
            <div style={{ color: 'var(--text-muted)' }}>
              Rank {line.rank}
              {/* CapturePower carries 0.0 on all 15 ranks — its effect lives
                  somewhere other than that column, so "+0%" would be a
                  confident wrong number rather than a missing one. */}
              {line.hasEffectRate ? ` · +${line.effectRate}%` : ''}
              {' · '}{line.spent} spent
              {line.nextCost != null ? ` · next costs ${line.nextCost}` : ' · maxed'}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ChecklistCard({
  icon,
  title,
  list,
  note,
  label = (e: ChecklistEntry) => e.name,
}: {
  icon: React.ReactNode;
  title: string;
  list: Checklist;
  note?: string;
  label?: (entry: ChecklistEntry) => string;
}) {
  const [showAll, setShowAll] = useState(false);
  const missing = list.missing ?? [];
  const shown = showAll ? missing : missing.slice(0, 12);

  return (
    <div className="glass-card" style={{ padding: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>
        {icon} <span>{title}</span>
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          {list.obtained}/{list.of}
        </span>
      </div>

      {note && (
        <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '6px 0 0' }}>{note}</p>
      )}

      {list.unlisted.length > 0 && (
        <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '6px 0 0' }}>
          {list.unlisted.length} counted but not in the bundled data — done, just
          not nameable here.
        </p>
      )}

      {/* "We are not allowed to tell you" and "there is nothing left" must not
          look the same. The backend dropped this half; it is not merely
          unrendered. */}
      {list.missingHidden ? (
        <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '8px 0 0' }}>
          The remaining {Math.max(0, list.of - list.obtained)} are hidden by this
          server&rsquo;s settings.
        </p>
      ) : missing.length === 0 ? (
        <p style={{ fontSize: 12, color: 'var(--status-online)', margin: '8px 0 0' }}>
          Complete.
        </p>
      ) : (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
            Still to find:
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
            {shown.map((entry) => (
              <span
                key={entry.id}
                className="badge"
                title={entry.nameHidden ? 'The game does not name this one yet' : entry.id}
              >
                {entry.nameHidden ? 'Not named yet' : label(entry)}
              </span>
            ))}
          </div>
          {missing.length > shown.length && (
            <button
              className="btn btn-ghost"
              style={{ marginTop: 6, fontSize: 11 }}
              onClick={() => setShowAll(true)}
            >
              Show all {missing.length}
            </button>
          )}
          {list.truncated && (
            <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>
              This list is capped — see the map for the rest.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function HumanBosses({
  bosses,
}: {
  bosses: { have: { id: string; name: string }[]; obtained: number; of: null };
}) {
  return (
    <div className="glass-card" style={{ padding: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>
        <Swords size={14} /> Field bosses (human)
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          {bosses.obtained}
        </span>
      </div>
      {/* NO DENOMINATOR, ON PURPOSE. The only enumeration available is the
          catalogue's 34 BOSS_ NPCs, and that list includes a merchant and a
          quest NPC — so "of 34" would be a confident wrong number. */}
      <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '6px 0 8px' }}>
        Defeated so far. No game file lists which human bosses exist, so there is
        no total to count towards.
      </p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
        {bosses.have.map((b) => (
          <span key={b.id} className="badge" title={b.id}>{b.name}</span>
        ))}
        {!bosses.have.length && (
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>None yet.</span>
        )}
      </div>
    </div>
  );
}

function Unavailable({ detail }: { detail: { available: boolean; reason: string } }) {
  if (detail.available) return null;
  return (
    <div className="glass-card" style={{ padding: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>
        <Info size={14} /> Dungeons
      </div>
      {/* Said out loud rather than shown as "0 of 23". An empty checklist reads
          as "you have cleared none"; this is "we cannot tell". */}
      <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '6px 0 0' }}>
        No checklist available. {detail.reason}
      </p>
    </div>
  );
}
