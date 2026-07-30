'use client';

import { useEffect, useState } from 'react';
import { useDashboardStore } from '@/lib/store';
import {
  sortContainers, stopContainer, startContainer, getBackendHealth,
  type SortResult,
} from '@/lib/save-api';
import {
  Lock, Unlock, ShieldCheck, Play, Square, ArrowUpDown, PenLine, Target,
} from 'lucide-react';
import { CAPABILITIES } from '@/lib/permissions';
import CharacterEditor from './character-editor';
import BulkPalEditor from './bulk-pal-editor';
import SlotEditor from './slot-editor';
import PalCheck from './pal-check';
import PalImport from './pal-import';
import WorldExport from './world-export';

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
    serverProcessRunning, backendOnline, serverState, capabilities, bases,
  } = useDashboardStore();

  const [feedback, setFeedback] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<SortResult | null>(null);
  // "" means the whole world. Scoping to one base is the safer default habit on
  // a shared server, but the world-wide sort is what most single-guild servers
  // actually want, so neither is forced.
  const [scope, setScope] = useState('');
  // Whether the operator configured STOP_COMMAND / START_COMMAND. Both are off
  // by default, so the container buttons stay hidden unless they would work.
  const [canStopContainer, setCanStopContainer] = useState(false);
  const [canStartContainer, setCanStartContainer] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getBackendHealth()
      .then((h) => {
        if (cancelled) return;
        setCanStopContainer(Boolean(h.lifecycle?.stopSupported));
        setCanStartContainer(Boolean(h.lifecycle?.startSupported));
      })
      .catch(() => undefined);   // the offline notice below already covers this
    return () => { cancelled = true; };
  }, []);

  const has = (capability: string) => capabilities.includes(capability);
  const flash = (msg: string) => {
    setFeedback(msg);
    setTimeout(() => setFeedback(null), 8000);
  };

  const runSort = async (mode: 'stackables' | 'all') => {
    const label = mode === 'stackables' ? 'stackable items' : 'ALL items including equipment';
    const target = scope
      ? `the containers belonging to ${bases.find((b) => b.id === scope)?.name ?? 'that base'}`
      : 'every container in the world';

    if (!confirm(
      `Sort ${target}, affecting ${label}?\n\n` +
      'A full backup is taken first, and the result is verified against the original ' +
      'item totals. If anything does not add up, the world is rolled back automatically.'
    )) return;

    setBusy(mode);
    setLastResult(null);
    try {
      const result = await sortContainers(mode, true, scope || undefined);
      setLastResult(result);
      flash(
        `Sorted ${result.slotsChanged} slots across ${result.containersTouched} of ` +
        `${result.containersInScope} containers ` +
        `(${result.scope === 'base' ? 'this base only' : 'whole world'}). ` +
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

        {/* Shown only when the operator actually configured the commands.
            They need a `docker` binary the runtime image deliberately does not
            ship — see the README — so rendering them unconditionally meant two
            buttons that always failed. Nothing is lost by hiding them: stopping
            the container by hand is identical, and the safety check detects it
            either way. */}
        {(canStopContainer || canStartContainer) && (
          <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
            {canStopContainer && (
              <button
                className="btn btn-warning"
                disabled={busy !== null || !serverProcessRunning}
                onClick={() => containerAction('stop')}
                title={!serverProcessRunning ? 'The server is already stopped' : undefined}
              >
                <Square size={13} /> Stop server container
              </button>
            )}
            {canStartContainer && (
              <button
                className="btn"
                disabled={busy !== null || serverProcessRunning}
                onClick={() => containerAction('start')}
                title={serverProcessRunning ? 'The server is already running' : undefined}
              >
                <Play size={13} /> Start server container
              </button>
            )}
          </div>
        )}

        <details style={{ marginTop: 12 }} open={!canStopContainer}>
          <summary style={{ fontSize: 12, color: 'var(--text-muted)', cursor: 'pointer' }}>
            {canStopContainer
              ? 'Prefer to do it yourself?'
              : 'How to stop the server for editing'}
          </summary>
          <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 8, lineHeight: 1.7 }}>
            {canStopContainer
              ? 'The same thing the buttons above do:'
              : 'Run these yourself — the dashboard stays up either way, since it is a ' +
                'separate container, and it notices the server going down on its own:'}
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

      {/* ─── Sort scope ─── */}
      <div className="glass-card" style={{ padding: 16 }}>
        <div className="section-title" style={{ marginBottom: 10 }}>
          <Target size={14} /> What to sort
        </div>
        <select
          className="input"
          value={scope}
          onChange={(e) => setScope(e.target.value)}
          disabled={busy !== null}
          style={{ maxWidth: 420 }}
        >
          <option value="">Every container in the world</option>
          {bases.map((base) => (
            <option key={base.id} value={base.id}>
              {base.name} — {base.guildName}
              {base.containerIds.length ? ` (${base.containerIds.length} containers)` : ''}
            </option>
          ))}
        </select>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 8, lineHeight: 1.6 }}>
          {scope
            ? 'Only this base’s storage is rewritten. Every other chest in the world is left byte-for-byte alone — though the conservation check still covers all of them.'
            : 'Every container on the server, including other guilds’ bases and world chests. On a shared server, pick a single base instead.'}
        </p>
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

      {/* The illegal-Pal scan is a read, so it sits outside the SAVE_EDIT_FULL
          gate below — finding out whether anyone has been cheating must not
          require the capability to rewrite the world. */}
      {has(CAPABILITIES.VIEW_DETAIL) && <PalCheck canEdit={canEdit && has(CAPABILITIES.SAVE_EDIT_FULL)} />}

      {/* Not gated on `canEdit`. This is the one operation here that never writes
          to the live world — it reads it and produces a separate copy — so a
          running server is no reason to hide it. */}
      <WorldExport canManage={has(CAPABILITIES.BACKUP_MANAGE)} />

      {has(CAPABILITIES.SAVE_EDIT_FULL) ? (
        <>
          <CharacterEditor canEdit={canEdit} />
          <BulkPalEditor canEdit={canEdit} />
          <SlotEditor canEdit={canEdit} />
          <div className="glass-card" style={{ padding: 16 }}>
            <PalImport canEdit={canEdit} />
          </div>
        </>
      ) : (
        <div className="glass-card" style={{ padding: 16, opacity: 0.75 }}>
          <div className="section-title" style={{ marginBottom: 8 }}>
            <PenLine size={14} /> Character editor
            <span className="badge" style={{ marginLeft: 'auto' }}>Locked</span>
          </div>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.7 }}>
            Editing Pal and player levels, experience, condenser rank and IVs needs the{' '}
            <span className="mono">save.edit.full</span> capability, which exists only at
            security level <strong>full</strong>. Servers default to <strong>safe</strong>,
            so this stays hidden until someone deliberately raises it — even for an Owner.
            Set <span className="mono">SECURITY_LEVEL=full</span> in your{' '}
            <span className="mono">.env</span>, or raise it on the Access tab if the
            environment ceiling already permits it.
          </p>
        </div>
      )}

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
