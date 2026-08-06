'use client';

import { useEffect, useState } from 'react';
import { Hammer, Skull, Package, Store, Factory, Lightbulb, X } from 'lucide-react';
import { getItemSources } from '@/lib/save-api';
import GameIcon from '@/components/game-icon';
import type { ItemSources, TechnologyUnlock } from '@/lib/types';

/**
 * "Where does this item come from" — the six bundled tables in one panel.
 *
 * Reads the catalogue, so it works with no parsed world: this describes what
 * Palworld has, while the list it opens from describes what this world holds.
 *
 * Three numbers it deliberately does not show, each because no game file
 * supports it: which workbench crafts a recipe, how often a chest is opened, and
 * anything between two drop bands.
 */
export default function ItemSourcePanel({
  itemId,
  onClose,
}: {
  itemId: string;
  onClose: () => void;
}) {
  const [data, setData] = useState<ItemSources | null>(null);
  const [error, setError] = useState<string | null>(null);

  // No state reset here: the caller keys this component on `itemId`, so picking
  // a different item remounts rather than reusing an instance holding the last
  // one's answer. Clearing in the effect would show the previous item's sources
  // for a frame, under the new item's name.
  useEffect(() => {
    let live = true;
    getItemSources(itemId)
      .then((d) => { if (live) setData(d); })
      .catch((e: unknown) => {
        // Kept and shown rather than swallowed into an empty panel: an empty
        // result is a legitimate answer here ("nothing produces this"), so a
        // catch that produced one would destroy the distinction.
        if (live) setError(e instanceof Error ? e.message : 'Could not load');
      });
    return () => { live = false; };
  }, [itemId]);

  return (
    <div className="glass-card" style={{ padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        <GameIcon src={data?.icon ?? null} title={data?.name ?? itemId} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
            {data?.name ?? itemId}
          </div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            {itemId}
          </div>
        </div>
        <button className="btn btn-ghost" onClick={onClose} aria-label="Close">
          <X size={13} />
        </button>
      </div>

      {error && (
        <div className="notice notice-warn" style={{ marginTop: 12, fontSize: 12 }}>
          {error}
        </div>
      )}

      {!data && !error && (
        <p style={{ marginTop: 12, fontSize: 12, color: 'var(--text-muted)' }}>Loading…</p>
      )}

      {data && !data.known && (
        <div className="notice" style={{ marginTop: 12, fontSize: 12 }}>
          No item with that ID exists in the bundled catalogue.
        </div>
      )}

      {data?.known && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 14 }}>
          {data.description && (
            <p style={{ fontSize: 12, color: 'var(--text-muted)', whiteSpace: 'pre-line' }}>
              {data.description}
            </p>
          )}

          {/* An item nothing produces is a real answer worth stating, not a
              blank panel that reads as a failed load. */}
          {!data.hasSource && (
            <div className="notice" style={{ fontSize: 12 }}>
              No recipe, drop table, chest, merchant or production structure in
              the game&rsquo;s own data produces this item.
              {data.usedIn?.length
                ? ' It is only used as a crafting material.'
                : ''}
            </div>
          )}

          <Crafting recipes={data.crafting ?? []} />
          <Drops drops={data.drops} />
          <Loot loot={data.loot ?? []} />
          <Shops shops={data.shops ?? []} />
          <Production production={data.production ?? []} />
          <UsedIn usedIn={data.usedIn ?? []} />
        </div>
      )}
    </div>
  );
}

function Section({
  icon,
  title,
  note,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div
        style={{
          display: 'flex', alignItems: 'center', gap: 6,
          fontSize: 12, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 6,
        }}
      >
        {icon} {title}
      </div>
      {children}
      {note && (
        <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>{note}</p>
      )}
    </div>
  );
}

function Crafting({ recipes }: { recipes: NonNullable<ItemSources['crafting']> }) {
  if (!recipes.length) return null;
  return (
    <Section
      icon={<Hammer size={13} />}
      title={recipes.length > 1 ? `Crafting (${recipes.length} ways)` : 'Crafting'}
      // The recipe table carries WorkableAttribute and it is 0 on all 1,414
      // rows, so which bench crafts a thing has no source. Saying so beats a
      // reader assuming the panel simply forgot.
      note="Which workbench crafts these is not recorded in any game file."
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {recipes.map((recipe) => (
          <div
            key={recipe.recipeId}
            style={{
              border: '1px solid var(--border-primary)', borderRadius: 6, padding: 8, fontSize: 12,
            }}
          >
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
              {recipe.materials.map((m) => (
                <span
                  key={m.itemId}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}
                >
                  <GameIcon src={m.icon} title={m.name} />
                  <span style={{ color: 'var(--text-secondary)' }}>
                    {m.name} <span className="mono">×{m.count}</span>
                  </span>
                </span>
              ))}
              <span style={{ color: 'var(--text-muted)' }}>
                → <span className="mono">×{recipe.count}</span>
              </span>
            </div>
            {recipe.unlockedBySchematic && (
              <div style={{ marginTop: 6, fontSize: 11, color: 'var(--text-muted)' }}>
                Needs the schematic <strong>{recipe.unlockedBySchematic.name}</strong>.
              </div>
            )}
            {recipe.technologies?.map((tech) => (
              <Technology key={tech.technologyId} tech={tech} />
            ))}
          </div>
        ))}
      </div>
    </Section>
  );
}

function Technology({ tech }: { tech: TechnologyUnlock }) {
  return (
    <div style={{ marginTop: 6, fontSize: 11, color: 'var(--text-muted)' }}>
      <Lightbulb size={11} style={{ display: 'inline', marginRight: 4 }} />
      Unlocked by <strong>{tech.name}</strong>
      {tech.cost != null && (
        <>
          {' '}&mdash; {tech.cost}{' '}
          {/* Two currencies, never added together: an Ancient Technology Point
              comes from a boss and an ordinary one from levelling. */}
          {tech.isBossTechnology ? 'Ancient Technology Point' : 'Technology Point'}
          {tech.cost === 1 ? '' : 's'}
        </>
      )}
      {tech.levelCap ? `, from level ${tech.levelCap}` : ''}
      {tech.requiresBoss ? `, after defeating ${tech.requiresBoss}` : ''}
      {tech.requires.length > 0 && (
        <div style={{ marginTop: 3 }}>
          Research first:{' '}
          {tech.requires.map((r, i) => (
            <span key={r.technologyId}>
              {i > 0 && ' → '}
              {r.name}
              {r.cost != null && <span className="mono"> ({r.cost})</span>}
            </span>
          ))}
          <ChainCost tech={tech} />
        </div>
      )}
    </div>
  );
}

/**
 * What the whole chain costs — **per currency, never as one number**.
 *
 * A boss technology is bought with Ancient Technology Points, which come from
 * beating a field boss, and an ordinary one with points from levelling. Adding
 * them gives a figure a player cannot spend, so the two totals stay apart and
 * only appear when the chain actually mixes or exceeds one step.
 */
function ChainCost({ tech }: { tech: TechnologyUnlock }) {
  const steps = [...tech.requires, tech];
  const ordinary = steps
    .filter((s) => !s.isBossTechnology)
    .reduce((sum, s) => sum + (s.cost ?? 0), 0);
  const ancient = steps
    .filter((s) => s.isBossTechnology)
    .reduce((sum, s) => sum + (s.cost ?? 0), 0);
  const parts = [
    ordinary > 0 ? `${ordinary} Technology Point${ordinary === 1 ? '' : 's'}` : '',
    ancient > 0 ? `${ancient} Ancient Technology Point${ancient === 1 ? '' : 's'}` : '',
  ].filter(Boolean);
  if (!parts.length) return null;
  return (
    <div style={{ marginTop: 2 }}>
      The whole chain: {parts.join(' and ')}.
    </div>
  );
}

function Drops({ drops }: { drops: ItemSources['drops'] }) {
  if (!drops?.total) return null;
  return (
    <Section
      icon={<Skull size={13} />}
      title={`Dropped by ${drops.total} ${drops.total === 1 ? 'Pal' : 'Pals'}`}
      note={
        drops.total > drops.shown.length
          ? `Showing the ${drops.shown.length} best rates of ${drops.total}. Level is the start of a band — the game records drops in tens.`
          : 'Level is the start of a band — the game records drops in tens.'
      }
    >
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {drops.shown.map((d) => (
          <span
            key={`${d.speciesId}-${d.levelFrom}`}
            className="badge"
            title={`${d.name}, level ${d.levelFrom}+ — ${d.rate}% chance of ${d.min}-${d.max}`}
          >
            {d.name}
            {/* An alpha has its own drop table and usually a better one, so the
                distinction is worth showing. The game still calls it by the
                plain name, so this is a badge rather than a suffix. */}
            {d.isBoss && <span style={{ color: 'var(--accent)' }}> α</span>}{' '}
            <span className="mono" style={{ color: 'var(--text-muted)' }}>
              {d.rate}% ×{d.min === d.max ? d.min : `${d.min}-${d.max}`}
            </span>
          </span>
        ))}
      </div>
    </Section>
  );
}

function Loot({ loot }: { loot: NonNullable<ItemSources['loot']> }) {
  if (!loot.length) return null;
  return (
    <Section
      icon={<Package size={13} />}
      title={`Found in ${loot.length} loot ${loot.length === 1 ? 'table' : 'tables'}`}
      // The percentage is the item's share of its own slot. Nothing in the
      // game's data says how often a given chest is rolled, so this is not a
      // per-chest chance and must not be labelled as one.
      note="The percentage is this item's share of that slot, not how often the container appears."
    >
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {loot.slice(0, 24).map((l) => (
          <span key={`${l.field}-${l.slot}`} className="badge" title={`${l.field} slot ${l.slot}`}>
            {l.field}{' '}
            <span className="mono" style={{ color: 'var(--text-muted)' }}>
              {l.slotShare != null ? `${Math.round(l.slotShare * 100)}%` : '—'} ×
              {l.min === l.max ? l.min : `${l.min}-${l.max}`}
            </span>
          </span>
        ))}
      </div>
    </Section>
  );
}

function Shops({ shops }: { shops: NonNullable<ItemSources['shops']> }) {
  if (!shops.length) return null;
  return (
    <Section icon={<Store size={13} />} title={`Sold by ${shops.length}`}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {shops.map((s) => (
          <span key={s.shop} className="badge">
            {s.shop}
            {s.price != null && (
              <span className="mono" style={{ color: 'var(--text-muted)' }}>
                {' '}{s.price.toLocaleString()}g
              </span>
            )}
          </span>
        ))}
      </div>
    </Section>
  );
}

function Production({ production }: { production: NonNullable<ItemSources['production']> }) {
  if (!production.length) return null;
  return (
    <Section icon={<Factory size={13} />} title="Produced at a base by">
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {production.map((p) => (
          <span key={p.structureId} className="badge">
            {p.name}
            {p.autoWorkPerSecond > 0 && (
              <span style={{ color: 'var(--text-muted)' }}> (runs unattended)</span>
            )}
          </span>
        ))}
      </div>
    </Section>
  );
}

function UsedIn({ usedIn }: { usedIn: NonNullable<ItemSources['usedIn']> }) {
  if (!usedIn.length) return null;
  return (
    <Section
      icon={<Hammer size={13} />}
      title={`Used to make ${usedIn.length}`}
      note={usedIn.length > 24 ? `Showing 24 of ${usedIn.length}, cheapest first.` : undefined}
    >
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {usedIn.slice(0, 24).map((u) => (
          <span key={`${u.itemId}-${u.needs}`} className="badge">
            {u.name} <span className="mono" style={{ color: 'var(--text-muted)' }}>×{u.needs}</span>
          </span>
        ))}
      </div>
    </Section>
  );
}
