'use client';

import { useEffect, useState } from 'react';

/**
 * Dashboard-chrome translation — the labelled-beta half of #109.
 *
 * Game nouns come from Pocketpair's own L10N tables (`use-language.ts`); the
 * buttons and headings are OURS, and until 2026-08-17 they stayed English
 * because a machine translation shipped beside the game's real strings would
 * be trusted the same way. The operator chose the labelled middle path: packs
 * are machine-translated, carry `provenance: "machine"` and `verified: false`,
 * and the language picker says so out loud. A human contributor upgrading a
 * pack flips the flag; the badge disappears. What the original refusal was
 * protecting — that a claim carries its provenance — travels in the pack
 * rather than being abandoned.
 *
 * **Safety-critical strings are deliberately NOT in any pack.** The
 * save-editing warnings ("The server must be stopped first", backup and
 * restore confirmations) stay English until a human who knows the language
 * verifies them — a mistranslated precondition there can cost someone a
 * world. `scripts/wrap-chrome-strings.py` owns the list.
 *
 * Same module-level store pattern as `use-language.ts`, for the same reason:
 * the value changes about once per install. `t()` is a plain function so the
 * seven-hundred-odd call sites need no hook; the ROOT component subscribes
 * via `useChromePack()`, and since nothing between `page.tsx` and the leaves
 * is memoized (checked — the one `useMemo` in `boss-planner` memoises data,
 * not JSX), a root re-render re-runs every `t()` in the tree.
 */

export interface ChromePack {
  language: string;
  /** "machine" until a human verifies; the picker badges it. */
  provenance: 'machine' | 'human';
  verified: boolean;
  strings: Record<string, string>;
}

let dict: Record<string, string> | null = null;
let meta: ChromePack | null = null;
let requested = '';
const listeners = new Set<() => void>();

function notify() {
  listeners.forEach((fn) => fn());
}

/** Translate one chrome string. Identity for English and for anything the
 *  pack does not carry — an untranslated string stays visibly English rather
 *  than blank, which is the same fallback rule the noun overlay follows. */
export function t(s: string): string {
  if (!dict) return s;
  return dict[s] ?? s;
}

/**
 * Definition-site marker for a translatable label that reaches `t()`
 * dynamically — `label: tl('Players')` in a data array, rendered as
 * `{t(layer.label)}`. Identity at runtime; its whole job is to be a literal
 * the manifest scan (wrap-chrome-strings.py) can see, because a dynamic
 * `t(x.label)` is invisible to it and the string would otherwise never reach
 * a language pack. Translate at the RENDER site, never here: a module-level
 * constant evaluates once, before any pack loads.
 */
export function tl(s: string): string {
  return s;
}

/** The active pack's metadata, for the picker's provenance badge. */
export function chromePackMeta(): ChromePack | null {
  return meta;
}

export function loadChromeLanguage(code: string): void {
  requested = code;
  if (!code || code === 'en') {
    dict = null;
    meta = null;
    notify();
    return;
  }
  // Bundled at build time as a code-split chunk per language — no network
  // beyond the dashboard's own assets, per the offline rule.
  import(`./chrome-langs/${code}.json`)
    .then((mod) => {
      const pack = (mod.default ?? mod) as ChromePack;
      // A response that lost the race with a newer choice must not apply —
      // the same guard use-language.ts carries.
      if (pack.language !== requested) return;
      dict = pack.strings || {};
      meta = pack;
      notify();
    })
    .catch(() => {
      // No pack for this language: chrome stays English, nouns still switch.
      if (code !== requested) return;
      dict = null;
      meta = null;
      notify();
    });
}

/** Root-level subscription. Call once in `page.tsx`; everything below
 *  re-renders when the pack changes, which re-runs every `t()`. */
export function useChromePack(): ChromePack | null {
  const [, force] = useState(0);
  useEffect(() => {
    const rerender = () => force((n) => n + 1);
    listeners.add(rerender);
    return () => {
      listeners.delete(rerender);
    };
  }, []);
  return meta;
}
