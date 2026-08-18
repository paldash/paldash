'use client';

import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { Package, Search, RefreshCw, Hammer } from 'lucide-react';
import { getItemTotals, getItemScopes, getStructureCatalogue } from '@/lib/save-api';
import GameIcon from '@/components/game-icon';
import ItemSourcePanel from '@/components/item-source';
import CraftingTree from '@/components/crafting-tree';
import type { CatalogueStructure } from '@/lib/types';
import { useLanguage } from '@/lib/use-language';
import { localName, matchesQuery } from '@/lib/language';
import type { ItemTotals } from '@/lib/types';
import { SortHead } from '@/components/sort-head';
import { t } from '@/lib/chrome';

/**
 * Every item on the server, totalled across every container — the equivalent of
 * what an item retrieval unit would tell you, but server-wide.
 *
 * Totals are computed during the save parse, so this view is a cache read and
 * costs nothing to open.
 */
export default function ItemsView() {
  const [data, setData] = useState<ItemTotals | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [scopes, setScopes] = useState<Awaited<ReturnType<typeof getItemScopes>> | null>(null);
  // The row whose sources are open. This view says how much of a thing the
  // server holds; the panel says where more of it comes from, which is the
  // question the first answer prompts.
  const [selected, setSelected] = useState<string | null>(null);
  // '' means the default the backend picks for you — your own guilds below the
  // threshold, server-wide above it.
  const [guild, setGuild] = useState('');
  const [langPack] = useLanguage();

  /**
   * Items or structures. Two different game tables, two different questions.
   *
   * Structures are here rather than on their own tab because the question is
   * the same one — "what does this cost and where do the parts come from" —
   * and because the crafting tree is the answer in both cases. What differs is
   * that a structure has no sources panel: it is not an item, so it has no
   * drops, no loot table and no merchant, and `itemsource.describe()` returns
   * `known: false` for every one of them.
   */
  const [mode, setMode] = useState<'items' | 'structures'>('items');
  const [structures, setStructures] = useState<CatalogueStructure[] | null>(null);
  const [structureError, setStructureError] = useState<string | null>(null);

  useEffect(() => {
    if (mode !== 'structures' || structures) return;
    getStructureCatalogue()
      .then((r) => setStructures(r.structures))
      // Let it show. An empty structure list and a failed fetch look identical,
      // which is the mistake this repo already records for the map layers.
      .catch((e: unknown) =>
        setStructureError(e instanceof Error ? e.message : 'Could not load structures'));
  }, [mode, structures]);

  /**
   * The item's name in the chosen language, falling back to English.
   *
   * Feeds BOTH the list and the filter below, for the reason `my-pals` records:
   * a localised list whose filter tests only English loses every query typed in
   * the selected language.
   *
   * **Items are where a language selection is actually visible.** Pocketpair
   * leaves most Pal names untranslated in Latin-script languages — 25 of 322
   * differ in German — while 1,831 of 1,851 item names do. A switcher wired
   * only to Pals reads as broken on exactly the languages most people pick.
   */
  const itemName = useCallback(
    (i: { itemId: string; name: string }) =>
      localName(langPack, 'items', i.itemId, i.name),
    [langPack]
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await getItemTotals(guild || undefined));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load item totals');
    } finally {
      setLoading(false);
    }
  }, [guild]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    getItemScopes().then(setScopes).catch(() => setScopes(null));
  }, []);

  const [sort, setSort] = useState<'name' | 'category' | 'count'>('count');
  const [desc, setDesc] = useState(true);

  const filtered = useMemo(() => {
    if (!data) return [];
    const q = query.trim().toLowerCase();
    if (!q) return data.items;
    // Match on the English name, the localised name, the internal ID and the
    // category, so "Ancient Civilization Part", "Teil einer alten Zivilisation"
    // and "AncientCivilizationParts" all find it.
    return data.items.filter(
      (i) =>
        matchesQuery(q, i.name, itemName(i), i.itemId) ||
        i.typeA.toLowerCase().includes(q) ||
        i.typeB.toLowerCase().includes(q)
    );
    // `itemName` closes over the language pack. Omitting it relabels the rows
    // while leaving the filter on the previous language — the same stale-deps
    // bug `my-pals` carries a comment about, twice.
  }, [data, query, itemName]);

  const sorted = useMemo(() => {
    // 'count' descending IS the server's own order, so the default renders
    // byte-identically to the unsortable table this replaces.
    if (sort === 'count' && desc) return filtered;
    const rows = [...filtered].sort((a, b) => {
      const v = sort === 'count'
        ? a.count - b.count
        : sort === 'category'
          ? (a.typeB || a.typeA || '').localeCompare(b.typeB || b.typeA || '')
          : itemName(a).localeCompare(itemName(b));
      return desc ? -v : v;
    });
    return rows;
  }, [filtered, sort, desc, itemName]);

  if (error) {
    return (
      <div className="notice notice-warn">
        <strong>{t('Item totals unavailable')}</strong>
        <div style={{ marginTop: 6 }}>{error}</div>
        <button className="btn btn-ghost" style={{ marginTop: 12 }} onClick={load}>
          <RefreshCw size={13} /> Retry
        </button>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div className="dashboard-grid grid-3">
        <div className="stat-card">
          <div className="stat-label">{t('Distinct items')}</div>
          <div className="stat-value" style={{ marginTop: 6 }}>{data?.itemTypes ?? '—'}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">{t('Total quantity')}</div>
          <div className="stat-value" style={{ marginTop: 6 }}>
            {data ? data.totalCount.toLocaleString() : '—'}
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">{t('Containers scanned')}</div>
          <div className="stat-value" style={{ marginTop: 6 }}>
            {data ? data.containersScanned.toLocaleString() : '—'}
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <div style={{ position: 'relative', flex: 1 }}>
          <Search size={13} style={{ position: 'absolute', left: 10, top: 9, color: 'var(--text-muted)' }} />
          <input
            className="input"
            style={{ paddingLeft: 30 }}
            placeholder={t('Filter items…')}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        {(scopes?.guilds.length ?? 0) > 0 && (
          <select
            className="select"
            style={{ width: 200 }}
            value={guild}
            onChange={(e) => setGuild(e.target.value)}
            title={t('Item totals are per guild, because containers belong to bases and bases belong to guilds')}
          >
            <option value="">
              {scopes?.serverWide ? 'Whole server' : 'My guilds'}
            </option>
            {scopes?.guilds.map((g) => (
              <option key={g.id} value={g.id}>{g.name}</option>
            ))}
          </select>
        )}
        <button className="btn btn-ghost" onClick={load} disabled={loading}>
          <RefreshCw size={13} /> Reload
        </button>
      </div>

      <div style={{ display: 'flex', gap: 6 }}>
        <button
          className={mode === 'items' ? 'btn' : 'btn btn-ghost'}
          style={{ padding: '3px 10px', fontSize: 12 }}
          onClick={() => { setMode('items'); setSelected(null); }}
        >
          <Package size={12} /> Items
        </button>
        <button
          className={mode === 'structures' ? 'btn' : 'btn btn-ghost'}
          style={{ padding: '3px 10px', fontSize: 12 }}
          onClick={() => { setMode('structures'); setSelected(null); }}
          title={t('What each buildable structure costs, expanded to raw materials')}
        >
          <Hammer size={12} /> Structures
        </button>
      </div>

      {mode === 'structures' && (
        <StructureList
          structures={structures}
          error={structureError}
          query={query}
          selected={selected}
          onSelect={setSelected}
        />
      )}

      {mode === 'items' && data?.truncated && (
        <div className="notice" style={{ fontSize: 12 }}>
          Showing the top {data.items.length} item types by quantity. Click a row for where an item comes from.
        </div>
      )}

      {mode === 'items' && (
      <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
        <table className="table">
          <thead>
            <tr>
              <SortHead label="Item" k="name" sort={sort} desc={desc}
                        set={setSort} flip={setDesc} />
              <SortHead label={t('Category')} k="category" sort={sort} desc={desc}
                        set={setSort} flip={setDesc} />
              <SortHead label="Total" k="count" sort={sort} desc={desc}
                        set={setSort} flip={setDesc} />
            </tr>
          </thead>
          <tbody>
            {sorted.map((item) => (
              <Fragment key={item.itemId}>
              <tr
                title={item.description || undefined}
                onClick={() => setSelected(item.itemId === selected ? null : item.itemId)}
                style={{
                  cursor: 'pointer',
                  background:
                    item.itemId === selected ? 'var(--bg-surface)' : undefined,
                }}
              >
                <td style={{ color: 'var(--text-primary)' }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                    {/* The path comes straight from the bundled game data. */}
                    <GameIcon src={item.icon} title={itemName(item)} />
                    <span>
                      {itemName(item)}
                      {/* Keep the internal ID visible but secondary — it is what the
                          save actually stores, and it is what you search a wiki for. */}
                      <span
                        className="mono"
                        style={{ color: 'var(--text-muted)', fontSize: 11, marginLeft: 8 }}
                      >
                        {item.itemId}
                      </span>
                    </span>
                  </span>
                </td>
                <td style={{ color: 'var(--text-muted)', fontSize: 12 }}>
                  {item.typeB || item.typeA || '—'}
                </td>
                <td className="mono">{item.count.toLocaleString()}</td>
              </tr>
              {/*
                The panel opens AT the row that was clicked, not above the
                table. It used to render above, which on a list of hundreds put
                it off-screen — so clicking a row appeared to do nothing, and
                the crafting tree inside it was reported as broken when it had
                simply never been scrolled to. One bug, two symptoms, and the
                invisible one cost more.

                Keyed on the item so a different row remounts the panel rather
                than reusing one still holding the previous item's answer.
              */}
              {selected === item.itemId && (
                <tr>
                  <td colSpan={3} style={{ padding: 0, background: 'var(--bg-surface)' }}>
                    <ItemSourcePanel
                      key={item.itemId}
                      itemId={item.itemId}
                      onClose={() => setSelected(null)}
                    />
                  </td>
                </tr>
              )}
              </Fragment>
            ))}
          </tbody>
        </table>

        {!loading && !filtered.length && (
          <p style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
            <Package size={16} style={{ display: 'block', margin: '0 auto 8px' }} />
            {data
              ? 'No items matched.'
              : 'No parsed save data yet — press Refresh in the header.'}
          </p>
        )}
      </div>
      )}

      {mode === 'items' && (
      <p style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        {data && !data.namesResolved
          ? 'Bundled game data is missing, so items show their internal IDs. Run scripts/build-gamedata.py. '
          : 'Names come from bundled Palworld 1.0 game data; the grey text is the internal ID stored in the save. '}
        {data?.scope === 'server'
          ? 'Totals cover every container in the world: base chests, guild chests, player inventories and palboxes.'
          : 'Totals cover the base storage of your guild(s). Player inventories and palboxes are not included — those containers belong to a person rather than a base, so folding them in would make a guild total include things nobody put in guild storage.'}
      </p>
      )}
    </div>
  );
}

/**
 * Every buildable structure, and its cost expanded to raw materials on click.
 *
 * Separate from the item table rather than folded into it: a structure has no
 * quantity in this world (it is a catalogue, not a census), no category column
 * worth the same width, and — importantly — **no sources panel**. It is not an
 * item, so it has no drops, no loot table and no merchant, and asking
 * `/api/world/items/{id}` about one returns `known: false`. The crafting tree
 * is the whole answer, so it is rendered directly.
 */
function StructureList({
  structures, error, query, selected, onSelect,
}: {
  structures: CatalogueStructure[] | null;
  error: string | null;
  query: string;
  selected: string | null;
  onSelect: (id: string | null) => void;
}) {
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return structures ?? [];
    return (structures ?? []).filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        s.structureId.toLowerCase().includes(q) ||
        s.typeB.toLowerCase().includes(q),
    );
  }, [structures, query]);

  if (error) {
    return (
      <div className="notice notice-warn" style={{ fontSize: 12 }}>
        <strong>{t('Structures unavailable')}</strong>
        <div style={{ marginTop: 6 }}>{error}</div>
      </div>
    );
  }
  if (!structures) {
    return <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>{t('Loading…')}</p>;
  }

  return (
    <>
      <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: '45%' }}>Structure &mdash; click for the full cost</th>
              <th style={{ width: '20%' }}>{t('Category')}</th>
              <th>{t('Materials')}</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((s) => (
              <Fragment key={s.structureId}>
                <tr
                  onClick={() => onSelect(s.structureId === selected ? null : s.structureId)}
                  style={{
                    cursor: 'pointer',
                    background: s.structureId === selected ? 'var(--bg-surface)' : undefined,
                  }}
                >
                  <td style={{ color: 'var(--text-primary)' }}>
                    {s.name}
                    <span className="mono" style={{ color: 'var(--text-muted)', fontSize: 11, marginLeft: 8 }}>
                      {s.structureId}
                    </span>
                  </td>
                  <td style={{ color: 'var(--text-muted)', fontSize: 12 }}>
                    {s.typeB || s.typeA || '—'}
                  </td>
                  {/* The DIRECT cost. The tree below expands it; showing the
                      expansion here would make every row a paragraph. */}
                  <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                    {s.materials.map((m) => `${m.count}× ${m.itemId}`).join(', ') || '—'}
                  </td>
                </tr>
                {selected === s.structureId && (
                  <tr>
                    <td colSpan={3} style={{ padding: 10, background: 'var(--bg-surface)' }}>
                      <CraftingTree itemId={s.structureId} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>

        {!filtered.length && (
          <p style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
            No structures matched.
          </p>
        )}
      </div>

      <p style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        {filtered.length} of {structures.length} buildable structures, from the
        game&rsquo;s own build table. This is the catalogue — it does not say
        what you have built or what you can afford.
      </p>
    </>
  );
}
