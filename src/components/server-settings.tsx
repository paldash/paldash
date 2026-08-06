'use client';

import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Check, RefreshCw, Save, Power, Sliders } from 'lucide-react';
import {
  applySettingsPreset, getBackendHealth, getIniSettings,
  noteShutdown, restartServer, writeIniSettings,
} from '@/lib/save-api';
import { announce, shutdownServer } from '@/lib/api';
import { useDashboardStore } from '@/lib/store';
import type { IniOption, IniSettings, LifecycleStatus } from '@/lib/types';

/**
 * PalWorldSettings.ini editor.
 *
 * Nothing here is hot-swappable: the dedicated server reads this file at boot
 * only, and the REST API has no settings-write endpoint. So the UI is honest
 * about it — changes are written immediately, but they apply on the next
 * restart, and there is a one-click "announce + restart" flow to make that
 * happen safely.
 */
export default function ServerSettings() {
  const { serverStatus } = useDashboardStore();
  const [settings, setSettings] = useState<IniSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<Record<string, string | number | boolean>>({});
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [restartSeconds, setRestartSeconds] = useState(60);
  const [lifecycle, setLifecycle] = useState<LifecycleStatus | null>(null);

  // Poll lifecycle so the "did it come back?" banner stays current.
  useEffect(() => {
    const tick = () =>
      getBackendHealth()
        .then((h) => setLifecycle(h.lifecycle ?? null))
        .catch(() => undefined);
    tick();

    // Same guard as the dashboard's own pollers: nothing probes while the tab
    // is hidden, and returning to it refreshes immediately rather than waiting
    // out the interval. That second half matters more here than anywhere else —
    // this is the "did the server come back?" banner, and the moment somebody
    // looks is exactly the moment they want the answer.
    const hidden = () => typeof document !== 'undefined' && document.hidden;
    // Raising a window fires `focus` and `visibilitychange` together, so without
    // this the catch-up is two requests every time. Same guard the other two
    // pollers carry; keeping the three consistent is the point, since an
    // exception here is how the next one gets written without it.
    let last = Date.now();
    const guarded = () => {
      if (hidden() || Date.now() - last < 3000) return;
      last = Date.now();
      tick();
    };
    document.addEventListener('visibilitychange', guarded);
    window.addEventListener('focus', guarded);
    const id = setInterval(guarded, 10000);
    return () => {
      document.removeEventListener('visibilitychange', guarded);
      window.removeEventListener('focus', guarded);
      clearInterval(id);
    };
  }, []);

  const load = useCallback(async () => {
    setError(null);
    try {
      setSettings(await getIniSettings());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not read PalWorldSettings.ini');
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const flash = (msg: string) => {
    setStatus(msg);
    setTimeout(() => setStatus(null), 5000);
  };

  const stage = (key: string, value: string | number | boolean) =>
    setPending((p) => ({ ...p, [key]: value }));

  const save = async () => {
    if (!Object.keys(pending).length) return;
    setBusy(true);
    try {
      const result = await writeIniSettings(pending);
      flash(
        result.changed
          ? `Saved ${result.applied.length} setting(s). Restart the server to apply.`
          : 'No changes to write.'
      );
      setPending({});
      await load();
    } catch (e) {
      flash(e instanceof Error ? e.message : 'Write failed');
    } finally {
      setBusy(false);
    }
  };

  // Not named `usePreset`: a plain callback prefixed with `use` is treated as a
  // hook by the rules-of-hooks lint, which then (correctly) objects to it being
  // called from an onClick.
  const applyPreset = async (id: string, label: string) => {
    if (!confirm(`Apply preset "${label}"? This writes PalWorldSettings.ini immediately.`)) return;
    setBusy(true);
    try {
      const result = await applySettingsPreset(id);
      const skipped = result.skippedKeys?.length
        ? ` (${result.skippedKeys.length} key(s) not present in your INI were skipped)`
        : '';
      flash(`Applied ${result.applied.length} change(s). Restart to apply.${skipped}`);
      await load();
    } catch (e) {
      flash(e instanceof Error ? e.message : 'Preset failed');
    } finally {
      setBusy(false);
    }
  };

  /**
   * Broadcast a countdown, then stop the server.
   *
   * The game's REST API can only stop the process — it cannot start one. Whether
   * the server comes back depends on how its container is supervised, so we
   * hand off to the backend's watcher and report what actually happened rather
   * than claiming a restart.
   */
  const stopWithWarning = async () => {
    const willReturn = lifecycle?.restartSupported;
    const verb = willReturn ? 'restart' : 'stop';
    if (!confirm(`Warn players and ${verb} the server in ${restartSeconds}s?`)) return;

    setBusy(true);
    try {
      const message = `Server ${willReturn ? 'restarting' : 'shutting down'} in ${restartSeconds} seconds to apply new settings.`;
      await announce(message).catch(() => undefined);
      await shutdownServer(restartSeconds, message);
      await noteShutdown('settings change').catch(() => undefined);
      flash(
        `Broadcast sent. Server stops in ${restartSeconds}s. ` +
          (willReturn
            ? 'It should come back automatically.'
            : 'Watching to see whether it comes back.')
      );
    } catch (e) {
      flash(e instanceof Error ? e.message : 'Shutdown failed');
    } finally {
      setBusy(false);
    }
  };

  /** Only available when the operator configured RESTART_COMMAND. */
  const hardRestart = async () => {
    if (!confirm('Restart the server container now? Players will be disconnected immediately.')) return;
    setBusy(true);
    try {
      await restartServer();
      flash('Restart command issued.');
    } catch (e) {
      flash(e instanceof Error ? e.message : 'Restart failed');
    } finally {
      setBusy(false);
    }
  };

  if (error) {
    return (
      <div className="notice notice-warn">
        <strong>PalWorldSettings.ini unavailable</strong>
        <div style={{ marginTop: 6 }}>{error}</div>
        <button className="btn btn-ghost" style={{ marginTop: 12 }} onClick={load}>
          <RefreshCw size={13} /> Retry
        </button>
      </div>
    );
  }

  if (!settings) {
    return <div style={{ color: 'var(--text-muted)', fontSize: 13 }}>Loading settings…</div>;
  }

  const dirty = Object.keys(pending).length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {status && <div className="notice">{status}</div>}

      {lifecycle?.cameBack === false && (
        <div className="notice notice-danger">
          <strong>The server has not come back.</strong> A shutdown was issued{' '}
          {lifecycle.secondsSinceShutdown}s ago and the game process is still
          gone. The REST API can only stop the server, never start it — if your
          server&apos;s container is still running, its supervisor did not
          relaunch PalServer. Restart the server container to bring it back
          (<span className="mono">docker compose restart palworld</span>), or set{' '}
          <span className="mono">RESTART_COMMAND</span> so this dashboard can do
          it. Save editing is unlocked while it is down.
        </div>
      )}

      {lifecycle?.watching && lifecycle.cameBack === null && (
        <div className="notice">
          Shutdown issued {lifecycle.secondsSinceShutdown}s ago — watching for the
          server to come back (up to {lifecycle.returnWatchSeconds}s).
        </div>
      )}

      {!lifecycle?.restartSupported && (
        <div className="notice">
          <strong>Heads up:</strong> &quot;Announce &amp; stop&quot; shuts the game
          process down; it does not start it again. That is a restart only if your
          server container exits when PalServer exits and has a restart policy. If
          your container stays up, the server stays down until you restart it.
        </div>
      )}

      <div className="notice notice-warn">
        <strong>Changes apply on restart.</strong> The server reads this file only
        when it starts, so nothing here takes effect live. Edits are written
        immediately and are safe while the server runs — this is the config
        directory, not the save directory.
      </div>

      {/* Measured, not guessed. The dashboard hashes this file when it writes
          it and again once the server has restarted; if it changed and we did
          not change it, this image regenerates its INI — a fact about THIS
          deployment covering every key, rather than a list of the ~15 that are
          commonly env-driven.

          `unknown` shows nothing: it means "not yet observed", and dressing that
          up as reassurance is exactly the failure the whole feature exists to
          avoid. */}
      {settings.iniWatch?.verdict === 'regenerated' && (
        <div className="notice notice-warn">
          <strong>This server rewrites PalWorldSettings.ini when it starts.</strong>{' '}
          {settings.iniWatch.detail}
          {settings.iniWatch.observedAt && (
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>
              Observed {new Date(settings.iniWatch.observedAt).toLocaleString()}. On{' '}
              <span className="mono">thijsvanloef</span> set{' '}
              <span className="mono">DISABLE_GENERATE_SETTINGS=true</span>; on{' '}
              <span className="mono">jammsen</span> set{' '}
              <span className="mono">SERVER_SETTINGS_MODE=manual</span>.
            </div>
          )}
        </div>
      )}
      {settings.iniWatch?.verdict === 'preserved' && (
        <div className="notice" style={{ fontSize: 12 }}>
          <strong>Your settings persist across restarts.</strong>{' '}
          {settings.iniWatch.detail}
        </div>
      )}
      {settings.iniWatch?.awaitingRestart && (
        <div className="notice" style={{ fontSize: 12 }}>
          Restart the server and come back — the dashboard will then be able to
          tell you whether your change survived, instead of warning you that it
          might not have.
        </div>
      )}

      {/* The restart that applies a change is the same restart that can undo it.
          Naming the affected keys matters: a setting silently reverted after the
          operator watched the save succeed is worse than one that refused.

          Suppressed once the check above has an answer either way: a measurement
          of this deployment beats a list of what images commonly do, and showing
          both would leave an operator who has been told "your settings persist"
          reading a warning that they might not. */}
      {(() => {
        if (settings.iniWatch && settings.iniWatch.verdict !== 'unknown') return null;
        const envKeys = Object.entries(settings.options)
          .filter(([, o]) => o.envManaged)
          .map(([k, o]) => [k, o.envManaged as string] as const);
        if (!envKeys.length) return null;
        return (
          <div className="notice notice-warn">
            <strong>Some of these are probably set by your container, not this file.</strong>{' '}
            The common server images —{' '}
            <span className="mono">thijsvanloef/palworld-server-docker</span> and{' '}
            <span className="mono">jammsen/docker-palworld-dedicated-server</span> — rewrite
            PalWorldSettings.ini from environment variables every time they start. For those
            keys, a change saved here lasts until the next restart and is then reverted.
            Change them in your <span className="mono">.env</span> file instead.
            <details style={{ marginTop: 8 }}>
              <summary style={{ cursor: 'pointer', fontSize: 12 }}>
                Which settings, and the variable to use
              </summary>
              <div style={{ marginTop: 8, fontSize: 12, lineHeight: 1.9 }}>
                {envKeys.map(([key, env]) => (
                  <div key={key}>
                    <span className="mono">{key}</span>
                    {' → '}
                    <span className="mono" style={{ color: 'var(--accent-amber)' }}>{env}</span>
                  </div>
                ))}
              </div>
              <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 8, lineHeight: 1.6 }}>
                The dashboard cannot read the game container’s environment, so it cannot tell
                which of these your setup actually sets — this is the list that is commonly
                managed that way, not a detection.
              </p>
            </details>
          </div>
        );
      })()}

      {/* Presets */}
      <div className="glass-card" style={{ padding: 16 }}>
        <div className="section-title" style={{ marginBottom: 4 }}>
          <Sliders size={14} /> Presets
        </div>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
          One click writes several related keys at once. Presets marked{' '}
          <em>from the game</em> are Palworld&rsquo;s own difficulty settings,
          read from its data tables; the rest are this dashboard&rsquo;s.
        </p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {settings.presets.map((preset) => (
            <div
              key={preset.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                padding: '10px 12px',
                background: 'var(--bg-surface)',
                border: '1px solid var(--border-primary)',
                borderRadius: 'var(--radius)',
              }}
            >
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 500 }}>
                  {preset.label}
                  {/* Which of the two "hardcore-ish" presets is Pocketpair's is
                      exactly the thing an operator needs to tell apart. */}
                  {preset.source === 'game' && (
                    <span className="badge" style={{ marginLeft: 7 }}>from the game</span>
                  )}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
                  {preset.description}
                </div>
              </div>
              <button className="btn" disabled={busy} onClick={() => applyPreset(preset.id, preset.label)}>
                Apply
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* Grouped settings */}
      {settings.groups.map((group) => {
        const keys = group.keys.filter((k) => settings.options[k]);
        if (!keys.length) return null;
        return (
          <div key={group.label} className="glass-card" style={{ padding: 16 }}>
            <div className="section-title" style={{ marginBottom: 12 }}>{group.label}</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {keys.map((key) => (
                <SettingRow
                  key={key}
                  name={key}
                  option={settings.options[key]}
                  pending={pending[key]}
                  onChange={(v) => stage(key, v)}
                />
              ))}
            </div>
          </div>
        );
      })}

      <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>
        {settings.count} settings in <span className="mono">{settings.path}</span>.
        Only the most-used ones are shown; the rest are preserved untouched.
      </p>

      {/* Actions */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          position: 'sticky',
          bottom: 0,
          padding: '12px 0',
          background: 'var(--bg-primary)',
          borderTop: '1px solid var(--border-primary)',
        }}
      >
        <button className="btn btn-primary" onClick={save} disabled={!dirty || busy}>
          <Save size={14} /> {dirty ? `Save ${dirty} change${dirty > 1 ? 's' : ''}` : 'No changes'}
        </button>
        {dirty > 0 && (
          <button className="btn btn-ghost" onClick={() => setPending({})} disabled={busy}>
            Discard
          </button>
        )}

        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          <input
            className="input"
            type="number"
            min={0}
            value={restartSeconds}
            onChange={(e) => setRestartSeconds(Number(e.target.value))}
            style={{ width: 72 }}
          />
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>sec warning</span>
          <button
            className="btn btn-warning"
            onClick={stopWithWarning}
            disabled={busy || serverStatus !== 'online'}
            title={serverStatus !== 'online' ? 'Server is not online' : undefined}
          >
            <Power size={14} />
            {lifecycle?.restartSupported ? 'Announce & restart' : 'Announce & stop'}
          </button>
          {lifecycle?.restartSupported && (
            <button className="btn btn-danger" onClick={hardRestart} disabled={busy}>
              Restart now
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function SettingRow({
  name, option, pending, onChange,
}: {
  name: string;
  option: IniOption;
  pending: string | number | boolean | undefined;
  onChange: (v: string | number | boolean) => void;
}) {
  const current = pending ?? option.value;
  const changed = pending !== undefined && String(pending) !== String(option.value);

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="mono" style={{ fontSize: 12, color: changed ? 'var(--accent)' : 'var(--text-secondary)' }}>
          {name}
          {changed && <Check size={11} style={{ marginLeft: 6, verticalAlign: '-1px' }} />}
          {option.envManaged && (
            <span
              style={{ marginLeft: 6, fontSize: 10, color: 'var(--accent-amber)' }}
              title={`Commonly set by the server container from ${option.envManaged}. If yours does, a change here is reverted on the next restart — edit .env instead.`}
            >
              ${option.envManaged}
            </span>
          )}
        </div>
        {option.secret && (
          <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>
            {option.isSet ? 'Set. Leave blank to keep it.' : 'Not set.'}
          </div>
        )}
      </div>

      {option.type === 'bool' ? (
        <select
          className="select"
          style={{ width: 110 }}
          value={String(current)}
          onChange={(e) => onChange(e.target.value === 'true')}
        >
          <option value="true">True</option>
          <option value="false">False</option>
        </select>
      ) : (
        <input
          className="input"
          style={{ width: 180 }}
          type={option.type === 'int' || option.type === 'float' ? 'number' : 'text'}
          step={option.type === 'float' ? '0.1' : undefined}
          value={String(current)}
          onChange={(e) =>
            onChange(
              option.type === 'int' || option.type === 'float'
                ? Number(e.target.value)
                : e.target.value
            )
          }
        />
      )}
    </div>
  );
}
