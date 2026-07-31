'use client';

import { useCallback, useEffect, useState } from 'react';
import { Building2, Compass, Egg, Eye, Lock, Package, Pickaxe, RefreshCw, ShieldCheck } from 'lucide-react';
import { getAccessPolicy, setAccessPolicy, type AccessPolicyInfo } from '@/lib/save-api';

/**
 * Access control: how much may be changed, and what guests may see.
 *
 * The security level is a ceiling on writes for everyone including admins —
 * it protects the world from mistakes rather than expressing distrust. Guest
 * visibility toggles are purely about what non-admins can read.
 */

const VISIBILITY_LABELS: Record<string, { label: string; hint: string }> = {
  serverStatus: { label: 'Server status', hint: 'Online/offline, FPS, player counts' },
  onlinePlayers: { label: 'Online players', hint: 'Live player list and their map positions' },
  bases: { label: 'Guild bases', hint: 'Base locations and build radius on the map' },
  guilds: { label: 'Guilds', hint: 'Guild names and membership' },
  mapObjects: { label: 'Map objects', hint: 'Palboxes, breeding farms, benches, production' },
  chests: { label: 'Chests', hint: 'Chest locations — effectively a treasure map' },
  items: { label: 'Item totals', hint: 'Server-wide totals of every item' },
  breeding: { label: 'Breeding & palboxes', hint: 'Which Pals players own, with IVs and passives' },
};

export default function AccessSettings() {
  const [policy, setPolicy] = useState<AccessPolicyInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setPolicy(await getAccessPolicy());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load access policy');
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const flash = (msg: string) => {
    setStatus(msg);
    setTimeout(() => setStatus(null), 5000);
  };

  const apply = async (update: Parameters<typeof setAccessPolicy>[0]) => {
    setBusy(true);
    try {
      setPolicy(await setAccessPolicy(update));
      flash('Access policy updated.');
    } catch (e) {
      flash(e instanceof Error ? e.message : 'Update failed');
    } finally {
      setBusy(false);
    }
  };

  if (error) {
    return (
      <div className="notice notice-warn">
        <strong>Access policy unavailable</strong>
        <div style={{ marginTop: 6 }}>{error}</div>
        <button className="btn btn-ghost" style={{ marginTop: 12 }} onClick={load}>
          <RefreshCw size={13} /> Retry
        </button>
      </div>
    );
  }

  if (!policy) {
    return <div style={{ color: 'var(--text-muted)', fontSize: 13 }}>Loading…</div>;
  }

  const ceilingIndex = ['readonly', 'safe', 'full'].indexOf(policy.envCeiling);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {status && <div className="notice">{status}</div>}

      {/* ─── Security level ─── */}
      <div className="glass-card" style={{ padding: 16 }}>
        <div className="section-title" style={{ marginBottom: 4 }}>
          <ShieldCheck size={14} /> Security level
        </div>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
          Caps what any session — including yours — is allowed to modify.
        </p>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {policy.levels.map((level, index) => {
            const blocked = index > ceilingIndex;
            const active = policy.securityLevel === level.id;
            return (
              <label
                key={level.id}
                style={{
                  display: 'flex', alignItems: 'flex-start', gap: 10,
                  padding: '10px 12px', borderRadius: 'var(--radius)',
                  background: active ? 'var(--bg-card-hover)' : 'var(--bg-surface)',
                  border: `1px solid ${active ? 'var(--accent)' : 'var(--border-primary)'}`,
                  cursor: blocked ? 'not-allowed' : 'pointer',
                  opacity: blocked ? 0.5 : 1,
                }}
              >
                <input
                  type="radio"
                  name="securityLevel"
                  checked={active}
                  disabled={blocked || busy}
                  onChange={() => apply({ securityLevel: level.id })}
                  style={{ marginTop: 3 }}
                />
                <div>
                  <div style={{ fontSize: 13, fontWeight: 500 }}>
                    {level.label}
                    {blocked && (
                      <span className="badge" style={{ marginLeft: 8 }}>
                        <Lock size={9} /> blocked by compose
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
                    {level.description}
                  </div>
                </div>
              </label>
            );
          })}
        </div>

        <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 12, lineHeight: 1.6 }}>
          <span className="mono">SECURITY_LEVEL={policy.envCeiling}</span> in your
          environment sets the ceiling. Levels above it cannot be enabled from
          here — change it in your compose file and restart. That way a
          compromised admin session cannot unlock writes you disabled.
        </p>
      </div>

      {/* ─── Guest visibility ─── */}
      <div className="glass-card" style={{ padding: 16 }}>
        <div className="section-title" style={{ marginBottom: 4 }}>
          <Eye size={14} /> Guest visibility
        </div>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
          What people without the admin password can see. Admins always see
          everything. Enforced server-side, so turning something off actually
          removes it from the API rather than just hiding a tab.
        </p>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {policy.visibilityKeys.map((key) => {
            const meta = VISIBILITY_LABELS[key] ?? { label: key, hint: '' };
            const on = policy.guestVisibility[key] === true;
            return (
              <label
                key={key}
                style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  padding: '8px 10px', borderRadius: 'var(--radius)',
                  cursor: busy ? 'wait' : 'pointer',
                }}
              >
                <input
                  type="checkbox"
                  checked={on}
                  disabled={busy}
                  onChange={() =>
                    apply({ guestVisibility: { ...policy.guestVisibility, [key]: !on } })
                  }
                />
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13 }}>{meta.label}</div>
                  {meta.hint && (
                    <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{meta.hint}</div>
                  )}
                </div>
                <span className="badge" style={{ opacity: on ? 1 : 0.5 }}>
                  {on ? 'visible' : 'hidden'}
                </span>
              </label>
            );
          })}
        </div>
      </div>

      {/* ─── Visibility presets ───
          These stay five independent settings on purpose — see
          `policy.VISIBILITY_PRESETS` for why a single "openness" dial cannot
          work. A preset is a starting point that writes all four and then gets
          out of the way; nothing below is locked afterwards. */}
      <div className="glass-card" style={{ padding: 16 }}>
        <div className="section-title" style={{ marginBottom: 4 }}>
          <Compass size={14} /> Visibility preset
        </div>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
          A starting point for the four settings below. They stay independently
          adjustable afterwards — real servers disagree across these, not along
          them, so there is no single dial that gets everyone right.
        </p>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {(policy.visibilityPresets ?? []).map((preset) => (
            <button
              key={preset.id}
              className="btn"
              disabled={busy}
              style={{
                flex: '1 1 200px',
                textAlign: 'left',
                padding: '10px 12px',
                alignItems: 'flex-start',
                flexDirection: 'column',
                gap: 3,
                borderColor: preset.active ? 'var(--border-accent)' : 'var(--border-primary)',
                background: preset.active ? 'var(--bg-card-hover)' : 'transparent',
              }}
              onClick={() => apply({ visibilityPreset: preset.id })}
            >
              <span style={{ fontSize: 12, fontWeight: 600 }}>
                {preset.label}
                {preset.active && (
                  <span className="badge" style={{ marginLeft: 6 }}>current</span>
                )}
              </span>
              <span style={{ fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'normal' }}>
                {preset.description}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* ─── Undiscovered content ─── */}
      <div className="glass-card" style={{ padding: 16 }}>
        <div className="section-title" style={{ marginBottom: 4 }}>
          <Compass size={14} /> Undiscovered locations
        </div>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
          The dashboard knows where all 174 fast-travel points and 396 effigies are,
          whether or not anyone has found them. This decides who sees the ones they
          have not. Everyone always sees their own discoveries.
        </p>
        <ThresholdPicker
          value={policy.discoveryVisibility}
          options={policy.discoveryLevels}
          busy={busy}
          onPick={(level) => apply({ discoveryVisibility: level })}
        />
      </div>

      {/* ─── Guild bases ─── */}
      <div className="glass-card" style={{ padding: 16 }}>
        <div className="section-title" style={{ marginBottom: 4 }}>
          <Building2 size={14} /> Other guilds&apos; bases
        </div>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
          Per-player privacy only protects people who have a dashboard account —
          someone who has never signed in has no privacy setting to apply, so their
          bases were visible to every signed-in player regardless. This is the
          server-wide rule, and it needs no account to take effect. Moderators and
          above always see every base, so moderation still works.
        </p>
        <ThresholdPicker
          value={policy.baseVisibility}
          options={policy.baseVisibilityLevels}
          busy={busy}
          onPick={(level) => apply({ baseVisibility: level })}
        />
      </div>

      {/* ─── Scope: whose data a view covers ─── */}
      <div className="glass-card" style={{ padding: 16 }}>
        <div className="section-title" style={{ marginBottom: 4 }}>
          <Package size={14} /> Server-wide item totals
        </div>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
          A server-wide total answers &ldquo;what exists here&rdquo;, which is an
          operations question rather than a player-facing one, and it discloses
          every guild&apos;s holdings in a single figure. Below this threshold the
          Items tab scopes to the viewer&apos;s own guilds instead of refusing.
        </p>
        <ThresholdPicker
          value={policy.serverTotalsVisibility}
          options={policy.scopeLevels}
          busy={busy}
          onPick={(level) => apply({ serverTotalsVisibility: level })}
        />
      </div>

      <div className="glass-card" style={{ padding: 16 }}>
        <div className="section-title" style={{ marginBottom: 4 }}>
          <Egg size={14} /> Everyone&apos;s Pals in breeding
        </div>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
          Below this, the breeding planner and the Pal list are scoped to the
          viewer&apos;s own character — which is the common case, and what makes
          both usable by a plain Player. Above it, they can plan across every Pal
          on the server.
        </p>
        <ThresholdPicker
          value={policy.allPalsVisibility}
          options={policy.scopeLevels}
          busy={busy}
          onPick={(level) => apply({ allPalsVisibility: level })}
        />
      </div>

      {/* ─── Static world objects, per category ─── */}
      {policy.worldObjectCategories.length > 0 && (
        <div className="glass-card" style={{ padding: 16 }}>
          <div className="section-title" style={{ marginBottom: 4 }}>
            <Pickaxe size={14} /> Static world objects
          </div>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
            Ore, chests, fishing spots and oil fields read out of the game&apos;s own
            files — every one that exists, not just the ones your save has touched.
            Set separately per category, because a complete chest map is close to a
            loot solution while a fishing-spot map is a convenience.
            {' '}<strong>A category someone may not see is not listed to them either</strong>,
            so they are not told what they are missing.
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {policy.worldObjectCategories.map((category) => {
              const current =
                policy.worldObjectVisibility[category.id] ?? 'everyone';
              return (
                <div key={category.id}>
                  <div style={{
                    display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 5,
                  }}>
                    <span style={{ fontSize: 13 }}>{category.label}</span>
                    <span style={{ fontSize: 11, color: 'var(--text-muted)' }} className="mono">
                      {category.count.toLocaleString()}
                    </span>
                  </div>
                  <ThresholdPicker
                    value={current}
                    options={policy.discoveryLevels}
                    busy={busy}
                    onPick={(level) =>
                      apply({
                        worldObjectVisibility: {
                          ...policy.worldObjectVisibility,
                          [category.id]: level,
                        },
                      })
                    }
                  />
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ─── Effective capabilities ─── */}
      <div className="glass-card" style={{ padding: 16 }}>
        <div className="section-title" style={{ marginBottom: 8 }}>Currently permitted writes</div>
        {policy.allowedCapabilities.length === 0 ? (
          <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>
            None — the dashboard cannot modify anything at this level.
          </p>
        ) : (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {policy.allowedCapabilities.map((capability) => (
              <span key={capability} className="badge mono">{capability}</span>
            ))}
          </div>
        )}
        <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 10 }}>
          These are grantable individually per user in a later release; today they
          all belong to the admin role.
        </p>
      </div>
    </div>
  );
}

/**
 * A role-threshold selector, shared by the discovery and world-object dials.
 *
 * The vocabulary is the same in both places — `everyone`, a role name meaning
 * that rank and above, or `nobody` — and the options come from the backend rather
 * than being listed here, so the ladder cannot drift from `roles.py`.
 *
 * Rendered as a row of buttons rather than a `<select>` because the choice is an
 * ordered ladder and seeing where the current setting sits on it is the point.
 */
function ThresholdPicker({
  value, options, busy, onPick,
}: {
  value: string;
  options: { id: string; label: string; description: string }[];
  busy: boolean;
  onPick: (level: string) => void;
}) {
  const active = options.find((o) => o.id === value);
  return (
    <div>
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
        {options.map((option) => {
          const on = option.id === value;
          return (
            <button
              key={option.id}
              className="btn"
              style={{
                padding: '3px 10px',
                fontSize: 11,
                background: on ? 'var(--bg-card-hover)' : 'transparent',
                color: on ? 'var(--text-primary)' : 'var(--text-muted)',
                borderColor: on ? 'var(--accent)' : 'var(--border-primary)',
              }}
              disabled={busy}
              onClick={() => onPick(option.id)}
              title={option.description}
            >
              {option.label}
            </button>
          );
        })}
      </div>
      {active && (
        <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>
          {active.description}
        </p>
      )}
    </div>
  );
}
