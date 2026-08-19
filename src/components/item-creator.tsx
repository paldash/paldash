'use client';

import { useCallback, useEffect, useState } from 'react';
import { Sparkles, AlertTriangle, ShieldCheck, X } from 'lucide-react';
import { previewItemCreate, applyItemCreate } from '@/lib/save-api';
import type { ItemCreatePlan } from '@/lib/save-api';
import type { CatalogueItem } from '@/lib/types';
import GameIcon from './game-icon';
import { asArray } from '@/lib/arrays';
import { resolveItem, shadowedByName } from '@/lib/item-lookup';
import { t } from '@/lib/chrome';

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
  // Which Pal an egg hatches. A REAL CHOICE, not a property of the item: one egg
  // item covers many species (`PalEgg_Dark_03` hatches 18), so leaving this
  // blank inherits whatever record the backend copied — which the plan then
  // flags as `hatchesFromTemplate` rather than presenting as decided.
  const [hatches, setHatches] = useState('');
  const [plan, setPlan] = useState<ItemCreatePlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  // Any change to the inputs invalidates the plan. The apply sends the plan's
  // hash and the backend refuses a stale one, but a preview left on screen
  // describing a different item is how someone confirms the wrong thing.
  const reset = useCallback(() => { setPlan(null); setDone(null); setError(null); }, []);
  useEffect(reset, [typed, durability, hatches, reset]);

  // Accepts either spelling, because the API speaks `Rankup_1` and people say
  // "Starfruit ☆1". A name that resolves to nothing is still sent — the backend
  // validates against all 2,466 items and gives the better error.
  //
  // `resolveItem` is shared with the slot editor. It used to be written out
  // here, and the editor had its own version; they disagreed about which of two
  // same-named items to pick and neither had reasoned about it. See
  // `src/lib/item-lookup.ts`.
  const resolved = resolveItem(catalogue, typed);
  const shadowed = shadowedByName(catalogue, typed);
  const itemId = resolved?.id ?? typed.trim();

  const preview = async () => {
    setBusy(true); setError(null); setDone(null);
    try {
      setPlan(await previewItemCreate(
        containerId, slotIndex, itemId, durability, hatches.trim() || undefined
      ));
    } catch (e) {
      setError(e instanceof Error ? e.message : t('Preview failed'));
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
        ? `It will hatch ${plan.hatchesName || plan.hatchesInto || 'a Pal'}` +
          (plan.hatchesFromTemplate
            ? ' — inherited from an existing egg, NOT chosen. Name a species to decide it.\n'
            : '.\n')
        : `Durability ${plan.durability}${plan.maxDurability ? ` of ${plan.maxDurability}` : ''}.\n`) +
      '\nThis puts an item into the world that was never obtained in it, and is ' +
      'recorded in the audit log under your name.\n\n' +
      'A full backup is taken first and the result is verified — if anything ' +
      'does not add up, the world is rolled back automatically.'
    )) return;

    setBusy(true); setError(null);
    try {
      const result = await applyItemCreate(
        containerId, slotIndex, itemId, plan.planHash, durability,
        hatches.trim() || undefined
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
          placeholder={t('Item id or name — e.g. Katana, or Large Fire Egg')}
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
          title={t('Leave blank for factory-fresh, which is what the game gives a new item.')}
          value={durability ?? ''}
          onChange={(e) =>
            setDurability(e.target.value === '' ? undefined : e.target.valueAsNumber)
          }
          disabled={busy}
        />
        <input
          className="input"
          /* No datalist: the Pal list is not fetched here, and pointing at one
             that does not exist gives a silently dead dropdown. Free text is
             fine — the backend validates against the character tables and names
             the problem, and it accepts an id or a display name. */
          style={{ width: 170, fontSize: 12, padding: '4px 7px' }}
          placeholder="hatches (eggs)"
          title="Which Pal this egg hatches. One egg item covers many species, so leaving this blank inherits an arbitrary one from an existing egg rather than picking for you."
          value={hatches}
          onChange={(e) => setHatches(e.target.value)}
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

      {/* A BADGE, NEVER A FILTER. Hiding these would be wrong twice over: the
          flag is not "unobtainable" (Key Spheres carry it and players hold
          them), and an id already sitting in somebody's save must stay
          writable. All this does is say which id the game still uses. */}
      {resolved?.liveTwin && (
        <div className="notice notice-warn" style={{ marginTop: 10, fontSize: 12 }}>
          <AlertTriangle size={13} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
          Two items share the name &ldquo;{resolved.name}&rdquo;, and the game
          flags this one <code>bLegalInGame = false</code> while{' '}
          <code>{resolved.liveTwin}</code> is flagged legal. If you meant the one
          players normally have, use <code>{resolved.liveTwin}</code>.
          {/* WHAT THE FLAG DOES is not stated here, because it is not known.
              A first draft said "crafting and recipes will not recognise it",
              which sounds right and is false: 88 of these 95 ids DO appear in
              DT_ItemRecipeDataTable. Report the facts, not the mechanic —
              basesupply.py's rule, and the same trap it was written for. */}
        </div>
      )}

      {/* The other half: a NAME that matched several ids, where we picked one.
          A substitution the operator did not make must be visible, even though
          it is the right one — the whole complaint behind this was a resolution
          nobody could see. */}
      {shadowed.length > 0 && resolved && (
        <div className="notice" style={{ marginTop: 10, fontSize: 12 }}>
          &ldquo;{resolved.name}&rdquo; is the name of{' '}
          {shadowed.length + 1} items. Using <code>{resolved.id}</code>, the one
          the game still flags as legal; the {shadowed.length === 1 ? 'other is' : 'others are'}{' '}
          {shadowed.map((id, n) => (
            <span key={id}>{n > 0 && ', '}<code>{id}</code></span>
          ))}. Type an id directly to pick one yourself.
        </div>
      )}

      {resolved && resolved.legalInGame === false && !resolved.liveTwin && (
        <div className="notice" style={{ marginTop: 10, fontSize: 12 }}>
          The game flags <code>{resolved.id}</code> as{' '}
          <code>bLegalInGame = false</code>. That is <strong>not</strong> the
          same as unobtainable — Key Spheres carry the same flag and are held on
          real servers — and no live item shares this name, so there is nothing
          to point you at instead.
        </div>
      )}

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
          {asArray(plan.problems, 'item plan problems').map((p, i) => <div key={i}>{p}</div>)}
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
            /* IT IS A CHOICE NOW, and the distinction between choosing and
               inheriting is the thing to show. An egg item does not decide the
               species — one covers up to 18 — so an inherited value is arbitrary
               and must not read as decided. */
            <div style={{ color: 'var(--text-secondary)' }}>
              Hatches <strong>{plan.hatchesName || plan.hatchesInto || '—'}</strong>
              {plan.hatchesFromTemplate ? (
                <span style={{ color: 'var(--status-warning)' }}>
                  {' '}— inherited from an egg already in this world, not chosen.
                  One egg item covers many species, so this is arbitrary. Name a
                  species above to decide it.
                </span>
              ) : (
                <span style={{ color: 'var(--text-muted)' }}>
                  {' '}— written into the new record and verified after the write.
                </span>
              )}
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
            title={canEdit ? undefined : t('The server must be provably stopped first.')}
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
