'use client';

import { useCallback, useEffect, useState } from 'react';
import { Hammer, Swords, RefreshCw, AlertTriangle, Info } from 'lucide-react';
import {
  getWorkRanking, getCombatRanking,
  type WorkRankingReport, type CombatRankingReport,
  type WorkRankedPal, type CombatRankedPal,
} from '@/lib/save-api';
import GameIcon from '@/components/game-icon';

/**
 * Who should be doing what.
 *
 * WORK is read, COMBAT is calculated, and the panel says which is which. Work
 * suitability levels come out of the save and the bundled species table; work
 * speed, attack, defense and HP are `palstats` running the game's formula, and
 * both the API and this UI label them so a derived number never inherits the
 * authority of a stored one.
 *
 * THERE IS NO DAMAGE MULTIPLIER AND THIS PANEL MUST NOT IMPLY ONE. The element
 * chart in `backend/elements.py` is a hand-entered *relation* — the game's own
 * settings object holds exactly one element-damage constant
 * (`DamageElementMatchRate = 1.2`, meaning inferred from its name) and no
 * halving counterpart, so the widely repeated "2x dealt, half taken" is
 * reproduced by no file this project can read.
 *
 * So the matchup is a **badge, never a sort key**. The ranking is identical with
 * and without a target selected, which is asserted on both sides of the wire
 * (`test_matchup_never_enters_the_ordering`, `test_a_matchup_does_not_reorder_the_ranking`).
 * Rendering "2x" here would be the whole quarantine leaking out through the UI.
 *
 * `chartIsCurrent` is surfaced because a game update adding a tenth element
 * makes every matchup involving it read as a confident "neutral" rather than as
 * a gap. Empty is the healthy state, so nothing is shown until it is not.
 */

const MATCHUP_STYLE: Record<string, { label: string; colour: string }> = {
  strong: { label: 'Strong', colour: 'var(--accent-emerald)' },
  weak: { label: 'Weak', colour: 'var(--accent-red)' },
  neutral: { label: 'Neutral', colour: 'var(--text-muted)' },
};

function Row({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0',
      borderBottom: '1px solid var(--border-primary)', fontSize: 12,
    }}>
      {children}
    </div>
  );
}

function PalCell({ pal }: { pal: { icon: string; name: string; level: number; rank: number; isBoss: boolean } }) {
  return (
    <>
      <GameIcon src={pal.icon} size={20} />
      <span style={{ color: 'var(--text-primary)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {pal.name}
        {pal.isBoss && <span style={{ color: 'var(--accent-amber)', marginLeft: 4 }}>α</span>}
        {pal.rank > 1 && (
          <span style={{ color: 'var(--accent-amber)', marginLeft: 4 }}>
            {'★'.repeat(pal.rank - 1)}
          </span>
        )}
      </span>
      <span className="mono" style={{ color: 'var(--text-muted)' }}>Lv {pal.level}</span>
    </>
  );
}

function WorkPanel() {
  const [report, setReport] = useState<WorkRankingReport | null>(null);
  const [work, setWork] = useState('Mining');
  const [error, setError] = useState('');

  const load = useCallback(async (id: string) => {
    setError('');
    try {
      setReport(await getWorkRanking(id, 10));
    } catch (e) {
      // Never `.catch(() => [])`: an empty ranking is a legitimate answer
      // ("nobody here can do this job"), so swallowing a failure into one
      // destroys the distinction between nothing and could-not-ask.
      setError(e instanceof Error ? e.message : String(e));
      setReport(null);
    }
  }, []);

  useEffect(() => { void load(work); }, [load, work]);

  const ranking = report?.rankings?.[0];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <h4 style={{ display: 'flex', alignItems: 'center', gap: 6, margin: 0, fontSize: 13, color: 'var(--text-primary)' }}>
          <Hammer size={14} /> Best at a job
        </h4>
        <div style={{ flex: 1 }} />
        <select
          value={work}
          onChange={(e) => setWork(e.target.value)}
          style={{
            background: 'var(--bg-input)', color: 'var(--text-primary)',
            border: '1px solid var(--border-primary)', borderRadius: 4,
            padding: '3px 6px', fontSize: 12,
          }}
        >
          {(report?.workTypes ?? []).map((t) => (
            <option key={t.id} value={t.id}>{t.display_name}</option>
          ))}
        </select>
      </div>

      {error && <div className="notice notice-warn">{error}</div>}

      {ranking && ranking.pals.length === 0 && (
        <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          None of your Pals can do this job.
        </div>
      )}

      {ranking?.pals.map((pal: WorkRankedPal) => (
        <Row key={pal.instanceId}>
          <PalCell pal={pal} />
          <span
            title={
              pal.work.bought
                ? `Level ${pal.work.base} from the species, +${pal.work.bought} bought with Pal Souls`
                : `Level ${pal.work.base} from the species`
            }
            className="mono"
            style={{ color: 'var(--accent-amber)', minWidth: 46, textAlign: 'right' }}
          >
            {pal.work.level}
            {pal.work.bought > 0 && (
              <span style={{ color: 'var(--accent-purple)', fontSize: 10 }}>
                {' '}+{pal.work.bought}
              </span>
            )}
          </span>
          <span
            className="mono"
            title="Work speed — calculated from the game's formula, not stored in the save"
            style={{ color: 'var(--text-muted)', minWidth: 40, textAlign: 'right' }}
          >
            {pal.workSpeed}*
          </span>
        </Row>
      ))}

      <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>
        Work level is read from the save. * Work speed is calculated.
      </div>
    </div>
  );
}

function CombatPanel() {
  const [report, setReport] = useState<CombatRankingReport | null>(null);
  const [against, setAgainst] = useState('');
  const [error, setError] = useState('');

  const load = useCallback(async (target: string) => {
    setError('');
    try {
      setReport(await getCombatRanking(target ? [target] : undefined, 15));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setReport(null);
    }
  }, []);

  useEffect(() => { void load(against); }, [load, against]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <h4 style={{ display: 'flex', alignItems: 'center', gap: 6, margin: 0, fontSize: 13, color: 'var(--text-primary)' }}>
          <Swords size={14} /> Strongest Pals
        </h4>
        <div style={{ flex: 1 }} />
        <label style={{ fontSize: 12, color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: 6 }}>
          Facing
          <select
            value={against}
            onChange={(e) => setAgainst(e.target.value)}
            style={{
              background: 'var(--bg-input)', color: 'var(--text-primary)',
              border: '1px solid var(--border-primary)', borderRadius: 4,
              padding: '3px 6px', fontSize: 12,
            }}
          >
            <option value="">Anything</option>
            {(report?.elements ?? []).map((el) => (
              <option key={el} value={el}>{el}</option>
            ))}
          </select>
        </label>
      </div>

      {error && <div className="notice notice-warn">{error}</div>}

      {report && !report.chartIsCurrent && (
        <div className="notice notice-warn" style={{ fontSize: 12 }}>
          <AlertTriangle size={13} /> The game has elements this dashboard&apos;s
          matchup chart does not know ({report.unknownElements.join(', ')}), so
          matchups involving them are not shown rather than guessed.
        </div>
      )}

      {report?.ranking.map((pal: CombatRankedPal) => (
        <Row key={pal.instanceId}>
          <PalCell pal={pal} />
          {pal.matchup && (
            <span
              title="Elemental relation only — the game files give no damage multiplier"
              style={{
                fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5,
                color: MATCHUP_STYLE[pal.matchup]?.colour ?? 'var(--text-muted)',
                minWidth: 48, textAlign: 'right',
              }}
            >
              {MATCHUP_STYLE[pal.matchup]?.label ?? pal.matchup}
            </span>
          )}
          <span className="mono" title="Attack" style={{ color: 'var(--accent-red)', minWidth: 44, textAlign: 'right' }}>
            {pal.attack}
          </span>
          <span className="mono" title="Defense" style={{ color: 'var(--accent-blue)', minWidth: 44, textAlign: 'right' }}>
            {pal.defense}
          </span>
          <span className="mono" title="HP" style={{ color: 'var(--accent-emerald)', minWidth: 50, textAlign: 'right' }}>
            {pal.hp}
          </span>
        </Row>
      ))}

      {report && against && (
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6, fontSize: 11, color: 'var(--text-muted)' }}>
          <Info size={12} style={{ flexShrink: 0, marginTop: 2 }} />
          <span>
            The matchup is a relation, not a damage figure — the game&apos;s files
            carry no multiplier, so the list stays ordered by stats and the badge
            is shown beside it rather than folded into the ranking.
          </span>
        </div>
      )}

      <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>
        All three stats are calculated from the game&apos;s formula, not stored.
      </div>
    </div>
  );
}

export default function PalOptimiser() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <WorkPanel />
      <CombatPanel />
    </div>
  );
}
