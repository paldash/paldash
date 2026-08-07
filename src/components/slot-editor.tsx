'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Boxes, ShieldCheck, AlertTriangle, Lock, Trash2, Search, Sparkles } from 'lucide-react';
import ItemCreator from '@/components/item-creator';
import GameIcon from '@/components/game-icon';
import {
  getBaseStorage, getContainerContents, getItemCatalogue, previewSlotEdit, applySlotEdit,
  getSavePlayers, getPlayerContainers, type PlayerContainer,
} from '@/lib/save-api';
import type {
  BaseStorage, BaseContainer, CatalogueItem, InventorySlot, SlotPatch, SlotEditPlan,
  PlayerSaveData,
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
  // Base storage and player inventories are the same kind of thing to the
  // writer — an item container — so they share one editor rather than two that
  // can drift. Only the picker differs.
  const [source, setSource] = useState<'base' | 'player'>('base');
  const [players, setPlayers] = useState<PlayerSaveData[]>([]);
  const [playerUid, setPlayerUid] = useState('');
  const [playerContainers, setPlayerContainers] = useState<PlayerContainer[]>([]);
  const [bases, setBases] = useState<BaseStorage[]>([]);
  const [baseId, setBaseId] = useState('');
  const [containerId, setContainerId] = useState('');
  const [slots, setSlots] = useState<InventorySlot[]>([]);
  // Which empty slot the creator is open on. Null when closed; only one at a
  // time, because creating is a deliberate act and a column of open panels
  // would invite treating it as ordinary editing.
  const [createSlot, setCreateSlot] = useState<number | null>(null);
  /**
   * Pending edits, **keyed by container**.
   *
   * One shared map meant switching chests silently discarded whatever was typed
   * in the previous one — the state was reset on every `loadContainer`. Since a
   * write is per container anyway, holding them per container costs nothing and
   * lets someone stage changes across several chests before applying.
   */
  const [editsByContainer, setEditsByContainer] =
    useState<Record<string, Record<number, SlotPatch>>>({});
  /**
   * The game's whole item catalogue, not this world's contents.
   *
   * It was the world's contents, from `/api/items` — so typing a perfectly
   * legitimate item that nobody on the server owned showed "not in this world"
   * and no icon, while the backend (which has always validated against the full
   * catalogue) accepted the very same input on preview. The editor was calling
   * valid entries wrong.
   */
  const [knownItems, setKnownItems] = useState<CatalogueItem[]>([]);
  const [plan, setPlan] = useState<SlotEditPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [slotQuery, setSlotQuery] = useState('');
  const [emptyOnly, setEmptyOnly] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getBaseStorage(), getItemCatalogue()])
      .then(([storage, catalogue]) => {
        if (cancelled) return;
        setBases(storage);
        // All 2,466 items the game has — the same set the backend validates
        // against, so what this editor accepts and what the write path accepts
        // are the same list rather than two that disagree.
        setKnownItems(catalogue.items ?? []);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load containers');
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    getSavePlayers()
      .then((list) => { if (!cancelled) setPlayers(list); })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, []);

  // A player's containers are fetched per player rather than all at once: the
  // endpoint is scoped per uid and most sessions only ever look at one.
  useEffect(() => {
    if (!playerUid) { setPlayerContainers([]); return; }
    let cancelled = false;
    getPlayerContainers(playerUid)
      .then((r) => { if (!cancelled) setPlayerContainers(r.containers); })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Could not read that inventory');
      });
    return () => { cancelled = true; };
  }, [playerUid]);

  /**
   * Containers in the *chosen base only*.
   *
   * This used to be every container in the world in one flat `<select>` — 262
   * entries across 11 bases on a mature save, which is a scroll rather than a
   * choice. The thing a person knows is which base they want, so that is asked
   * first.
   */
  const containers = useMemo(() => {
    const base = bases.find((b) => b.baseId === baseId);
    if (!base) return [] as { container: BaseContainer; base: BaseStorage }[];
    return (base.containers ?? []).map((container) => ({ container, base }));
  }, [bases, baseId]);

  /**
   * Name **or** id -> the catalogue row, case-insensitively.
   *
   * The API needs `AIcore`; a person knows "AI Core". Accepting either and
   * showing what resolved makes a wrong entry visible before preview instead of
   * surfacing as a rejection after it.
   */
  const itemIndex = useMemo(() => {
    const byId = new Map<string, (typeof knownItems)[number]>();
    const byName = new Map<string, (typeof knownItems)[number]>();
    for (const item of knownItems) {
      byId.set(item.id.toLowerCase(), item);
      if (item.name) byName.set(item.name.toLowerCase(), item);
    }
    return { byId, byName };
  }, [knownItems]);

  const resolveItem = useCallback(
    (typed: string) => {
      const key = typed.trim().toLowerCase();
      if (!key) return null;
      return itemIndex.byId.get(key) ?? itemIndex.byName.get(key) ?? null;
    },
    [itemIndex]
  );

  const loadContainer = useCallback(async (id: string) => {
    setContainerId(id);
    // Deliberately NOT clearing edits: they are kept per container now, so
    // coming back to a chest finds what was staged there.
    setPlan(null);
    setError(null);
    setDone(null);
    setSlotQuery('');
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

  /**
   * Edits staged for the container currently open.
   *
   * Memoised because the `?? {}` fallback allocated a fresh object on every
   * render, so a memo depending on it recomputed every time — which for a
   * 960-slot palbox is the whole filter and diff, on every keystroke.
   */
  const edits = useMemo(
    () => editsByContainer[containerId] ?? {},
    [editsByContainer, containerId]
  );

  const patch = (index: number, next: Partial<SlotPatch>) => {
    setPlan(null);
    setEditsByContainer((all) => {
      const current = all[containerId] ?? {};
      const slot = slots.find((s) => s.slotIndex === index);
      const base: SlotPatch = current[index] ?? {
        slotIndex: index,
        itemId: slot?.isEmpty ? '' : (slot?.itemId ?? ''),
        stackCount: slot?.isEmpty ? 0 : (slot?.stackCount ?? 0),
      };
      return {
        ...all,
        [containerId]: { ...current, [index]: { ...base, ...next } },
      };
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

  const visibleSlots = useMemo(() => {
    const q = slotQuery.trim().toLowerCase();
    return slots.filter((slot) => {
      if (emptyOnly && !slot.isEmpty) return false;
      if (!q) return true;
      return (
        (slot.itemName ?? '').toLowerCase().includes(q) ||
        (slot.itemId ?? '').toLowerCase().includes(q) ||
        String(slot.slotIndex) === q
      );
    });
  }, [slots, slotQuery, emptyOnly]);

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
      setEditsByContainer((all) => ({ ...all, [containerId]: {} }));
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
        <Boxes size={14} /> Base inventory editor
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

      {/* Base storage or a player's own bags. Same writer either way — an item
          container is an item container — so they share one editor rather than
          two that can drift apart. Only the picker differs. */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 10 }}>
        {(['base', 'player'] as const).map((m) => (
          <button
            key={m}
            className="btn"
            style={{
              padding: '4px 12px', fontSize: 12,
              background: source === m ? 'var(--bg-card-hover)' : 'transparent',
              color: source === m ? 'var(--text-primary)' : 'var(--text-muted)',
            }}
            onClick={() => { setSource(m); loadContainer(''); }}
            disabled={busy}
          >
            {m === 'base' ? 'Base storage' : 'Player inventory'}
          </button>
        ))}
      </div>

      {source === 'player' && (
        <>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
            <select
              className="input"
              style={{ width: 250 }}
              value={playerUid}
              onChange={(e) => { setPlayerUid(e.target.value); loadContainer(''); }}
              disabled={busy}
            >
              <option value="">Pick a player…</option>
              {players.map((p) => (
                <option key={p.uid} value={p.uid}>
                  {p.name || p.uid.slice(0, 8)} — Lv {p.level}
                </option>
              ))}
            </select>

            <select
              className="input"
              style={{ width: 380 }}
              value={containerId}
              onChange={(e) => loadContainer(e.target.value)}
              disabled={busy || !playerUid}
            >
              <option value="">{playerUid ? 'Pick a bag…' : 'Pick a player first'}</option>
              {playerContainers.map((c) => (
                <option key={c.containerId} value={c.containerId} disabled={!c.decoded}>
                  {c.label}
                  {c.decoded
                    ? ` — ${c.usedSlots}/${c.totalSlots} used` +
                      (c.lockedSlots ? `, ${c.lockedSlots} locked` : '')
                    : ' — not decoded (parse with items enabled)'}
                </option>
              ))}
            </select>
          </div>

          {playerUid && (
            <p style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 12 }}>
              {/* Said before the pick, not after the writer refuses. Measured on
                  the reference world: every weapon and armour slot carries a
                  dynamic_id; not one key item does. */}
              Weapons and armour carry durability records and are shown read-only.{' '}
              <strong>Saddles, harnesses and key spheres are editable</strong> — they
              carry no durability record.
            </p>
          )}
        </>
      )}

      {source === 'base' && (
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
        <select
          className="input"
          style={{ width: 250 }}
          value={baseId}
          onChange={(e) => { setBaseId(e.target.value); loadContainer(''); }}
          disabled={busy}
        >
          <option value="">Pick a base…</option>
          {bases.map((base) => (
            <option key={base.baseId} value={base.baseId}>
              {/* Guild first. Most bases have never been renamed in game, so
                  `baseName` is our positional fallback ("Base 3") — which says
                  nothing about *whose* it is, and whose is the thing you need to
                  know before editing someone's chest. */}
              {base.guildName || 'Unknown guild'} · {base.baseName || base.baseId.slice(0, 8)}
              {' '}— {base.containerCount} containers, {base.itemCount.toLocaleString()} items
            </option>
          ))}
        </select>

        <select
          className="input"
          style={{ width: 330 }}
          value={containerId}
          onChange={(e) => loadContainer(e.target.value)}
          disabled={busy || !baseId}
        >
          <option value="">{baseId ? 'Pick a container…' : 'Pick a base first'}</option>
          {containers.map(({ container }) => (
            <option key={container.containerId} value={container.containerId}>
              {container.kindName || container.kind}{' '}
              ({container.usedSlots}/{container.totalSlots} slots
              {container.itemCount ? `, ${container.itemCount.toLocaleString()} items` : ''})
            </option>
          ))}
        </select>
      </div>

      )}
      {containerId && slots.length > 0 && (
        <>
          <datalist id="slot-editor-items">
            {/* Name as the *value*: that is what a person knows, and
                `resolveItem` maps it back to the id the API needs. Typing a raw
                id still works — both are indexed. */}
            {knownItems.map((i) => (
              <option key={i.id} value={i.name || i.id}>{i.id}</option>
            ))}
          </datalist>

          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
            <div style={{ position: 'relative', flex: 1, minWidth: 180 }}>
              <Search size={13} style={{ position: 'absolute', left: 10, top: 9, color: 'var(--text-muted)' }} />
              <input
                className="input"
                style={{ paddingLeft: 30 }}
                placeholder="Filter slots by item or slot number…"
                value={slotQuery}
                onChange={(e) => setSlotQuery(e.target.value)}
              />
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--text-secondary)' }}>
              <input type="checkbox" checked={emptyOnly} onChange={(e) => setEmptyOnly(e.target.checked)} />
              Empty only
            </label>
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              {visibleSlots.length} of {slots.length}
            </span>
          </div>

          <div style={{
            maxHeight: 420, overflowY: 'auto',
            border: '1px solid var(--border-primary)', borderRadius: 6,
          }}>
            {visibleSlots.map((slot) => {
              const edit = edits[slot.slotIndex];
              const locked = slot.maxDurability > 0 || slot.durability > 0;
              const typed = edit ? edit.itemId : (slot.isEmpty ? '' : slot.itemId);
              const resolved = resolveItem(typed);
              const count = edit ? edit.stackCount : (slot.isEmpty ? 0 : slot.stackCount);
              // Shown before the backend refuses it, rather than after.
              const ceiling = resolved?.maxStack || slot.maxStack || 0;
              const over = ceiling > 0 && count > ceiling;
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
                      <GameIcon src={slot.icon} size={20} />
                      <span style={{ flex: 1 }}>{slot.itemName || slot.itemId}</span>
                      <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                        has durability — cannot be overwritten without orphaning its record
                      </span>
                    </>
                  ) : (
                    <>
                      {/* The icon tracks what is *typed*, not what is stored, so
                          a mistyped name goes blank before you press preview. */}
                      <GameIcon src={resolved?.icon || (typed ? undefined : slot.icon)} size={20} />
                      <input
                        className="input"
                        list="slot-editor-items"
                        style={{ flex: 1, minWidth: 110, fontSize: 12, padding: '3px 6px' }}
                        placeholder="(empty)"
                        value={typed}
                        title={resolved
                          ? `${resolved.name} (${resolved.id})`
                          : 'No item in the game has this id or name'}
                        onChange={(e) => {
                          const next = resolveItem(e.target.value);
                          // Store the id once the text resolves; otherwise keep
                          // exactly what was typed and let the backend rule on
                          // it, since it knows all 2,466 items and this list
                          // only knows the ones present in this world.
                          patch(slot.slotIndex, { itemId: next ? next.id : e.target.value });
                        }}
                      />
                      <span style={{
                        width: 120, fontSize: 11,
                        color: typed && !resolved ? 'var(--accent-amber)' : 'var(--text-muted)',
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                      }}>
                        {typed ? (resolved ? resolved.name : 'unknown item') : ''}
                      </span>
                      <input
                        className="input"
                        type="number"
                        min={0}
                        style={{
                          width: 84, fontSize: 12, padding: '3px 6px',
                          borderColor: over ? 'var(--accent-amber)' : undefined,
                        }}
                        value={count}
                        title={ceiling ? `Game stack ceiling: ${ceiling.toLocaleString()}` : undefined}
                        onChange={(e) =>
                          patch(slot.slotIndex, { stackCount: e.target.valueAsNumber || 0 })
                        }
                      />
                      {/* Only on genuinely empty slots. Creating INTO an
                          occupied one is refused by the backend anyway, and
                          offering the button there would teach the wrong model
                          of what this does. */}
                      {slot.isEmpty && !typed && (
                        <button
                          className="btn btn-ghost"
                          style={{ padding: '2px 6px' }}
                          title="Create equipment or an egg here"
                          onClick={() =>
                            setCreateSlot(
                              createSlot === slot.slotIndex ? null : slot.slotIndex
                            )
                          }
                        >
                          <Sparkles size={12} />
                        </button>
                      )}
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
            {visibleSlots.length === 0 && (
              <div style={{ padding: 16, textAlign: 'center', fontSize: 12, color: 'var(--text-muted)' }}>
                No slots matched that filter.
              </div>
            )}
          </div>

          {createSlot !== null && containerId && (
            <ItemCreator
              containerId={containerId}
              slotIndex={createSlot}
              catalogue={knownItems}
              canEdit={canEdit}
              onClose={() => setCreateSlot(null)}
              onCreated={() => { setCreateSlot(null); void loadContainer(containerId); }}
            />
          )}

          {/* Staged elsewhere. Keeping edits per container is only an
              improvement if you can see that you have them — otherwise it just
              moves the surprise. */}
          {(() => {
            const others = Object.entries(editsByContainer)
              .filter(([id, e]) => id !== containerId && Object.keys(e).length > 0);
            if (!others.length) return null;
            const total = others.reduce((n, [, e]) => n + Object.keys(e).length, 0);
            return (
              <p style={{ fontSize: 11, color: 'var(--accent-amber)', marginTop: 10 }}>
                {total} unsaved change{total === 1 ? '' : 's'} staged in{' '}
                {others.length} other container{others.length === 1 ? '' : 's'}.
                Each container is written separately — switch to it and apply.
              </p>
            );
          })()}

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
              onClick={() => { setEditsByContainer((all) => ({ ...all, [containerId]: {} })); setPlan(null); }}
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
