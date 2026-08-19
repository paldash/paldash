'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { FlaskConical, RefreshCw, Check, Lock } from 'lucide-react';
import { useDashboardStore } from '@/lib/store';
import { asArray } from '@/lib/arrays';
import { num } from '@/lib/format';
import GameIcon from '@/components/game-icon';
import { t } from '@/lib/chrome';

/**
 * The Pal Lab research tree, and how far a guild has got through it.
 *
 * Research is **guild-wide and permanent**, which is why this sits under Bases
 * rather than on a Pal: it is the one upgrade that explains why two identical
 * Pals produce differently on two different servers, and nothing in the
 * dashboard knew a guild had bought +10% Handiwork.
 *
 * **The catalogue renders with no parsed world.** 168 nodes with their costs
 * and effects are worth reading on a fresh server, so `known: false` shows the
 * tree without progress rather than an empty state — and the header says which
 * it is, because "0 of 168" and "we have not looked" must not share a
 * rendering.
 */

interface ResearchNode {
  id: string;
  work: string | null;
  workName: string | null;
  subType: string | null;
  requires: string | null;
  workAmount: number;
  materials: { itemId: string; name: string; icon: string; count: number }[];
  effect: {
    kind: string | null;
    label: string;
    value: number;
    unit?: string;
    category?: string | null;
    categoryLabel?: string;
  };
  essential: boolean;
  workDone?: number;
  complete?: boolean;
  inProgress?: boolean;
  isCurrent?: boolean;
  available?: boolean;
}

interface ResearchTree {
  nodes: ResearchNode[];
  byWork: Record<string, number>;
  known: boolean;
  note: string;
  completed?: number;
  total?: number;
  currentResearchId?: string;
  scopeRefused?: boolean;
}

export default function LabResearchPanel() {
  const { guilds, backendOnline } = useDashboardStore();
  const [tree, setTree] = useState<ResearchTree | null>(null);
  const [guildId, setGuildId] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [work, setWork] = useState('');
  const [hideDone, setHideDone] = useState(false);

  const load = useCallback(async (guild: string) => {
    setLoading(true);
    setError(null);
    try {
      const query = guild ? `?guild=${encodeURIComponent(guild)}` : '';
      const res = await fetch(`/api/save/world/research${query}`);
      if (!res.ok) throw new Error(`Research unavailable (${res.status})`);
      setTree(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the research tree');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (backendOnline) queueMicrotask(() => load(guildId));
  }, [backendOnline, guildId, load]);

  const nodes = asArray<ResearchNode>(tree?.nodes, 'research nodes');

  const workTypes = useMemo(() => {
    const seen = new Map<string, string>();
    for (const node of nodes) {
      if (node.work) seen.set(node.work, node.workName || node.work);
    }
    return [...seen].sort((a, b) => a[1].localeCompare(b[1]));
  }, [nodes]);

  const shown = useMemo(
    () =>
      nodes.filter(
        (n) => (!work || n.work === work) && !(hideDone && n.complete)
      ),
    [nodes, work, hideDone]
  );

  if (!backendOnline) return null;

  return (
    <div className="glass-card" style={{ padding: 16, marginTop: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
        <h3 style={{ display: 'flex', alignItems: 'center', gap: 6, margin: 0, fontSize: 14 }}>
          <FlaskConical size={15} /> Pal Lab research
        </h3>
        <div style={{ flex: 1 }} />

        {guilds.length > 0 && (
          <select
            className="select"
            value={guildId}
            onChange={(e) => setGuildId(e.target.value)}
            style={{ fontSize: 12, padding: '3px 6px' }}
          >
            {/* Empty means the catalogue with no progress, which is a real and
                useful view rather than a placeholder. */}
            <option value="">{t('The tree (no guild)')}</option>
            {guilds.map((g) => (
              <option key={g.id} value={g.id}>{g.name || g.id.slice(0, 8)}</option>
            ))}
          </select>
        )}

        <select
          className="select"
          value={work}
          onChange={(e) => setWork(e.target.value)}
          style={{ fontSize: 12, padding: '3px 6px' }}
        >
          <option value="">{t('All work types')}</option>
          {workTypes.map(([id, label]) => (
            <option key={id} value={id}>{label}</option>
          ))}
        </select>

        {tree?.known && (
          <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
            <input type="checkbox" checked={hideDone} onChange={(e) => setHideDone(e.target.checked)} />
            Hide finished
          </label>
        )}

        <button className="btn btn-ghost" onClick={() => load(guildId)} disabled={loading}
                style={{ padding: '3px 10px', fontSize: 11 }}>
          <RefreshCw size={11} /> {t('Refresh')}
        </button>
      </div>

      {error && <div className="notice notice-warn">{error}</div>}

      {tree?.scopeRefused && (
        <div className="notice" style={{ fontSize: 12, marginBottom: 8 }}>
          Showing the tree without progress — you are not in that guild.
        </div>
      )}

      {/* "0 of 168" and "we have not looked" must not share a rendering. */}
      <p style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 10 }}>
        {tree?.known
          ? `${num(tree.completed)} of ${num(tree.total)} researched.`
          : 'Showing the tree only — pick a guild to see what it has researched.'}
        {tree?.currentResearchId ? ` Currently researching ${tree.currentResearchId}.` : ''}
        {' '}Research is shared by the whole guild and is permanent.
      </p>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {shown.map((node) => (
          <div
            key={node.id}
            style={{
              display: 'flex', alignItems: 'flex-start', gap: 8, padding: '5px 7px',
              borderRadius: 4, fontSize: 12,
              background: node.isCurrent ? 'var(--bg-input)' : undefined,
              opacity: node.complete ? 0.55 : 1,
            }}
          >
            <span style={{ width: 15, flexShrink: 0, marginTop: 1 }}>
              {node.complete ? <Check size={13} color="var(--accent-green)" /> : null}
              {/* Locked means the PREREQUISITE is not done — never a claim
                  about whether the materials are in stock. */}
              {!node.complete && tree?.known && node.available === false ? (
                <Lock size={12} color="var(--text-muted)" />
              ) : null}
            </span>

            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'baseline' }}>
                <span style={{ color: 'var(--text-primary)' }}>{node.id}</span>
                <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>{node.workName}</span>
                {node.effect.kind ? (
                  <span style={{ fontSize: 11, color: 'var(--accent-blue)' }}>
                    {node.effect.value > 0 ? '+' : ''}{node.effect.value}
                    {node.effect.unit === 'percent' ? '%' : ''} {node.effect.label}
                  </span>
                ) : (
                  <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{node.effect.label}</span>
                )}
                {node.isCurrent && (
                  <span style={{ fontSize: 10, color: 'var(--accent-blue)' }}>· in progress now</span>
                )}
              </div>

              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 2 }}>
                {node.materials.map((m) => (
                  <span key={m.itemId} title={m.name}
                        style={{ display: 'inline-flex', alignItems: 'center', gap: 3,
                                 fontSize: 11, color: 'var(--text-muted)' }}>
                    <GameIcon src={m.icon} size={13} />{m.count}× {m.name}
                  </span>
                ))}
                {/* WORK UNITS, never a time. How long it takes depends on which
                    Pals are assigned, which no game file states. */}
                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  {num(node.workAmount)} work
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>

      {shown.length === 0 && !loading && (
        <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          Nothing matches those filters.
        </div>
      )}
    </div>
  );
}
