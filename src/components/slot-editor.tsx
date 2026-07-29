'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Boxes, ShieldCheck, AlertTriangle, Lock, Trash2 } from 'lucide-react';
import {
  getBaseStorage, getContainerContents, getItemTotals, previewSlotEdit, applySlotEdit,
} from '@/lib/save-api';
import type {
  BaseStorage, BaseContainer, InventorySlot, SlotPatch, SlotEditPlan,
} from '@/lib/types';

/**
 * Inventory slot editor.
 *
 * Edits go through the same write path as a container import, so everything it
 * already guarantees applies unchanged: unknown item ids refused, stack counts
 * bounded by the real per-item ceiling, and — after writing — the target
 * container must match the plan while every other container in the world is
 * byte-for-byte what it was, or the whole thing rolls back.
 *
 * Slots holding an item with durability are locked rather than editable.
 * Overwriting one orphans its `DynamicItemSaveData` record and a replacement
 * cannot be fabricated, so the backend refuses those outright; showing them as
 * editable would only produce a rejection at preview time.
 */
export default function SlotEditor({ canEdit }: { canEdit: boolean }) {
  const [bases, setBases] = useState<BaseStorage[]>([]);
  const [containerId, setContainerId] = useState('');
  const [slots, setSlots] = useState<InventorySlot[]>([]);
  const [edits, setEdits] = useState<Record<number, SlotPatch>>({});
  const [knownItems, setKnownItems] = useState<{ id: string; name: string }[]>([]);
  const [plan, setPlan] = useState<SlotEditPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getBaseStorage(), getItemTotals()])
      .then(([storage, totals]) => {
        if (cancelled) return;
        setBases(storage);
        // Item ids actually present in this world, as autocomplete. Not the full
        // 2,466-item catalogue — the backend validates against that, and this is
        // only here so the common case does not need typing an internal id.
        setKnownItems(
          (totals.items ?? []).map((i) => ({ id: i.itemId, name: i.name }))
        );
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load containers');
      });
    return () => { cancelled = true; };
  }, []);

  const containers = useMemo(() => {
    const out: { container: BaseContainer; base: BaseStorage }[] = [];
    for (const base of bases) {
      for (const container of base.containers ?? []) out.push({ container, base });
    }
    return out;
  }, [bases]);

  const loadContainer = useCallback(async (id: string) => {
    setContainerId(id);
    setEdits({});
    setPlan(null);
    setError(null);
    setDone(null);
    if (!id) { setSlots([]); return; }

    setBusy(true);
    try {
      const contents = await getContainerContents(id);
      setSlots(contents.slots ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not read that container');
      setSlots([]);
    } finally {
      setBusy(false);
    }
  }, []);

  const patch = (index: number, next: Partial<SlotPatch>) => {
    setPlan(null);
    setEdits((current) => {
      const slot = slots.find((s) => s.slotIndex === index);
      const base: SlotPatch = current[index] ?? {
        slotIndex: index,
        itemId: slot?.isEmpty ? '' : (slot?.itemId ?? ''),
        stackCount: slot?.isEmpty ? 0 : (slot?.stackCount ?? 0),
      };
      return { ...current, [index]: { ...base, ...next } };
    });
  };

  const patches = useMemo(() => {
    // Only send slots that actually differ, so a touched-then-restored field
    // does not turn into a no-op write.
    return Object.values(edits).filter((p) => {
      const slot = slots.find((s) => s.slotIndex === p.slotIndex);
      if (!slot) return false;
      const wasId = slot.isEmpty ? '' : slot.itemId;
      const wasCount = slot.isEmpty ? 0 : slot.stackCount;
      return p.itemId !== wasId || p.stackCount !== wasCount;
    });
  }, [edits, slots]);

  const preview = async () => {
    setBusy(true); setError(null); setDone(null);
    try {
      setPlan(await previewSlotEdit(containerId, patches));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Preview failed');
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!plan?.ok || !plan.planHash) return;
    if (!confirm(
      `Write ${plan.slotsChanged} slot change(s) to this container?\n\n` +
      'A full backup is taken first. Afterwards the world is re-read and checked: ' +
      'this container must match exactly and every other container must be unchanged, ' +
      'or it rolls back automatically.'
    )) return;

    setBusy(true); setError(null);
    try {
      const result = await applySlotEdit(containerId, patches, plan.planHash);
      setDone(
        `Wrote ${result.slotsChanged} slot(s) and verified. Item total ` +
        `${result.itemsBefore.toLocaleString()} → ${result.itemsAfter.toLocaleString()}. ` +
        `Rollback point: ${result.backupId}.`
      );
      setPlan(null);
      await loadContainer(containerId);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Slot edit failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="glass-card" style={{ padding: 16 }}>
      <div className="section-title" style={{ marginBottom: 10 }}>
        <Boxes size={14} /> Inventory slot editor
        {patches.length > 0 && (
          <span className="badge" style={{ marginLeft: 'auto' }}>
            {patches.length} slot{patches.length === 1 ? '' : 's'} edited
          </span>
        )}
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

      <select
        className="input"
        style={{ width: '100%', maxWidth: 560, marginBottom: 12 }}
        value={containerId}
        onChange={(e) => loadContainer(e.target.value)}
        disabled={busy}
      >
        <option value="">Pick a container…</option>
        {containers.map(({ container, base }) => (
          <option key={container.containerId} value={container.containerId}>
            {base.baseName} — {container.kindName || container.kind}{' '}
            ({container.usedSlots}/{container.totalSlots} slots)
          </option>
        ))}
      </select>

      {containerId && slots.length > 0 && (
        <>
          <datalist id="slot-editor-items">
            {knownItems.map((i) => (
              <option key={i.id} value={i.id}>{i.name}</option>
            ))}
          </datalist>

          <div style={{
            maxHeight: 340, overflowY: 'auto',
            border: '1px solid var(--border-primary)', borderRadius: 6,
          }}>
            {slots.map((slot) => {
              const edit = edits[slot.slotIndex];
              const locked = slot.maxDurability > 0 || slot.durability > 0;
              return (
                <div
                  key={slot.slotIndex}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    padding: '5px 10px', fontSize: 12,
                    borderBottom: '1px solid var(--border-primary)',
                    opacity: locked ? 0.55 : 1,
                  }}
                >
                  <span className="mono" style={{ width: 30, color: 'var(--text-muted)' }}>
                    {slot.slotIndex}
                  </span>

                  {locked ? (
                    <>
                      <Lock size={12} style={{ color: 'var(--accent-amber)' }} />
                      <span style={{ flex: 1 }}>{slot.itemName || slot.itemId}</span>
                      <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                        has durability — cannot be overwritten without orphaning its record
                      </span>
                    </>
                  ) : (
                    <>
                      <input
                        className="input"
                        list="slot-editor-items"
                        style={{ flex: 1, minWidth: 120, fontSize: 12, padding: '3px 6px' }}
                        placeholder="(empty)"
                        value={edit ? edit.itemId : (slot.isEmpty ? '' : slot.itemId)}
                        onChange={(e) => patch(slot.slotIndex, { itemId: e.target.value })}
                      />
                      <input
                        className="input"
                        type="number"
                        min={0}
                        style={{ width: 84, fontSize: 12, padding: '3px 6px' }}
                        value={edit ? edit.stackCount : (slot.isEmpty ? 0 : slot.stackCount)}
                        onChange={(e) =>
                          patch(slot.slotIndex, { stackCount: e.target.valueAsNumber || 0 })
                        }
                      />
                      <button
                        className="btn btn-ghost"
                        style={{ padding: '2px 6px' }}
                        title="Clear this slot"
                        onClick={() => patch(slot.slotIndex, { itemId: '', stackCount: 0 })}
                      >
                        <Trash2 size={12} />
                      </button>
                    </>
                  )}
                </div>
              );
            })}
          </div>

          <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
            <button
              className="btn"
              disabled={busy || patches.length === 0}
              onClick={preview}
              title={patches.length === 0 ? 'Change a slot first' : undefined}
            >
              {busy ? 'Working…' : `Preview ${patches.length} change(s)`}
            </button>
            <button
              className="btn btn-ghost"
              disabled={busy || patches.length === 0}
              onClick={() => { setEdits({}); setPlan(null); }}
            >
              Discard edits
            </button>
          </div>

          {plan && (
            <div style={{ marginTop: 12 }}>
              {!plan.ok ? (
                <div className="notice notice-warn">
                  <strong>Cannot apply:</strong>
                  <ul style={{ margin: '6px 0 0 16px', fontSize: 12, lineHeight: 1.6 }}>
                    {plan.problems.map((p, i) => (
                      <li key={i}>
                        {p.slotIndex != null && <>Slot {p.slotIndex}: </>}{p.problem}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : (
                <div style={{
                  padding: 12, border: '1px solid var(--border-primary)',
                  borderRadius: 6, background: 'var(--bg-input)',
                }}>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 8 }}>
                    {plan.summary}
                  </div>
                  {plan.changes.map((c) => (
                    <div key={c.slotIndex} style={{ fontSize: 11, padding: '2px 0' }}>
                      <span className="mono" style={{ color: 'var(--text-muted)' }}>
                        slot {c.slotIndex}
                      </span>{' '}
                      {c.before.itemName || c.before.itemId || '(empty)'} ×{c.before.stackCount}
                      {' → '}
                      {c.after.itemName || c.after.itemId || '(empty)'} ×{c.after.stackCount}
                    </div>
                  ))}
                  <button
                    className="btn btn-primary"
                    style={{ marginTop: 10 }}
                    disabled={!canEdit || busy}
                    onClick={apply}
                    title={!canEdit ? 'The server must be stopped first' : undefined}
                  >
                    {busy ? 'Writing…' : `Write ${plan.slotsChanged} slot(s)`}
                  </button>
                </div>
              )}
            </div>
          )}
        </>
      )}

      {containerId && slots.length === 0 && !busy && (
        <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          That container decoded as empty.
        </p>
      )}
    </div>
  );
}
