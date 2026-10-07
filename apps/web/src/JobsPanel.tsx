import { useEffect, useState } from 'react';
import { api } from './api';
import { JobProgress, type Job } from './JobProgress';

export function JobsPanel() {
  const [jobs, setJobs] = useState<Job[]>([]), [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    const refresh = () => api<Job[]>('/jobs').then(result => { if (live) { setJobs(result); setError(''); } }).catch(e => { if (live) setError(e.message); });
    void refresh(); const timer = setInterval(refresh, 2000);
    return () => { live = false; clearInterval(timer); };
  }, []);
  return <section><h2>任务中心</h2><p>汇总全部任务。各功能页面也会显示执行进度，刷新页面不会取消任务。</p>{error && <p className="error">{error}</p>}{!jobs.length && <div className="empty">还没有任务。</div>}{jobs.map(job => <article className="card" key={job.id}><JobProgress job={job} /></article>)}</section>;
}
