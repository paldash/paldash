'use client';

import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, HelpCircle, Check, RefreshCw } from 'lucide-react';
import { getGameBuildStatus, acknowledgeGameBuild, reloadWorldPacks } from '@/lib/save-api';
import { useDashboardStore } from '@/lib/store';
import { CAPABILITIES } from '@/lib/permissions';
import type { GameBuildStatus } from '@/lib/types';
import { t } from '@/lib/chrome';

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
              ? 'Palworld updated — some new content may not be named yet'
              : status.buildDirection === 'down'
                ? 'Palworld was rolled back to an older build'
                : 'Palworld has updated'}
          </strong>

          {/*
            WHAT AN OPERATOR CAN ACTUALLY DO, WHICH IS USUALLY NOTHING.

            This used to read "Bundled world data is out of date" in red, above
            27 filenames and four `python3 scripts/…` commands. For the person
            who actually sees it — someone running the container beside a game
            server for friends — that is unactionable and alarming in equal
            measure: the runtime image ships no `scripts/` directory and no
            4.8 GB game install, so not one of those commands can be run where
            the banner is being read.

            A warning nobody can act on is the failure this repo already
            records for the empty map layer and the empty work-suitability
            panel: it reads as breakage rather than as a caveat. So the banner
            now leads with what still works, names what might be missing, and
            reserves the commands for the person rebuilding the dashboard.
          */}
          <div style={{ marginTop: 6, lineHeight: 1.6 }}>
            Everything already on screen is still correct — Pal and item names,
            the map, and every figure come from the previous build and did not
            change. What the update <em>may</em> have added is content this
            dashboard has not seen: a new Pal, item or structure would show its
            internal id instead of a name, and would not appear on the map.
          </div>
          <div style={{ marginTop: 6, lineHeight: 1.6 }}>
            <strong>{t('Nothing for you to do.')}</strong> This is fixed by a dashboard
            update that bundles the new build&rsquo;s data, not by anything on
            this server. Save editing, backups and moderation are unaffected.
          </div>

          {/* Everything below is for whoever rebuilds the dashboard, and says
              so — it is behind the same "Why?" toggle rather than shown to
              everyone by default. */}
          <button
            className="btn btn-ghost"
            style={{ padding: '2px 8px', fontSize: 11, marginTop: 8 }}
            onClick={() => setDetail((d) => !d)}
          >
            {detail ? 'Hide' : 'Details for whoever builds the dashboard'}
          </button>

          {detail && (
            <>
              <div style={{ marginTop: 6, lineHeight: 1.6, fontSize: 11 }}>
                {status.reason}
              </div>

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
                  server.

                  IT NEEDS THE NEW BUILD'S FILES. Run against an install that has
                  not been updated it compares the bundle against the same pak it
                  was built from, which can only ever report "unchanged" — that is
                  a statement about the local copy, not about the patch. */}
              <div style={{ marginTop: 8, fontSize: 11, color: 'var(--text-muted)' }}>
                Update the reference install first, then:{' '}
                <span className="mono">python3 scripts/check-game-build.py --extract</span>
                <div style={{ marginTop: 2 }}>
                  Against a stale copy of the game this can only report
                  &ldquo;unchanged&rdquo;, which says nothing about the patch.
                </div>
              </div>
            </>
          )}

          {/* THE STEP THAT COMES FIRST, and the only thing that can spot content
              the dashboard does not know about yet.

              Every extractor finds its table by exact name, so a RENAMED table
              raises and a NEW one is invisible — no error, just an absence
              nobody is looking for. Regenerating the existing bundles reproduces
              exactly what was already known and would miss a whole new feature's
              worth of data. `--check` diffs the pak against the committed index
              and names what appeared, vanished, or changed columns.

              A changed column is the dangerous one: an extractor reading a
              renamed column gets nothing and writes a silent zero. */}
          {detail && (
            <div style={{ marginTop: 6, fontSize: 11, color: 'var(--text-muted)' }}>
              Before regenerating, check whether the update <em>added</em> anything:{' '}
              <span className="mono">python3 scripts/mine-datatables.py --check</span>
              <div style={{ marginTop: 2 }}>
                It names new, removed and changed tables. Regenerating alone only
                reproduces what is already known.
              </div>
            </div>
          )}

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
                title={t('Re-reads the bundled data files from disk. Use after replacing them; no restart needed.')}
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
