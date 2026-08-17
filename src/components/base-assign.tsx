'use client';

import { useCallback, useEffect, useState } from 'react';
import { HardHat, RefreshCw, AlertTriangle, Check, Users, Info } from 'lucide-react';
import {
  getBaseAssignments,
  type BaseAssignment,
  type AssignNeed,
  type AssignCandidate,
} from '@/lib/save-api';
import { t } from '@/lib/chrome';

/**
 * What work each base needs, and who to put there.
 *
 * THIS PANEL RECOMMENDS AND NEVER APPLIES. Moving a Pal between containers is
 * `palclone`/`charedit` territory with its own verification and its own
 * capability; a suggestion list that could also act would be one bug away from
 * rearranging someone's server. There is deliberately no button here, and the
 * backend says `advisoryOnly` in the payload so this cannot drift.
 *
 * TWO RANKS, AND SHOWING ONLY ONE WOULD LIE. A station list can be tiered — the
 * research lab has ten slots at ranks 1 through 10, so a rank-1 Pal can start on
 * it — while the Ancient Multi Product Mining rig has ten slots all at rank 6.
 * So "covered" means the *lowest* station is staffed, and `topStationStaffed`
 * separately says whether the hardest one is. A base can be covered and still
 * have its Ancient Workbench standing idle, and that is worth seeing.
 *
 * A COMMITTED PAL IS SHOWN, NOT HIDDEN. "Your only Pal that can smelt is at Ore
 * Outpost" is a real answer; omitting it would look like owning nothing
 * suitable. Availability is shown as a chip so the trade-off is visible rather
 * than implied, and free Pals are already ordered first by the backend.
 */

const AVAILABILITY_STYLE: Record<
  AssignCandidate['availability'],
  { label: string; color: string }
> = {
  free: { label: 'Free', color: 'var(--accent-green)' },
  party: { label: 'In a party', color: 'var(--accent-amber)' },
  base: { label: 'At another base', color: 'var(--accent-amber)' },
  committed: { label: 'Here', color: 'var(--text-muted)' },
};

function Candidate({ row }: { row: AssignCandidate }) {
  const style = AVAILABILITY_STYLE[row.availability] ?? AVAILABILITY_STYLE.free;
  return (
    <div
      style={{
        display: 'flex', alignItems: 'center', gap: 8, fontSize: 12,
        padding: '3px 0',
      }}
    >
      <span style={{ flex: '1 1 auto', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {row.nickname || row.name}
        <span style={{ color: 'var(--text-muted)' }}> · Lv {row.level}</span>
      </span>
      <span style={{ color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
        work {row.work.level}
        {row.work.bought > 0 && (
          /* Bought ranks are a real investment in this individual Pal and are
             kept visible: "level 4, three of them bought" is a different fact
             from "naturally level 4". */
          <span title={`${row.work.base} from the species, ${row.work.bought} bought with Pal Souls`}>
            {' '}({row.work.base}+{row.work.bought})
          </span>
        )}
      </span>
      <span
        title={row.where}
        style={{
          color: style.color, fontSize: 11, whiteSpace: 'nowrap',
          border: `1px solid ${style.color}`, borderRadius: 3, padding: '0 5px',
        }}
      >
        {row.availability === 'base' ? row.where : style.label}
      </span>
    </div>
  );
}

function Need({ need }: { need: AssignNeed }) {
  const tiered = need.maxRank > need.minRank;
  return (
    <div
      style={{
        borderTop: '1px solid var(--border)', padding: '8px 0',
        display: 'flex', flexDirection: 'column', gap: 5,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
        {need.covered
          ? <Check size={13} style={{ color: 'var(--accent-green)', flexShrink: 0 }} />
          : <AlertTriangle size={13} style={{ color: 'var(--accent-amber)', flexShrink: 0 }} />}
        <strong style={{ flex: '1 1 auto' }}>{need.workName}</strong>
        <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>
          {/* A range only where the stations really differ. Printing "1–1"
              everywhere reads as a range that means nothing. */}
          needs {tiered ? `${need.minRank}–${need.maxRank}` : need.minRank}
          {need.slots > 1 && ` · ${need.slots} slots`}
        </span>
      </div>

      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        {need.structures.join(', ')}
      </div>

      {need.covered ? (
        <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          Worked by {need.coveredBy.map((c) => `${c.name} (${c.level})`).join(', ')}
          {/* The distinction the two ranks exist for. */}
          {!need.topStationStaffed && (
            <span style={{ color: 'var(--accent-amber)' }}>
              {' '}· best here is {need.bestRank}, top station needs {need.maxRank}
            </span>
          )}
        </div>
      ) : need.candidates.length > 0 ? (
        <div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 2 }}>
            Nobody here can do this. Candidates:
          </div>
          {need.candidates.map((c) => <Candidate key={c.instanceId} row={c} />)}
          {need.candidateCount > need.candidates.length && (
            <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              +{need.candidateCount - need.candidates.length} more
            </div>
          )}
        </div>
      ) : (
        <div style={{ fontSize: 11, color: 'var(--accent-amber)' }}>
          Nobody in your Pals can work this at rank {need.minRank} or above.
        </div>
      )}
    </div>
  );
}

function BaseCard({ report }: { report: BaseAssignment }) {
  return (
    <div className="glass-card" style={{ padding: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <strong style={{ fontSize: 13, flex: '1 1 auto' }}>{report.baseName}</strong>
        <span style={{ fontSize: 11, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 4 }}>
          <Users size={11} />
          {/* `workerCapacity` absent means UNKNOWN, not zero — rendering "n/0"
              would read as a base with no room at all. */}
          {report.workerCapacity
            ? `${report.workerCount} / ${report.workerCapacity} workers`
            : `${report.workerCount} workers`}
        </span>
      </div>

      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
        {report.guildName || 'No guild'}
        {report.uncovered > 0
          ? ` · ${report.uncovered} job${report.uncovered === 1 ? '' : 's'} uncovered`
          : report.needs.length > 0 ? ' · all jobs covered' : ''}
        {/* Said outright so "40 objects, 6 jobs" does not read as data loss. */}
        {report.structuresWithoutWork > 0 &&
          ` · ${report.structuresWithoutWork} structures need no worker`}
      </div>

      {report.needs.length === 0 ? (
        <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          Nothing built here needs a worker yet.
        </div>
      ) : (
        report.needs.map((n) => <Need key={n.work} need={n} />)
      )}
    </div>
  );
}

export default function BaseAssignPanel() {
  const [data, setData] = useState<BaseAssignment[] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const result = await getBaseAssignments();
      setData(result.bases);
      setError('');
    } catch (e) {
      // Let it say which, rather than rendering an empty list. An empty
      // collection is a legitimate answer for almost everything this dashboard
      // fetches, so a swallowed error is indistinguishable from "no bases".
      setData(null);
      setError(e instanceof Error ? e.message : 'Could not load work assignments');
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <HardHat size={15} style={{ color: 'var(--accent-cyan)' }} />
        <strong style={{ fontSize: 13, flex: '1 1 auto' }}>{t('Work assignment')}</strong>
        <button className="btn btn-ghost" onClick={() => void load()} disabled={busy}>
          <RefreshCw size={12} className={busy ? 'spin' : undefined} /> {t('Refresh')}
        </button>
      </div>

      <p style={{ fontSize: 11, color: 'var(--text-muted)', display: 'flex', gap: 6 }}>
        <Info size={12} style={{ flexShrink: 0, marginTop: 1 }} />
        <span>
          Work requirements come from the game&apos;s own structure data. These are
          suggestions only — nothing here moves a Pal, and assignments are made in
          game.
        </span>
      </p>

      {error && (
        <div style={{ fontSize: 12, color: 'var(--accent-amber)', display: 'flex', gap: 6 }}>
          <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1 }} /> {error}
        </div>
      )}

      {data?.length === 0 && !error && (
        <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          No bases visible to you.
        </div>
      )}

      {data?.map((report) => <BaseCard key={report.baseId} report={report} />)}
    </div>
  );
}
