'use client';

import { useEffect, useMemo, useState } from 'react';
import { Network, CornerDownRight, Recycle, AlertTriangle } from 'lucide-react';
import { getCraftingTree } from '@/lib/save-api';
import { asArray } from '@/lib/arrays';
import { num } from '@/lib/format';
import GameIcon from '@/components/game-icon';
import type { CraftNode, CraftTree } from '@/lib/types';

/**
 * Everything an item is made of, all the way down, with the quantities added up.
 *
 * The recursive half of the panel it sits in — that one stops one level down,
 * which is where "and where do I get *those*" starts. Catalogue data, so it
 * works with no parsed world.
 *
 * **The shopping list comes first and the tree second**, which is the opposite
 * of how the data is shaped and the right way round for the question: standing
 * at a bench, "265 Wood, 20 Leather, 8 Ore" is the answer and the tree is the
 * working.
 *
 * Three things it refuses to say, each mirroring `backend/crafting.py` rather
 * than being decided here:
 *
 * - **Work is units, never minutes.** How fast a base delivers them depends on
 *   which Pals are assigned, which no game file states.
 * - **Nothing here checked your chests.** `checksStock: false` travels in the
 *   payload for exactly this reason.
 * - **A dismantle is named, not walked.** Paldium Fragment comes back out of a
 *   Pal Sphere, and following that is the cycle the backend exists to break.
 */
export default function CraftingTree({ itemId }: { itemId: string }) {
  const [count, setCount] = useState(1);
  const [data, setData] = useState<CraftTree | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showSteps, setShowSteps] = useState(false);
  const [prefer, setPrefer] = useState<string[]>([]);

  useEffect(() => {
    let live = true;
    setError(null);
    getCraftingTree(itemId, count, prefer)
      .then((d) => { if (live) setData(d); })
      .catch((e: unknown) => {
        // Let it show rather than rendering an empty tree: "nothing to expand"
        // is a real answer here, so a swallowed failure is indistinguishable
        // from a raw material.
        if (live) setError(e instanceof Error ? e.message : 'Could not load the tree');
      });
    return () => { live = false; };
  }, [itemId, count, prefer]);

  const raw = asArray(data?.raw, 'crafting raw materials');
  const steps = asArray(data?.steps, 'crafting steps');

  // Every product on the path with more than one recipe, so the choice can be
  // offered where it exists instead of on a control that is usually inert.
  const choices = useMemo(() => collectChoices(data?.tree), [data]);

  if (error) {
    return <div className="notice notice-warn" style={{ fontSize: 12 }}>{error}</div>;
  }
  if (!data?.known || !data.craftable) return null;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
                    fontSize: 12, fontWeight: 600, color: 'var(--text-primary)',
                    marginBottom: 8 }}>
        <Network size={13} /> Full crafting tree
        <div style={{ flex: 1 }} />
        <label style={{ display: 'flex', alignItems: 'center', gap: 4,
                        fontWeight: 400, color: 'var(--text-muted)' }}>
          Make
          <input
            className="input"
            type="number"
            min={1}
            max={9999}
            value={count}
            onChange={(e) => setCount(Math.max(1, Math.min(9999, Number(e.target.value) || 1)))}
            style={{ width: 70, fontSize: 12, padding: '2px 6px' }}
          />
        </label>
      </div>

      {/* THE ANSWER, ABOVE THE WORKING. */}
      <div style={{ border: '1px solid var(--border-primary)', borderRadius: 6,
                    padding: 9, marginBottom: 10 }}>
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6 }}>
          Everything you have to gather for {num(count)}×
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
          {raw.map((material) => (
            <span key={material.itemId} title={material.itemId}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
              <GameIcon src={material.icon} title={material.name} />
              <span style={{ color: 'var(--text-secondary)' }}>{material.name}</span>
              <span className="mono" style={{ color: 'var(--text-primary)' }}>
                ×{num(material.count)}
              </span>
            </span>
          ))}
        </div>
      </div>

      {choices.map((choice) => (
        <div key={choice.itemId} style={{ fontSize: 11, marginBottom: 6,
                                          display: 'flex', alignItems: 'center',
                                          gap: 6, flexWrap: 'wrap' }}>
          <span style={{ color: 'var(--text-muted)' }}>
            {choice.name} can be made {choice.options.length} ways:
          </span>
          <select
            className="select"
            value={choice.recipeId}
            onChange={(e) => setPrefer((p) => [
              // Drop any previous choice for THIS product before adding the new
              // one — `prefer` is keyed by product on the backend, so leaving
              // both in makes the winner depend on list order.
              ...p.filter((id) => !choice.options.some((o) => o.recipeId === id)),
              e.target.value,
            ])}
            style={{ fontSize: 11, padding: '2px 5px' }}
          >
            {choice.options.map((option) => (
              <option key={option.recipeId} value={option.recipeId}>
                from {option.from.map((f) => `${f.count}× ${f.name}`).join(' + ')}
              </option>
            ))}
          </select>
        </div>
      ))}

      {data.tree && <Node node={data.tree} depth={0} />}

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center',
                    marginTop: 8, fontSize: 11, color: 'var(--text-muted)' }}>
        <span>
          {/* WORK UNITS. The payload says `workIsUnits` and this renders it as
              such — a figure in minutes would need a throughput nothing states. */}
          {num(data.totalWork)} work in total
        </span>
        {steps.length > 1 && (
          <button className="btn btn-ghost" onClick={() => setShowSteps((s) => !s)}
                  style={{ padding: '2px 8px', fontSize: 11 }}>
            {showSteps ? 'Hide' : 'Show'} craft order ({steps.length})
          </button>
        )}
      </div>

      {showSteps && (
        <ol style={{ margin: '8px 0 0', paddingLeft: 20, fontSize: 12,
                     display: 'flex', flexDirection: 'column', gap: 3 }}>
          {steps.map((step) => (
            <li key={step.recipeId} style={{ color: 'var(--text-secondary)' }}>
              <GameIcon src={step.icon} title={step.name} size={13} />{' '}
              {step.name} <span className="mono">×{num(step.made)}</span>
              <span style={{ color: 'var(--text-muted)' }}>
                {' '}— {num(step.batches)} craft{step.batches === 1 ? '' : 's'}
                {step.surplus > 0 ? `, ${num(step.surplus)} spare` : ''}
              </span>
            </li>
          ))}
        </ol>
      )}

      {data.truncated && (
        <div className="notice notice-warn" style={{ fontSize: 11, marginTop: 8 }}>
          <AlertTriangle size={11} /> The tree was cut short — some branches are
          not fully expanded.
        </div>
      )}

      <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 8 }}>
        Work is the game&rsquo;s own unit, not a time: how long it takes depends
        on the Pals you assign, which no game file records. Nothing here checked
        what you already have.
      </p>
    </div>
  );
}

function Node({ node, depth }: { node: CraftNode; depth: number }) {
  const children = asArray(node.materials, 'crafting materials');
  return (
    <div>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 5, fontSize: 12,
        padding: '2px 0', paddingLeft: depth * 16,
      }}>
        {depth > 0 && <CornerDownRight size={11} color="var(--text-muted)" />}
        <GameIcon src={node.icon} title={node.name} size={15} />
        <span style={{ color: 'var(--text-primary)' }}>{node.name}</span>
        <span className="mono" style={{ color: 'var(--text-secondary)' }}>
          ×{num(node.need)}
        </span>
        {node.batches !== undefined && node.batches > 1 && (
          <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>
            {num(node.batches)} crafts
            {node.surplus ? `, ${num(node.surplus)} spare` : ''}
          </span>
        )}
        {/* A leaf and a branch must not look alike. "Gather" is why this one
            stopped; a stopped-short branch says so separately. */}
        {node.leafReason === 'raw' && (
          <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>gather</span>
        )}
        {node.leafReason === 'depth' && (
          <span style={{ fontSize: 10, color: 'var(--accent-amber, #d1a05a)' }}>
            not expanded
          </span>
        )}
        {node.leafReason === 'cycle' && (
          <span style={{ fontSize: 10, color: 'var(--accent-amber, #d1a05a)' }}>
            already above this
          </span>
        )}
      </div>

      {/* Named, never walked — see the module docstring. Shown only on the
          material itself, where somebody is deciding how to get one. */}
      {node.leaf && node.alsoFrom?.length ? (
        <div style={{ paddingLeft: depth * 16 + 22, fontSize: 11,
                      color: 'var(--text-muted)', display: 'flex',
                      alignItems: 'center', gap: 4 }}>
          <Recycle size={10} />
          also from dismantling {node.alsoFrom.length === 1
            ? node.alsoFrom[0].from.map((f) => f.name).join(', ')
            : `${node.alsoFrom.length} other items`}
        </div>
      ) : null}

      {children.map((child, i) => (
        <Node key={`${child.itemId}-${i}`} node={child} depth={depth + 1} />
      ))}
    </div>
  );
}

/**
 * Products in this tree with more than one recipe, and which one is in use.
 *
 * Only four products in the whole catalogue have alternates, so this is
 * usually empty — which is the point of deriving it from the tree rather than
 * rendering a control that is inert on 1,395 of 1,399 items.
 */
function collectChoices(root: CraftNode | undefined) {
  const found = new Map<string, {
    itemId: string; name: string; recipeId: string;
    options: NonNullable<CraftNode['otherRecipes']>;
  }>();
  const walk = (node: CraftNode | undefined) => {
    if (!node) return;
    if (node.recipeId && node.otherRecipes?.length && !found.has(node.itemId)) {
      found.set(node.itemId, {
        itemId: node.itemId,
        name: node.name,
        recipeId: node.recipeId,
        options: node.otherRecipes,
      });
    }
    for (const child of asArray(node.materials, 'crafting materials')) walk(child);
  };
  walk(root);
  return [...found.values()];
}
