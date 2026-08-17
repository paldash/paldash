'use client';

import { Moon, Sun } from 'lucide-react';
import { setTheme, useTheme } from '@/lib/theme';
import { t } from '@/lib/chrome';

/**
 * One button, two states. Dark is the default; the choice persists in
 * localStorage and is applied before first paint by layout.tsx's inline
 * script, so this component only ever flips it.
 *
 * The icon shows what you GET, not what you have — a sun on a dark page reads
 * as "switch to light", which is the convention every OS toggle follows.
 */
export function ThemeToggle() {
  const theme = useTheme();
  const next = theme === 'dark' ? 'light' : 'dark';
  return (
    <button
      className="btn btn-ghost"
      onClick={() => setTheme(next)}
      title={next === 'light' ? t('Switch to the light theme')
                              : t('Switch to the dark theme')}
      aria-label={next === 'light' ? t('Switch to the light theme')
                                   : t('Switch to the dark theme')}
      style={{ padding: '5px 8px' }}
    >
      {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
    </button>
  );
}
