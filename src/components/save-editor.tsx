'use client';

import { useState } from 'react';
import { useDashboardStore } from '@/lib/store';
import {
  createBackup, restoreBackup, getBackups,
  sortContainers, stopContainer, startContainer,
  type SortResult,
} from '@/lib/save-api';
import {
  Lock, Unlock, AlertTriangle, Save, RotateCcw, Download,
  Clock, ShieldCheck, Play, Square, ArrowUpDown, PenLine,
} from 'lucide-react';
import { CAPABILITIES } from '@/lib/permissions';

/**
 * Save Tools.
 *
 * Every write here is gated three ways: the server must be provably stopped,
 * the session must hold the specific capability, and the operation takes a full
 * backup and verifies itself afterwards (rolling back automatically if the
 * verification fails).
 */
export default function SaveEditor() {
  const {
    serverProcessRunning, backendOnline, backups, setBackups,
    serverState, capabilities,
  } = useDashboardStore();

  const [feedback, setFeedback] = useState<string | null>(null);
  const [backupDesc, setBackupDesc] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<SortResult | null>(null);

  const has = (capability: string) => capabilities.includes(capability);
  const flash = (msg: string) => {
    setFeedback(msg);
    setTimeout(() => setFeedback(null), 8000);
  };

  const refreshBackups = async () => {
    try {
      setBackups(await getBackups());
    } catch { /* ignore */ }
  };

  const handleBackup = async () => {
    setBusy('backup');
    try {
      await createBackup(backupDesc || undefined);
      flash('Backup created.');
      setBackupDesc('');
      await refreshBackups();
    } catch (e) {
      flash(e instanceof Error ? e.message : 'Backup failed');
    } finally {
      setBusy(null);
    }
  };

  const handleRestore = async (id: string) => {
    if (!confirm('Restore this backup? The current world will be replaced (a snapshot of it is taken first).')) return;
    setBusy('restore');
    try {
      await restoreBackup(id);
      flash('Backup restored.');
    } catch (e) {
      flash(e instanceof Error ? e.message : 'Restore failed');
    } finally {
      setBusy(null);
    }
  };

  const runSort = async (mode: 'stackables' | 'all') => {
    const label = mode === 'stackables' ? 'stackable items' : 'ALL items including equipment';
    if (!confirm(
      `Sort every container, affecting ${label}?\n\n` +
      'A full backup is taken first, and the result is verified against the original ' +
      'item totals. If anything does not add up, the world is rolled back automatically.'
    )) return;

    setBusy(mode);
    setLastResult(null);
    try {
      const result = await sortContainers(mode, true);
      setLastResult(result);
      flash(
        `Sorted ${result.slotsChanged} slots across ${result.containersTouched} containers. ` +
        `Verified. Rollback point: ${result.backupId}.`
      );
      await refreshBackups();
    } catch (e) {
      flash(e instanceof Error ? e.message : 'Sort failed');
    } finally {
      setBusy(null);
    }
  };

  const containerAction = async (action: 'stop' | 'start') => {
    setBusy(action);
    try {
      await (action === 'stop' ? stopContainer() : startContainer());
      flash(action === 'stop'
        ? 'Stop command sent. Waiting for the server to go down…'
        : 'Start command sent.');
    } catch (e) {
      flash(e instanceof Error ? e.message : `Could not ${action} the container`);
    } finally {
      setBusy(null);
    }
  };

  if (!backendOnline) {
    return (
      <div className="notice notice-warn">
        <strong>Save backend offline.</strong> The Python backend must be running
        to use save tools.
      </div>
    );
  }

  const canEdit = !serverProcessRunning;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {feedback && <div className="notice">{feedback}</div>}

      {/* ─── Maintenance mode ─── */}
      <div className="glass-card" style={{ padding: 16 }}>
        <div className="section-title" style={{ marginBottom: 10 }}>
          {canEdit ? <Unlock size={14} style={{ color: 'var(--accent-emerald)' }} />
                   : <Lock size={14} style={{ color: 'var(--accent-amber)' }} />}
          Maintenance mode
        </div>

        <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
          {canEdit
            ? 'The server is stopped and editing is unlocked. Make your changes, then start it again.'
            : 'The server is running, so all save writes are blocked. Stop it to unlock editing.'}
        </p>

        {serverState && (
          <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 6 }}>
            {serverState.reason}
          </p>
        )}

        <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
          <button
            className="btn btn-warning"
            disabled={busy !== null || !serverProcessRunning}
            onClick={() => containerAction('stop')}
            title={!serverProcessRunning ? 'The server is already stopped' : undefined}
          >
            <Square size={13} /> Stop server container
          </button>
          <button
            className="btn"
            disabled={busy !== null || serverProcessRunning}
            onClick={() => containerAction('start')}
            title={serverProcessRunning ? 'The server is already running' : undefined}
          >
            <Play size={13} /> Start server container
          </button>
        </div>

        <details style={{ marginTop: 12 }}>
          <summary style={{ fontSize: 12, color: 'var(--text-muted)', cursor: 'pointer' }}>
            No container controls configured? Do it manually
          </summary>
          <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 8, lineHeight: 1.7 }}>
            The buttons above need <span className="mono">STOP_COMMAND</span> /{' '}
            <span className="mono">START_COMMAND</span> (see the README). Without
            them, run these yourself — the dashboard stays up either way, since it
            is a separate container:
          </p>
          <pre className="mono" style={{
            fontSize: 12, marginTop: 8, padding: 10, lineHeight: 1.6,
            background: 'var(--bg-input)', border: '1px solid var(--border-primary)',
            borderRadius: 'var(--radius)', overflowX: 'auto',
          }}>
{`docker compose stop palworld     # unlocks editing here
# ... make your changes ...
docker compose start palworld    # bring it back`}
          </pre>
        </details>
      </div>

      {/* ─── Container sorting ─── */}
      <div className="dashboard-grid grid-2">
        <OperationCard
          icon={<ArrowUpDown size={14} />}
          title="Sort chests — stackables only"
          description="Tidies and merges plain stackable items (ore, wood, food). Anything with durability — weapons, armour, tools — is left exactly where it is, so nothing can be orphaned."
          badge="Safest"
          allowed={has(CAPABILITIES.SAVE_SORT_STACKABLES)}
          canEdit={canEdit}
          busy={busy === 'stackables'}
          onRun={() => runSort('stackables')}
        />

        <OperationCard
          icon={<ArrowUpDown size={14} />}
          title="Sort chests — all items"
          description="Also relocates equipment and durability items, carrying their internal references along. More thorough, and touches more of the save."
          badge="Higher risk"
          allowed={has(CAPABILITIES.SAVE_SORT_ALL)}
          canEdit={canEdit}
          busy={busy === 'all'}
          onRun={() => runSort('all')}
        />
      </div>

      <OperationCard
        icon={<PenLine size={14} />}
        title="Full editor — players, Pals, individual slots"
        description="Arbitrary edits to player levels, technology points, Pal IVs and passives, and individual container slots. Not implemented yet: the write path is proven, but each field needs validating before it can be exposed."
        badge="Not implemented"
        allowed={has(CAPABILITIES.SAVE_EDIT_FULL)}
        canEdit={canEdit}
        busy={false}
        disabledReason="Coming later — sorting uses the same verified write path."
        onRun={() => undefined}
      />

      {lastResult && (
        <div className="notice">
          <ShieldCheck size={14} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 6 }} />
          Last operation verified: item totals matched before and after, checked
          again after re-reading the file from disk. Rollback point{' '}
          <span className="mono">{lastResult.backupId}</span>.
        </div>
      )}

      <BackupSection
        backups={backups}
        onRestore={handleRestore}
        onBackup={handleBackup}
        backupDesc={backupDesc}
        setBackupDesc={setBackupDesc}
        busy={busy !== null}
        allowed={has(CAPABILITIES.BACKUP_MANAGE)}
      />
    </div>
  );
}

function OperationCard({
  icon, title, description, badge, allowed, canEdit, busy, onRun, disabledReason,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  badge: string;
  allowed: boolean;
  canEdit: boolean;
  busy: boolean;
  onRun: () => void;
  disabledReason?: string;
}) {
  const blocked = !allowed
    ? 'You do not have permission for this operation.'
    : !canEdit
      ? 'The server must be stopped first.'
      : disabledReason;

  return (
    <div className="glass-card" style={{ padding: 16, opacity: allowed ? 1 : 0.6 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span className="section-title">{icon} {title}</span>
        <span className="badge" style={{ marginLeft: 'auto' }}>{badge}</span>
      </div>
      <p style={{ fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.6, minHeight: 54 }}>
        {description}
      </p>
      <button
        className="btn btn-primary"
        style={{ marginTop: 10 }}
        disabled={!!blocked || busy}
        onClick={onRun}
        title={blocked}
      >
        {busy ? 'Working…' : 'Run'}
      </button>
      {blocked && (
        <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 8 }}>{blocked}</p>
      )}
    </div>
  );
}

function BackupSection({
  backups, onRestore, onBackup, backupDesc, setBackupDesc, busy, allowed,
}: {
  backups: { id: string; timestamp: string; sizeBytes: number; description: string }[];
  onRestore: (id: string) => void;
  onBackup: () => void;
  backupDesc: string;
  setBackupDesc: (v: string) => void;
  busy: boolean;
  allowed: boolean;
}) {
  return (
    <div className="glass-card" style={{ padding: 16 }}>
      <div className="section-title" style={{ marginBottom: 12 }}>
        <Save size={14} /> Backups
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <input
          className="input"
          value={backupDesc}
          onChange={(e) => setBackupDesc(e.target.value)}
          placeholder="Description (optional)"
          disabled={!allowed}
        />
        <button className="btn btn-primary" onClick={onBackup} disabled={busy || !allowed}>
          <Download size={13} /> Create
        </button>
      </div>

      {backups.length === 0 ? (
        <p style={{ color: 'var(--text-muted)', fontSize: 13, textAlign: 'center', padding: 16 }}>
          No backups yet. Every save-modifying operation creates one automatically.
        </p>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {backups.map((b) => (
            <div
              key={b.id}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '9px 12px', background: 'var(--bg-input)',
                borderRadius: 'var(--radius)', border: '1px solid var(--border-primary)',
              }}
            >
              <div>
                <div style={{ fontSize: 13 }}>{b.description || `Backup ${b.id}`}</div>
                <div style={{ fontSize: 11, color: 'var(--text-muted)', display: 'flex', gap: 12, marginTop: 2 }}>
                  <span><Clock size={10} style={{ display: 'inline', verticalAlign: '-1px' }} /> {new Date(b.timestamp).toLocaleString()}</span>
                  <span className="mono">{(b.sizeBytes / 1024 / 1024).toFixed(1)} MB</span>
                </div>
              </div>
              <button
                className="btn btn-ghost"
                style={{ padding: '3px 9px', fontSize: 11 }}
                onClick={() => onRestore(b.id)}
                disabled={busy || !allowed}
              >
                <RotateCcw size={11} /> Restore
              </button>
            </div>
          ))}
        </div>
      )}

      <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 12, display: 'flex', gap: 6 }}>
        <AlertTriangle size={12} style={{ flexShrink: 0, marginTop: 2 }} />
        Backups taken while the server is running are best-effort snapshots — files
        may be mid-autosave. Stop the server for a guaranteed-clean one.
      </p>
    </div>
  );
}
