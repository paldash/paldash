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
  { error: Error | null; where: string }
> {
  state: { error: Error | null; where: string } = { error: null, where: '' };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  /**
   * **Keep the component stack, because the message alone is not actionable.**
   * A minified build reports things like "((intermediate value) ?? []).map is
   * not a function", which names no file and no component — and a tab's render
   * tree is several components deep, so that cost two wrong fixes and three
   * round trips before anyone knew which file to open. React hands us the stack
   * here; throwing it away was the mistake.
   */
  componentDidCatch(error: Error, info: { componentStack?: string | null }) {
    const stack = (info?.componentStack || '').trim();
    this.setState({ where: stack.split('\n').slice(0, 6).join('\n') });
    console.error('[dashboard] tab error', error, stack);
  }

  /*
   * **THERE IS NO `componentDidUpdate` HERE ANY MORE, AND THAT WAS THE BUG.**
   *
   * It used to clear the error whenever `children` changed identity, reasoning
   * that a tab switch is the user saying "try something else". But JSX builds a
   * NEW element object on every parent render, so `prev.children !==
   * this.props.children` is true every single time — and `page.tsx` re-renders
   * every 5 seconds from the live-player poller.
   *
   * The result was a loop: poller fires -> parent re-renders -> boundary clears
   * the error -> children re-render -> throw -> boundary catches -> poller
   * fires again. On screen that is the tab flashing between an empty panel and
   * the error message, twice a second, which reads as "the fix did nothing"
   * rather than as a second bug sitting on top of the first. It also made the
   * message impossible to read long enough to act on.
   *
   * `page.tsx` already mounts this with `key={activeTab}`, so a tab switch
   * remounts it and clears the state for free. The retry button covers the
   * manual case. Nothing was needed here.
   */


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
              {this.state.where ? `\n\nin:${this.state.where}` : ''}
            </pre>
            <button
              className="btn btn-ghost"
              style={{ marginTop: 8, padding: '3px 10px', fontSize: 11 }}
              onClick={() => this.setState({ error: null, where: '' })}
            >
              <RefreshCw size={11} /> Try again
            </button>
          </div>
        </div>
      </div>
    );
  }
}
