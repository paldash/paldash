'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  LineChart, Line, XAxis, YAxis, ResponsiveContainer, Tooltip, CartesianGrid, ReferenceArea,
} from 'recharts';
import { Activity, AlertTriangle, HardDrive, TrendingUp } from 'lucide-react';
import { getMetricsHistory, getMetricsSummary } from '@/lib/api';
import type { MetricsHistory, MetricsPoint, MetricsSummary } from '@/lib/types';
import { t } from '@/lib/chrome';

const RANGES = [
  { label: '6h', hours: 6 },
  { label: '24h', hours: 24 },
  { label: '7d', hours: 24 * 7 },
  { label: '30d', hours: 24 * 30 },
];

/**
 * Server history: FPS, players, CPU, memory, disk, world size over time.
 *
 * OUTAGES ARE DRAWN, NOT SKIPPED
 * ------------------------------
 * `reachable` is the *fraction* of each bucket the game answered in, so a bucket
 * below 1 is a partial outage. Buckets at 0 are shaded rather than simply left
 * blank, because a blank region on a line chart reads as "no interest here" when
 * it actually means "the server was gone".
 *
 * Nulls are passed to recharts as nulls with `connectNulls={false}`, so the line
 * genuinely breaks. Substituting 0 would draw a plunge to the floor and back,
 * which looks like a catastrophic performance event rather than an absence of data.
 */
export default function MetricsHistoryPanel() {
  const [hours, setHours] = useState(24);
  const [history, setHistory] = useState<MetricsHistory | null>(null);
  const [summary, setSummary] = useState<MetricsSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [series, totals] = await Promise.all([
        getMetricsHistory(hours, 120),
        getMetricsSummary(),
      ]);
      setHistory(series);
      setSummary(totals);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load history');
    } finally {
      setLoading(false);
    }
  }, [hours]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return (
      <div className="glass-card" style={{ padding: 16 }}>
        <div className="notice notice-warn">
          <AlertTriangle size={12} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
          {error}
        </div>
      </div>
    );
  }

  const points = history?.points ?? [];
  const enabled = history?.enabled !== false;

  return (
    <div className="glass-card" style={{ padding: 16 }}>
      <div className="section-title" style={{ marginBottom: 4 }}>
        <TrendingUp size={14} /> History
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
          {RANGES.map((range) => (
            <button
              key={range.label}
              className={hours === range.hours ? 'btn' : 'btn btn-ghost'}
              style={{ padding: '2px 10px', fontSize: 11 }}
              onClick={() => setHours(range.hours)}
            >
              {range.label}
            </button>
          ))}
        </div>
      </div>

      <Coverage summary={summary} enabled={enabled} loading={loading} points={points.length} />

      {!enabled ? (
        <div className="notice" style={{ marginTop: 10, fontSize: 12 }}>
          History is switched off (<span className="mono">{t('METRICS_ENABLED=false')}</span>).
          Live figures above still work.
        </div>
      ) : points.length < 2 ? (
        <div className="notice" style={{ marginTop: 10, fontSize: 12 }}>
          Not enough samples yet. One is taken every{' '}
          {history?.intervalSeconds ?? 60}s, so a chart appears within a few minutes
          of the backend starting.
        </div>
      ) : (
        <>
          <Chart
            title={t('Server FPS')}
            points={points}
            lines={[{ key: 'serverFps', colour: '#00d4ff', label: 'FPS' }]}
            hours={hours}
          />
          <Chart
            title={t('Players')}
            points={points}
            lines={[
              { key: 'playersPeak', colour: '#34d399', label: 'Peak' },
              { key: 'playersAvg', colour: '#6d747e', label: 'Average' },
            ]}
            hours={hours}
          />
          <Chart
            title={t('Dashboard CPU & memory')}
            points={points}
            lines={[
              { key: 'cpuPercent', colour: '#fbbf24', label: 'CPU %' },
              { key: 'memUsedMb', colour: '#a78bfa', label: 'Memory MB' },
            ]}
            hours={hours}
          />
          {/* THE GAME'S OWN MEMORY, and a separate chart on purpose. Putting it
              beside the dashboard's would invite reading one line as the other,
              and they answer different questions — this is the process that
              leaks. Absent entirely when the dashboard cannot see the process,
              rather than drawn as a flat zero. */}
          <GameMemoryChart points={points} hours={hours} />
          {/* Steal is the only host signal that can say the problem is not the
              operator's. Shown only when something reported it. */}
          <HostContentionChart points={points} hours={hours} />
          <Chart
            title={t('World size & Pals')}
            points={points}
            lines={[
              { key: 'worldSizeMb', colour: '#f472b6', label: 'Level.sav MB' },
              { key: 'palCount', colour: '#38bdf8', label: 'Pals' },
            ]}
            hours={hours}
          />
          <DiskNote points={points} />
        </>
      )}
    </div>
  );
}

function Coverage({
  summary, enabled, loading, points,
}: {
  summary: MetricsSummary | null;
  enabled: boolean;
  loading: boolean;
  points: number;
}) {
  if (!summary) {
    return (
      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        {loading ? 'Loading…' : ''}
      </div>
    );
  }

  const uptime = summary.uptimeFraction;
  const oldest = summary.oldest ? new Date(summary.oldest * 1000) : null;

  return (
    <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 8 }}>
      {summary.samples.toLocaleString()} samples
      {oldest && <> since {oldest.toLocaleString()}</>}
      {enabled && <>, one every {summary.intervalSeconds}s, kept {summary.retentionDays} days</>}
      {uptime !== null && (
        <>
          {' · '}
          <strong style={{ color: uptime > 0.99 ? 'var(--accent-emerald)' : 'inherit' }}>
            {(uptime * 100).toFixed(2)}%
          </strong>{' '}
          {/* Labelled explicitly: this is the retained window, not all time. */}
          reachable over the last {summary.retentionDays} days
        </>
      )}
      {points > 0 && <> · {points} buckets shown</>}
    </div>
  );
}

interface LineSpec {
  key: keyof MetricsPoint;
  colour: string;
  label: string;
}

function Chart({
  title, points, lines, hours,
}: {
  title: string;
  points: MetricsPoint[];
  lines: LineSpec[];
  hours: number;
}) {
  const data = points.map((p) => ({
    ...p,
    time: formatTick(p.ts, hours),
  }));

  return (
    <div style={{ marginTop: 12 }}>
      <div style={{
        fontSize: 11, color: 'var(--text-muted)', marginBottom: 4,
        display: 'flex', gap: 10, alignItems: 'center',
      }}>
        <Activity size={11} /> {title}
        {lines.map((line) => (
          <span key={String(line.key)} style={{ color: line.colour }}>
            ■ {line.label}
          </span>
        ))}
      </div>
      <div style={{ height: 130 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
            <CartesianGrid stroke="var(--border-primary)" strokeDasharray="2 4" vertical={false} />
            <XAxis
              dataKey="time"
              tick={{ fontSize: 10, fill: 'var(--text-muted)' }}
              interval="preserveStartEnd"
              minTickGap={40}
            />
            <YAxis tick={{ fontSize: 10, fill: 'var(--text-muted)' }} width={44} />
            <Tooltip
              contentStyle={{
                background: 'var(--bg-elevated)',
                border: '1px solid var(--border-primary)',
                borderRadius: 6,
                fontSize: 11,
              }}
              labelStyle={{ color: 'var(--text-muted)' }}
            />
            {outageBands(points).map((band, i) => (
              <ReferenceArea
                key={i}
                x1={formatTick(band.from, hours)}
                x2={formatTick(band.to, hours)}
                fill="#ef4444"
                fillOpacity={0.09}
                ifOverflow="extendDomain"
              />
            ))}
            {lines.map((line) => (
              <Line
                key={String(line.key)}
                type="monotone"
                dataKey={String(line.key)}
                stroke={line.colour}
                strokeWidth={1.6}
                dot={false}
                // The line must break where data is missing. Substituting 0 would
                // draw a crash to the floor that never happened.
                connectNulls={false}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/** Contiguous runs where the server answered in none of the bucket. */
function outageBands(points: MetricsPoint[]): { from: number; to: number }[] {
  const bands: { from: number; to: number }[] = [];
  let start: number | null = null;

  for (const point of points) {
    const down = point.reachable !== null && point.reachable === 0;
    if (down && start === null) start = point.ts;
    if (!down && start !== null) {
      bands.push({ from: start, to: point.ts });
      start = null;
    }
  }
  if (start !== null) bands.push({ from: start, to: points[points.length - 1].ts });
  return bands;
}

/**
 * The game server process's memory over time.
 *
 * **Rendered only when something reported it.** In the ordinary container
 * deployment the dashboard has no shared PID namespace and cannot see the game's
 * `/proc` entries at all, so the honest output is no chart plus a line saying
 * why — not an empty axis, and certainly not a flat zero, which would read as a
 * server using no memory.
 *
 * Separate from the dashboard's own CPU/memory chart on purpose: two memory
 * lines on one axis invite reading either as the other, and only this one is the
 * process that leaks.
 */
function GameMemoryChart({ points, hours }: { points: MetricsPoint[]; hours: number }) {
  const seen = points.some((p) => p.gameMemMb !== null);
  if (!seen) {
    return (
      <div className="notice" style={{ marginTop: 12, fontSize: 12 }}>
        <Activity size={12} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
        The game server&rsquo;s own memory is not visible from this container. It
        needs to share a PID namespace with the server to be measured — the
        chart above is the dashboard&rsquo;s memory, not the game&rsquo;s.
      </div>
    );
  }
  const peak = Math.max(...points.map((p) => p.gameMemMb ?? 0));
  return (
    <>
      <Chart
        title={t('Game server memory')}
        points={points}
        lines={[{ key: 'gameMemMb', colour: '#f97316', label: 'Game MB' }]}
        hours={hours}
      />
      <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '4px 0 0' }}>
        Peak {(peak / 1024).toFixed(1)} GB. Palworld&rsquo;s server leaks over
        time; a line climbing steadily is what that looks like.
      </p>
    </>
  );
}

/**
 * Steal time and swap — the two signals that say the trouble is below the game.
 *
 * Steal is the reason this chart exists. On a rented VPS a non-zero figure means
 * the host is oversubscribed and the stutter is not the operator's doing, which
 * nothing else in the dashboard can tell them. 0 on bare metal is a real answer,
 * so the chart appears whenever the value was *measured* rather than whenever it
 * is interesting.
 */
function HostContentionChart({ points, hours }: { points: MetricsPoint[]; hours: number }) {
  const steal = points.some((p) => p.cpuSteal !== null);
  const swap = points.some((p) => (p.swapTotalMb ?? 0) > 0);
  if (!steal && !swap) return null;

  const lines = [];
  if (steal) lines.push({ key: 'cpuSteal' as const, colour: '#ef4444', label: 'CPU steal %' });
  // Swap is charted only where the box HAS swap. A permanently flat zero line on
  // the majority of servers that have none is noise pretending to be data.
  if (swap) lines.push({ key: 'swapUsedMb' as const, colour: '#8b5cf6', label: 'Swap MB' });

  const worstSteal = Math.max(...points.map((p) => p.cpuSteal ?? 0));
  return (
    <>
      <Chart title={t('Host contention')} points={points} lines={lines} hours={hours} />
      {steal && worstSteal > 5 && (
        <p style={{ fontSize: 11, color: 'var(--status-warning)', margin: '4px 0 0' }}>
          CPU steal peaked at {worstSteal.toFixed(1)}% — the host gave this
          machine&rsquo;s time to another tenant. Frame drops in that window are
          not something a setting here can fix.
        </p>
      )}
    </>
  );
}

function DiskNote({ points }: { points: MetricsPoint[] }) {
  const latest = [...points].reverse().find((p) => p.diskFreeMb !== null);
  if (!latest?.diskFreeMb) return null;

  const gb = latest.diskFreeMb / 1024;
  // A full save volume stops the game writing the world, which is a real way to
  // lose progress — so this is a warning, not a statistic.
  const low = gb < 5;

  return (
    <div
      className={low ? 'notice notice-warn' : 'notice'}
      style={{ marginTop: 12, fontSize: 12 }}
    >
      <HardDrive size={12} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
      {gb.toFixed(1)} GB free on the save volume
      {low && ' — a full disk stops the server writing the world.'}
    </div>
  );
}

function formatTick(ts: number, hours: number): string {
  const date = new Date(ts * 1000);
  if (hours > 48) {
    return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
  }
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
