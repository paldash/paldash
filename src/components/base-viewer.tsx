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
import BaseSupplyPanel from '@/components/base-supply';
import BaseAssignPanel from '@/components/base-assign';
import BaseWorkingPanel from '@/components/base-working';
import LabResearchPanel from '@/components/lab-research';
import { t } from '@/lib/chrome';

/** A base this full is about to start dropping what its Pals produce. */
const NEARLY_FULL = 90;

export default function BaseViewer() {
  const { bases, guilds, backendOnline, setActiveTab, capabilities, saveDataError } =
    useDashboardStore();

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
        <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 4 }}>{t('Save Backend Offline')}</h3>
        <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>
          The Python backend must be running to view base camp data from save files.
        </p>
      </div>
    );
  }

  // An empty list has two completely different causes and this used to state
  // one of them as fact. "Make sure save files are accessible" is a guess, and
  // it is the wrong guess whenever the fetch failed for a reason the dashboard
  // already knows — a role that lacks the capability, a world that has not been
  // parsed, a backend that is up but refusing. `saveDataError` carries the real
  // one; only when there is none is "the world has no bases" the honest answer.
  if (bases.length === 0) {
    return (
      <div className="glass-card" style={{ padding: 40, textAlign: 'center' }}>
        <Building2 size={40} style={{ color: 'var(--text-muted)', marginBottom: 12 }} />
        <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 4 }}>
          {saveDataError ? 'Could not load base camps' : 'No Base Camps Found'}
        </h3>
        <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>
          {saveDataError ??
            'The parsed world contains no base camps. If you expected some, press ' +
            'Refresh on the Overview tab to re-parse the save.'}
        </p>
      </div>
    );
  }

  const storageById = new Map(storage.map((s) => [s.baseId, s]));
  const storedTotal = storage.reduce((acc, s) => acc + s.itemCount, 0);
  const full = storage.filter((s) => s.fillPercent >= NEARLY_FULL);
  const deployedPals = bases.reduce((acc, b) => acc + (b.palCount ?? 0), 0);
  const scopeHint = maySeeDetail
    ? undefined
    : 'This server shows you your own guild\u2019s bases. An Administrator sets who ' +
      'sees everyone\u2019s on the Access tab.';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {error && <div className="notice notice-warn">{error}</div>}

      {/* Summary */}
      <div className="dashboard-grid grid-4">
        {/* "Total" is a claim about the server, and below VIEW_DETAIL these
            lists are filtered to what this viewer may see — by `baseVisibility`,
            by per-player privacy, and by per-base visibility. Labelling a
            filtered count as a total is the same class of mistake the breeding
            planner's "All Pals on the server" header made: the number is right
            and the word above it is wrong, so nothing looks broken. */}
        <div className="stat-card">
          <div className="stat-value" style={{ color: 'var(--accent-amber)' }}>{bases.length}</div>
          <div className="stat-label" title={scopeHint}>
            {maySeeDetail ? 'Total Bases' : 'Bases You Can See'}
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-value" style={{ color: 'var(--accent-purple)' }}>{guilds.length}</div>
          <div className="stat-label" title={scopeHint}>
            {maySeeDetail ? 'Guilds' : 'Your Guilds'}
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-value" style={{ color: 'var(--accent-emerald)' }}>
            {/* Pals working at a base. Safe to sum — each base owns its own
                worker container, so nothing is counted twice. */}
            {deployedPals.toLocaleString()}
          </div>
          <div className="stat-label" title={t('Pals assigned to work at a base. Pals in a palbox are counted under guild Pals instead.')}>
            {maySeeDetail ? 'Pals Working at Bases' : 'Pals Working at Your Bases'}
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

      {/* Said in the page, not only in a tooltip. Two people independently read
          "I cannot see my friend's base" as the dashboard being broken, and the
          setting that causes it lives on a tab a Player cannot open. */}
      {!maySeeDetail && (
        <p style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          You are seeing your own guild&apos;s bases. Whether players see other
          guilds&apos; bases is a server setting an Administrator controls on the
          Access tab.
        </p>
      )}

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
              title={t('Structured JSON export with a checksum, for archiving or transfer')}
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
                        title={t('This base has never been renamed in game, so the dashboard numbers it by position.')}
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
                      /* A count with no denominator answers nothing: "11 Pals
                         here" is full on one base and a third full on another.
                         The capacity is absent rather than 0 when the worker
                         container did not resolve, so fall back to the bare
                         count instead of rendering "11 / 0". */
                      text={base.workerCapacity
                        ? `${base.palCount ?? 0} / ${base.workerCapacity} Pals working`
                        : `${base.palCount ?? 0} Pals working`}
                      title={base.workerCapacity
                        ? `Pals assigned to this base's worker container, of ${base.workerCapacity} slots. `
                          + 'The capacity is what the game allocated for this base — it already '
                          + "accounts for the server's BaseCampWorkerMaxNum and the base's level."
                        : "Pals assigned to this base's worker container. Capacity unknown."} />
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
                    <span>{t('Storage used')}</span>
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
                    {guild.members.length}
                    {/* Only when the INI was readable. `GuildPlayerMaxNum` is
                        the operator's setting, not a game rule, so an absent
                        cap means no denominator rather than a default. */}
                    {guild.memberCap ? ` of ${guild.memberCap}` : ''} members
                    {' · '}{guild.baseCampIds.length} bases
                    {/* The save's own `base_camp_level`, parsed since Phase 4 and
                        never shown. Rendered as a guild figure with no per-base
                        arithmetic done to it — see GuildInfo for why. */}
                    {guild.baseCampLevel ? ` · base camp level ${guild.baseCampLevel}` : ''}
                  </span>
                  {/* WHO CAN OPEN THE CHEST. The dashboard has reported what is
                      in a guild chest since guild storage shipped and never who
                      may reach it — "40,000 Ore in a box two of four ranks can
                      open" is the operational half. Ranks are named; the
                      permission numbers beside them are not shown at all,
                      because the game's enum order is unestablished. */}
                  {guild.chestAllowedRoleNames?.length ? (
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 3 }}>
                      Guild chest: {guild.chestAllowedRoleNames.join(', ')}
                      {guild.roleCount
                        ? ` (${guild.chestAllowedRoleNames.length} of ${guild.roleCount} ranks)`
                        : ''}
                    </div>
                  ) : null}
                </div>
                <ChevronRight size={14} style={{ color: 'var(--text-muted)' }} />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Supply. Its own section rather than a column on the base list, because
          the guild chest is a guild-level container and does not belong in a
          per-base row at all — see `basesupply`. */}
      <BaseSupplyPanel />

      {/* Who should work where. Separate from supply because it answers a
          different question off different data — structures and Pals rather
          than containers — and because it recommends where supply only
          reports. */}
      <BaseAssignPanel />

      {/* Directly beneath, because the pair is the point: the panel above ranks
          who SHOULD work where, this reports who the game actually assigned.
          Separating them across tabs would let a recommendation be mistaken for
          a fact, which is the one confusion worth spending vertical space on. */}
      <BaseWorkingPanel />

      {/* Guild-wide and permanent, which is why it sits with Bases rather than
          on a Pal: it is the upgrade that explains why two identical Pals
          produce differently on two different servers. */}
      <LabResearchPanel />
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
          title={t('Export every item in this base as CSV')}
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
