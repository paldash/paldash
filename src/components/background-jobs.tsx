'use client';

import { useEffect, useState } from 'react';
import { getBackgroundJobs, cancelBackgroundJob, type BackgroundJob } from '@/lib/save-api';

export default function BackgroundJobs() {
  const [jobs, setJobs] = useState<BackgroundJob[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const result = await getBackgroundJobs();
        if (active) { setJobs(result.jobs); setError(''); }
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : 'Could not read export progress');
      }
      if (active) timer = setTimeout(load, 4000);
    };
    void load();
    return () => { active = false; clearTimeout(timer); };
  }, []);
  if (!jobs.length && !error) return null;
  return <details className="glass-card" style={{ padding: 12, marginBottom: 12 }}>
    <summary>Export activity {jobs.some(j => ['queued', 'running'].includes(j.state)) ? '— working in the background' : ''}</summary>
    {error && <p role="alert">{error}</p>}
    {jobs.map(job => <div key={job.jobId} style={{ paddingTop: 8 }}>
      <span>{job.kind.replaceAll('-', ' ')}: {job.error || job.stage}</span>
      {job.state === 'queued' && <button className="btn" onClick={async () => {
        try { await cancelBackgroundJob(job.jobId); setJobs((await getBackgroundJobs()).jobs); }
        catch (e) { setError(e instanceof Error ? e.message : 'Cancellation failed'); }
      }}>Cancel queued export</button>}
      {job.state === 'completed' && job.kind === 'self-export' && <a download href="/api/save/export/self/download">Download own world</a>}
      {job.state === 'completed' && job.kind === 'solo-export' && <a download href={`/api/save/jobs/${job.jobId}/download`}>Download world copy</a>}
      {job.state === 'completed' && job.kind === 'server-export' && job.result !== null && typeof job.result === 'object' && 'artifactId' in job.result && typeof job.result.artifactId === 'string' && /^[a-f0-9]{32}$/.test(job.result.artifactId) &&
        <a href={`/api/save/export/server/${job.result.artifactId}/download`}>Download server world</a>}
    </div>)}
    <p className="text-muted">Queued exports can be cancelled. Running exports finish safely, even if you leave this page.</p>
  </details>;
}
