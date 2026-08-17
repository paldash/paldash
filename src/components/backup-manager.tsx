'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  Archive, RefreshCw, ShieldCheck, Trash2, Download, RotateCcw,
  Pencil, Clock, AlertTriangle, Check,
} from 'lucide-react';
import {
  getBackups, createBackup, verifyBackup, renameBackup, deleteBackup,
  previewRestore, restoreBackup, pruneBackups, backupDownloadUrl,
  getBackupSchedule, setBackupSchedule,
} from '@/lib/save-api';
import { useDashboardStore } from '@/lib/store';
import type {
  BackupListing, BackupVerification, RestorePreview, BackupSchedule,
} from '@/lib/types';
import { asArray } from '@/lib/arrays';

function bytes(n: number): string {
  if (!n) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`;
}

const TRIGGER_LABEL: Record<string, string> = {
  manual: 'Manual',
  'pre-edit': 'Before an edit',
  'pre-restore': 'Before a restore',
};

function triggerLabel(trigger: string): string {
  if (TRIGGER_LABEL[trigger]) return TRIGGER_LABEL[trigger];
  if (trigger?.startsWith('schedule:')) return `Scheduled (${trigger.split(':')[1]})`;
  return trigger || 'Manual';
}

/**
 * Backup browser.
 *
 * A restore is the most destructive thing here, so it is deliberately two steps:
 * preview what would change, then confirm. The preview is read-only, and a
 * restore always leaves a rollback point behind.
 */
export default function BackupManager() {
  const { serverProcessRunning } = useDashboardStore();
  const [data, setData] = useState<BackupListing | null>(null);
  const [schedule, setSchedule] = useState<BackupSchedule | null>(null);
  const [verifications, setVerifications] = useState<Record<string, BackupVerification>>({});
  const [preview, setPreview] = useState<RestorePreview | null>(null);
  const [scope, setScope] = useState('world');
  const [description, setDescription] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [listing, sched] = await Promise.all([getBackups(), getBackupSchedule()]);
      setData(listing);
      setSchedule(sched);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load backups');
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const flash = (message: string) => {
    setStatus(message);
    setTimeout(() => setStatus(null), 5000);
  };

  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      flash(label);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Action failed');
    } finally {
      setBusy(false);
    }
  };

  const runVerify = async (id: string) => {
    setBusy(true);
    try {
      const verdict = await verifyBackup(id);
      setVerifications((v) => ({ ...v, [id]: verdict }));
      flash(verdict.ok
        ? `Backup ${id} verified — ${verdict.checkedFiles} files intact`
        : `Backup ${id} FAILED verification`);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Verification failed');
    } finally {
      setBusy(false);
    }
  };

  const openPreview = async (id: string) => {
    setBusy(true);
    setError(null);
    try {
      setPreview(await previewRestore(id, scope));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not preview the restore');
    } finally {
      setBusy(false);
    }
  };

  const confirmRestore = async () => {
    if (!preview) return;
    await act(`Restored from ${preview.backupId}`, async () => {
      const result = await restoreBackup(preview.backupId, preview.scope);
      setPreview(null);
      flash(
        `Restored ${result.restoredFiles.length} file(s). ` +
        `Rollback point: ${result.rollbackId}`
      );
    });
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {error && <div className="notice notice-warn">{error}</div>}
      {status && <div className="notice">{status}</div>}
      {data?.available === false && (
        /* An unmounted or read-only backup volume — "we could not look" is a
           different answer from "no backups yet", and taking one would fail
           too, so say so before the button teaches that lesson. */
        <div className="notice notice-warn">
          {data.reason || 'The backup directory is not usable.'}
          {data.directory ? ` (${data.directory})` : ''}{' '}
          Check that the backup volume is mounted and writable by the dashboard.
        </div>
      )}

      {/* ─── Create ─── */}
      <div className="glass-card" style={{ padding: 16 }}>
        <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 10 }}>Take a backup</h3>
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            className="input"
            placeholder="What is this backup for? (optional)"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <button
            className="btn btn-primary"
            disabled={busy}
            onClick={() =>
              act('Backup created', async () => {
                await createBackup(description);
                setDescription('');
              })
            }
          >
            <Archive size={13} /> Back up now
          </button>
          <button className="btn btn-ghost" onClick={load} disabled={busy}>
            <RefreshCw size={13} />
          </button>
        </div>
        {serverProcessRunning && (
          <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 8 }}>
            The server is running, so this will be a best-effort snapshot — files may
            be mid-autosave. Stop the server for a guaranteed-clean backup. Either
            way it is recorded on the backup itself.
          </p>
        )}
      </div>

      {/* ─── Schedule ─── */}
      {schedule && (
        <div className="glass-card" style={{ padding: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
            <Clock size={15} style={{ color: 'var(--text-muted)' }} />
            <h3 style={{ fontSize: 14, fontWeight: 600 }}>Automatic backups</h3>
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
              <input
                type="checkbox"
                checked={schedule.enabled}
                disabled={busy}
                onChange={(e) =>
                  act('Schedule updated', () => setBackupSchedule({ enabled: e.target.checked }))
                }
              />
              Enabled
            </label>
            <select
              className="input"
              style={{ maxWidth: 160 }}
              value={schedule.frequency}
              disabled={busy}
              onChange={(e) =>
                act('Schedule updated', () => setBackupSchedule({ frequency: e.target.value }))
              }
            >
              {schedule.frequencies.map((f) => (
                <option key={f} value={f}>{f}</option>
              ))}
            </select>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
              <input
                type="checkbox"
                checked={schedule.pruneAfter}
                disabled={busy}
                onChange={(e) =>
                  act('Schedule updated', () => setBackupSchedule({ pruneAfter: e.target.checked }))
                }
              />
              Apply retention afterwards
            </label>
          </div>
          <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 8 }}>
            {schedule.lastRun
              ? `Last run ${new Date(schedule.lastRun).toLocaleString()} — ${schedule.lastResult}`
              : 'Has not run yet.'}
            {schedule.enabled && schedule.nextRun &&
              ` Next due ${new Date(schedule.nextRun).toLocaleString()}.`}
            {' '}A missed window is skipped rather than replayed, so a machine that was
            asleep will not wake up and take a week of catch-up backups.
          </p>
        </div>
      )}

      {/* ─── Restore preview ─── */}
      {preview && (
        <div className="glass-card" style={{ padding: 16, borderColor: 'var(--accent-amber)' }}>
          <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 4 }}>
            Restore preview — {preview.backupId}
          </h3>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 10 }}>
            {preview.scopeDescription} Nothing has changed yet.
          </p>

          <div style={{ display: 'flex', gap: 16, marginBottom: 10, fontSize: 12 }}>
            <span><strong>{preview.summary.replace}</strong> replaced</span>
            <span><strong>{preview.summary.create}</strong> created</span>
            <span style={{ color: 'var(--text-muted)' }}>
              <strong>{preview.summary.identical}</strong> already identical
            </span>
          </div>

          {preview.serverWasRunning && (
            <div className="notice notice-warn" style={{ fontSize: 12, marginBottom: 10 }}>
              <AlertTriangle size={12} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
              This backup was taken while the server was running, so it may have caught
              files mid-autosave.
            </div>
          )}

          <div style={{ maxHeight: 200, overflowY: 'auto', overflowX: 'auto', marginBottom: 10 }}>
            <table className="table">
              <tbody>
                {preview.changes
                  .filter((c) => c.action !== 'identical')
                  .map((c) => (
                    <tr key={c.path}>
                      <td className="mono" style={{ fontSize: 11 }}>{c.path}</td>
                      <td style={{ fontSize: 11, color: 'var(--text-muted)', width: 90 }}>
                        {c.action}
                      </td>
                      <td className="mono" style={{ fontSize: 11, width: 90 }}>{bytes(c.size)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>

          {preview.keptUntouched.length > 0 && (
            <p style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 10 }}>
              {preview.keptUntouched.length} file(s) exist now but are not in this backup —
              for example players who joined afterwards. A restore leaves them alone rather
              than deleting them.
            </p>
          )}

          <div style={{ display: 'flex', gap: 8 }}>
            <button
              className="btn btn-primary"
              disabled={busy || serverProcessRunning}
              onClick={confirmRestore}
              title={serverProcessRunning ? 'Stop the server first' : undefined}
            >
              <RotateCcw size={13} /> Restore
            </button>
            <button className="btn btn-ghost" onClick={() => setPreview(null)} disabled={busy}>
              Cancel
            </button>
          </div>
          {serverProcessRunning && (
            <p style={{ fontSize: 11, color: 'var(--accent-amber)', marginTop: 8 }}>
              Restoring is blocked while the server may be running.
            </p>
          )}
        </div>
      )}

      {/* ─── Browser ─── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          {data?.usage?.count ?? 0} backups · {bytes(data?.usage?.totalBytes ?? 0)}
        </span>
        <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
          Restore scope
          <select
            className="input"
            style={{ padding: '3px 6px', fontSize: 12 }}
            value={scope}
            onChange={(e) => setScope(e.target.value)}
          >
            {Object.entries(data?.scopes ?? {}).map(([id, label]) => (
              <option key={id} value={id} title={label}>{id}</option>
            ))}
          </select>
        </label>
        <button
          className="btn btn-ghost"
          style={{ marginLeft: 'auto' }}
          disabled={busy}
          onClick={async () => {
            const result = await pruneBackups(true);
            if (!result.removed.length) {
              flash('Retention would remove nothing.');
              return;
            }
            if (window.confirm(
              `Retention would delete ${result.removed.length} backup(s), ` +
              `freeing ${bytes(result.freedBytes)}. Proceed?`
            )) {
              act('Retention applied', () => pruneBackups(false));
            }
          }}
        >
          <Trash2 size={13} /> Apply retention
        </button>
      </div>

      <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
        <table className="table">
          <thead>
            <tr>
              <th>When</th>
              <th>Description</th>
              <th>Reason</th>
              <th>Size</th>
              <th style={{ width: 180 }}></th>
            </tr>
          </thead>
          <tbody>
            {asArray(data?.backups, 'backups').map((b) => {
              const verdict = verifications[b.id];
              return (
                <tr key={b.id}>
                  <td style={{ fontSize: 12 }}>
                    {new Date(b.timestamp).toLocaleString()}
                    <div className="mono" style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                      {b.id}
                    </div>
                  </td>
                  <td style={{ fontSize: 12 }}>
                    {b.description || <span style={{ color: 'var(--text-muted)' }}>—</span>}
                    {verdict && (
                      <div style={{ fontSize: 11, marginTop: 3, color: verdict.ok ? '#4d9e75' : '#c25757' }}>
                        {verdict.ok
                          ? <><Check size={10} style={{ verticalAlign: '-1px' }} /> verified, {verdict.checkedFiles} files</>
                          : <><AlertTriangle size={10} style={{ verticalAlign: '-1px' }} /> {verdict.problems[0]}</>}
                      </div>
                    )}
                  </td>
                  <td style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    {triggerLabel(b.trigger)}
                    {b.serverWasRunning && (
                      <div style={{ color: 'var(--accent-amber)' }}>server was live</div>
                    )}
                  </td>
                  <td className="mono" style={{ fontSize: 11 }}>
                    {bytes(b.sizeBytes)}
                    <div style={{ color: 'var(--text-muted)', fontSize: 10 }}>
                      {b.fileCount} files
                    </div>
                  </td>
                  <td>
                    <div style={{ display: 'flex', gap: 3 }}>
                      <button className="btn btn-ghost" style={{ padding: '3px 6px' }}
                        disabled={busy} title="Verify checksums" onClick={() => runVerify(b.id)}>
                        <ShieldCheck size={12} />
                      </button>
                      <button className="btn btn-ghost" style={{ padding: '3px 6px' }}
                        disabled={busy} title="Preview a restore" onClick={() => openPreview(b.id)}>
                        <RotateCcw size={12} />
                      </button>
                      <a className="btn btn-ghost" style={{ padding: '3px 6px' }}
                        href={backupDownloadUrl(b.id)} title="Download this archive">
                        <Download size={12} />
                      </a>
                      <button className="btn btn-ghost" style={{ padding: '3px 6px' }}
                        disabled={busy} title="Rename"
                        onClick={() => {
                          const next = window.prompt('Description for this backup:', b.description);
                          if (next !== null) act('Renamed', () => renameBackup(b.id, next));
                        }}>
                        <Pencil size={12} />
                      </button>
                      <button className="btn btn-ghost"
                        style={{ padding: '3px 6px', color: '#c25757' }}
                        disabled={busy} title="Delete"
                        onClick={() => {
                          if (window.confirm(`Delete backup ${b.id}? This cannot be undone.`)) {
                            act('Deleted', () => deleteBackup(b.id));
                          }
                        }}>
                        <Trash2 size={12} />
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {!data?.backups.length && !busy && (
          <p style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
            <Archive size={16} style={{ display: 'block', margin: '0 auto 8px' }} />
            No backups yet.
          </p>
        )}
      </div>

      <p style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        Each backup is one verified archive: every file is checksummed, and verifying
        re-hashes the lot. A restore checks the archive before touching anything and
        leaves a rollback point behind, so a restore is itself reversible.
      </p>
    </div>
  );
}
