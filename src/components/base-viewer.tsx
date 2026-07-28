'use client';

import { useDashboardStore } from '@/lib/store';
import { Building2, MapPin, Users, Package, ChevronRight } from 'lucide-react';
import { formatCoords } from '@/lib/map-coordinates';

export default function BaseViewer() {
  const { bases, guilds, backendOnline, setActiveTab } = useDashboardStore();

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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Summary Bar */}
      <div className="dashboard-grid grid-3">
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
            {bases.reduce((acc, b) => acc + b.palCount, 0)}
          </div>
          <div className="stat-label">Total Base Pals</div>
        </div>
      </div>

      {/* Base Cards Grid */}
      <div className="dashboard-grid grid-2">
        {bases.map(base => (
          <div key={base.id} className="glass-card" style={{ padding: 20 }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 12 }}>
              <div>
                <h3 style={{ fontSize: 15, fontWeight: 600, marginBottom: 2 }}>
                  {base.name || 'Base Camp'}
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
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <Users size={12} style={{ color: 'var(--accent-cyan)' }} />
                <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                  {base.palCount} Pals
                </span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <Package size={12} style={{ color: 'var(--accent-amber)' }} />
                <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                  {base.containerIds.length} Containers
                </span>
              </div>
            </div>

            <div style={{
              marginTop: 12, padding: '8px 12px',
              background: 'var(--bg-input)', borderRadius: 6,
              fontFamily: 'JetBrains Mono, monospace', fontSize: 11,
              color: 'var(--text-muted)'
            }}>
              {formatCoords(base.x, base.y, base.z)}
            </div>
          </div>
        ))}
      </div>

      {/* Guild List */}
      {guilds.length > 0 && (
        <div className="glass-card" style={{ padding: 20 }}>
          <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
            <Users size={16} style={{ color: 'var(--accent-purple)' }} />
            Guilds
          </h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {guilds.map(guild => (
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
