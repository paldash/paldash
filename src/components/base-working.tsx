'use client';

import { useCallback, useEffect, useState } from 'react';
import { Briefcase, RefreshCw, AlertTriangle, Info, Ghost } from 'lucide-react';
import {
  getActualWork,
  type ActualWorkReport,
  type ActualJob,
  type WorkMismatch,
} from '@/lib/save-api';
import { t } from '@/lib/chrome';

/**
 * Who the game has ACTUALLY assigned to each job.
 *
 * The sibling of `base-assign.tsx`, and the pair is easy to confuse: that one
 * ranks who *should* work at a base, this reports who *is*, from the save's own
 * `WorkSaveData`. Both are on the Bases tab and the headings say which is which,
 * because a recommendation shown as a fact would be much worse than either.
 *
 * IT REPORTS, IT DOES NOT ADVISE. There is no candidate list and no button.
 * "Nobody is on this Ranch" is a fact; who to put there is the other panel's
 * question, and it already has the ranking to justify an answer.
 *
 * `null` IN A WORK LEVEL IS NOT ZERO. It means the character has no work table
 * at all — an NPC, of which the reference world has 99 sharing
 * `CharacterSaveParameterMap` with the Pals. Rendered as an em dash rather than
 * a 0, because 0 would read as "cannot do this job".
 *
 * THE STATE INTEGER IS NEVER TRANSLATED. The game names those values nowhere
 * this project can read, and inventing a legend from an observed distribution is
 * the `icon_type` mistake. `stateIsUnnamed` travels in the payload for exactly
 * this reason and nothing here renders one.
 */

function Level({ work, value }: { work: string; value: number | null }) {
  // null: no work table for this character at all. Never a 0 — see above.
  const unknown = value === null || value === undefined;
  return (
    <span
      style={{
        fontSize: 11,
        padding: '1px 5px',
        borderRadius: 4,
        border: '1px solid var(--border-primary)',
        color: unknown
          ? 'var(--text-muted)'
          : value === 0
            ? 'var(--accent-amber)'
            : 'var(--text-primary)',
      }}
      title={unknown
        ? `${work}: this character has no work table — it is probably an NPC`
        : `${work}: level ${value}`}
    >
      {work} {unknown ? '—' : value}
    </span>
  );
}

function Job({ job }: { job: ActualJob }) {
  return (
    <div
      style={{
        padding: '6px 0',
        borderTop: '1px solid var(--border-primary)',
        display: 'flex',
        gap: 10,
        flexWrap: 'wrap',
        alignItems: 'baseline',
      }}
    >
      <strong style={{ fontSize: 12, minWidth: 150 }}>{job.structureName}</strong>

      <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        {job.anyPalQualifies
          ? 'any Pal'
          : job.needs.length
            ? job.needs.join(' · ')
            : 'no assignable work'}
      </span>

      {job.assigned.length === 0 ? (
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>— nobody assigned</span>
      ) : (
        job.assigned.map((pal) => (
          <span key={pal.instanceId} style={{ display: 'flex', gap: 5, alignItems: 'baseline' }}>
            <span style={{ fontSize: 12 }}>
              {pal.name || pal.speciesId}{' '}
              <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>Lv {pal.level}</span>
            </span>
            {Object.entries(pal.workLevels).map(([work, value]) => (
              <Level key={work} work={work} value={value} />
            ))}
          </span>
        ))
      )}

      {job.staleAssignments > 0 && (
        <span
          style={{ fontSize: 11, color: 'var(--accent-amber)', display: 'flex', gap: 4 }}
          title={t('The save has this slot assigned to a Pal that no longer exists. The game will not fill it until something clears it.')}
        >
          <Ghost size={12} style={{ alignSelf: 'center' }} />
          {job.staleAssignments} stale
        </span>
      )}
    </div>
  );
}

export default function BaseWorking({ baseId }: { baseId?: string }) {
  const [data, setData] = useState<ActualWorkReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showEmpty, setShowEmpty] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setData(await getActualWork(baseId));
    } catch (e) {
      // Let it surface. An empty report and a failed fetch must not look the
      // same — `.catch(() => [])` is the bug this project keeps recording.
      setError(e instanceof Error ? e.message : 'Could not load work assignments');
      setData(null);
    } finally {
      setBusy(false);
    }
  }, [baseId]);

  useEffect(() => {
    load();
  }, [load]);

  const unsuitable = (data?.mismatches ?? []).filter((m) => m.kind === 'unsuitable');
  const empty = (data?.mismatches ?? []).filter((m) => m.kind === 'empty');

  return (
    <div style={{ marginTop: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
        <Briefcase size={14} style={{ color: 'var(--accent-blue)' }} />
        <strong style={{ fontSize: 13 }}>Who is working — the game&rsquo;s own record</strong>
        <button
          className="btn btn-ghost"
          style={{ marginLeft: 'auto', padding: '2px 6px' }}
          onClick={load}
          disabled={busy}
          title="Reload"
        >
          <RefreshCw size={12} />
        </button>
      </div>

      <p style={{ fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.6, marginBottom: 8 }}>
        Read from the save&rsquo;s own assignment record, not inferred. This is
        what the game has actually done; the panel above ranks what it thinks
        <em> should</em> happen.
      </p>

      {error && (
        <div className="notice notice-warn" style={{ fontSize: 12 }}>
          <AlertTriangle size={13} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
          {error}
        </div>
      )}

      {data && (
        <>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 8 }}>
            {data.totalAssigned} Pal{data.totalAssigned === 1 ? '' : 's'} on{' '}
            {data.totalJobs} job{data.totalJobs === 1 ? '' : 's'}
            {data.staleAssignments > 0 && (
              <> · <span style={{ color: 'var(--accent-amber)' }}>
                {data.staleAssignments} slot{data.staleAssignments === 1 ? '' : 's'} assigned to a Pal that no longer exists
              </span></>
            )}
          </div>

          {unsuitable.length > 0 && (
            <div className="notice notice-warn" style={{ fontSize: 12, marginBottom: 8 }}>
              <AlertTriangle size={13} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
              {unsuitable.length} Pal{unsuitable.length === 1 ? ' is' : 's are'} on a job
              they have no work level for:{' '}
              {unsuitable.map((m: WorkMismatch, i) => (
                <span key={`${m.instanceId}-${i}`}>
                  {i > 0 && ', '}
                  {m.name || m.speciesId} on {m.structureName}
                </span>
              ))}
            </div>
          )}

          {data.bases.map((base) => (
            <div key={base.baseId} style={{ marginBottom: 10 }}>
              <div style={{ fontSize: 12, fontWeight: 600 }}>
                {base.baseName}{' '}
                <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>
                  · {base.workersAssigned} working
                </span>
              </div>
              {base.jobs.filter((j) => showEmpty || j.assigned.length > 0).map((job) => (
                <Job key={job.workId} job={job} />
              ))}
              {!showEmpty && base.jobs.every((j) => j.assigned.length === 0) && (
                <div style={{ fontSize: 11, color: 'var(--text-muted)', padding: '4px 0' }}>
                  Nobody is assigned to anything at this base.
                </div>
              )}
            </div>
          ))}

          {/* Collapsed by default: on the reference world 109 of 160 jobs are
              unstaffed, which is the ordinary state of a base rather than a
              finding, and showing them all buries the ones with workers. */}
          {empty.length > 0 && (
            <button
              className="btn btn-ghost"
              style={{ fontSize: 11, padding: '2px 6px' }}
              onClick={() => setShowEmpty((v) => !v)}
            >
              {showEmpty ? 'Hide' : 'Show'} {empty.length} unstaffed job
              {empty.length === 1 ? '' : 's'}
            </button>
          )}

          {data.unbased.length > 0 && (
            <div style={{ marginTop: 8, fontSize: 11, color: 'var(--text-muted)' }}>
              <Info size={12} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 4 }} />
              {data.unbased.length} job{data.unbased.length === 1 ? '' : 's'} outside any base
              (a world-placed structure being repaired).
            </div>
          )}
        </>
      )}
    </div>
  );
}
