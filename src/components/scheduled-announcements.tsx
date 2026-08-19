'use client';

import { useCallback, useEffect, useState } from 'react';
import { Clock, Plus, Trash2, Send, AlertTriangle } from 'lucide-react';
import {
  getAnnouncements, createAnnouncement, updateAnnouncement,
  deleteAnnouncement, sendAnnouncementNow,
} from '@/lib/save-api';
import type { AnnouncementList, ScheduledAnnouncement } from '@/lib/types';
import { asArray } from '@/lib/arrays';
import { t } from '@/lib/chrome';

/**
 * Recurring announcements — rules reminders, restart notices, a Discord link.
 *
 * The intervals come from the backend rather than being listed here, so adding
 * one is a single-sided change.
 *
 * "Only when players are online" is on by default and worth understanding: a
 * skipped window is *consumed*, not queued. That is deliberate — queueing would
 * mean logging in greeted you with every message whose window passed while the
 * server was empty. `lastResult` says which happened, so a message that has been
 * quietly skipping for a week is visible rather than mysterious.
 */
export default function ScheduledAnnouncements() {
  const [data, setData] = useState<AnnouncementList | null>(null);
  const [message, setMessage] = useState('');
  const [interval, setIntervalKey] = useState('hourly');
  const [onlyWhenOnline, setOnlyWhenOnline] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const fresh = await getAnnouncements();
      setData(fresh);
      if (!fresh.intervals.some((i) => i.id === interval)) {
        setIntervalKey(fresh.intervals[0]?.id ?? 'hourly');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the schedule');
    }
  }, [interval]);

  useEffect(() => {
    queueMicrotask(load);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const run = async (label: string, action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      await action();
      setNote(label);
      setTimeout(() => setNote(null), 4000);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : `${label} failed`);
    } finally {
      setBusy(false);
    }
  };

  const add = () =>
    run('Added.', async () => {
      await createAnnouncement({ message, interval, onlyWhenOnline });
      setMessage('');
    });

  const full = Boolean(data && data.announcements.length >= data.max);

  return (
    <div className="glass-card" style={{ padding: 16 }}>
      <div className="section-title" style={{ marginBottom: 10 }}>
        <Clock size={14} /> Recurring announcements
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-muted)' }}>
          checked once a minute
        </span>
      </div>

      {error && (
        <div className="notice notice-warn" style={{ fontSize: 12, marginBottom: 10 }}>
          <AlertTriangle size={12} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
          {error}
        </div>
      )}
      {note && <div className="notice" style={{ fontSize: 12, marginBottom: 10 }}>{note}</div>}

      {data?.announcements.length === 0 && (
        <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
          Nothing scheduled. Anything added here is broadcast on its own interval
          and audited exactly like a manual announcement.
        </p>
      )}

      {/* Scrolls inside itself. Without the wrapper the widest row sets the
          page width and the whole dashboard pans sideways on a phone. */}
      {data && data.announcements.length > 0 && (
        <div style={{ overflowX: 'auto', marginBottom: 14 }}>
          <table className="table">
          <thead>
            <tr>
              <th>{t('Message')}</th>
              <th style={{ width: 130 }}>{t('Every')}</th>
              <th style={{ width: 150 }}>{t('Last result')}</th>
              <th style={{ width: 110 }} />
            </tr>
          </thead>
          <tbody>
            {data.announcements.map((entry) => (
              <Row
                key={entry.id}
                entry={entry}
                intervals={data.intervals}
                busy={busy}
                onToggle={() =>
                  run(entry.enabled ? 'Paused.' : 'Resumed.', () =>
                    updateAnnouncement(entry.id, { enabled: !entry.enabled })
                  )
                }
                onInterval={(next) =>
                  run('Interval changed.', () =>
                    updateAnnouncement(entry.id, { interval: next })
                  )
                }
                onSend={() =>
                  run('Sent now — the interval was reset.', () =>
                    sendAnnouncementNow(entry.id)
                  )
                }
                onDelete={() => {
                  if (!confirm(`Delete this announcement?\n\n"${entry.message}"`)) return;
                  void run('Deleted.', () => deleteAnnouncement(entry.id));
                }}
              />
            ))}
          </tbody>
          </table>
        </div>
      )}

      {/* ─── Add ─── */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <input
          className="input"
          style={{ flex: '1 1 260px' }}
          placeholder={t('Message to repeat…')}
          value={message}
          maxLength={200}
          disabled={full}
          onChange={(e) => setMessage(e.target.value)}
        />
        <select
          className="select"
          value={interval}
          disabled={full}
          onChange={(e) => setIntervalKey(e.target.value)}
        >
          {asArray(data?.intervals, 'announcement intervals').map((i) => (
            <option key={i.id} value={i.id}>{i.label}</option>
          ))}
        </select>
        <label style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, color: 'var(--text-muted)' }}>
          <input
            type="checkbox"
            checked={onlyWhenOnline}
            onChange={(e) => setOnlyWhenOnline(e.target.checked)}
          />
          only when players are on
        </label>
        <button className="btn" disabled={busy || full || !message.trim()} onClick={add}>
          <Plus size={12} /> Add
        </button>
      </div>

      {full && (
        <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 8 }}>
          At the limit of {data?.max}. Delete one to add another.
        </p>
      )}
    </div>
  );
}

function Row({
  entry, intervals, busy, onToggle, onInterval, onSend, onDelete,
}: {
  entry: ScheduledAnnouncement;
  intervals: { id: string; label: string }[];
  busy: boolean;
  onToggle: () => void;
  onInterval: (next: string) => void;
  onSend: () => void;
  onDelete: () => void;
}) {
  const skipping = entry.lastResult?.startsWith('skipped');
  const failed = entry.lastResult?.startsWith('failed');

  return (
    <tr style={{ opacity: entry.enabled ? 1 : 0.55 }}>
      <td>
        <div style={{ fontSize: 12 }}>{entry.message}</div>
        <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>
          {entry.enabled
            ? entry.nextRun
              ? `next ${formatWhen(entry.nextRun)}`
              : 'next tick'
            : 'paused'}
          {entry.onlyWhenOnline && ' · only when players are on'}
        </div>
      </td>
      <td>
        <select
          className="select"
          style={{ fontSize: 11, padding: '2px 6px' }}
          value={entry.interval}
          disabled={busy}
          onChange={(e) => onInterval(e.target.value)}
        >
          {intervals.map((i) => (
            <option key={i.id} value={i.id}>{i.label}</option>
          ))}
        </select>
      </td>
      <td>
        {/* Shown verbatim rather than reduced to a tick or a cross: "skipped:
            nobody online" and "failed: could not reach the server" call for
            completely different responses from the operator. */}
        <span
          style={{
            fontSize: 11,
            color: failed
              ? 'var(--accent-red)'
              : skipping
                ? 'var(--accent-amber)'
                : 'var(--text-muted)',
          }}
        >
          {entry.lastResult ?? 'not yet run'}
        </span>
      </td>
      <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
        <button
          className="btn btn-ghost"
          style={{ padding: '2px 7px', fontSize: 10 }}
          disabled={busy}
          onClick={onSend}
          title={t('Send this now, as you. Resets the interval.')}
        >
          <Send size={10} />
        </button>
        <button
          className="btn btn-ghost"
          style={{ padding: '2px 7px', fontSize: 10, marginLeft: 4 }}
          disabled={busy}
          onClick={onToggle}
        >
          {entry.enabled ? 'Pause' : 'Resume'}
        </button>
        <button
          className="btn btn-ghost"
          style={{ padding: '2px 7px', fontSize: 10, marginLeft: 4, color: '#f87171' }}
          disabled={busy}
          onClick={onDelete}
        >
          <Trash2 size={10} />
        </button>
      </td>
    </tr>
  );
}

/** "in 12m" / "in 3h" / "overdue" — a timestamp answers the wrong question here. */
function formatWhen(iso: string): string {
  const delta = new Date(iso).getTime() - Date.now();
  if (delta <= 0) return 'on the next check';
  const minutes = Math.round(delta / 60000);
  if (minutes < 60) return `in ${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `in ${hours}h`;
  return `in ${Math.round(hours / 24)}d`;
}
