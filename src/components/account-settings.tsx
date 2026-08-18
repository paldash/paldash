'use client';

import { useCallback, useEffect, useState } from 'react';
import { EyeOff, KeyRound, UserCircle, AlertTriangle, Check, Building2 } from 'lucide-react';
import {
  getMyPrivacy, setMyPrivacy, changeOwnPassword,
  getManageableBases, setBaseHidden,
} from '@/lib/save-api';
import { useDashboardStore } from '@/lib/store';
import { ROLE_LABEL, type Role } from '@/lib/auth-types';
import type { MyPrivacy, ManageableBase, ManageableBases } from '@/lib/types';
import { t, tl } from '@/lib/chrome';

/**
 * Your own account: who you are here, who can see you on the map, and your
 * password.
 *
 * The privacy control is deliberately self-service and not behind any management
 * capability. An Owner able to switch someone else's privacy off would defeat the
 * point of having it — and oversight does not need that, because the rule already
 * lets staff see everyone below them regardless of the setting.
 */
//: The privacy-mode labels arrive from the BACKEND (`backend/privacy.py`),
//: so the manifest scan cannot see them in a render expression. This mirror
//: is what puts them into the language packs; `t(mode.label)` at the render
//: site does the lookup. Keep in step with the backend — a drifted entry
//: costs nothing worse than that string staying English.
const PRIVACY_MODE_STRINGS = [
  tl('Visible to everyone'),
  tl('Hide me'),
  tl('Hide me and my solo bases'),
  tl('Hide me and my whole guild'),
  tl('Hide my bases, not me'),
];
void PRIVACY_MODE_STRINGS;

export default function AccountSettings() {
  const store = useDashboardStore();
  const [privacy, setPrivacy] = useState<MyPrivacy | null>(null);
  const [privacyError, setPrivacyError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getMyPrivacy()
      .then(setPrivacy)
      .catch((e: unknown) =>
        setPrivacyError(e instanceof Error ? e.message : 'Could not load your privacy setting')
      );
  }, []);

  const choose = async (mode: string) => {
    if (!privacy || mode === privacy.mode) return;
    setSaving(true);
    setPrivacyError(null);
    setSaved(false);
    try {
      await setMyPrivacy(mode);
      setPrivacy({ ...privacy, mode });
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e) {
      setPrivacyError(e instanceof Error ? e.message : 'Could not save');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18, maxWidth: 720 }}>
      <div className="glass-card" style={{ padding: 16 }}>
        <SectionTitle icon={<UserCircle size={15} />} text="Signed in as" />
        <dl style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '7px 16px', fontSize: 12 }}>
          <dt style={{ color: 'var(--text-muted)' }}>{t('Display name')}</dt>
          <dd>{store.user?.displayName || '—'}</dd>
          <dt style={{ color: 'var(--text-muted)' }}>{t('Username')}</dt>
          <dd className="mono">{store.user?.username || '—'}</dd>
          <dt style={{ color: 'var(--text-muted)' }}>{t('Role')}</dt>
          <dd>{ROLE_LABEL[store.userRole as Role] ?? store.userRole}</dd>
          <dt style={{ color: 'var(--text-muted)' }}>{t('Linked character')}</dt>
          <dd className="mono">
            {store.user?.steamUid
              ? store.user.steamUid
              : <span style={{ color: 'var(--text-muted)' }} className="mono">not linked</span>}
          </dd>
        </dl>
        {privacy && !privacy.linkedToPlayer && (
          <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 10 }}>
            Without a linked character this account has nothing on the map to hide,
            so the setting below has no effect yet. An Administrator links it from
            the Users tab.
          </p>
        )}
      </div>

      <div className="glass-card" style={{ padding: 16 }}>
        <SectionTitle icon={<EyeOff size={15} />} text="Map privacy" />
        <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 12 }}>
          Controls what other players see of you on the map and in the roster. It
          never hides you from staff — a setting that did would break moderation,
          so anyone ranked above you sees you regardless.
        </p>

        {privacyError && (
          <div className="notice notice-danger" style={{ fontSize: 12, marginBottom: 12 }}>
            {privacyError}
          </div>
        )}

        {!privacy && !privacyError && (
          <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>{t('Loading…')}</p>
        )}

        {privacy && (
          <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {privacy.modes.map((mode) => {
                const active = mode.id === privacy.mode;
                return (
                  <button
                    key={mode.id}
                    onClick={() => choose(mode.id)}
                    disabled={saving}
                    style={{
                      textAlign: 'left',
                      padding: '10px 12px',
                      background: active ? 'var(--bg-card-hover)' : 'var(--bg-surface)',
                      border: `1px solid ${active ? 'var(--accent)' : 'var(--border-primary)'}`,
                      borderRadius: 'var(--radius)',
                      cursor: saving ? 'not-allowed' : 'pointer',
                      color: 'var(--text-primary)',
                      opacity: saving && !active ? 0.6 : 1,
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, fontWeight: 600 }}>
                      {t(mode.label)}
                      {active && <Check size={13} style={{ color: 'var(--accent)' }} />}
                      {mode.id === 'guild' && (
                        <span className="badge" style={{ fontSize: 9 }}>affects your guildmates</span>
                      )}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 3 }}>
                      {t(mode.description)}
                    </div>
                  </button>
                );
              })}
            </div>

            {saved && (
              <div className="notice" style={{ fontSize: 12, marginTop: 12 }}>
                Saved. It applies to the next map or roster load.
              </div>
            )}

            {/* Naming the ranks rather than saying "peers and below" — a Trusted
                player has no way to know which roles that covers. */}
            <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 12 }}>
              {privacy.mode === 'off'
                ? 'Nothing is hidden right now.'
                : `Hidden from: ${privacy.hidesFrom
                    .map((r) => ROLE_LABEL[r as Role] ?? r)
                    .join(', ')}. Everyone above those ranks still sees you.`}
            </p>
          </>
        )}
      </div>

      <BaseVisibilityCard />
      <PasswordCard />
    </div>
  );
}

/**
 * Per-base visibility, for the narrower ask: a guild happy to be on the map that
 * would rather one particular base were not.
 *
 * Shown to everyone with an account, because the answer to "why can't I do this"
 * is itself worth reading — the backend returns a reason rather than an empty
 * list, since "you are not a guild master" and "nothing is hidden" look identical
 * otherwise.
 */
function BaseVisibilityCard() {
  const [data, setData] = useState<ManageableBases | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState('');

  const load = useCallback(() => {
    getManageableBases()
      .then(setData)
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : 'Could not load your bases')
      );
  }, []);

  useEffect(load, [load]);

  const toggle = async (base: ManageableBase) => {
    setBusy(base.baseId);
    setError(null);
    try {
      await setBaseHidden(base.baseId, !base.hidden);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save');
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="glass-card" style={{ padding: 16 }}>
      <SectionTitle icon={<Building2 size={15} />} text="Individual bases" />
      <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 12 }}>
        Bases belong to guilds, so this is the guild master&apos;s setting rather
        than any one member&apos;s. Hiding a base removes its marker, the objects
        standing inside it and its storage contents — your own guild still sees it,
        and so does anyone ranked above you.
      </p>

      {error && (
        <div className="notice notice-danger" style={{ fontSize: 12, marginBottom: 10 }}>
          {error}
        </div>
      )}

      {data?.reason && (
        <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>{data.reason}</p>
      )}

      {data?.bases.map((base) => (
        <div
          key={base.baseId}
          style={{
            display: 'flex', alignItems: 'center', gap: 10,
            padding: '8px 0', borderBottom: '1px solid var(--border-primary)',
          }}
        >
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 12 }}>{base.name}</div>
            <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>
              {base.guildName} · {base.authority}
            </div>
          </div>
          {base.hidden && <span className="badge badge-warning" style={{ fontSize: 9 }}>hidden</span>}
          <button
            className="btn btn-ghost"
            style={{ padding: '3px 10px', fontSize: 11 }}
            disabled={busy === base.baseId}
            onClick={() => void toggle(base)}
          >
            {base.hidden ? 'Show on map' : 'Hide from others'}
          </button>
        </div>
      ))}
    </div>
  );
}

/**
 * Changing a password revokes every session for the account — including this one.
 * That is the right behaviour (a password change is what you do when you think
 * something is compromised), but it has to be said before the button is pressed,
 * not discovered as a mysterious logout afterwards.
 */
function PasswordCard() {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (next !== confirm) {
      setError('The two new passwords do not match.');
      return;
    }
    setBusy(true);
    try {
      await changeOwnPassword(current, next);
      setCurrent(''); setNext(''); setConfirm('');
      setDone(true);
      // The session this page is holding is already dead server-side; reloading
      // lands on the sign-in screen instead of on a page whose every fetch 401s.
      setTimeout(() => window.location.reload(), 2500);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not change password');
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <div className="glass-card" style={{ padding: 16 }}>
        <SectionTitle icon={<KeyRound size={15} />} text="Password" />
        <div className="notice" style={{ fontSize: 12 }}>
          Password changed. All sessions were signed out — reloading so you can sign
          in again.
        </div>
      </div>
    );
  }

  return (
    <form className="glass-card" style={{ padding: 16 }} onSubmit={submit}>
      <SectionTitle icon={<KeyRound size={15} />} text="Password" />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 320 }}>
        <input
          className="input" type="password" placeholder={t('Current password')}
          value={current} onChange={(e) => setCurrent(e.target.value)}
          autoComplete="current-password" required
        />
        <input
          className="input" type="password" placeholder={t('New password')}
          value={next} onChange={(e) => setNext(e.target.value)}
          autoComplete="new-password" required
        />
        <input
          className="input" type="password" placeholder={t('New password again')}
          value={confirm} onChange={(e) => setConfirm(e.target.value)}
          autoComplete="new-password" required
        />
      </div>

      {error && (
        <div className="notice notice-danger" style={{ fontSize: 12, marginTop: 10 }}>{error}</div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 12 }}>
        <button className="btn btn-primary" type="submit" disabled={busy || !current || !next}>
          <KeyRound size={13} /> {busy ? 'Changing…' : 'Change password'}
        </button>
        <span style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, color: 'var(--text-muted)' }}>
          <AlertTriangle size={11} style={{ color: 'var(--accent-amber)' }} />
          Signs out every session, including this one.
        </span>
      </div>
    </form>
  );
}

function SectionTitle({ icon, text }: { icon: React.ReactNode; text: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 12, fontSize: 13, fontWeight: 600 }}>
      {icon} {text}
    </div>
  );
}
