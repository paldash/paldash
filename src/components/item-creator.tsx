'use client';

import { useCallback, useEffect, useState } from 'react';
import { Sparkles, AlertTriangle, ShieldCheck, X } from 'lucide-react';
import { previewItemCreate, applyItemCreate } from '@/lib/save-api';
import type { ItemCreatePlan } from '@/lib/save-api';
import type { CatalogueItem } from '@/lib/types';
import GameIcon from './game-icon';

/**
 * Create one piece of equipment or one egg into an empty slot.
 *
 * SEPARATE FROM THE SLOT EDITOR ON PURPOSE, at both ends.
 *
 * The slot editor moves and stacks items that already exist and refuses to touch
 * anything carrying a durability record. This brings an item into the world that
 * was never obtained in it — a different act, with a different audit action
 * behind it (`item.create`), and it should not be reachable by mistyping in a
 * row of the ordinary editor.
 *
 * Two steps, because the preview is where the things you cannot choose get said:
 * what an egg will hatch into, and what durability it starts at.
 */
export default function ItemCreator({
  containerId,
  slotIndex,
  catalogue,
  canEdit,
  onClose,
  onCreated,
}: {
  containerId: string;
  slotIndex: number;
  catalogue: CatalogueItem[];
  canEdit: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [typed, setTyped] = useState('');
  const [durability, setDurability] = useState<number | undefined>(undefined);
  const [plan, setPlan] = useState<ItemCreatePlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  // Any change to the inputs invalidates the plan. The apply sends the plan's
  // hash and the backend refuses a stale one, but a preview left on screen
  // describing a different item is how someone confirms the wrong thing.
  const reset = useCallback(() => { setPlan(null); setDone(null); setError(null); }, []);
  useEffect(reset, [typed, durability, reset]);

  // Accepts either spelling, because the API speaks `Rankup_1` and people say
  // "Starfruit ☆1". A name that resolves to nothing is still sent — the backend
  // validates against all 2,466 items and gives the better error.
  const resolve = (text: string): CatalogueItem | undefined => {
    const q = text.trim().toLowerCase();
    if (!q) return undefined;
    return catalogue.find(
      (i) => i.id.toLowerCase() === q || (i.name ?? '').toLowerCase() === q
    );
  };
  const resolved = resolve(typed);
  const itemId = resolved?.id ?? typed.trim();

  const preview = async () => {
    setBusy(true); setError(null); setDone(null);
    try {
      setPlan(await previewItemCreate(containerId, slotIndex, itemId, durability));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Preview failed');
      setPlan(null);
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!plan?.ok || !plan.planHash) return;
    if (!confirm(
      `Create ${plan.itemName} in slot ${slotIndex}?\n\n` +
      (plan.type === 'egg'
        ? `It will hatch a ${plan.hatchesInto || 'Pal chosen by the game'}.\n`
        : `Durability ${plan.durability}${plan.maxDurability ? ` of ${plan.maxDurability}` : ''}.\n`) +
      '\nThis puts an item into the world that was never obtained in it, and is ' +
      'recorded in the audit log under your name.\n\n' +
      'A full backup is taken first and the result is verified — if anything ' +
      'does not add up, the world is rolled back automatically.'
    )) return;

    setBusy(true); setError(null);
    try {
      const result = await applyItemCreate(
        containerId, slotIndex, itemId, plan.planHash, durability
      );
      setDone(
        `Created ${result.itemName} in slot ${result.slotIndex}. Verified. ` +
        `Rollback point: ${result.backupId}.`
      );
      setPlan(null);
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Creation failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{
      border: '1px solid var(--border-primary)', borderRadius: 6,
      padding: 12, marginTop: 10,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <Sparkles size={13} style={{ color: 'var(--accent-amber)' }} />
        <strong style={{ fontSize: 13 }}>Create equipment or an egg — slot {slotIndex}</strong>
        <button
          className="btn btn-ghost"
          style={{ marginLeft: 'auto', padding: '2px 6px' }}
          onClick={onClose}
          title="Close"
        >
          <X size={12} />
        </button>
      </div>

      <p style={{ fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.6, marginBottom: 10 }}>
        Weapons, armour and Pal eggs each carry their own durability record, so
        they cannot be typed into the rows above the way ordinary items can. The
        new record is copied from one this world already has — an item type
        nobody here owns yet has nothing to copy, and is refused rather than
        guessed at.
      </p>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <GameIcon src={resolved?.icon} size={22} />
        <input
          className="input"
          list="slot-editor-items"
          style={{ flex: '1 1 200px', fontSize: 12, padding: '4px 7px' }}
          placeholder="Item id or name — e.g. Katana, or Large Fire Egg"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          disabled={busy}
        />
        <input
          className="input"
          type="number"
          min={0}
          style={{ width: 110, fontSize: 12, padding: '4px 7px' }}
          placeholder="durability"
          title="Leave blank for factory-fresh, which is what the game gives a new item."
          value={durability ?? ''}
          onChange={(e) =>
            setDurability(e.target.value === '' ? undefined : e.target.valueAsNumber)
          }
          disabled={busy}
        />
        <button
          className="btn btn-ghost"
          onClick={preview}
          disabled={busy || !typed.trim()}
        >
          Preview
        </button>
      </div>

      {error && (
        <div className="notice notice-warn" style={{ marginTop: 10, fontSize: 12 }}>
          <AlertTriangle size={13} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
          {error}
        </div>
      )}

      {done && (
        <div className="notice" style={{ marginTop: 10, fontSize: 12 }}>
          <ShieldCheck size={13} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
          {done}
        </div>
      )}

      {plan && !plan.ok && (
        <div className="notice notice-warn" style={{ marginTop: 10, fontSize: 12 }}>
          {(plan.problems ?? []).map((p, i) => <div key={i}>{p}</div>)}
        </div>
      )}

      {plan?.ok && (
        <div style={{
          marginTop: 10, padding: 10, borderRadius: 6,
          border: '1px solid var(--border-primary)', fontSize: 12, lineHeight: 1.7,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            <GameIcon src={plan.icon} size={20} />
            <strong>{plan.itemName}</strong>
            <span style={{ color: 'var(--text-muted)' }}>({plan.type})</span>
          </div>

          {plan.type === 'egg' ? (
            /* Not a choice, and saying so matters: the item id fixes the egg's
               kind and the record decides the species. Someone expecting to pick
               would otherwise assume the blank field meant "random". */
            <div style={{ color: 'var(--text-secondary)' }}>
              Hatches <strong>{plan.hatchesInto || 'a Pal the game chooses'}</strong> —
              copied from an egg of this same kind already in the world, not
              selectable here.
            </div>
          ) : (
            <div style={{ color: 'var(--text-secondary)' }}>
              Durability {plan.durability}
              {plan.maxDurability ? ` of ${plan.maxDurability} (factory-fresh)` : ''}.
              No passive skills — a new item starts clean rather than inheriting
              whatever the copied one had.
            </div>
          )}

          <button
            className="btn"
            style={{ marginTop: 8 }}
            onClick={apply}
            disabled={busy || !canEdit}
            title={canEdit ? undefined : 'The server must be provably stopped first.'}
          >
            {busy ? 'Creating…' : 'Create it'}
          </button>
          {!canEdit && (
            <div style={{ color: 'var(--text-muted)', fontSize: 11 }}>
              Stop the server to enable this. Writing to Level.sav while the game
              is running is how a world gets corrupted.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
