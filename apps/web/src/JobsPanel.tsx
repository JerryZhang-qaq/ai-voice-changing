import { useEffect, useState } from 'react';
import { api, json } from './api';

interface Job { id: string; kind: string; status: string; error: string | null; metadata: { stage?: string; done?: number; total?: number; result_id?: string; outputs?: Record<string,string>; cancel_requested?: boolean }; }
const statuses: Record<string, string> = { queued: '排队中', running: '处理中', completed: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断' };
const names: Record<string,string> = {dataset_prepare:'数据集准备',dataset_edit:'切片边界编辑',dataset_export:'数据集导出',resources:'基础资源下载与校验',separation:'音轨分离',rvc_train:'RVC 训练',rvc_convert:'音色转换',mix:'混音导出',cover:'制作翻唱'};

export function JobsPanel() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState('');
  const [logs, setLogs] = useState<Record<string,string>>({});
  useEffect(() => {
    let live = true;
    const refresh = () => api<Job[]>('/jobs').then(j => { if (live) { setJobs(j); setError(''); } }).catch(e => { if (live) setError(e.message); });
    void refresh(); const interval = setInterval(refresh, 2000);
    return () => { live = false; clearInterval(interval); };
  }, []);
  return <section><h2>任务中心</h2><p>处理任务保存在服务器上。刷新页面不会取消任务；中断的任务会明确标记。</p>
    {error && <p className="error" role="alert">{error}</p>}
    {!jobs.length && <div className="empty">还没有任务。</div>}
    {jobs.map(j => <article className="card" key={j.id}><div className="section-head"><h3>{names[j.kind] ?? j.kind}</h3><span className="badge">{statuses[j.status] ?? j.status}</span></div><small>{j.id}</small><p>{j.metadata.stage ?? '等待工作进程'}{j.metadata.total ? ` · ${j.metadata.done ?? 0}/${j.metadata.total}` : ''}</p>
      {j.error && <p className="error">{j.error}</p>}
      {j.metadata.result_id && <p>产物编号：{j.metadata.result_id.slice(0, 12)}。可到相应功能页刷新后查看。</p>}
      {j.metadata.outputs && <p>已保存产物：{Object.entries(j.metadata.outputs).map(([name,id])=>`${name} (${id.slice(0,8)})`).join(' · ')}</p>}
      <button onClick={()=>{void api<{text:string}>(`/jobs/${j.id}/logs`).then(d=>setLogs(previous=>({...previous,[j.id]:d.text||'暂无引擎日志。'}))).catch(e=>setError(e.message));}}>查看日志</button>
      {logs[j.id] && <pre className="job-log">{logs[j.id]}</pre>}
      {['queued', 'running'].includes(j.status) && <button disabled={j.metadata.cancel_requested} onClick={() => { void api(`/jobs/${j.id}/cancel`, json({})).then(() => api<Job[]>('/jobs')).then(setJobs).catch(e => setError(e.message)); }}>{j.metadata.cancel_requested ? '等待安全停止…' : '取消任务'}</button>}
    </article>)}
  </section>;
}
