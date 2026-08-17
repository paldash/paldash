'use client';

import { useEffect, useState } from 'react';
import { Languages } from 'lucide-react';
import { getLanguages } from '@/lib/save-api';
import { useLanguage, setLanguage } from '@/lib/use-language';
import { useChromePack } from '@/lib/chrome';

/** What the game calls each language, so the list reads in the target language. */
const LABELS: Record<string, string> = {
  en: 'English',
  de: 'Deutsch',
  es: 'Español',
  'es-MX': 'Español (LatAm)',
  fr: 'Français',
  id: 'Bahasa Indonesia',
  it: 'Italiano',
  ko: '한국어',
  pl: 'Polski',
  'pt-BR': 'Português (BR)',
  ru: 'Русский',
  th: 'ไทย',
  tr: 'Türkçe',
  vi: 'Tiếng Việt',
  'zh-Hans': '简体中文',
  'zh-Hant': '繁體中文',
};

/**
 * Picks the language — for the GAME's own names (Pals, items, structures,
 * from Pocketpair's L10N tables) and, since #109's labelled beta, for the
 * dashboard's own chrome too.
 *
 * The chrome half is machine-translated until a human verifies a language,
 * and the badge below the control says so — the pack carries
 * `provenance: "machine"`, and the provenance travelling visibly is what
 * makes this different from the silent machine translation the project
 * refused. Safety-critical strings (save-editing preconditions, backup and
 * restore confirmations) stay English in every machine pack.
 */
export function LanguagePicker() {
  const [available, setAvailable] = useState<string[]>([]);
  const [, current] = useLanguage();
  const chrome = useChromePack();

  useEffect(() => {
    getLanguages()
      .then((r) => setAvailable(r.languages))
      // A server without the bundles simply offers no control, rather than a
      // dropdown with one entry that does nothing.
      .catch(() => setAvailable([]));
  }, []);

  if (available.length < 2) return null;

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <label
        htmlFor="language-picker"
        style={{ display: 'inline-flex', alignItems: 'center', gap: 5,
                 fontSize: 12, color: 'var(--text-muted)' }}
      >
        <Languages size={13} /> Pal and item names
      </label>
      <select
        id="language-picker"
        className="select"
        value={current}
        onChange={(e) => setLanguage(e.target.value)}
        title="The game's own names for Pals, items and structures. The dashboard's own labels stay in English."
      >
        {available.map((code) => (
          <option key={code} value={code}>{LABELS[code] ?? code}</option>
        ))}
      </select>
      {chrome && chrome.provenance === 'machine' && !chrome.verified && (
        /* The label IS the feature: a machine translation shipped without it
           would be indistinguishable from a human one, which is the exact
           failure this project records refusing. */
        <span
          title="The dashboard's own labels in this language are machine-translated and not yet verified by a person. Game names come from the game itself. Safety-critical messages stay in English. See docs/TRANSLATING.md to help verify."
          style={{ fontSize: 10, padding: '1px 6px', borderRadius: 8,
                   background: 'var(--bg-surface)', color: 'var(--text-muted)',
                   border: '1px solid var(--border)', whiteSpace: 'nowrap' }}
        >
          auto-translated β
        </span>
      )}
    </div>
  );
}
