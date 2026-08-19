'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { UserPlus, RefreshCw, Trash2, ShieldCheck, Ban, KeyRound } from 'lucide-react';
import {
  getUsers,
  getRolePresets,
  createUser,
  updateUser,
  deleteUser,
} from '@/lib/save-api';
import { useDashboardStore } from '@/lib/store';
import { ROLE_RANK, type Role } from '@/lib/auth-types';
import type { ManagedUser, RolePreset } from '@/lib/types';
import { t } from '@/lib/chrome';

/**
 * Account management.
 *
 * Everything here is also enforced in the backend — you cannot create or modify
 * an account above your own role, and the last Owner cannot be demoted, disabled
 * or deleted. The UI hides those actions; the server refuses them regardless.
 */
export default function UserManager() {
  const { user: me } = useDashboardStore();
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [roles, setRoles] = useState<RolePreset[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showCreate, setShowCreate] = useState(false);

  const [draft, setDraft] = useState({
    username: '',
    password: '',
    role: 'player',
    displayName: '',
    steamUid: '',
  });

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [list, presets] = await Promise.all([getUsers(), getRolePresets()]);
      setUsers(list);
      setRoles(presets.filter((r) => r.assignable));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load accounts');
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    queueMicrotask(load);
  }, [load]);

  const flash = (message: string) => {
    setStatus(message);
    setTimeout(() => setStatus(null), 4000);
  };

  const myRank = ROLE_RANK[(me?.role ?? 'guest') as Role] ?? 0;

  // You may only act on accounts at or below your own level.
  const manageable = useMemo(
    () => new Set(users.filter((u) => (ROLE_RANK[u.role as Role] ?? 0) <= myRank).map((u) => u.username)),
    [users, myRank]
  );

  const assignableRoles = roles.filter((r) => r.rank <= myRank);

  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      flash(label);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : t('Action failed'));
    } finally {
      setBusy(false);
    }
  };

  const submitCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    await act(`Created ${draft.username}`, async () => {
      await createUser({ ...draft, mustChangePassword: true });
      setDraft({ username: '', password: '', role: 'player', displayName: '', steamUid: '' });
      setShowCreate(false);
    });
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {error && <div className="notice notice-warn">{error}</div>}
      {status && <div className="notice">{status}</div>}

      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <button className="btn" onClick={() => setShowCreate((v) => !v)} disabled={busy}>
          <UserPlus size={13} /> {t('New account')}
        </button>
        <button className="btn btn-ghost" onClick={load} disabled={busy}>
          <RefreshCw size={13} /> {busy ? 'Working…' : 'Reload'}
        </button>
        <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-muted)' }}>
          {users.length} account{users.length === 1 ? '' : 's'}
        </span>
      </div>

      {showCreate && (
        <form className="glass-card" style={{ padding: 16 }} onSubmit={submitCreate}>
          <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>{t('New account')}</h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 10 }}>
            <label style={{ fontSize: 12 }}>
              {t('Username')}
              <input
                className="input"
                value={draft.username}
                onChange={(e) => setDraft({ ...draft, username: e.target.value })}
                required
                autoComplete="off"
              />
            </label>
            <label style={{ fontSize: 12 }}>
              Password
              <input
                className="input"
                type="password"
                value={draft.password}
                onChange={(e) => setDraft({ ...draft, password: e.target.value })}
                required
                minLength={10}
                autoComplete="new-password"
              />
            </label>
            <label style={{ fontSize: 12 }}>
              {t('Role')}
              <select
                className="input"
                value={draft.role}
                onChange={(e) => setDraft({ ...draft, role: e.target.value })}
              >
                {assignableRoles.map((r) => (
                  <option key={r.id} value={r.id}>{r.label}</option>
                ))}
              </select>
            </label>
            <label style={{ fontSize: 12 }}>
              {t('Display name')}
              <input
                className="input"
                value={draft.displayName}
                onChange={(e) => setDraft({ ...draft, displayName: e.target.value })}
              />
            </label>
            <label style={{ fontSize: 12 }}>
              Steam / player UID (optional)
              <input
                className="input"
                value={draft.steamUid}
                onChange={(e) => setDraft({ ...draft, steamUid: e.target.value })}
                placeholder={t('Links this login to a character in the save')}
              />
            </label>
          </div>
          <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '10px 0' }}>
            {assignableRoles.find((r) => r.id === draft.role)?.description}
          </p>
          <button className="btn" type="submit" disabled={busy}>{t('Create')}</button>
        </form>
      )}

      <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
        <table className="table">
          <thead>
            <tr>
              <th>{t('Account')}</th>
              <th>{t('Role')}</th>
              <th>{t('Linked character')}</th>
              <th>{t('Last sign-in')}</th>
              <th style={{ width: 140 }}></th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => {
              const editable = manageable.has(u.username);
              const isMe = u.username.toLowerCase() === me?.username?.toLowerCase();
              return (
                <tr key={u.id} style={{ opacity: u.disabled ? 0.5 : 1 }}>
                  <td>
                    <div style={{ color: 'var(--text-primary)' }}>
                      {u.displayName}
                      {isMe && (
                        <span style={{ color: 'var(--text-muted)', fontSize: 11, marginLeft: 6 }}>
                          (you)
                        </span>
                      )}
                    </div>
                    <div className="mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      {u.username}
                      {u.disabled && ' · disabled'}
                      {u.mustChangePassword && ' · must change password'}
                    </div>
                  </td>
                  <td>
                    {editable && !isMe ? (
                      <select
                        className="input"
                        style={{ padding: '3px 6px', fontSize: 12 }}
                        value={u.role}
                        disabled={busy}
                        onChange={(e) =>
                          act(`${u.username} is now ${e.target.value}`, () =>
                            updateUser(u.username, { role: e.target.value })
                          )
                        }
                      >
                        {assignableRoles.map((r) => (
                          <option key={r.id} value={r.id}>{r.label}</option>
                        ))}
                      </select>
                    ) : (
                      <span style={{ fontSize: 12 }}>
                        {roles.find((r) => r.id === u.role)?.label ?? u.role}
                      </span>
                    )}
                  </td>
                  <td className="mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    {u.steamUid || '—'}
                  </td>
                  <td style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    {u.lastLogin ? new Date(u.lastLogin).toLocaleString() : 'never'}
                  </td>
                  <td>
                    {editable && !isMe && (
                      <div style={{ display: 'flex', gap: 4 }}>
                        <button
                          className="btn btn-ghost"
                          style={{ padding: '3px 7px', fontSize: 11 }}
                          disabled={busy}
                          title={u.disabled ? 'Enable' : 'Disable — signs them out immediately'}
                          onClick={() =>
                            act(
                              u.disabled ? `${u.username} enabled` : `${u.username} disabled`,
                              () => updateUser(u.username, { disabled: !u.disabled })
                            )
                          }
                        >
                          {u.disabled ? <ShieldCheck size={12} /> : <Ban size={12} />}
                        </button>
                        <button
                          className="btn btn-ghost"
                          style={{ padding: '3px 7px', fontSize: 11 }}
                          disabled={busy}
                          title={t('Set a new password')}
                          onClick={() => {
                            const next = window.prompt(
                              `New password for ${u.username} (min 10 characters).\nThis signs them out everywhere.`
                            );
                            if (next) {
                              act(`Password reset for ${u.username}`, () =>
                                updateUser(u.username, { password: next })
                              );
                            }
                          }}
                        >
                          <KeyRound size={12} />
                        </button>
                        <button
                          className="btn btn-ghost"
                          style={{ padding: '3px 7px', fontSize: 11, color: 'var(--accent-red, #c25757)' }}
                          disabled={busy}
                          title={t('Delete this account')}
                          onClick={() => {
                            if (window.confirm(`Delete the account ${u.username}? This cannot be undone.`)) {
                              act(`Deleted ${u.username}`, () => deleteUser(u.username));
                            }
                          }}
                        >
                          <Trash2 size={12} />
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {!users.length && !busy && (
          <p style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
            No accounts yet.
          </p>
        )}
      </div>

      <div className="glass-card" style={{ padding: 16 }}>
        <h3 style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>{t('What each role can do')}</h3>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {roles.map((r) => (
            <div key={r.id} style={{ fontSize: 12 }}>
              <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{r.label}</span>
              <span style={{ color: 'var(--text-muted)', marginLeft: 8 }}>{r.description}</span>
            </div>
          ))}
        </div>
        <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 12 }}>
          A role grants a capability; the security level on the Access tab can still
          withhold it. Both must agree before anything is written.
        </p>
      </div>
    </div>
  );
}
