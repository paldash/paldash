'use client';

import { useCallback, useEffect, useState } from 'react';
import { useDashboardStore } from '@/lib/store';
import {
  Building2, MapPin, Users, Package, ChevronRight, ChevronDown,
  Download, Boxes, AlertTriangle,
} from 'lucide-react';
import { formatCoords } from '@/lib/map-coordinates';
import { getBaseStorage, downloadReport, downloadExport, type ReportFormat } from '@/lib/save-api';
import type { BaseStorage } from '@/lib/types';
import { CAPABILITIES } from '@/lib/permissions';

/** A base this full is about to start dropping what its Pals produce. */
const NEARLY_FULL = 90;

export default function BaseViewer() {
  const { bases, guilds, backendOnline, setActiveTab, capabilities } = useDashboardStore();

  const [storage, setStorage] = useState<BaseStorage[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const maySeeDetail = capabilities.includes(CAPABILITIES.VIEW_DETAIL);
  // Storage is VIEW_SELF now — the backend scopes it to your own guild's bases.
  // Gating the fetch on VIEW_DETAIL meant a Player saw "0 items stored" for
  // bases that are theirs, which reads as an empty world rather than as a
  // permission they do not have. Bulk exports stay VIEW_DETAIL.
  const maySeeStorage = capabilities.includes(CAPABILITIES.VIEW_SELF);

  useEffect(() => {
    if (!backendOnline || !maySeeStorage) return;
    let cancelled = false;

    getBaseStorage()
      .then((data) => { if (!cancelled) setStorage(data); })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load base storage');
      });

    return () => { cancelled = true; };
  }, [backendOnline, maySeeStorage]);

  const runExport = useCallback(async (download: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await download();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Export failed');
    } finally {
      setBusy(false);
    }
  }, []);

  const exportReport = useCallback(
    (report: string, format: ReportFormat, baseId?: string) =>
      runExport(() => downloadReport(report, format, baseId)),
    [runExport]
  );

  if (!backendOnline) {
    return (
      <div className="glass-card" style={{ padding: 40, textAlign: 'center' }}>
        <Building2 size={40} style={{ color: 'var(--text-muted)', marginBottom: 12 }} />
        <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 4 }}>Save Backend Offline</h3>
        <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>
          The Python backend must be running to view base camp data from save files.
        </p>
      </div>
    );
  }

  if (bases.length === 0) {
    return (
      <div className="glass-card" style={{ padding: 40, textAlign: 'center' }}>
        <Building2 size={40} style={{ color: 'var(--text-muted)', marginBottom: 12 }} />
        <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 4 }}>No Base Camps Found</h3>
        <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>
          No base camp data available. Make sure save files are accessible.
        </p>
      </div>
    );
  }

  const storageById = new Map(storage.map((s) => [s.baseId, s]));
  const storedTotal = storage.reduce((acc, s) => acc + s.itemCount, 0);
  const full = storage.filter((s) => s.fillPercent >= NEARLY_FULL);
  const deployedPals = bases.reduce((acc, b) => acc + (b.palCount ?? 0), 0);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {error && <div className="notice notice-warn">{error}</div>}

      {/* Summary */}
      <div className="dashboard-grid grid-4">
        <div className="stat-card">
          <div className="stat-value" style={{ color: 'var(--accent-amber)' }}>{bases.length}</div>
          <div className="stat-label">Total Bases</div>
        </div>
        <div className="stat-card">
          <div className="stat-value" style={{ color: 'var(--accent-purple)' }}>{guilds.length}</div>
          <div className="stat-label">Guilds</div>
        </div>
        <div className="stat-card">
          <div className="stat-value" style={{ color: 'var(--accent-emerald)' }}>
            {/* Pals working at a base. Safe to sum — each base owns its own
                worker container, so nothing is counted twice. */}
            {deployedPals.toLocaleString()}
          </div>
          <div className="stat-label" title="Pals assigned to work at a base. Pals in a palbox are counted under Guild Pals instead.">
            Pals Working at Bases
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-value" style={{ color: 'var(--accent-cyan)' }}>
            {/* An em dash, not 0 — "we did not ask" and "there is nothing"
                must not share a representation. */}
            {maySeeStorage ? storedTotal.toLocaleString() : '—'}
          </div>
          <div className="stat-label">
            {maySeeStorage
              ? (maySeeDetail ? 'Items Stored in Bases' : 'Items in Your Bases')
              : 'Storage — sign in to see'}
          </div>
        </div>
      </div>

      {full.length > 0 && (
        <div className="notice notice-warn">
          <AlertTriangle size={14} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 6 }} />
          <strong>{full.length} base{full.length === 1 ? '' : 's'} at {NEARLY_FULL}%+ storage.</strong>{' '}
          Once every slot is taken, anything your Pals produce is dropped on the floor:{' '}
          {full.map((s) => s.baseName).join(', ')}.
        </div>
      )}

      {maySeeDetail && (
        <div className="glass-card" style={{ padding: 14, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span className="section-title" style={{ marginRight: 'auto' }}>
            <Download size={13} /> Export
          </span>
          {([
            ['base-summary', 'Storage by base'],
            ['base-items', 'Items by base'],
            ['containers', 'Every container'],
            ['world-items', 'Server-wide totals'],
          ] as const).map(([id, label]) => (
            <div key={id} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{label}</span>
              {(['csv', 'json', 'txt'] as ReportFormat[]).map((fmt) => (
                <button
                  key={fmt}
                  className="btn btn-ghost"
                  style={{ padding: '2px 6px', fontSize: 10, textTransform: 'uppercase' }}
                  disabled={busy}
                  onClick={() => exportReport(id, fmt)}
                >
                  {fmt}
                </button>
              ))}
            </div>
          ))}

          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              Full world (checksummed)
            </span>
            <button
              className="btn btn-ghost"
              style={{ padding: '2px 6px', fontSize: 10, textTransform: 'uppercase' }}
              disabled={busy}
              onClick={() => runExport(() => downloadExport('world'))}
              title="Structured JSON export with a checksum, for archiving or transfer"
            >
              json
            </button>
          </div>
        </div>
      )}

      {/* Base cards */}
      <div className="dashboard-grid grid-2">
        {bases.map((base) => {
          const store = storageById.get(base.id);
          const isOpen = expanded === base.id;

          return (
            <div key={base.id} className="glass-card" style={{ padding: 20 }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 12 }}>
                <div>
                  <h3 style={{ fontSize: 15, fontWeight: 600, marginBottom: 2 }}>
                    {base.name}
                    {base.playerNamed === false && (
                      <span
                        style={{ fontSize: 10, color: 'var(--text-muted)', marginLeft: 6, fontWeight: 400 }}
                        title="This base has never been renamed in game, so the dashboard numbers it by position."
                      >
                        unnamed
                      </span>
                    )}
                  </h3>
                  <span style={{ fontSize: 12, color: 'var(--accent-purple)' }}>
                    {base.guildName}
                  </span>
                </div>
                <button
                  className="btn btn-ghost"
                  style={{ padding: '4px 8px', fontSize: 11 }}
                  onClick={() => setActiveTab('map')}
                >
                  <MapPin size={11} /> View on Map
                </button>
              </div>

              <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                <Stat icon={<Users size={12} style={{ color: 'var(--accent-cyan)' }} />}
                      text={`${base.palCount ?? 0} Pals working`}
                      title="Pals assigned to this base's worker container." />
                <Stat icon={<Users size={12} style={{ color: 'var(--accent-purple)' }} />}
                      text={`${base.guildPalCount ?? 0} in guild`}
                      title="Every Pal this guild owns, palboxes included. Shared across the guild's bases — do not add these up." />
                <Stat icon={<Package size={12} style={{ color: 'var(--accent-amber)' }} />}
                      text={`${store?.containerCount ?? base.containerIds.length} Containers`} />
                {store && (
                  <Stat icon={<Boxes size={12} style={{ color: 'var(--accent-emerald)' }} />}
                        text={`${store.itemCount.toLocaleString()} items · ${store.uniqueItems} types`} />
                )}
              </div>

              {store && store.totalSlots > 0 && (
                <div style={{ marginTop: 12 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
                    <span>Storage used</span>
                    <span>{store.usedSlots} / {store.totalSlots} slots ({store.fillPercent}%)</span>
                  </div>
                  <div style={{ height: 6, background: 'var(--bg-input)', borderRadius: 3, overflow: 'hidden' }}>
                    <div style={{
                      width: `${Math.min(100, store.fillPercent)}%`,
                      height: '100%',
                      background: store.fillPercent >= NEARLY_FULL
                        ? 'var(--accent-amber)'
                        : 'var(--accent-emerald)',
                    }} />
                  </div>
                </div>
              )}

              <div style={{
                marginTop: 12, padding: '8px 12px',
                background: 'var(--bg-input)', borderRadius: 6,
                fontFamily: 'JetBrains Mono, monospace', fontSize: 11,
                color: 'var(--text-muted)',
              }}>
                {formatCoords(base.x, base.y, base.z)}
              </div>

              {store && store.containerCount > 0 && (
                <>
                  <button
                    className="btn btn-ghost"
                    style={{ marginTop: 10, padding: '4px 8px', fontSize: 11 }}
                    onClick={() => setExpanded(isOpen ? null : base.id)}
                  >
                    {isOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                    {isOpen ? 'Hide contents' : 'What is in it?'}
                  </button>

                  {isOpen && <BaseContents store={store} onExport={exportReport} busy={busy} />}
                </>
              )}
            </div>
          );
        })}
      </div>

      {/* Guilds */}
      {guilds.length > 0 && (
        <div className="glass-card" style={{ padding: 20 }}>
          <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
            <Users size={16} style={{ color: 'var(--accent-purple)' }} />
            Guilds
          </h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {guilds.map((guild) => (
              <div key={guild.id} style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '10px 14px', background: 'var(--bg-input)', borderRadius: 8,
                border: '1px solid var(--border-primary)',
              }}>
                <div>
                  <span style={{ fontSize: 13, fontWeight: 500 }}>{guild.name}</span>
                  <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 8 }}>
                    {guild.members.length} members · {guild.baseCampIds.length} bases
                  </span>
                </div>
                <ChevronRight size={14} style={{ color: 'var(--text-muted)' }} />
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ icon, text, title }: { icon: React.ReactNode; text: string; title?: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }} title={title}>
      {icon}
      <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{text}</span>
    </div>
  );
}

function BaseContents({
  store, onExport, busy,
}: {
  store: BaseStorage;
  onExport: (report: string, format: ReportFormat, baseId?: string) => void;
  busy: boolean;
}) {
  const top = store.items.slice(0, 12);

  return (
    <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border-primary)' }}>
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          Top {top.length} of {store.uniqueItems} item types
        </span>
        <button
          className="btn btn-ghost"
          style={{ marginLeft: 'auto', padding: '2px 6px', fontSize: 10 }}
          disabled={busy}
          onClick={() => onExport('base-items', 'csv', store.baseId)}
          title="Export every item in this base as CSV"
        >
          <Download size={10} /> CSV
        </button>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
        {top.map((item) => (
          <div key={item.itemId} style={{
            display: 'flex', justifyContent: 'space-between',
            fontSize: 12, padding: '3px 0',
          }}>
            <span style={{ color: 'var(--text-secondary)' }}>{item.itemName}</span>
            <span className="mono" style={{ color: 'var(--text-muted)' }}>
              {item.count.toLocaleString()}
            </span>
          </div>
        ))}
      </div>

      <div style={{ marginTop: 10, fontSize: 11, color: 'var(--text-muted)' }}>
        {store.containers.length} containers ·{' '}
        {store.containers.filter((c) => c.usedSlots >= c.totalSlots && c.totalSlots > 0).length} full
      </div>
    </div>
  );
}
