'use client';

import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, HelpCircle, Check, RefreshCw } from 'lucide-react';
import { getGameBuildStatus, acknowledgeGameBuild, reloadWorldPacks } from '@/lib/save-api';
import { useDashboardStore } from '@/lib/store';
import { CAPABILITIES } from '@/lib/permissions';
import type { GameBuildStatus } from '@/lib/types';

/**
 * "The game updated; the bundled positions may be wrong."
 *
 * Shown on the map, because that is where the consequence is: ore nodes, chests
 * and effigies come from the game's own files at a particular build, and a content
 * patch can move them with nothing in the save file saying so. A confidently wrong
 * map is worse than a caveated one.
 *
 * `unknown` renders differently from `stale` and much more quietly. It is the
 * normal state for a container that cannot see the game's install directory, and
 * treating "we cannot tell" as "something is broken" would train people to ignore
 * the banner that matters.
 *
 * Nothing here is shown when the data checks out — a green "data is current" notice
 * on every page load is noise that makes the real warning less visible.
 */
export default function BuildBanner() {
  const { capabilities } = useDashboardStore();
  const [status, setStatus] = useState<GameBuildStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState(false);
  const [reloaded, setReloaded] = useState<string | null>(null);

  const load = useCallback(() => {
    getGameBuildStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  useEffect(load, [load]);

  if (!status) return null;
  if (status.verdict === 'current') return null;
  if (status.acknowledged) return null;

  const stale = status.verdict === 'stale';
  const canAcknowledge = capabilities.includes(CAPABILITIES.POLICY_MANAGE);

  // An unknown verdict with no build change at all is the ordinary case for a
  // container without the game directory mounted. It is worth being able to find,
  // but not worth a banner on every visit.
  if (!stale && !status.buildChanged) {
    return (
      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        <HelpCircle size={11} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 4 }} />
        Static positions come from the game files; this dashboard cannot verify they
        match your installed build.{' '}
        <button
          onClick={() => setDetail(!detail)}
          style={{
            background: 'none', border: 'none', padding: 0, cursor: 'pointer',
            color: 'var(--accent)', font: 'inherit', textDecoration: 'underline',
          }}
        >
          {detail ? 'Hide' : 'Why?'}
        </button>
        {detail && (
          <div style={{ marginTop: 6, lineHeight: 1.6 }}>{status.reason}</div>
        )}
      </div>
    );
  }

  return (
    <div className={stale ? 'notice notice-danger' : 'notice notice-warn'} style={{ fontSize: 12 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
        <AlertTriangle
          size={14}
          style={{ flexShrink: 0, marginTop: 1, color: stale ? 'var(--accent-red)' : 'var(--accent-amber)' }}
        />
        <div style={{ flex: 1 }}>
          <strong>
            {stale
              ? 'Bundled world data is out of date'
              : status.buildDirection === 'down'
                ? 'Palworld was rolled back to an older build'
                : 'Palworld has updated'}
          </strong>
          <div style={{ marginTop: 4, lineHeight: 1.6 }}>{status.reason}</div>

          {status.artifacts.some((a) => a.state === 'stale') && (
            <div style={{ marginTop: 8 }}>
              {status.artifacts
                .filter((a) => a.state === 'stale')
                .map((artifact) => (
                  <div key={artifact.artifact} style={{ marginBottom: 4 }}>
                    <span className="mono" style={{ fontSize: 11 }}>{artifact.artifact}</span>
                    {artifact.regenerateWith && (
                      <div style={{ fontSize: 11, color: 'var(--text-muted)' }} className="mono">
                        {artifact.regenerateWith}
                      </div>
                    )}
                  </div>
                ))}
            </div>
          )}

          {/* The diff is the actionable step, and it is deliberately a command
              rather than a button: it walks 9,977 cell packages and takes minutes,
              which is not something to start from a web page next to a live game
              server. */}
          <div style={{ marginTop: 8, fontSize: 11, color: 'var(--text-muted)' }}>
            To see what actually moved:{' '}
            <span className="mono">python3 scripts/check-game-build.py --extract</span>
          </div>

          {canAcknowledge && (
            <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
              {/* Reload, not regenerate. Once the files on disk have been
                  replaced, this is what picks them up — the alternative was
                  restarting the container, which takes the dashboard away from
                  everyone else to fix a data file. */}
              <button
                className="btn btn-ghost"
                style={{ padding: '3px 10px', fontSize: 11 }}
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  setReloaded(null);
                  try {
                    const result = await reloadWorldPacks();
                    setStatus(result.build);
                    setReloaded(
                      `${result.worldObjects.total.toLocaleString()} objects, ` +
                        `${result.effigies.count} effigies, ` +
                        `${result.gamedata.items.toLocaleString()} items`,
                    );
                  } catch (e) {
                    setReloaded(e instanceof Error ? e.message : 'Reload failed');
                  } finally {
                    setBusy(false);
                  }
                }}
                title="Re-reads the bundled data files from disk. Use after replacing them; no restart needed."
              >
                <RefreshCw size={11} /> Reload data packs
              </button>

              <button
                className="btn btn-ghost"
                style={{ padding: '3px 10px', fontSize: 11 }}
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    setStatus(await acknowledgeGameBuild(status.buildId));
                  } catch {
                    // The backend refuses to acknowledge a build that is not the
                    // installed one — correctly, since silencing a warning about
                    // a build that is no longer there would hide the one that
                    // is. But this banner can be holding a stale id (the game
                    // updated while the page sat open), and the old handler just
                    // reloaded, so the operator had to click twice and saw a
                    // failure in between. Re-read and retry once against the
                    // build that is actually installed.
                    try {
                      const fresh = await getGameBuildStatus();
                      setStatus(
                        fresh.buildId && fresh.buildId !== status.buildId
                          ? await acknowledgeGameBuild(fresh.buildId)
                          : fresh,
                      );
                    } catch {
                      load();
                    }
                  } finally {
                    setBusy(false);
                  }
                }}
                title={`Hides this for build ${status.buildId} only. The next update raises it again.`}
              >
                <Check size={11} /> I have checked this build
              </button>
            </div>
          )}

          {/* Counts, not "done" — a wrongly-compressed or truncated pack loads
              to an empty bundle without erroring, and that is precisely the
              failure worth surfacing here. */}
          {reloaded && (
            <div style={{ marginTop: 8, fontSize: 11, color: 'var(--text-muted)' }}>
              Loaded: {reloaded}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
