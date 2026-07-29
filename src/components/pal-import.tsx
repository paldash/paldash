'use client';

import { useEffect, useState } from 'react';
import { Upload, ShieldCheck, AlertTriangle, EyeOff } from 'lucide-react';
import {
  previewPalImport, applyPalImport, getPalContainers,
} from '@/lib/save-api';
import type { PalContainer, PalImportPlan, IgnoredField } from '@/lib/types';

type Mode = 'overwrite' | 'create';

/**
 * Pal import from a JSON export.
 *
 * The file is a `saveexport` envelope of kind `pal` or `player` — the same thing
 * the export button produces. It is sent through byte-for-byte: the envelope's
 * checksum covers its payload, so anything this component reformatted would fail
 * verification server-side.
 *
 * Two modes, because they are genuinely different operations:
 *
 *   overwrite  writes the document's fields onto Pals already in the world,
 *              matched by instance id. Re-importing this world's own export is
 *              therefore a restore.
 *   create     adds a Pal, by copying a same-species record already in the save
 *              and then applying the document's fields.
 *
 * The `ignored` list is shown rather than hidden. An export says more than an
 * import may write — owner, container, slot — and quietly dropping those would
 * let someone believe a Pal changed hands when it did not.
 */
export default function PalImport({ canEdit }: { canEdit: boolean }) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<Mode>('overwrite');
  const [document_, setDocument] = useState<unknown>(null);
  const [fileName, setFileName] = useState('');
  const [containers, setContainers] = useState<PalContainer[]>([]);
  const [containerId, setContainerId] = useState('');
  const [plan, setPlan] = useState<PalImportPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    if (mode !== 'create' || containers.length) return;
    getPalContainers()
      .then((r) => setContainers(r.containers))
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : 'Could not list containers')
      );
  }, [mode, containers.length]);

  const readFile = async (file: File) => {
    setError(null); setDone(null); setPlan(null);
    try {
      const parsed = JSON.parse(await file.text());
      setDocument(parsed);
      setFileName(file.name);
      // A `pal`/`player` export names its own kind, so the obvious mode can be
      // suggested — but never forced, since "restore this team" and "add a copy
      // of this Pal" are both legitimate readings of the same file.
      if (parsed?.kind === 'pal' || parsed?.kind === 'player') {
        setMode('overwrite');
      } else {
        setError(
          `This is a '${parsed?.kind ?? 'unknown'}' export. Pal imports need a 'pal' or ` +
          "'player' export; inventory goes through the slot editor."
        );
      }
    } catch {
      setError('That file is not valid JSON.');
      setDocument(null);
      setFileName('');
    }
  };

  const preview = async () => {
    if (!document_) return;
    setBusy(true); setError(null); setDone(null);
    try {
      setPlan(await previewPalImport(document_, mode, { containerId }));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Preview failed');
      setPlan(null);
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!document_ || !plan?.ok || !plan.planHash) return;

    const what = mode === 'create'
      ? `Add 1 ${plan.speciesId ?? 'Pal'} to the chosen container?\n\n` +
        'This copies an existing Pal of the same species and then applies the ' +
        "document's level, stars, skills and passives. The character list and the " +
        'target container must both grow by exactly one, and no other container may ' +
        'change — anything else rolls back.'
      : `Overwrite ${plan.palsChanged ?? 0} Pal(s) with the values in this file?\n\n` +
        'A full backup is taken first. The batch is all-or-nothing: if any Pal does ' +
        'not read back correctly the whole world is rolled back.';
    if (!confirm(what)) return;

    setBusy(true); setError(null);
    try {
      const result = await applyPalImport(document_, mode, plan.planHash, {
        containerId,
        templateInstanceId: plan.templateInstanceId,
      });
      setDone(
        result.mode === 'create'
          ? `Added 1 Pal in slot ${(result.slotIndices ?? []).join(', ')} and verified. ` +
            `Rollback point: ${result.backupId}.`
          : `Updated ${result.palsChanged ?? 0} Pal(s), ${result.fieldsChanged ?? 0} field(s), ` +
            `and verified. Rollback point: ${result.backupId}.`
      );
      setPlan(null);
      setContainers([]);       // capacities moved
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Import failed');
    } finally {
      setBusy(false);
    }
  };

  if (!canEdit) return null;

  if (!open) {
    return (
      <button
        className="btn btn-ghost"
        style={{ fontSize: 11, padding: '3px 10px' }}
        onClick={() => setOpen(true)}
      >
        <Upload size={12} /> Import a Pal from JSON
      </button>
    );
  }

  return (
    <div style={{
      padding: 12, border: '1px solid var(--border-primary)', borderRadius: 6,
    }}>
      <div className="section-title" style={{ marginBottom: 8, fontSize: 12 }}>
        <Upload size={12} /> Import a Pal
        <button
          className="btn btn-ghost"
          style={{ marginLeft: 'auto', padding: '1px 8px', fontSize: 10 }}
          onClick={() => { setOpen(false); setPlan(null); setDocument(null); setFileName(''); }}
        >
          Close
        </button>
      </div>

      {error && (
        <div className="notice notice-warn" style={{ marginBottom: 8 }}>
          <AlertTriangle size={12} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
          {error}
        </div>
      )}
      {done && (
        <div className="notice" style={{ marginBottom: 8 }}>
          <ShieldCheck size={12} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
          {done}
        </div>
      )}

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <div style={{ flex: '1 1 220px' }}>
          <label style={{ display: 'block', fontSize: 11, color: 'var(--text-muted)', marginBottom: 3 }}>
            Export file (.json)
          </label>
          <input
            className="input"
            style={{ width: '100%' }}
            type="file"
            accept="application/json,.json"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void readFile(file);
            }}
          />
        </div>

        <div style={{ width: 150 }}>
          <label style={{ display: 'block', fontSize: 11, color: 'var(--text-muted)', marginBottom: 3 }}>
            Mode
          </label>
          <select
            className="input"
            style={{ width: '100%' }}
            value={mode}
            onChange={(e) => { setMode(e.target.value as Mode); setPlan(null); }}
          >
            <option value="overwrite">Overwrite existing</option>
            <option value="create">Add as a new Pal</option>
          </select>
        </div>

        {mode === 'create' && (
          <div style={{ flex: '1 1 240px' }}>
            <label style={{ display: 'block', fontSize: 11, color: 'var(--text-muted)', marginBottom: 3 }}>
              Destination
            </label>
            <select
              className="input"
              style={{ width: '100%' }}
              value={containerId}
              onChange={(e) => { setContainerId(e.target.value); setPlan(null); }}
            >
              <option value="">Pick a container…</option>
              {containers.map((c) => (
                <option key={c.containerId} value={c.containerId} disabled={c.free === 0}>
                  {c.containerId.slice(0, 8)}… — {c.used}/{c.capacity} used, {c.free} free
                </option>
              ))}
            </select>
          </div>
        )}

        <button
          className="btn"
          disabled={busy || !document_ || (mode === 'create' && !containerId)}
          onClick={() => void preview()}
        >
          Preview
        </button>
      </div>

      {fileName && (
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>
          {fileName}
        </div>
      )}

      {plan && <PlanSummary plan={plan} mode={mode} busy={busy} onApply={() => void apply()} />}
    </div>
  );
}

function PlanSummary({
  plan, mode, busy, onApply,
}: {
  plan: PalImportPlan;
  mode: Mode;
  busy: boolean;
  onApply: () => void;
}) {
  if (!plan.ok) {
    return (
      <div className="notice notice-warn" style={{ marginTop: 10 }}>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>Nothing applied</div>
        <ul style={{ margin: 0, paddingLeft: 16, fontSize: 12 }}>
          {(plan.problems ?? []).slice(0, 8).map((p, i) => (
            <li key={i}>{p.field ? `${p.field}: ` : ''}{p.problem}</li>
          ))}
        </ul>
      </div>
    );
  }

  return (
    <div style={{ marginTop: 10 }}>
      {mode === 'create' ? (
        <div style={{ fontSize: 12, marginBottom: 6 }}>
          Will add <strong>{plan.speciesId}</strong> in slot{' '}
          {(plan.slotIndices ?? []).join(', ')}, copied from an existing{' '}
          {plan.speciesId} in this world.
        </div>
      ) : (
        <div style={{ fontSize: 12, marginBottom: 6 }}>
          Will change <strong>{plan.palsChanged ?? 0}</strong> Pal(s),{' '}
          {plan.fieldsChanged ?? 0} field(s).
          {(plan.palsUnchanged ?? 0) > 0 && (
            <span style={{ color: 'var(--text-muted)' }}>
              {' '}{plan.palsUnchanged} already match and will be skipped.
            </span>
          )}
        </div>
      )}

      {(plan.pals ?? []).slice(0, 12).map((pal) => (
        <div key={pal.instanceId} style={{ fontSize: 11, marginBottom: 3 }}>
          <span style={{ color: 'var(--text-muted)' }}>
            {pal.nickname || pal.instanceId.slice(0, 8)}:{' '}
          </span>
          {pal.changes
            .map((c) => `${c.label || c.field} ${JSON.stringify(c.before)} → ${JSON.stringify(c.after)}`)
            .join(', ')}
        </div>
      ))}
      {(plan.pals ?? []).length > 12 && (
        <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          …and {(plan.pals ?? []).length - 12} more.
        </div>
      )}

      <IgnoredList ignored={plan.ignored ?? []} />

      <button className="btn" style={{ marginTop: 10 }} disabled={busy} onClick={onApply}>
        {busy ? 'Working…' : 'Apply import'}
      </button>
    </div>
  );
}

/**
 * The fields the file carried that will not be written.
 *
 * Deliberately visible before the apply button. The alternative — dropping them
 * silently — is how someone ends up believing an imported Pal changed owner.
 */
function IgnoredList({ ignored }: { ignored: IgnoredField[] }) {
  const [expanded, setExpanded] = useState(false);
  if (!ignored.length) return null;

  // One line per distinct field, not per Pal: a 40-Pal team import would
  // otherwise repeat the same seven reasons forty times.
  const byField = new Map<string, string>();
  for (const entry of ignored) {
    if (entry.field && !byField.has(entry.field)) byField.set(entry.field, entry.problem);
  }

  return (
    <div style={{ marginTop: 8, fontSize: 11 }}>
      <button
        className="btn btn-ghost"
        style={{ padding: '1px 8px', fontSize: 10 }}
        onClick={() => setExpanded(!expanded)}
      >
        <EyeOff size={11} /> {byField.size} field(s) in this file will not be written
      </button>
      {expanded && (
        <ul style={{ margin: '6px 0 0', paddingLeft: 16, color: 'var(--text-muted)' }}>
          {[...byField].map(([field, problem]) => (
            <li key={field}><code>{field}</code> — {problem}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
