'use client';

import { useCallback, useEffect, useState } from 'react';
import { RefreshCw, ScrollText, ChevronLeft, ChevronRight } from 'lucide-react';
import { getAuditLog } from '@/lib/save-api';
import type { AuditPage } from '@/lib/types';
import { asArray } from '@/lib/arrays';
import { t } from '@/lib/chrome';

const PAGE_SIZE = 100;

const RESULT_COLOUR: Record<string, string> = {
  ok: 'var(--text-muted)',
  failed: '#c9973f',
  denied: '#c25757',
};

/**
 * The audit log.
 *
 * Append-only and read-only: there is deliberately no way to delete an entry
 * from the UI. Old entries age out on a retention timer, and that pruning is
 * itself audited.
 */
export default function AuditLog() {
  const [page, setPage] = useState<AuditPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [action, setAction] = useState('');
  const [result, setResult] = useState('');
  const [username, setUsername] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setPage(await getAuditLog({ limit: PAGE_SIZE, offset, action, result, username }));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the audit log');
    } finally {
      setLoading(false);
    }
  }, [offset, action, result, username]);

  useEffect(() => {
    load();
  }, [load]);

  const total = page?.total ?? 0;
  const shown = page?.entries.length ?? 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {error && <div className="notice notice-warn">{error}</div>}

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <select
          className="input"
          style={{ maxWidth: 200 }}
          value={action}
          onChange={(e) => { setOffset(0); setAction(e.target.value); }}
        >
          <option value="">{t('All actions')}</option>
          {asArray(page?.actions, 'audit actions').map((a) => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>

        <select
          className="input"
          style={{ maxWidth: 150 }}
          value={result}
          onChange={(e) => { setOffset(0); setResult(e.target.value); }}
        >
          <option value="">{t('Any outcome')}</option>
          <option value="ok">{t('Succeeded')}</option>
          <option value="failed">{t('Failed')}</option>
          <option value="denied">{t('Denied')}</option>
        </select>

        <input
          className="input"
          style={{ maxWidth: 180 }}
          placeholder={t('Filter by user…')}
          value={username}
          onChange={(e) => { setOffset(0); setUsername(e.target.value); }}
        />

        <button className="btn btn-ghost" onClick={load} disabled={loading}>
          <RefreshCw size={13} /> {loading ? 'Loading…' : 'Reload'}
        </button>

        <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-muted)' }}>
          {total.toLocaleString()} entries · kept {page?.retentionDays ?? '—'} days
        </span>
      </div>

      <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: 165 }}>{t('When')}</th>
              <th style={{ width: 130 }}>{t('Who')}</th>
              <th style={{ width: 150 }}>{t('Action')}</th>
              <th>{t('Detail')}</th>
              <th style={{ width: 110 }}>{t('From')}</th>
            </tr>
          </thead>
          <tbody>
            {asArray(page?.entries, 'audit entries').map((entry) => (
              <tr key={entry.id}>
                <td className="mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  {new Date(entry.ts).toLocaleString()}
                </td>
                <td style={{ fontSize: 12 }}>
                  {entry.username ?? <span style={{ color: 'var(--text-muted)' }}>anonymous</span>}
                  {entry.role && (
                    <span style={{ color: 'var(--text-muted)', fontSize: 11, marginLeft: 5 }}>
                      {entry.role}
                    </span>
                  )}
                </td>
                <td>
                  <span
                    className="mono"
                    style={{ fontSize: 11, color: RESULT_COLOUR[entry.result] ?? 'var(--text-muted)' }}
                  >
                    {entry.action}
                  </span>
                  {entry.result !== 'ok' && (
                    <span style={{ fontSize: 10, color: RESULT_COLOUR[entry.result], marginLeft: 6 }}>
                      {entry.result}
                    </span>
                  )}
                </td>
                <td style={{ fontSize: 11, color: 'var(--text-muted)', wordBreak: 'break-word' }}>
                  {entry.target && (
                    <span className="mono" style={{ color: 'var(--text-primary)', marginRight: 6 }}>
                      {entry.target}
                    </span>
                  )}
                  {entry.detail}
                </td>
                <td className="mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  {entry.ip || '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {!shown && !loading && (
          <p style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
            <ScrollText size={16} style={{ display: 'block', margin: '0 auto 8px' }} />
            Nothing recorded yet for these filters.
          </p>
        )}
      </div>

      {total > PAGE_SIZE && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', justifyContent: 'center' }}>
          <button
            className="btn btn-ghost"
            disabled={offset === 0 || loading}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          >
            <ChevronLeft size={13} /> Newer
          </button>
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            {offset + 1}–{offset + shown} of {total.toLocaleString()}
          </span>
          <button
            className="btn btn-ghost"
            disabled={offset + PAGE_SIZE >= total || loading}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            Older <ChevronRight size={13} />
          </button>
        </div>
      )}

      <p style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        Every action that changes something is recorded here, along with refused
        ones. Entries cannot be edited or individually deleted; they age out after
        the retention period, and that pruning is logged too.
      </p>
    </div>
  );
}
