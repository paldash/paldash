'use client';

import { useEffect, useState } from 'react';
import { getLanguagePack } from './save-api';
import { DEFAULT_LANG, type LanguagePack } from './language';

const STORAGE_KEY = 'palworld-dashboard-lang';

/**
 * The chosen language, shared across components without a provider.
 *
 * A React context would be tidier and would mean threading a provider through
 * `page.tsx` for a value that changes about once per install. This is a module
 * -level store with subscribers instead: every hook instance re-renders on a
 * change, including in another tab, which is what the `storage` event is for.
 */
let current: string = DEFAULT_LANG;
let pack: LanguagePack | null = null;
const listeners = new Set<() => void>();

function notify() {
  listeners.forEach((fn) => fn());
}

export function setLanguage(code: string) {
  current = code || DEFAULT_LANG;
  try {
    window.localStorage.setItem(STORAGE_KEY, current);
  } catch {
    // Private browsing, or storage disabled. The choice still applies for this
    // session — losing the preference is not a reason to refuse the change.
  }
  // **English is not a pack.** It is the name every payload already carries, so
  // selecting it clears the overlay rather than fetching an empty file.
  if (current === DEFAULT_LANG) {
    pack = null;
    notify();
    return;
  }
  getLanguagePack(current)
    .then((data) => {
      // Ignore a response that lost the race with a newer choice — otherwise
      // clicking through three languages can leave the second one applied.
      if (data.lang !== current) return;
      pack = { lang: data.lang, names: data.names };
      notify();
    })
    .catch(() => {
      // A missing pack falls back to English rather than blanking every name.
      pack = null;
      notify();
    });
}

/** `[pack, code]`. `pack` is null for English and while one is loading. */
export function useLanguage(): [LanguagePack | null, string] {
  const [, force] = useState(0);

  useEffect(() => {
    const rerender = () => force((n) => n + 1);
    listeners.add(rerender);

    if (!pack && current === DEFAULT_LANG) {
      let stored = '';
      try {
        stored = window.localStorage.getItem(STORAGE_KEY) || '';
      } catch {
        stored = '';
      }
      if (stored && stored !== DEFAULT_LANG) setLanguage(stored);
    }

    // Another tab changed it. Without this the two disagree until reload, and
    // the one that did not change looks like it ignored the setting.
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY && e.newValue && e.newValue !== current) {
        setLanguage(e.newValue);
      }
    };
    window.addEventListener('storage', onStorage);
    return () => {
      listeners.delete(rerender);
      window.removeEventListener('storage', onStorage);
    };
  }, []);

  return [pack, current];
}
