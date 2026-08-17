'use client';

import { useEffect, useState } from 'react';

/**
 * Theme choice — dark (the default and the identity) or light.
 *
 * Same module-level store pattern as `use-language.ts`, for the same reason:
 * the value changes about once per install and a context would thread a
 * provider through everything for it. The theme itself is one attribute on
 * <html>; every component already styles itself with `var(--*)` tokens, so
 * flipping the attribute is the entire mechanism (`globals.css`,
 * `[data-theme="light"]`).
 *
 * **The stored choice is applied before first paint by an inline script in
 * layout.tsx**, not here — an effect runs after hydration, and a light-theme
 * user would see a dark flash on every load. This module only handles
 * subsequent toggles and cross-tab sync.
 */

const STORAGE_KEY = 'palworld-dashboard-theme';

export type Theme = 'dark' | 'light';

function applied(): Theme {
  if (typeof document === 'undefined') return 'dark';
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
}

const listeners = new Set<() => void>();

export function setTheme(theme: Theme) {
  if (theme === 'light') {
    document.documentElement.dataset.theme = 'light';
  } else {
    // Absent, not "dark": the dark tokens live on bare :root, so removing the
    // attribute is what restores them. A literal data-theme="dark" would work
    // today and silently stop matching if a [data-theme="dark"] block ever
    // appeared with different values.
    delete document.documentElement.dataset.theme;
  }
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Private browsing. The choice still applies for this session.
  }
  listeners.forEach((fn) => fn());
}

/** Current theme, re-rendering the caller on change — including a change made
 *  in another tab, so two windows never disagree until reload. */
export function useTheme(): Theme {
  const [, force] = useState(0);

  useEffect(() => {
    const rerender = () => force((n) => n + 1);
    listeners.add(rerender);
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY && e.newValue &&
          e.newValue !== applied()) {
        setTheme(e.newValue === 'light' ? 'light' : 'dark');
      }
    };
    window.addEventListener('storage', onStorage);
    return () => {
      listeners.delete(rerender);
      window.removeEventListener('storage', onStorage);
    };
  }, []);

  return applied();
}
