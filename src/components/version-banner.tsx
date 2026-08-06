'use client';

import { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';

/**
 * "A new version of the dashboard is running — reload."
 *
 * WHY THIS IS NEEDED AT ALL, since Next already content-hashes its bundles: a
 * hash change is picked up on **navigation**, and nobody navigates a dashboard
 * they leave open on a second monitor. That is most of this audience, so without
 * this a deploy lands invisibly for exactly the people using it most.
 *
 * IT NEVER RELOADS BY ITSELF. Someone mid-way through a slot edit, a bulk Pal
 * change or a settings form would lose their work, and this dashboard's whole
 * posture is that a destructive surprise is worse than a stale one. The reload
 * is a button.
 *
 * `unknown` on either side is treated as "no opinion" rather than as a mismatch.
 * A dev server, a build without `BUILD_ID` set, or a fetch that failed must not
 * produce a permanent nag — a banner that cannot be dismissed and means nothing
 * trains people to ignore the banner that does mean something. Same reasoning as
 * `BuildBanner`'s quiet `unknown` state.
 *
 * DISTINCT FROM `BuildBanner`, which compares the bundled *game* data against the
 * installed game build and answers "is this map still accurate". This is about
 * the dashboard's own code. The copy has to keep them apart or two similar
 * warnings become one ignored one.
 */

// Slow on purpose, and it is the FALLBACK rather than the mechanism. The answer
// only changes on a deploy, this runs in every open tab of every signed-in user,
// and the two checks below cover the moment anyone actually cares.
const POLL_MS = 5 * 60 * 1000;

// Focus and visibility fire together when a window is raised, and a browser
// waking a throttled tab can fire both again. One probe is enough.
const MIN_GAP_MS = 3000;

export default function VersionBanner() {
  const running = process.env.NEXT_PUBLIC_BUILD_ID;
  const [latest, setLatest] = useState<string | null>(null);

  useEffect(() => {
    if (!running || running === 'unknown') return;

    let cancelled = false;
    let lastCheck = 0;
    const check = async () => {
      lastCheck = Date.now();
      try {
        const res = await fetch('/api/version', { cache: 'no-store' });
        if (!res.ok) return;
        const body = (await res.json()) as { build?: string };
        if (!cancelled && body.build && body.build !== 'unknown') {
          setLatest(body.build);
        }
      } catch {
        // A failed probe means the server is restarting or the network blinked.
        // Silence is right: it is not evidence of a new version, and a banner
        // on every transient failure is noise.
      }
    };

    void check();

    // **THE POLL WAS NEVER THE ANSWER TO "I JUST REBUILT".** Nothing can tell an
    // open tab that a deploy happened — there is no push channel here — so the
    // tab has to ask, and the only question is when. Waiting out an interval is
    // the worst available answer for the one person who knows a deploy just
    // happened: they are alt-tabbing back from the terminal that did it.
    //
    // So the real trigger is coming back to the tab. It costs one request at the
    // exact moment somebody is about to look, and nothing at all while the tab
    // sits on a second monitor — which is most of this audience most of the time.
    const recheck = () => {
      if (document.visibilityState !== 'visible') return;
      if (Date.now() - lastCheck < MIN_GAP_MS) return;
      void check();
    };
    document.addEventListener('visibilitychange', recheck);
    window.addEventListener('focus', recheck);

    // Both, because they answer different questions: `visibilitychange` fires on
    // switching tabs within the browser, `focus` on switching to the browser
    // from another application. Alt-tabbing from a terminal is the second one,
    // and on several browsers it does not fire the first.

    const timer = setInterval(recheck, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
      document.removeEventListener('visibilitychange', recheck);
      window.removeEventListener('focus', recheck);
    };
  }, [running]);

  if (!running || !latest || latest === running) return null;

  return (
    <div
      className="notice notice-info"
      style={{
        display: 'flex', alignItems: 'center', gap: 10, fontSize: 12,
        margin: '0 0 10px',
      }}
    >
      <RefreshCw size={14} style={{ flexShrink: 0 }} />
      <span style={{ flex: 1 }}>
        A newer version of the dashboard is running on the server. Reload to pick
        it up — anything you are part-way through will be lost, so finish first.
      </span>
      <button
        className="btn btn-ghost"
        style={{ padding: '3px 10px', fontSize: 11 }}
        onClick={() => window.location.reload()}
      >
        Reload
      </button>
    </div>
  );
}
