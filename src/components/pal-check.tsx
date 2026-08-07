'use client';

import { useState } from 'react';
import { ScanSearch, ShieldCheck, AlertTriangle, Wrench, Info } from 'lucide-react';
import { scanIllegalPals, previewPalRepair, applyPalRepair } from '@/lib/save-api';
import type { PalCheckScan, PalRepairPlan } from '@/lib/types';
import { asArray } from '@/lib/arrays';

const CODE_LABELS: Record<string, string> = {
  iv_out_of_range: 'IV out of range',
  rank_out_of_range: 'Condenser rank out of range',
  level_out_of_range: 'Level above the cap',
  exp_mismatch: 'EXP beyond its level',
  too_many_passives: 'Too many passive skills',
  duplicate_passives: 'Duplicate passive skills',
  unknown_passive: 'Unrecognised passive skill',
  unknown_species: 'Unrecognised species',
};

/**
 * Illegal-Pal detection and repair.
 *
 * Two things this UI is careful about, because getting either wrong makes the
 * feature worse than useless:
 *
 * 1. **Advisories are not accusations.** An id the bundled data does not
 *    recognise usually means our tables are incomplete, not that someone
 *    cheated — 13 of the reference world's own characters are ordinary NPCs
 *    missing from them. Those are shown separately and never counted.
 * 2. **Repair makes Pals weaker, deliberately.** Clamping IV 255 to 100 is a
 *    judgement about someone else's Pal, so it is always preview-then-confirm
 *    and never automatic.
 */
export default function PalCheck({ canEdit }: { canEdit: boolean }) {
  const [scan, setScan] = useState<PalCheckScan | null>(null);
  const [plan, setPlan] = useState<PalRepairPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const run = async () => {
    setBusy(true); setError(null); setDone(null); setPlan(null);
    try {
      setScan(await scanIllegalPals());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Scan failed');
    } finally {
      setBusy(false);
    }
  };

  const preview = async () => {
    setBusy(true); setError(null);
    try {
      setPlan(await previewPalRepair());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Repair preview failed');
    } finally {
      setBusy(false);
    }
  };

  const repair = async () => {
    if (!plan?.planHash) return;
    if (!confirm(
      `Clamp ${plan.palsChanged} Pal(s) back into legal range?\n\n` +
      'This makes those Pals weaker — that is the point, and it cannot be undone ' +
      'except by restoring the backup taken first. Every Pal is verified after ' +
      'the write; any mismatch rolls the whole world back.'
    )) return;

    setBusy(true); setError(null);
    try {
      const result = await applyPalRepair(plan.planHash);
      setDone(
        `Repaired ${result.palsChanged} Pal(s), ${result.fieldsChanged} field(s), verified. ` +
        (result.palsWithUnfixableIssues
          ? `${result.palsWithUnfixableIssues} still carry a problem that cannot be fixed by writing a value. `
          : '') +
        `Rollback point: ${result.backupId}.`
      );
      setPlan(null);
      await run();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Repair failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="glass-card" style={{ padding: 16 }}>
      <div className="section-title" style={{ marginBottom: 10 }}>
        <ScanSearch size={14} /> Illegal Pal check
        <button
          className="btn"
          style={{ marginLeft: 'auto', padding: '3px 10px', fontSize: 11 }}
          disabled={busy}
          onClick={run}
        >
          {busy ? 'Scanning…' : scan ? 'Re-scan' : 'Scan the world'}
        </button>
      </div>

      {error && <div className="notice notice-warn" style={{ marginBottom: 12 }}>{error}</div>}
      {done && (
        <div className="notice" style={{ marginBottom: 12 }}>
          <ShieldCheck size={13} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 6 }} />
          {done}
        </div>
      )}

      {!scan ? (
        <p style={{ fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.7 }}>
          Checks every Pal against what the game can actually produce — IVs 0–100,
          condenser rank 1–5, level within the cap, and EXP that is not beyond the
          level it displays. Read-only; nothing is written until you ask.
        </p>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 12 }}>
            <Stat label="Pals scanned" value={scan.palsScanned.toLocaleString()} />
            <Stat
              label="With illegal stats"
              value={scan.palsFlagged.toLocaleString()}
              tone={scan.palsFlagged ? 'warn' : 'ok'}
            />
            <Stat label="Repairable" value={scan.palsRepairable.toLocaleString()} />
            <Stat label="Unrecognised ids" value={scan.palsUnrecognised.toLocaleString()} />
          </div>

          {scan.palsFlagged === 0 ? (
            <div className="notice" style={{ fontSize: 12 }}>
              <ShieldCheck size={13} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 6 }} />
              Every Pal is within the bounds the game can produce — IVs up to{' '}
              {scan.bounds.maxIv}, rank {scan.bounds.rank[0]}–{scan.bounds.rank[1]},
              level up to {scan.bounds.maxLevel}.
            </div>
          ) : (
            <>
              <div style={{
                maxHeight: 300, overflowY: 'auto',
                border: '1px solid var(--border-primary)', borderRadius: 6,
              }}>
                {scan.pals.map((p) => (
                  <div key={p.instanceId} style={{
                    padding: '7px 10px', fontSize: 12,
                    borderBottom: '1px solid var(--border-primary)',
                  }}>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                      <strong>{p.nickname || p.speciesName}</strong>
                      <span style={{ color: 'var(--text-muted)' }}>
                        Lv {p.level}{p.ownerName ? ` · ${p.ownerName}` : ''}
                      </span>
                      {!p.repairable && (
                        <span className="badge" style={{ marginLeft: 'auto' }}>not repairable</span>
                      )}
                    </div>
                    {p.issues.map((issue, i) => (
                      <div key={i} style={{
                        fontSize: 11, color: 'var(--text-secondary)',
                        marginTop: 3, lineHeight: 1.5,
                      }}>
                        <AlertTriangle
                          size={11}
                          style={{
                            display: 'inline', verticalAlign: '-1px', marginRight: 5,
                            color: 'var(--accent-amber)',
                          }}
                        />
                        <strong>{CODE_LABELS[issue.code] ?? issue.code}</strong> — {issue.detail}
                        {issue.repairable && issue.fix != null && (
                          <span style={{ color: 'var(--accent-emerald)' }}>
                            {' '}Would become {String(issue.fix)}.
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                ))}
              </div>

              <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
                <button
                  className="btn"
                  disabled={busy || scan.palsRepairable === 0}
                  onClick={preview}
                  title={scan.palsRepairable === 0
                    ? 'Nothing here can be fixed by writing a value'
                    : undefined}
                >
                  <Wrench size={12} /> Preview repair of {scan.palsRepairable} Pal(s)
                </button>
              </div>

              {plan && (
                <div style={{
                  marginTop: 12, padding: 12,
                  border: '1px solid var(--border-primary)', borderRadius: 6,
                  background: 'var(--bg-input)',
                }}>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 8 }}>
                    {plan.palsChanged ?? 0} Pal(s) would be clamped back into range
                    {plan.palsWithUnfixableIssues > 0 && (
                      <>; {plan.palsWithUnfixableIssues} keep a problem this build cannot
                      fix by writing a value (passive skill lists are not scalars)</>
                    )}.
                  </div>
                  {asArray(plan.pals, 'palcheck pals').slice(0, 20).map((p) => (
                    <div key={p.instanceId} style={{ fontSize: 11, padding: '2px 0' }}>
                      <strong>{p.nickname || p.instanceId.slice(0, 8)}</strong>{' '}
                      <span style={{ color: 'var(--text-muted)' }}>
                        {p.changes.map((c) => `${c.label} ${String(c.before)} → ${String(c.after)}`).join(', ')}
                      </span>
                    </div>
                  ))}
                  <button
                    className="btn btn-primary"
                    style={{ marginTop: 10 }}
                    disabled={!canEdit || busy || !plan.palsChanged}
                    onClick={repair}
                    title={!canEdit ? 'The server must be stopped first' : undefined}
                  >
                    {busy ? 'Writing…' : `Repair ${plan.palsChanged} Pal(s)`}
                  </button>
                </div>
              )}
            </>
          )}

          {scan.palsUnrecognised > 0 && (
            <details style={{ marginTop: 12 }}>
              <summary style={{ fontSize: 12, color: 'var(--text-muted)', cursor: 'pointer' }}>
                <Info size={12} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
                {scan.palsUnrecognised} character(s) with an id we do not recognise —
                probably not cheating
              </summary>
              <p style={{ fontSize: 11, color: 'var(--text-secondary)', margin: '8px 0', lineHeight: 1.7 }}>
                These are almost always NPCs the bundled reference data does not list —
                guards, merchants, villagers. The reference world has 13 of its own.
                They are shown here rather than counted as violations, because an id we
                lack is a gap in our data, not evidence about your players.
              </p>
              <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                {[...new Set(scan.advisories?.map?.((a) => a.speciesId) ?? [])]
                  .slice(0, 30)
                  .map((id) => <span key={id} className="mono" style={{ marginRight: 10 }}>{id}</span>)}
              </div>
            </details>
          )}
        </>
      )}
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'ok' | 'warn' }) {
  return (
    <div>
      <div style={{ fontSize: 10, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
        {label}
      </div>
      <div style={{
        fontSize: 20, fontWeight: 600,
        color: tone === 'warn' ? 'var(--accent-amber)'
             : tone === 'ok' ? 'var(--accent-emerald)' : 'var(--text-primary)',
      }}>
        {value}
      </div>
    </div>
  );
}
