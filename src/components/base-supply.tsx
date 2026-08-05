'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  Package, RefreshCw, AlertTriangle, Warehouse, Utensils, Egg, Info,
} from 'lucide-react';
import { getBaseSupply, type SupplyReport, type BaseSupply, type SupplyContainer } from '@/lib/save-api';
import GameIcon from '@/components/game-icon';

/**
 * What each base is holding, and what is conspicuously missing.
 *
 * THIS PANEL REPORTS FACTS AND NEVER PRESCRIBES, which is a deliberate limit
 * rather than a shortfall of ambition. `DT_MapObjectMasterDataTable` decodes out
 * of the server pak and confirms Feed Box, Guild Chest, Breeding Farm and
 * Medicine Rack are distinct structures — but its columns are HP, Defense and
 * material type, so **no game file this project can read says what any of them
 * consumes**. "This base has a Feed Box and it is empty" needs no rule cited.
 * "Move your food out of the chest" would be a claim about game behaviour that
 * nothing here can back, so it is not made.
 *
 * THE GUILD CHEST IS NOT A BASE CONTAINER. There is exactly one per guild,
 * shared by every base that guild owns, so it is shown in its own section. Two
 * chests placed at two bases in one guild are two doors into the same box, and
 * printing its contents against each base would report stock that is not there.
 *
 * THE FLOOR IS THE OPERATOR'S NUMBER, NOT THE GAME'S. Every material in the game
 * stacks to 9,999, so "keep one stack at each base" resolves to 110,000 Wood
 * across eleven bases — not what anyone means by a reserve. The control says so
 * next to itself rather than letting a chosen threshold read as a game rule.
 */

const FLOORS = [100, 250, 500, 1000, 2000];

function Containers({ rows, empty }: { rows: SupplyContainer[]; empty: string }) {
  if (rows.length === 0) {
    return <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>{empty}</div>;
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {rows.map((row) => (
        <div key={row.containerId} style={{ fontSize: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
            <span style={{ color: 'var(--text-primary)' }}>{row.kindName}</span>
            <span style={{ color: row.itemCount === 0 ? 'var(--status-warning)' : 'var(--text-muted)' }}>
              {row.itemCount === 0
                ? 'empty'
                : `${row.usedSlots}/${row.totalSlots} slots`}
            </span>
          </div>
          {row.items.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 4 }}>
              {row.items.slice(0, 8).map((item) => (
                <span
                  key={item.itemId}
                  title={item.itemName}
                  style={{
                    display: 'inline-flex', alignItems: 'center', gap: 4,
                    background: 'var(--bg-input)', borderRadius: 4, padding: '2px 6px',
                    color: 'var(--text-secondary)',
                  }}
                >
                  <GameIcon src={item.icon} size={14} />
                  {item.count.toLocaleString()}
                </span>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function BaseCard({ base }: { base: BaseSupply }) {
  return (
    <div
      style={{
        background: 'var(--bg-card)', border: '1px solid var(--border-primary)', borderRadius: 8,
        padding: 14, display: 'flex', flexDirection: 'column', gap: 10,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
        <div>
          <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{base.baseName}</div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            {base.guildName || 'No guild'} · {base.palCount} Pals
          </div>
        </div>
      </div>

      {base.notes.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {base.notes.map((note) => (
            <div
              key={note.kind}
              style={{
                display: 'flex', alignItems: 'flex-start', gap: 6, fontSize: 12,
                color: 'var(--status-warning)',
              }}
            >
              <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1 }} />
              <span>{note.text}</span>
            </div>
          ))}
        </div>
      )}

      <div>
        <div style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 5, display: 'flex', alignItems: 'center', gap: 5 }}>
          <Utensils size={12} /> Food boxes
        </div>
        <Containers rows={base.feedBoxes} empty="None built at this base." />
      </div>

      {base.breedingFarms.length > 0 && (
        <div>
          <div style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 5, display: 'flex', alignItems: 'center', gap: 5 }}>
            <Egg size={12} /> Breeding farms
          </div>
          <Containers rows={base.breedingFarms} empty="" />
        </div>
      )}

      <div>
        <div style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 5 }}>
          Staple materials
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {base.staples.map((staple) => (
            <span
              key={staple.itemId}
              title={`${staple.itemName}: ${staple.count.toLocaleString()} held (stacks to ${staple.stackSize.toLocaleString()})`}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 4,
                background: staple.below ? 'var(--bg-surface)' : 'var(--bg-input)',
                border: `1px solid ${staple.below ? 'var(--status-warning)' : 'var(--border-primary)'}`,
                borderRadius: 4, padding: '2px 6px', fontSize: 12,
                color: staple.below ? 'var(--status-warning)' : 'var(--text-secondary)',
              }}
            >
              <GameIcon src={staple.icon} size={14} />
              {staple.count.toLocaleString()}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function BaseSupplyPanel() {
  const [report, setReport] = useState<SupplyReport | null>(null);
  const [floor, setFloor] = useState(500);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async (value: number) => {
    setLoading(true);
    setError('');
    try {
      setReport(await getBaseSupply(value));
    } catch (e) {
      // Reported rather than swallowed into an empty list: an empty supply
      // report and a failed fetch look identical otherwise, which is exactly
      // how the base-marker outage went undiagnosed.
      setError(e instanceof Error ? e.message : String(e));
      setReport(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(floor); }, [load, floor]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <h3 style={{ display: 'flex', alignItems: 'center', gap: 7, margin: 0, fontSize: 15, color: 'var(--text-primary)' }}>
          <Package size={16} /> Base supply
        </h3>
        <div style={{ flex: 1 }} />
        <label style={{ fontSize: 12, color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: 6 }}>
          Flag below
          <select
            value={floor}
            onChange={(e) => setFloor(Number(e.target.value))}
            style={{
              background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border-primary)',
              borderRadius: 4, padding: '3px 6px', fontSize: 12,
            }}
          >
            {FLOORS.map((f) => <option key={f} value={f}>{f.toLocaleString()}</option>)}
          </select>
        </label>
        <button
          onClick={() => void load(floor)}
          disabled={loading}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 5,
            background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border-primary)',
            borderRadius: 4, padding: '4px 9px', fontSize: 12, cursor: 'pointer',
          }}
        >
          <RefreshCw size={13} className={loading ? 'spin' : undefined} /> Refresh
        </button>
      </div>

      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6, fontSize: 11, color: 'var(--text-muted)' }}>
        <Info size={12} style={{ flexShrink: 0, marginTop: 2 }} />
        <span>
          The threshold is yours, not the game&apos;s — every material here stacks to
          9,999. This panel reports what is stored where; the game files do not say
          what any structure consumes, so it does not tell you what to move.
        </span>
      </div>

      {error && (
        <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--accent-red)', borderRadius: 6, padding: 10, fontSize: 12, color: 'var(--status-warning)' }}>
          Could not load the supply report: {error}
        </div>
      )}

      {report && report.guildChests.length > 0 && (
        <div>
          <div style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 5 }}>
            <Warehouse size={12} /> Guild chests — one per guild, shared by all its bases
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 10 }}>
            {report.guildChests.map((chest) => (
              <div key={chest.guildId} style={{ background: 'var(--bg-card)', border: '1px solid var(--border-primary)', borderRadius: 8, padding: 12 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                  <span style={{ fontWeight: 600, color: 'var(--text-primary)', fontSize: 13 }}>{chest.guildName}</span>
                  <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    {chest.usedSlots}/{chest.totalSlots} slots
                  </span>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
                  {chest.items.slice(0, 12).map((item) => (
                    <span
                      key={item.itemId}
                      title={item.itemName}
                      style={{ display: 'inline-flex', alignItems: 'center', gap: 4, background: 'var(--bg-input)', borderRadius: 4, padding: '2px 6px', fontSize: 12, color: 'var(--text-secondary)' }}
                    >
                      <GameIcon src={item.icon} size={14} />
                      {item.count.toLocaleString()}
                    </span>
                  ))}
                  {chest.items.length === 0 && (
                    <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Empty.</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {report && (
        report.bases.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            No bases to report on. Either the world has not been parsed yet, or none
            are visible to this account.
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 10 }}>
            {report.bases.map((base) => <BaseCard key={base.baseId} base={base} />)}
          </div>
        )
      )}
    </div>
  );
}
