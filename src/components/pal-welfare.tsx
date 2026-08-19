'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  HeartPulse, RefreshCw, AlertTriangle, ShieldCheck, Utensils, Brain, Stethoscope,
} from 'lucide-react';
import {
  getWelfare, previewBulkPalEdit, applyBulkPalEdit,
  type WelfareReport, type WelfareProblem, type PalRecord,
} from '@/lib/save-api';
import type { BulkEditPlan } from '@/lib/types';
import GameIcon from '@/components/game-icon';
import { asArray } from '@/lib/arrays';
import { num, count } from '@/lib/format';
import { t, tl } from '@/lib/chrome';

/**
 * Pals that need attention — sick, starving, injured, or losing their minds.
 *
 * All four conditions were sitting in the save from the beginning and none were
 * read. The game tells you about them by making a base stop producing, which is
 * a symptom you notice days later and cannot trace to a cause.
 *
 * TWO THINGS THIS PANEL IS CAREFUL ABOUT:
 *
 * 1. **The counts sum higher than the row count, and that is right.** A Pal
 *    that is both sick and starving appears under both, because "how many are
 *    sick" is the question being asked. Deduplicating would answer a different
 *    one, so the row count is stated separately rather than implied.
 * 2. **Clearing hunger without feeding is undone by the game.** The flag is a
 *    consequence of `FullStomach`, not a cause, so the fix has to raise the
 *    fullness too — and the ceiling is per species and per level and is stored
 *    nowhere. See `feedTo` below: the number comes from the operator's own
 *    world rather than from a constant this file invented.
 */

const PROBLEMS: {
  key: WelfareProblem;
  label: string;
  icon: React.ReactNode;
  colour: string;
}[] = [
  { key: 'sick', label: tl('Sick'), icon: <Stethoscope size={13} />, colour: '#e5484d' },
  // Muted, not amber: nothing is wrong with these Pals. `WorkerSick` is a
  // base-camp worker state that lingers on the record after the Pal leaves —
  // the game shows them healthy, and the flags sit unchanged indefinitely.
  // An earlier label said "Recovering in the box", which claimed a process
  // nobody has observed. Listed so the operator can clear the residue.
  { key: 'staleSick', label: tl('Stale sickness flag (healthy in game)'), icon: <Stethoscope size={13} />, colour: 'var(--text-muted)' },
  { key: 'injured', label: tl('Injured'), icon: <HeartPulse size={13} />, colour: '#e5484d' },
  { key: 'starving', label: tl('Starving'), icon: <Utensils size={13} />, colour: '#e5484d' },
  { key: 'hungry', label: tl('Hungry'), icon: <Utensils size={13} />, colour: '#f5a524' },
  { key: 'lowSanity', label: tl('Low sanity'), icon: <Brain size={13} />, colour: '#f5a524' },
];

type Remedy = {
  id: string;
  label: string;
  /** Which reported problems this remedy addresses. */
  covers: WelfareProblem[];
  /** The change set to spread across the selection. */
  changes: (feedTo: number) => Record<string, unknown>;
  /** Why it might not be offered right now. */
  blocked?: (report: WelfareReport, feedTo: number) => string | null;
};

const REMEDIES: Remedy[] = [
  {
    id: 'cure',
    label: tl('Cure sickness'),
    covers: ['sick', 'staleSick'],
    // `null` is the whole request. A healthy Pal has no `WorkerSick` property
    // at all, so there is no well value to write — curing is a deletion, and
    // the result is byte-identical to a Pal that was never ill.
    changes: () => ({ workerSick: null }),
  },
  {
    id: 'heal',
    label: tl('Heal injuries'),
    covers: ['injured'],
    changes: () => ({ physicalHealth: null }),
  },
  {
    id: 'feed',
    label: tl('Feed'),
    covers: ['hungry', 'starving'],
    // Both halves, and the order does not matter because they are written in
    // one guarded pass. Clearing the flag alone leaves `FullStomach` where it
    // was and the game simply sets it again at the next tick — an edit the
    // operator watched succeed and then silently lose.
    changes: (feedTo) => ({ hungerType: null, fullStomach: feedTo }),
    blocked: (_report, feedTo) =>
      feedTo > 0 ? null : 'No fullness reading to work from on these Pals.',
  },
  {
    id: 'calm',
    label: tl('Restore sanity'),
    covers: ['lowSanity'],
    changes: () => ({ sanity: 100 }),
  },
];

export default function PalWelfare({ canEdit }: { canEdit: boolean }) {
  const [report, setReport] = useState<WelfareReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [plan, setPlan] = useState<{ remedy: Remedy; ids: string[]; plan: BulkEditPlan } | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      setReport(await getWelfare());
    } catch (e) {
      // Let it say which. An empty panel and a failed request look identical,
      // and "no Pals need attention" is the single most misleading thing this
      // component could say when it simply could not ask.
      setError(e instanceof Error ? e.message : 'Could not load Pal welfare');
      setReport(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { queueMicrotask(load); }, [load]);

  /**
   * What to raise fullness to, taken from the operator's own world.
   *
   * The real ceiling is per species and per level and is stored nowhere in the
   * save, so there is no correct constant to ship — 150 would under-feed a
   * Jetragon and 620 is meaningless for a Lamball. The highest reading among
   * the Pals actually in front of us is a real number from this world, and the
   * game clamps an overshoot itself, so erring high costs nothing.
   */
  const feedTo = useMemo(() => {
    const seen = asArray(report?.pals, 'welfare pals')
      .map((p) => p.fullStomach)
      .filter((v): v is number => typeof v === 'number');
    return seen.length ? Math.ceil(Math.max(...seen)) : 0;
  }, [report]);

  const idsFor = useCallback(
    (remedy: Remedy) =>
      asArray(report?.pals, 'welfare pals')
        .filter((p) => p.problems.some((problem) => remedy.covers.includes(problem)))
        .map((p) => p.instanceId),
    [report]
  );

  const preview = async (remedy: Remedy) => {
    const ids = idsFor(remedy);
    if (!ids.length) return;
    setBusy(remedy.id); setError(null); setDone(null); setPlan(null);
    try {
      // `autoExp` off: nothing here touches level, and letting the bulk writer
      // rewrite EXP as a side effect of curing a cold is not what was asked for.
      const result = await previewBulkPalEdit(ids, remedy.changes(feedTo), false);
      setPlan({ remedy, ids, plan: result });
    } catch (e) {
      setError(e instanceof Error ? e.message : t('Preview failed'));
    } finally {
      setBusy(null);
    }
  };

  const apply = async () => {
    if (!plan?.plan.ok || !plan.plan.planHash) return;
    if (!confirm(
      `${plan.remedy.label} on ${plan.plan.palsChanged} Pal(s)?\n\n` +
      'A full backup is taken first. Every Pal is validated before anything is ' +
      'written, and a verification failure on any one of them rolls the whole ' +
      'world back.'
    )) return;

    setBusy(plan.remedy.id); setError(null);
    try {
      const result = await applyBulkPalEdit(
        plan.ids, plan.remedy.changes(feedTo), plan.plan.planHash, false
      );
      setDone(
        `${plan.remedy.label}: ${result.palsChanged} Pal(s) updated and verified. ` +
        `Rollback point: ${result.backupId}.`
      );
      setPlan(null);
      // The world moved, so the report we are showing is now the old one.
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : t('Apply failed'));
    } finally {
      setBusy(null);
    }
  };

  const counts = report?.counts ?? {};
  const active = PROBLEMS.filter((p) => (counts[p.key] ?? 0) > 0);

  return (
    <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <div className="section-title" style={{ margin: 0 }}>
          <HeartPulse size={14} /> Pal welfare
        </div>
        <button
          className="btn btn-ghost"
          style={{ marginLeft: 'auto', padding: '2px 8px', fontSize: 11 }}
          onClick={() => void load()}
          disabled={loading}
        >
          <RefreshCw size={11} /> {loading ? 'Checking…' : 'Recheck'}
        </button>
      </div>

      {error && (
        <p style={{ fontSize: 12, color: 'var(--accent-danger, #e5484d)', marginBottom: 8 }}>
          <AlertTriangle size={12} style={{ verticalAlign: -2 }} /> {error}
        </p>
      )}
      {done && (
        <p style={{ fontSize: 12, color: 'var(--accent-success, #30a46c)', marginBottom: 8 }}>
          <ShieldCheck size={12} style={{ verticalAlign: -2 }} /> {done}
        </p>
      )}

      {report && active.length === 0 && !loading && (
        <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          Nothing needs attention — all {num(report.scanned)} Pals are
          fed, well and above {report.lowSanityBelow} sanity.
        </p>
      )}

      {report && active.length > 0 && (
        <>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
            {asArray(active, 'active welfare pals').map((p) => (
              <span
                key={p.key}
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: 5,
                  fontSize: 12, padding: '3px 9px', borderRadius: 5,
                  background: 'var(--bg-input)', color: p.colour,
                }}
              >
                {p.icon} {counts[p.key]} {t(p.label).toLowerCase()}
              </span>
            ))}
          </div>

          {/* Stated rather than implied. The chips above deliberately sum higher
              than this, because a Pal with three things wrong with it is three
              answers to "how many are sick / hungry / injured" and one row. */}
          <p style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 10 }}>
            {count(report.pals)} Pal
            {report.pals.length === 1 ? '' : 's'} of {num(report.scanned)} need
            attention. A Pal with more than one problem is counted once per problem above.
          </p>

          {/* WHAT "SICK" ACTUALLY COSTS.
              A red dot said a Pal was ill and nothing else — so there was no way
              to tell a Sprain (move -5%, cured in a few hours) from a
              Troublemaker (work AND move -50%, 3% an hour, effectively
              permanent without medicine). The game ships all of it. */}
          {(report.illnesses?.length ?? 0) > 0 && (
            <div style={{ marginBottom: 12 }}>
              {asArray(report.illnesses, 'illnesses').map((ill) => (
                <div
                  key={ill.id}
                  title={ill.description}
                  style={{
                    display: 'flex', alignItems: 'baseline', gap: 8,
                    fontSize: 11, padding: '3px 0',
                    borderTop: '1px solid var(--border)',
                  }}
                >
                  <strong style={{ minWidth: 96 }}>{ill.name}</strong>
                  <span style={{ color: 'var(--text-muted)', flex: '1 1 auto' }}>
                    {/* Only the penalties that are non-zero. A Sprain costs no
                        work speed at all, and printing "work 0%" beside it
                        reads as a measured zero rather than as "not affected". */}
                    {[
                      ill.workSpeed ? `work ${ill.workSpeed}%` : null,
                      ill.moveSpeed ? `move ${ill.moveSpeed}%` : null,
                      ill.satietyDecrease ? `hunger +${ill.satietyDecrease}%` : null,
                    ].filter(Boolean).join(' · ') || 'no speed penalty'}
                  </span>
                  <span style={{ color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                    {/* A bare percentage is a rate with no denominator; the
                        game rolls this once an hour. */}
                    palbox cures {ill.palboxRecoveryPercent}%
                    {report.palboxCurePeriodSeconds === 3600
                      ? ' an hour'
                      : report.palboxCurePeriodSeconds
                        ? ` per ${Math.round(report.palboxCurePeriodSeconds / 60)} min`
                        : ''}
                  </span>
                </div>
              ))}
            </div>
          )}

          {canEdit && (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
              {REMEDIES.map((remedy) => {
                const count = idsFor(remedy).length;
                const why = remedy.blocked?.(report, feedTo) ?? null;
                if (!count) return null;
                return (
                  <button
                    key={remedy.id}
                    className="btn"
                    style={{ fontSize: 12 }}
                    disabled={busy !== null || Boolean(why)}
                    title={why ?? undefined}
                    onClick={() => void preview(remedy)}
                  >
                    {busy === remedy.id ? 'Working…' : `${t(remedy.label)} (${count})`}
                  </button>
                );
              })}
            </div>
          )}

          {/* What "Feed" will actually do, in the open. A number derived from
              this world beats a constant, but only if it is shown. */}
          {canEdit && feedTo > 0 && (
            <p style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 10 }}>
              Feeding sets fullness to <strong>{feedTo}</strong> — the highest reading among
              these Pals, since the real ceiling is per species and is not stored in the save.
              The game clamps an overshoot itself.
            </p>
          )}

          {plan && (
            <div style={{
              padding: 10, borderRadius: 6, marginBottom: 12,
              background: 'var(--bg-input)', fontSize: 12,
            }}>
              {plan.plan.ok ? (
                <>
                  <p style={{ marginBottom: 6 }}>
                    <strong>{t(plan.remedy.label)}</strong> — {plan.plan.palsChanged} Pal(s) will
                    change, {plan.plan.palsUnchanged} already fine.
                  </p>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn" disabled={busy !== null} onClick={() => void apply()}>
                      Apply
                    </button>
                    <button className="btn btn-ghost" onClick={() => setPlan(null)}>
                      Cancel
                    </button>
                  </div>
                </>
              ) : (
                <ul style={{ margin: 0, paddingLeft: 16, color: 'var(--accent-danger, #e5484d)' }}>
                  {asArray(plan.plan.problems, 'welfare plan problems').slice(0, 6).map((p, i) => (
                    <li key={i}>{p.field ? `${p.field}: ` : ''}{p.problem}</li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <div style={{ maxHeight: 260, overflowY: 'auto' }}>
            {asArray(report.pals, 'welfare pals').slice(0, 200).map((pal) => (
              <WelfareRow key={pal.instanceId} pal={pal} />
            ))}
          </div>
          {report.pals.length > 200 && (
            <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>
              Showing the worst 200 of {count(report.pals)}. The buttons
              above act on all of them.
            </p>
          )}
        </>
      )}
    </div>
  );
}

function WelfareRow({ pal }: { pal: PalRecord & { problems: WelfareProblem[] } }) {
  const where = pal.location === 'base' && pal.baseName
    ? pal.baseName
    : pal.storageKind || pal.location || '';

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8,
      padding: '5px 0', borderBottom: '1px solid var(--border-primary)', fontSize: 12,
    }}>
      <GameIcon src={pal.icon} title={pal.speciesName ?? pal.speciesId} size={22} />
      <span style={{ fontWeight: 500 }}>
        {pal.nickname || pal.speciesName || pal.speciesId}
      </span>
      <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>Lv {pal.level}</span>
      {where && (
        <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>· {where}</span>
      )}
      <span style={{ marginLeft: 'auto', display: 'flex', gap: 5, flexWrap: 'wrap' }}>
        {asArray(pal.problems, 'welfare problems').map((problem) => {
          const meta = PROBLEMS.find((p) => p.key === problem);
          return (
            <span
              key={problem}
              style={{
                fontSize: 10, padding: '1px 6px', borderRadius: 4,
                background: 'var(--bg-input)', color: meta?.colour,
              }}
            >
              {/* The affliction's own name where the save has one — "Fracture"
                  is a different problem from "Depression" and both read as
                  "sick" otherwise. */}
              {problem === 'sick' ? pal.workerSick
                : problem === 'staleSick' ? `Stale: ${pal.workerSick}`
                : problem === 'injured' ? pal.physicalHealth
                : problem === 'lowSanity' ? `SAN ${Math.round(pal.sanity ?? 0)}`
                : meta ? t(meta.label) : problem}
            </span>
          );
        })}
      </span>
    </div>
  );
}
