'use client';

import { useState } from 'react';
import { useDashboardStore } from '@/lib/store';
import {
  sortContainers, stopContainer, startContainer,
  type SortResult,
} from '@/lib/save-api';
import {
  Lock, Unlock, ShieldCheck, Play, Square, ArrowUpDown, PenLine,
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
    serverProcessRunning, backendOnline, serverState, capabilities,
  } = useDashboardStore();

  const [feedback, setFeedback] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<SortResult | null>(null);

  const has = (capability: string) => capabilities.includes(capability);
  const flash = (msg: string) => {
    setFeedback(msg);
    setTimeout(() => setFeedback(null), 8000);
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

      {/* Backups have their own tab now — one place to create, verify, preview
          and restore them, rather than a second half-featured copy here. */}
      {has(CAPABILITIES.BACKUP_MANAGE) && (
        <div className="notice" style={{ fontSize: 12 }}>
          Every operation above takes a full backup first. Browse, verify and
          restore them on the <strong>Backups</strong> tab.
        </div>
      )}
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
