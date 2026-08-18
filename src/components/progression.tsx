'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  Trophy, RefreshCw, MapPin, Sparkles, Compass, Swords, EyeOff, Info,
  BookMarked, Gift, Lock, CircleHelp,
} from 'lucide-react';
import {
  getProgress, getProgressDetail, getRaidBosses, getPaldeckCompletion,
  type PlayerProgress, type RelicLine,
} from '@/lib/save-api';
import GameIcon from '@/components/game-icon';
import { asArray } from '@/lib/arrays';
import BossPlanner from '@/components/boss-planner';
import DungeonGuide from '@/components/dungeon-guide';
import type {
  Checklist, ChecklistEntry, ProgressDetailReport, RaidBossReport,
  PaldeckCompletion, AchievementSummary, AchievementCategory, AchievementTier,
} from '@/lib/types';
import { t, tl } from '@/lib/chrome';

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
  const [raids, setRaids] = useState<RaidBossReport | null>(null);
  const [dex, setDex] = useState<PaldeckCompletion[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    // Settled independently: the counts and the checklists come from different
    // endpoints, and one failing must not blank the other. A Promise.all with a
    // catch returning [] is how the base markers vanished from the map.
    const [a, b, c, d] = await Promise.allSettled([
      getProgress(), getProgressDetail(), getRaidBosses(), getPaldeckCompletion(),
    ]);
    const problems: string[] = [];
    if (a.status === 'fulfilled') {
      setSummary(a.value.players);
      setTotals(a.value.knownTotals || {});
    } else {
      problems.push(`Progress counts: ${String(a.reason)}`);
    }
    if (b.status === 'fulfilled') setDetail(b.value);
    else problems.push(`Checklists: ${String(b.reason)}`);
    // Reference data rather than progress, so a failure here is worth saying
    // but must not blank the rest of the tab.
    if (c.status === 'fulfilled') setRaids(c.value);
    else problems.push(`Raid bosses: ${String(c.reason)}`);
    if (d.status === 'fulfilled') setDex(d.value.players);
    else problems.push(`Paldeck: ${String(d.reason)}`);
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
      {player && <RecordCounters player={player} />}
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
            title={t('Tower and major bosses')}
            list={lists.towerBosses}
            note="Eight towers, three World Tree roots and two encounters the game does not name yet."
          />
          <ChecklistCard
            icon={<Swords size={14} />}
            title={t('Field bosses (Pals)')}
            list={lists.fieldBosses.pals}
            note="Placed alpha spawners, with the level each one is."
            label={(e) => (e.level && e.levelMax && e.level !== e.levelMax
              ? `${e.name} · Lv ${e.level}-${e.levelMax}`
              : `${e.name}${e.level ? ` · Lv ${e.level}` : ''}`)}
          />
          <HumanBosses bosses={lists.fieldBosses.humans} />
          <ChecklistCard
            icon={<Compass size={14} />}
            title={t('Regions discovered')}
            list={lists.areasFound}
            note="From the game's own world-map area table."
          />
          <ChecklistCard
            icon={<MapPin size={14} />}
            title={t('Fast travel')}
            list={lists.fastTravel}
            label={(e) => (e.kind && e.kind !== 'travel' ? `${e.name} (${e.kind})` : e.name)}
          />
          <ChecklistCard
            icon={<Sparkles size={14} />}
            title={t('Effigies')}
            list={lists.effigies}
            note="Positions only — a relic has no name of its own. The map draws them."
          />
          {/* "Show me this Pal" requests. A real checklist — the save records
              each RequestID — so it belongs beside the others rather than in a
              reference tab. The species is what a player recognises; the raw
              `Area_F1_1` is the join key and never the label. */}
          <ChecklistCard
            icon={<Sparkles size={14} />}
            title={t('Pal requests')}
            list={lists.palDisplay}
            label={(e) => (e.area ? `${e.name} — ${e.area.replace('Area_', '')}` : e.name)}
            note="NPCs who want to be shown a particular Pal."
          />
          <Unavailable detail={lists.dungeonsCleared} />
        </div>
      )}

      {/* The game's own milestone NPC. NOT Steam achievements — those live on
          Steam's servers behind an API this project cannot depend on, and are
          per-account rather than per-server. The caption says so, because a
          panel labelled "Achievements" would be read as Steam's. */}
      <MilestoneCard achievements={lists?.achievements} />

      <PaldeckCompletionCard entries={dex} who={who} />

      {/* Reference data rather than this player's progress, so it sits below
          the checklists and does not depend on a parsed world. */}
      <BossPlanner />
      <DungeonGuide />

      {raids && <RaidBosses report={raids} defeated={player} />}
    </div>
  );
}


/**
 * Which Pals this player still needs, and how to get each one.
 *
 * **The denominator is Paldeck entries (204), never species forms (753)**, and
 * it comes off the payload rather than being counted here — `HadesBird` and
 * `HadesBird_Electric` are one Helzephyr, and counting forms puts 100%
 * permanently out of reach.
 *
 * Missing-first, because that is the list somebody came for. A route is shown
 * only for what you have not caught: telling you where to find a Pal you own is
 * noise, and it is also the half `discoveryVisibility` can remove.
 */
function PaldeckCompletionCard({ entries, who }: {
  entries: PaldeckCompletion[];
  who: string;
}) {
  const [showCaught, setShowCaught] = useState(false);
  const report = entries.find((e) => e.uid === who) ?? entries[0];
  const rows = asArray(report?.entries, 'paldeck completion entries');
  if (!report) return null;

  const shown = showCaught ? rows : rows.filter((r) => !r.caught);

  return (
    <div className="glass-card" style={{ padding: 14, marginTop: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
                    marginBottom: 8 }}>
        <h3 style={{ display: 'flex', alignItems: 'center', gap: 6, margin: 0,
                     fontSize: 14 }}>
          <BookMarked size={15} /> Paldeck
        </h3>
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          {/* NOT a percentage of 753. `denominator` says which it is. */}
          {report.caught} of {report.total} caught · {report.missing} to go
        </span>
        <div style={{ flex: 1 }} />
        {rows.some((r) => r.caught) && (
          <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
            <input type="checkbox" checked={showCaught}
                   onChange={(e) => setShowCaught(e.target.checked)} />
            {t('Show caught')}
          </label>
        )}
      </div>

      {/* No linked character means every row reads uncaught. That is not a
          score of zero — it is no score at all, and saying so beats a panel
          that looks like a fresh save. */}
      {!report.linked && (
        <div className="notice" style={{ fontSize: 11, marginBottom: 8 }}>
          This account is not linked to a character, so nothing here counts as
          caught yet.
        </div>
      )}
      {report.missingHidden && (
        <div className="notice" style={{ fontSize: 11, marginBottom: 8 }}>
          The server hides Pals another player has not caught.
        </div>
      )}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {shown.map((row) => (
          <span
            key={row.id}
            title={routeLabel(row)}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 4,
              border: '1px solid var(--border-primary)', borderRadius: 5,
              padding: '2px 6px', fontSize: 11,
              opacity: row.caught ? 0.5 : 1,
            }}
          >
            <GameIcon src={row.icon} size={18} />
            <span style={{ color: 'var(--text-primary)' }}>{row.name}</span>
            {!row.caught && (
              <span style={{ color: 'var(--text-muted)' }}>{routeShort(row)}</span>
            )}
          </span>
        ))}
      </div>

      {shown.length === 0 && (
        <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: 0 }}>
          Every Paldeck entry caught.
        </p>
      )}
    </div>
  );
}

/** A word, for the chip. `?` is a real answer — see `routeLabel`. */
function routeShort(row: PaldeckCompletion['entries'][number]): string {
  if (row.route?.catch) return 'catch';
  if (row.route?.breed && row.route.breed.kind !== 'never') return 'breed';
  return '?';
}

/**
 * The full explanation, on hover.
 *
 * **"Unknown" is stated rather than smoothed over.** A Pal with no world
 * spawner and no pairing is a raid boss or a quest form, and "go and catch it"
 * about Bellanoir would be wrong in a way that wastes an evening.
 */
function routeLabel(row: PaldeckCompletion['entries'][number]): string {
  if (row.caught) return `${row.name} — caught`;
  const parts: string[] = [];
  if (row.route?.catch) {
    parts.push(`spawns in ${row.route.catch.cells} places`);
  }
  const breed = row.route?.breed;
  if (breed && breed.kind === 'named_pairing') {
    parts.push('the game names an exact pairing for this');
  } else if (breed && breed.kind === 'standard') {
    parts.push('breedable');
  } else if (breed && breed.kind === 'unverified') {
    parts.push('breeding unverified — no game column settles it');
  } else if (breed && breed.kind === 'never') {
    parts.push(breed.breedsTrue
      ? 'no pairing produces this; two of them breed true'
      : 'no pairing produces this');
  }
  if (!parts.length) {
    parts.push('no world spawner and no pairing — a raid or quest form');
  }
  return `${row.name} — ${parts.join('; ')}`;
}

function Counts({
  player,
  totals,
}: {
  player: PlayerProgress;
  totals: Record<string, { total: number; source: string }>;
}) {
  const rows: [string, string][] = [
    ['towerBosses', tl('Tower bosses')],
    ['fieldBosses', tl('Field bosses')],
    ['fastTravel', tl('Fast travel')],
    ['paldeck', tl('Paldeck')],
    ['effigies', tl('Effigies')],
    ['areasFound', tl('Regions')],
  ];
  return (
    <div className="dashboard-grid grid-3">
      {rows.map(([key, label]) => {
        const entry = player[key] as { obtained: number; of: number; source: string } | undefined;
        if (!entry || typeof entry.obtained !== 'number') return null;
        const source = entry.source || totals[key]?.source;
        return (
          <div key={key} className="stat-card">
            <div className="stat-label">{t(label)}</div>
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

/**
 * The lifetime counters the save keeps under `RecordData` (#138) — counts,
 * never "n of N": nothing enumerates how many camps or conversations exist, so
 * a denominator would be invented.
 *
 * **Only counters the save actually carries render.** The game writes a
 * counter the first time it has something to count, so absent and zero are
 * different facts — the parser drops absent keys and this renders nothing for
 * them rather than a 0 (the trap that once "refuted" the boss counters).
 * `raidBossesDefeated` is deliberately not here; the Raid bosses card below
 * already shows it beside the reference list.
 */
const RECORD_COUNTERS: {
  key: string;
  label: string;
  /** What one entry in the map IS, where that is known — else distinct is hidden. */
  distinctLabel?: string;
  title?: string;
}[] = [
  { key: 'itemsCrafted', label: tl('Items crafted'), distinctLabel: tl('kinds') },
  {
    key: 'palRankUps', label: tl('Condenser rank-ups'),
    title: 'The save counts rank-ups per rank reached, so a per-species breakdown does not exist.',
  },
  {
    key: 'mutations', label: tl('Mutations'),
    title: 'The counter is named MutationCount and no file states what one unit is.',
  },
  { key: 'towerBossDefeats', label: tl('Tower boss defeats'), distinctLabel: tl('bosses') },
  { key: 'campsConquered', label: tl('Camps conquered') },
  { key: 'oilrigsCleared', label: tl('Oil rigs cleared') },
  { key: 'npcTalks', label: tl('NPC conversations'), distinctLabel: tl('NPCs') },
];

function RecordCounters({ player }: { player: PlayerProgress }) {
  const rows = RECORD_COUNTERS
    .map((c) => ({ ...c, entry: player[c.key] as { total: number; distinct: number | null } | undefined }))
    .filter((c) => c.entry && typeof c.entry.total === 'number');
  if (!rows.length) return null;
  return (
    <div className="glass-card" style={{ padding: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 8 }}>
        <Info size={14} /> {t('Lifetime counters')}
        <span style={{ fontSize: 10, fontWeight: 400, color: 'var(--text-muted)' }}>
          {t('what the save counts — nothing states a total to compare against')}
        </span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 18px' }}>
        {rows.map((c) => (
          <div key={c.key} style={{ fontSize: 12 }} title={c.title}>
            <span style={{ color: 'var(--text-muted)' }}>{t(c.label)} </span>
            <span className="mono" style={{ color: 'var(--text-primary)' }}>
              {c.entry!.total.toLocaleString()}
            </span>
            {c.distinctLabel && c.entry!.distinct != null && (
              <span style={{ color: 'var(--text-muted)' }}>
                {' '}· {c.entry!.distinct.toLocaleString()} {t(c.distinctLabel)}
              </span>
            )}
          </div>
        ))}
      </div>
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
              {/* Maxed is nextRank === null, from the payload. This used to
                  test a `nextCost` field the backend never emitted, so every
                  line — rank 0 included — read "maxed". */}
              {line.nextRank != null ? ` · next costs ${line.relicsToNext}` : ' · maxed'}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Human labels for the game's three milestone categories. */
const MILESTONE_LABEL: Record<string, string> = {
  PalCapture: tl('Pals captured'),
  PalDex: tl('Paldeck species'),
  BossDefeat: tl('Bosses defeated'),
};

const TIER_STYLE: Record<AchievementTier['state'], { color: string; label: string }> = {
  claimed: { color: 'var(--accent-green)', label: 'Collected' },
  unclaimed: { color: 'var(--accent-amber)', label: 'Ready to collect' },
  locked: { color: 'var(--text-muted)', label: 'Not yet reached' },
  unknown: { color: 'var(--text-muted)', label: 'Progress not known' },
};

function MilestoneRow({ name, category }: { name: string; category: AchievementCategory }) {
  const label = t(MILESTONE_LABEL[name] ?? name);
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <strong style={{ fontSize: 12 }}>{label}</strong>
        {/* `hasProgress` false means no save counter is established for this
            category — BossDefeat. A bar at 0% would be a claim about a number
            the backend explicitly says it does not have. */}
        {category.hasProgress ? (
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            {category.value?.toLocaleString()} so far
          </span>
        ) : (
          <span
            style={{ fontSize: 11, color: 'var(--text-muted)', display: 'inline-flex', gap: 4 }}
            title="No counter in the save is established for this category, so how far along you are cannot be shown. Collected rewards are still exact — the save names them."
          >
            <CircleHelp size={11} style={{ alignSelf: 'center' }} />
            progress not tracked
          </span>
        )}
        <span style={{ fontSize: 11, marginLeft: 'auto', color: 'var(--text-muted)' }}>
          {category.claimed}/{category.total} collected
          {category.unclaimed > 0 && (
            <span style={{ color: 'var(--accent-amber)' }}>
              {' '}· {category.unclaimed} ready
            </span>
          )}
        </span>
      </div>

      <div style={{ display: 'flex', gap: 3, marginTop: 4, flexWrap: 'wrap' }}>
        {category.tiers.map((tier) => {
          const style = TIER_STYLE[tier.state] ?? TIER_STYLE.unknown;
          const reward = tier.rewards
            .map((r) => (r.count > 1 ? `${r.itemId} x${r.count}` : r.itemId))
            .join(', ');
          return (
            <span
              key={tier.id}
              title={`${tier.requireCount.toLocaleString()} needed — ${style.label}${reward ? ` · reward: ${reward}` : ''}`}
              style={{
                fontSize: 10,
                padding: '1px 5px',
                borderRadius: 3,
                border: `1px solid ${style.color}`,
                color: style.color,
                opacity: tier.state === 'locked' || tier.state === 'unknown' ? 0.55 : 1,
              }}
            >
              {tier.requireCount.toLocaleString()}
            </span>
          );
        })}
      </div>
    </div>
  );
}

function MilestoneCard({ achievements }: { achievements?: AchievementSummary }) {
  if (!achievements || !Object.keys(achievements.categories ?? {}).length) return null;
  const ready = achievements.unclaimed;
  return (
    <div className="card" style={{ marginTop: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
        <Gift size={14} style={{ color: 'var(--accent-blue)' }} />
        <strong style={{ fontSize: 13 }}>{t('Milestone rewards')}</strong>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-muted)' }}>
          {achievements.claimed}/{achievements.total} collected
        </span>
      </div>

      {/* Stated, not implied. The backend sends `isSteam: false` precisely so
          this caption cannot drift into calling them Steam achievements. */}
      <p style={{ fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.6, margin: '0 0 8px' }}>
        The in-game NPC who hands out rewards for capture and boss milestones.
        These are <strong>not Steam achievements</strong> — those live on Steam&rsquo;s
        servers and are per-account; these are read from each player&rsquo;s own save
        and work offline.
      </p>

      {ready > 0 && (
        <div className="notice" style={{ fontSize: 12, marginBottom: 8 }}>
          <Lock size={12} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
          {ready} reward{ready === 1 ? '' : 's'} earned and not collected — they
          are waiting with the NPC.
        </div>
      )}

      {Object.entries(achievements.categories).map(([name, category]) => (
        <MilestoneRow key={name} name={name} category={category} />
      ))}
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
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{t('None yet.')}</span>
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

/**
 * The altar-summoned bosses: what summons each, at what level, what it drops.
 *
 * **Not on the map, and the panel says why.** `DT_BossSpawnerLoactionData` holds
 * zero `RAID_` ids, which is correct rather than a gap — a raid boss is summoned
 * at an altar, so a table of locations has nothing to say about it. Giving one a
 * marker would be inventing a position.
 *
 * It sits here rather than beside the field bosses because the save records
 * `RaidBossDefeatCount`, so there is a real per-player number to show against
 * the reference list — which is the only thing that makes it progression rather
 * than a wiki page.
 */
function RaidBosses({
  report,
  defeated,
}: {
  report: RaidBossReport;
  defeated?: PlayerProgress;
}) {
  const [open, setOpen] = useState(false);
  const count = (defeated?.raidBossesDefeated as { total: number } | undefined)?.total;

  return (
    <div className="glass-card" style={{ padding: 12 }}>
      <button
        onClick={() => setOpen(!open)}
        style={{
          display: 'flex', alignItems: 'center', gap: 7, width: '100%',
          background: 'none', border: 'none', color: 'var(--text-primary)',
          padding: 0, fontSize: 13, fontWeight: 600, cursor: 'pointer', textAlign: 'left',
        }}
      >
        <Swords size={14} /> {t('Raid bosses')}
        <span style={{ flex: 1 }} />
        {/* A count, never "n of 11": the save counts DEFEATS, not which ones,
            so a denominator would imply a checklist the data cannot back. */}
        {count != null && (
          <span className="mono" style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            {count} defeated
          </span>
        )}
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{open ? 'Hide' : 'Show'}</span>
      </button>

      <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '6px 0 0' }}>
        {report.positionNote}
      </p>

      {open && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 10 }}>
          {report.bosses.map((boss) => (
            <div
              key={boss.summonItemId}
              style={{
                border: '1px solid var(--border-primary)', borderRadius: 6,
                padding: 8, fontSize: 12,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                <GameIcon src={boss.summonItemIcon} title={boss.summonItemName} />
                <strong style={{ color: 'var(--text-primary)' }}>
                  {boss.forms.map((f) => f.name).join(' / ')}
                </strong>
                {boss.forms.map((f) => (
                  <span key={f.speciesId} className="badge">Lv {f.level}</span>
                ))}
              </div>
              <div style={{ color: 'var(--text-muted)', marginTop: 4 }}>
                Summoned with <strong>{boss.summonItemName}</strong>
              </div>
              {boss.forms.some((f) => f.nameIsInternal) && (
                <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 3 }}>
                  {/* The `_2` difficulty variants have no character-table entry, so
                      their species name is humanised. The summon item IS named
                      properly, which is why it leads above. */}
                  The game names the harder variant only through its summon item.
                </div>
              )}
              {boss.rewards.length > 0 && (
                <div style={{ marginTop: 6 }}>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{t('Always drops:')}</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 3 }}>
                    {boss.rewards.map((r) => (
                      <span key={r.itemId} className="badge" title={`${r.rate}%`}>
                        <GameIcon src={r.icon} size={14} />
                        {r.name}{' '}
                        <span className="mono" style={{ color: 'var(--text-muted)' }}>
                          ×{r.min === r.max ? r.min : `${r.min}-${r.max}`}
                        </span>
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {boss.rewardsAnyOne.length > 0 && (
                <div style={{ marginTop: 6 }}>
                  {/* The game's own distinction — SuccessAnyOneItemList is ONE of
                      these. Folding the two lists together would overstate what a
                      clear gives you. */}
                  <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    Plus one of:
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 3 }}>
                    {boss.rewardsAnyOne.map((r) => (
                      <span key={r.itemId} className="badge">
                        <GameIcon src={r.icon} size={14} />
                        {r.name}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {!boss.eggWeightsRead && (
                <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>
                  {/* Said rather than shown as an empty list: EggPalIDAndWeight is
                      a MapProperty the table reader does not decode, so "no eggs"
                      would be a claim about the game instead of about the reader. */}
                  Egg rewards are not readable from the game files.
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
