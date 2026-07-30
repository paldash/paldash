'use client';

import { Component, type ReactNode } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';

/**
 * Contains a crash to one tab instead of losing the whole dashboard.
 *
 * There is no `error.tsx` in this app, so an uncaught render error propagates to
 * the root and React unmounts everything — which presents as "this page
 * couldn't load" with nothing working until a reload. That is a bad trade for a
 * tool whose other tabs include the backup manager and the server controls:
 * losing the ability to restart your server because a stat card divided by
 * undefined is not a reasonable failure mode.
 *
 * The specific crash that motivated this: `serverMetrics.frametime.toFixed(1)`,
 * guarded on `serverMetrics` existing but not on the field. `frametime` comes
 * from the *game's* REST API, which this project does not control — every type
 * in `types.ts` describing that API is a claim, not a guarantee. Switching to
 * another tab and back re-rendered the bad value and killed the tree every time.
 *
 * Deliberately shows the real message rather than a friendly nothing. The person
 * looking at this is running a game server and can act on "cannot read
 * properties of undefined"; "something went wrong" wastes their time.
 */
export default class ErrorBoundary extends Component<
  { children: ReactNode; label?: string },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidUpdate(prev: { children: ReactNode }) {
    // Switching tabs swaps the children, which is the user's way of saying
    // "try something else" — so clear the error rather than pinning them to it.
    if (prev.children !== this.props.children && this.state.error) {
      this.setState({ error: null });
    }
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="notice notice-danger" style={{ fontSize: 13 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
          <AlertTriangle size={15} style={{ flexShrink: 0, marginTop: 1, color: 'var(--accent-red)' }} />
          <div style={{ flex: 1 }}>
            <strong>
              {this.props.label ? `The ${this.props.label} tab hit an error` : 'This tab hit an error'}
            </strong>
            <div style={{ marginTop: 6, lineHeight: 1.6 }}>
              The rest of the dashboard is unaffected — switch tabs and come back,
              or reload.
            </div>
            <pre
              className="mono"
              style={{
                marginTop: 10, fontSize: 11, color: 'var(--text-muted)',
                whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              }}
            >
              {error.message || String(error)}
            </pre>
            <button
              className="btn btn-ghost"
              style={{ marginTop: 8, padding: '3px 10px', fontSize: 11 }}
              onClick={() => this.setState({ error: null })}
            >
              <RefreshCw size={11} /> Try again
            </button>
          </div>
        </div>
      </div>
    );
  }
}
