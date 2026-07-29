'use client';

import { useEffect, useMemo, useState } from 'react';
import { Layers, Search, ShieldCheck, AlertTriangle, CheckSquare, Square } from 'lucide-react';
import {
  getEditSchema, getPals, previewBulkPalEdit, applyBulkPalEdit, type PalRecord,
} from '@/lib/save-api';
import type { EditSchema, BulkEditPlan } from '@/lib/types';

/** Fields worth offering across many Pals at once. */
const BULK_FIELDS = ['level', 'rank', 'ivs.hp', 'ivs.shot', 'ivs.defense'] as const;

/**
 * Bulk Pal editor.
 *
 * One change set, many Pals, one backup, all-or-nothing. The batch is atomic on
 * the backend: every Pal is validated before anything is written, and a
 * verification failure on any one of them rolls the whole world back. A batch
 * that half-applies leaves no record of where it stopped, which is worse than
 * one that refuses outright — so this UI never offers a "continue anyway".
 */
export default function BulkPalEditor({ canEdit }: { canEdit: boolean }) {
  const [schema, setSchema] = useState<EditSchema | null>(null);
  const [pals, setPals] = useState<PalRecord[]>([]);
  const [search, setSearch] = useState('');
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [fields, setFields] = useState<Record<string, number>>({});
  const [autoExp, setAutoExp] = useState(true);
  const [plan, setPlan] = useState<BulkEditPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getEditSchema('pal'), getPals()])
      .then(([s, p]) => {
        if (cancelled) return;
        setSchema(s);
        setPals(p);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load Pals');
      });
    return () => { cancelled = true; };
  }, []);

  const matches = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return pals.slice(0, 200);
    return pals
      .filter((p) =>
        (p.nickname || '').toLowerCase().includes(q) ||
        (p.speciesName || p.speciesId).toLowerCase().includes(q)
      )
      .slice(0, 200);
  }, [pals, search]);

  const offered = useMemo(
    () => (schema?.fields ?? []).filter((f) => (BULK_FIELDS as readonly string[]).includes(f.name)),
    [schema]
  );

  const changes = useMemo(() => {
    const out: Record<string, number> = {};
    for (const [key, value] of Object.entries(fields)) {
      if (Number.isFinite(value)) out[key] = value;
    }
    return out;
  }, [fields]);

  const toggle = (id: string) => {
    setPlan(null);
    setPicked((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  /** Select every Pal the current search shows — not every Pal in the world. */
  const selectMatches = () => {
    setPlan(null);
    setPicked(new Set(matches.map((p) => p.instanceId)));
  };

  const ids = useMemo(() => [...picked], [picked]);

  const preview = async () => {
    setBusy(true); setError(null); setDone(null);
    try {
      setPlan(await previewBulkPalEdit(ids, changes, autoExp));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Preview failed');
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!plan?.ok || !plan.planHash) return;
    if (!confirm(
      `Apply these changes to ${plan.palsChanged} Pal(s)?\n\n` +
      'A full backup is taken first. Every Pal is verified after the write — if any ' +
      'one of them does not read back correctly the whole world is rolled back, so ' +
      'this cannot land half-applied.'
    )) return;

    setBusy(true); setError(null);
    try {
      const result = await applyBulkPalEdit(ids, changes, plan.planHash, autoExp);
      setDone(
        `Edited ${result.palsChanged} Pal(s), ${result.fieldsChanged} field(s), verified. ` +
        `Rollback point: ${result.backupId}.`
      );
      setPlan(null);
      setPicked(new Set());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Bulk edit failed');
    } finally {
      setBusy(false);
    }
  };

  if (error && !schema) return <div className="notice notice-warn">{error}</div>;
  if (!schema) return <div className="notice">Loading…</div>;

  return (
    <div className="glass-card" style={{ padding: 16 }}>
      <div className="section-title" style={{ marginBottom: 10 }}>
        <Layers size={14} /> Bulk Pal editor
        <span className="badge" style={{ marginLeft: 'auto' }}>
          {picked.size} selected
        </span>
      </div>

      {!canEdit && (
        <div className="notice notice-warn" style={{ marginBottom: 12 }}>
          <AlertTriangle size={13} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 6 }} />
          The server must be stopped before anything can be written. Previewing still works.
        </div>
      )}
      {error && <div className="notice notice-warn" style={{ marginBottom: 12 }}>{error}</div>}
      {done && (
        <div className="notice" style={{ marginBottom: 12 }}>
          <ShieldCheck size={13} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 6 }} />
          {done}
        </div>
      )}

      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        {/* ─── Who ─── */}
        <div style={{ flex: '1 1 280px', minWidth: 250 }}>
          <div style={{ position: 'relative', marginBottom: 8 }}>
            <Search size={13} style={{ position: 'absolute', left: 8, top: 9, color: 'var(--text-muted)' }} />
            <input
              className="input"
              style={{ paddingLeft: 26, width: '100%' }}
              placeholder={`Filter ${pals.length} Pals by name or species…`}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <div style={{ display: 'flex', gap: 6, marginBottom: 8, flexWrap: 'wrap' }}>
            <button className="btn btn-ghost" style={{ padding: '2px 8px', fontSize: 11 }}
                    onClick={selectMatches}>
              Select these {matches.length}
            </button>
            <button className="btn btn-ghost" style={{ padding: '2px 8px', fontSize: 11 }}
                    onClick={() => { setPicked(new Set()); setPlan(null); }}>
              Clear
            </button>
          </div>

          <div style={{
            maxHeight: 300, overflowY: 'auto',
            border: '1px solid var(--border-primary)', borderRadius: 6,
          }}>
            {matches.map((p) => {
              const on = picked.has(p.instanceId);
              return (
                <button
                  key={p.instanceId}
                  onClick={() => toggle(p.instanceId)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 8, width: '100%',
                    textAlign: 'left', padding: '6px 10px', fontSize: 12, cursor: 'pointer',
                    background: on ? 'var(--bg-input)' : 'transparent',
                    border: 'none', borderBottom: '1px solid var(--border-primary)',
                    color: 'var(--text-primary)',
                  }}
                >
                  {on ? <CheckSquare size={13} style={{ color: 'var(--accent-emerald)' }} />
                      : <Square size={13} style={{ color: 'var(--text-muted)' }} />}
                  <span style={{ fontWeight: 500 }}>
                    {p.nickname || p.speciesName || p.speciesId}
                  </span>
                  <span style={{ color: 'var(--text-muted)', marginLeft: 'auto' }}>
                    Lv {p.level} · rank {p.rank}
                  </span>
                </button>
              );
            })}
          </div>
          {matches.length === 200 && (
            <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>
              Showing the first 200. Narrow the filter to reach the rest.
            </p>
          )}
        </div>

        {/* ─── What ─── */}
        <div style={{ flex: '1 1 280px', minWidth: 260 }}>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 10, lineHeight: 1.6 }}>
            Leave a field blank to leave it alone. Every selected Pal gets the same value.
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {offered.map((field) => (
              <div key={field.name}>
                <label style={{ display: 'block', fontSize: 11, color: 'var(--text-muted)', marginBottom: 3 }}>
                  {field.label} {field.max != null && <span>({field.min}–{field.max})</span>}
                </label>
                <input
                  className="input"
                  style={{ width: '100%' }}
                  type="number"
                  min={field.min ?? undefined}
                  max={field.max ?? undefined}
                  placeholder="unchanged"
                  value={fields[field.name] ?? ''}
                  onChange={(e) => {
                    setPlan(null);
                    setFields((f) => {
                      const next = { ...f };
                      if (e.target.value === '') delete next[field.name];
                      else next[field.name] = e.target.valueAsNumber;
                      return next;
                    });
                  }}
                />
              </div>
            ))}
          </div>

          <label style={{
            display: 'flex', alignItems: 'flex-start', gap: 8, marginTop: 12,
            fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.5, cursor: 'pointer',
          }}>
            <input
              type="checkbox"
              checked={autoExp}
              onChange={(e) => { setAutoExp(e.target.checked); setPlan(null); }}
              style={{ marginTop: 2 }}
            />
            <span>
              Move EXP to match the new level. Without this a level change leaves each
              Pal on its old EXP, so the next battle it wins drags it back down.
            </span>
          </label>

          <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
            <button
              className="btn"
              disabled={busy || picked.size === 0 || Object.keys(changes).length === 0}
              onClick={preview}
              title={
                picked.size === 0 ? 'Select at least one Pal'
                  : Object.keys(changes).length === 0 ? 'Set at least one field'
                    : undefined
              }
            >
              {busy ? 'Working…' : `Preview ${picked.size} Pal(s)`}
            </button>
          </div>

          {plan && <BulkPlanView plan={plan} canEdit={canEdit} busy={busy} onApply={apply} />}
        </div>
      </div>
    </div>
  );
}

function BulkPlanView({
  plan, canEdit, busy, onApply,
}: {
  plan: BulkEditPlan; canEdit: boolean; busy: boolean; onApply: () => void;
}) {
  if (!plan.ok) {
    return (
      <div className="notice notice-warn" style={{ marginTop: 12 }}>
        <strong>Nothing would be applied.</strong> One rejected Pal refuses the whole
        batch — a partial bulk edit leaves no record of where it stopped.
        <ul style={{ margin: '6px 0 0 16px', fontSize: 11, lineHeight: 1.6 }}>
          {plan.problems.slice(0, 6).map((p, i) => (
            <li key={i}>
              {p.instanceId && <span className="mono">{p.instanceId.slice(0, 8)}…</span>}{' '}
              {p.problem}
            </li>
          ))}
          {plan.problems.length > 6 && <li>…and {plan.problems.length - 6} more</li>}
        </ul>
      </div>
    );
  }

  if (!plan.palsChanged) {
    return (
      <div className="notice" style={{ marginTop: 12, fontSize: 12 }}>
        Nothing to do — every selected Pal already has those values.
      </div>
    );
  }

  return (
    <div style={{
      marginTop: 12, padding: 12,
      border: '1px solid var(--border-primary)', borderRadius: 6, background: 'var(--bg-input)',
    }}>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 8 }}>
        {plan.palsChanged} Pal(s) would change, {plan.fieldsChanged} field(s) in total
        {plan.palsUnchanged ? `; ${plan.palsUnchanged} already match and are skipped` : ''}.
      </div>

      <div style={{ maxHeight: 160, overflowY: 'auto', fontSize: 11 }}>
        {plan.pals.slice(0, 30).map((p) => (
          <div key={p.instanceId} style={{ padding: '3px 0', borderBottom: '1px solid var(--border-primary)' }}>
            <span style={{ fontWeight: 500 }}>{p.nickname || p.instanceId.slice(0, 8)}</span>
            <span style={{ color: 'var(--text-muted)', marginLeft: 8 }}>
              {p.changes.map((c) => `${c.label} ${String(c.before)} → ${String(c.after)}`).join(', ')}
            </span>
          </div>
        ))}
        {plan.pals.length > 30 && (
          <p style={{ color: 'var(--text-muted)', paddingTop: 6 }}>
            …and {plan.pals.length - 30} more.
          </p>
        )}
      </div>

      <button
        className="btn btn-primary"
        style={{ marginTop: 10 }}
        disabled={!canEdit || busy}
        onClick={onApply}
        title={!canEdit ? 'The server must be stopped first' : undefined}
      >
        {busy ? 'Writing…' : `Apply to ${plan.palsChanged} Pal(s)`}
      </button>
    </div>
  );
}
