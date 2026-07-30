'use client';

import { useEffect, useRef, useState } from 'react';
import { Layers, ChevronDown, ChevronRight, X } from 'lucide-react';
import { prettyClass } from '@/lib/pretty-class';

export interface LayerDef {
  id: string;
  label: string;
  color: string;
  group: 'live' | 'world' | 'static' | 'base';
}

export interface StaticCategory {
  id: string;
  label: string;
  count: number;
  kinds: { cls: string; count: number }[];
}

const GROUP_LABEL: Record<LayerDef['group'], string> = {
  live: 'Live',
  world: 'From the save',
  static: 'From the game files',
  base: 'Base structures',
};

/**
 * Real artwork where the icon set has it, a colour swatch everywhere else.
 *
 * Deliberately not all-or-nothing: four of these have genuine game icons and
 * the rest do not, and inventing glyphs for the others would be worse than a
 * swatch that matches the marker colour on the map — which is the actual job of
 * the thing in the list.
 */
const LAYER_ICON: Record<string, string> = {
  players: '/icons/game/playericon.webp',
  bases: '/icons/game/baseicon.webp',
  palbox: '/icons/structures/T_icon_buildObject_PalBoxV2.webp',
};

/**
 * Map layer controls, behind one button.
 *
 * This replaced 22 toggle buttons laid out across the top of the map plus a
 * stack of per-kind filter cards below it. That arrangement showed everything
 * at once, which meant the controls competed with the map for attention and
 * grew every time a category was added.
 *
 * A panel rather than nested dropdowns: submenus are awkward on touch, hide the
 * state you are trying to compare, and this is fundamentally a set of checkboxes
 * — several of which people want to see the state of simultaneously.
 */
export default function MapLayersPanel({
  layers,
  active,
  counts,
  onToggle,
  staticCategories,
  staticKindsOff,
  onToggleKind,
  onSetKinds,
}: {
  layers: LayerDef[];
  active: Record<string, boolean>;
  counts: Record<string, number>;
  onToggle: (id: string) => void;
  staticCategories: StaticCategory[];
  staticKindsOff: Record<string, string[]>;
  onToggleKind: (category: string, cls: string) => void;
  onSetKinds: (category: string, off: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  // Click-away and Escape. A panel that only closes via its own button is a
  // panel people leave open over the thing they wanted to look at.
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', away);
    document.addEventListener('keydown', esc);
    return () => {
      document.removeEventListener('mousedown', away);
      document.removeEventListener('keydown', esc);
    };
  }, [open]);

  const activeCount = layers.filter((l) => active[l.id]).length;
  const groups: LayerDef['group'][] = ['live', 'world', 'static', 'base'];

  return (
    <div ref={boxRef} style={{ position: 'relative', display: 'inline-block' }}>
      <button className="btn btn-ghost" onClick={() => setOpen(!open)}>
        <Layers size={14} /> Layers
        <span className="badge" style={{ marginLeft: 6 }}>
          {activeCount}/{layers.length}
        </span>
      </button>

      {open && (
        <div
          className="glass-card"
          style={{
            position: 'absolute', top: 'calc(100% + 6px)', left: 0, zIndex: 1000,
            width: 320, maxHeight: '70vh', overflowY: 'auto', padding: 10,
            boxShadow: '0 8px 28px rgba(0,0,0,.45)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: 8 }}>
            <strong style={{ fontSize: 12 }}>Map layers</strong>
            <button
              onClick={() => setOpen(false)}
              style={{
                marginLeft: 'auto', background: 'none', border: 'none',
                cursor: 'pointer', color: 'var(--text-muted)', padding: 2,
              }}
              aria-label="Close"
            >
              <X size={13} />
            </button>
          </div>

          {groups.map((group) => {
            const inGroup = layers.filter((l) => l.group === group);
            if (!inGroup.length) return null;
            const on = inGroup.filter((l) => active[l.id]).length;

            return (
              <div key={group} style={{ marginBottom: 10 }}>
                <div
                  style={{
                    display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4,
                    fontSize: 10, textTransform: 'uppercase', letterSpacing: '.04em',
                    color: 'var(--text-muted)',
                  }}
                >
                  <span>{GROUP_LABEL[group]}</span>
                  <span>({on}/{inGroup.length})</span>
                  <button
                    className="btn btn-ghost"
                    style={{ padding: '0 6px', fontSize: 10, marginLeft: 'auto' }}
                    onClick={() => {
                      const turnOn = on < inGroup.length;
                      inGroup.forEach((l) => {
                        if (active[l.id] !== turnOn) onToggle(l.id);
                      });
                    }}
                  >
                    {on < inGroup.length ? 'All' : 'None'}
                  </button>
                </div>

                {inGroup.map((layer) => {
                  const isOn = !!active[layer.id];
                  const category = layer.id.startsWith('static:')
                    ? staticCategories.find((c) => `static:${c.id}` === layer.id)
                    : undefined;
                  const off = category ? staticKindsOff[category.id] ?? [] : [];
                  const isExpanded = expanded === layer.id;
                  const art = LAYER_ICON[layer.id];

                  return (
                    <div key={layer.id}>
                      <div
                        style={{
                          display: 'flex', alignItems: 'center', gap: 8,
                          padding: '3px 4px', borderRadius: 4,
                          opacity: isOn ? 1 : 0.5,
                        }}
                      >
                        <button
                          onClick={() => onToggle(layer.id)}
                          style={{
                            display: 'flex', alignItems: 'center', gap: 8, flex: 1,
                            background: 'none', border: 'none', padding: 0,
                            cursor: 'pointer', color: 'inherit', font: 'inherit',
                            textAlign: 'left',
                          }}
                        >
                          <input type="checkbox" checked={isOn} readOnly style={{ pointerEvents: 'none' }} />
                          {art ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img
                              src={art}
                              alt=""
                              // Explicit style, not attributes: the source art
                              // is up to 512px and attributes lose to CSS.
                              style={{ width: 16, height: 16, objectFit: 'contain', flexShrink: 0 }}
                            />
                          ) : (
                            <span
                              style={{
                                width: 10, height: 10, borderRadius: 2, flexShrink: 0,
                                background: layer.color,
                              }}
                            />
                          )}
                          <span style={{ fontSize: 12 }}>{layer.label}</span>
                          {counts[layer.id] != null && (
                            <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--text-muted)' }}>
                              {counts[layer.id].toLocaleString()}
                            </span>
                          )}
                        </button>

                        {/* Only categories with sub-kinds expand, and only while
                            switched on — a filter for a hidden layer is noise. */}
                        {category && isOn && category.kinds.length > 1 && (
                          <button
                            onClick={() => setExpanded(isExpanded ? null : layer.id)}
                            style={{
                              background: 'none', border: 'none', cursor: 'pointer',
                              color: 'var(--text-muted)', padding: 0, display: 'flex',
                            }}
                            aria-label={isExpanded ? 'Collapse kinds' : 'Expand kinds'}
                          >
                            {isExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                          </button>
                        )}
                      </div>

                      {category && isOn && isExpanded && (
                        <div style={{ margin: '2px 0 6px 26px' }}>
                          <div style={{ display: 'flex', gap: 6, marginBottom: 4 }}>
                            <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                              {category.kinds.length - off.length}/{category.kinds.length} kinds
                            </span>
                            <button
                              className="btn btn-ghost"
                              style={{ padding: '0 6px', fontSize: 10, marginLeft: 'auto' }}
                              onClick={() =>
                                onSetKinds(
                                  category.id,
                                  off.length >= category.kinds.length
                                    ? []
                                    : category.kinds.map((k) => k.cls)
                                )
                              }
                            >
                              {off.length >= category.kinds.length ? 'All' : 'None'}
                            </button>
                          </div>
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
                            {category.kinds.map((kind) => {
                              const kindOn = !off.includes(kind.cls);
                              return (
                                <button
                                  key={kind.cls}
                                  onClick={() => onToggleKind(category.id, kind.cls)}
                                  className="btn"
                                  style={{
                                    padding: '1px 6px', fontSize: 10,
                                    opacity: kindOn ? 1 : 0.4,
                                    background: kindOn ? 'var(--bg-tertiary)' : 'transparent',
                                  }}
                                  title={`${kind.count.toLocaleString()} in the world`}
                                >
                                  {prettyClass(kind.cls)}
                                </button>
                              );
                            })}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
