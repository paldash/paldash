'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Package, Search, RefreshCw } from 'lucide-react';
import { getItemTotals } from '@/lib/save-api';
import type { ItemTotals } from '@/lib/types';

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

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await getItemTotals());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load item totals');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const filtered = useMemo(() => {
    if (!data) return [];
    const q = query.trim().toLowerCase();
    if (!q) return data.items;
    // Match on the display name, the internal ID and the category, so both
    // "Ancient Civilization Part" and "AncientCivilizationParts" find it.
    return data.items.filter(
      (i) =>
        i.name.toLowerCase().includes(q) ||
        i.itemId.toLowerCase().includes(q) ||
        i.typeA.toLowerCase().includes(q) ||
        i.typeB.toLowerCase().includes(q)
    );
  }, [data, query]);

  if (error) {
    return (
      <div className="notice notice-warn">
        <strong>Item totals unavailable</strong>
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
          <div className="stat-label">Distinct items</div>
          <div className="stat-value" style={{ marginTop: 6 }}>{data?.itemTypes ?? '—'}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Total quantity</div>
          <div className="stat-value" style={{ marginTop: 6 }}>
            {data ? data.totalCount.toLocaleString() : '—'}
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Containers scanned</div>
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
            placeholder="Filter items…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <button className="btn btn-ghost" onClick={load} disabled={loading}>
          <RefreshCw size={13} /> Reload
        </button>
      </div>

      {data?.truncated && (
        <div className="notice" style={{ fontSize: 12 }}>
          Showing the top {data.items.length} item types by quantity.
        </div>
      )}

      <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: '55%' }}>Item</th>
              <th style={{ width: '25%' }}>Category</th>
              <th>Total</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((item) => (
              <tr key={item.itemId} title={item.description || undefined}>
                <td style={{ color: 'var(--text-primary)' }}>
                  {item.name}
                  {/* Keep the internal ID visible but secondary — it is what the
                      save actually stores, and it is what you search a wiki for. */}
                  <span
                    className="mono"
                    style={{ color: 'var(--text-muted)', fontSize: 11, marginLeft: 8 }}
                  >
                    {item.itemId}
                  </span>
                </td>
                <td style={{ color: 'var(--text-muted)', fontSize: 12 }}>
                  {item.typeB || item.typeA || '—'}
                </td>
                <td className="mono">{item.count.toLocaleString()}</td>
              </tr>
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

      <p style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        {data && !data.namesResolved
          ? 'Bundled game data is missing, so items show their internal IDs. Run scripts/build-gamedata.py. '
          : 'Names come from bundled Palworld 1.0 game data; the grey text is the internal ID stored in the save. '}
        Totals cover every container in the world: base chests, guild chests,
        player inventories and palboxes.
      </p>
    </div>
  );
}
