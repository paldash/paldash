'use client';

import { useEffect, useState } from 'react';
import { Languages } from 'lucide-react';
import { getLanguages } from '@/lib/save-api';
import { useLanguage, setLanguage } from '@/lib/use-language';

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
 * Picks the language for the GAME's own names — Pals, items, structures.
 *
 * **It does not translate the dashboard.** The buttons and headings are ours;
 * Pocketpair never wrote them, and only 3% of them have a checkable equivalent
 * in the game's strings (measured — see AGENTS.md). Saying so under the control
 * is the difference between a scoped feature and one that looks broken.
 */
export function LanguagePicker() {
  const [available, setAvailable] = useState<string[]>([]);
  const [, current] = useLanguage();

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
    </div>
  );
}
