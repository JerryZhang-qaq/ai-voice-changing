import { useEffect, useState } from 'react';
import { api, json } from './api';

import { JobProgress, useTask } from './JobProgress';

interface Model { id: string; name: string; task: string; ready: boolean; downloaded: boolean; validated: boolean; evidence: string; }
interface Engines { separation: { environment: { ready: boolean; reason?: string; gpu_name?: string }; models: Model[]; recommended_package: string }; }
interface Artifact { id: string; name: string; role: string; metadata: { kind?: string; stem?: string }; }

export function SeparationPanel() {
  const task=useTask('separation');
  const [engines, setEngines] = useState<Engines | null>(null);
  const [items, setItems] = useState<Artifact[]>([]);
  const [source, setSource] = useState('');
  const [model, setModel] = useState('vocals_melband_unwa');
  const [segment, setSegment] = useState(256);
  const [overlap, setOverlap] = useState(8);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  async function refresh() {
    const [e, a] = await Promise.all([api<Engines>('/engines'), api<{items: Artifact[]}>('/artifacts')]); setEngines(e); setItems(a.items);
  }
  useEffect(() => { refresh().catch(e => setError(e.message)); }, []);
  const spec = engines?.separation.models.find(m => m.id === model);
  return <section><div className="section-head"><div><h2>人声与和声分离</h2><p>使用成熟的 RoFormer 预训练模型，保留可试听的独立音轨。</p></div><button disabled={busy} onClick={() => { void refresh().catch(e => setError(e.message)); }}>刷新引擎状态</button></div>
    {error && <p className="error" role="alert">{error}</p>}{message && <p className="notice" role="status">{message}</p>}
    {engines && !engines.separation.environment.ready && <p className="notice">引擎未就绪：{engines.separation.environment.reason}。需要 GPU、独立引擎环境和权重文件。</p>}
    <article className="card"><h3>选择输入与处理方式</h3><div className="toolbar"><select value={source} onChange={e => setSource(e.target.value)} aria-label="选择分离音频"><option value="">选择音频</option>{items.filter(i => i.role === 'source' || i.metadata.kind === 'separated_audio').map(i => <option key={i.id} value={i.id}>{i.name}</option>)}</select>
      <select value={model} onChange={e => setModel(e.target.value)} aria-label="选择分离模型">{engines?.separation.models.map(m => <option key={m.id} value={m.id}>{m.name} · {m.downloaded ? '已下载' : '未下载'}</option>)}</select></div>
      {spec && <p>{spec.evidence}</p>}
      {spec?.task === 'lead_backing' && <p>先使用人声/伴奏模型获得人声音轨，再分离主唱与和声。和声仍可能残留，需要试听。</p>}
      <div className="toolbar"><label>分块长度 <input className="number" type="number" min="64" max="512" value={segment} onChange={e => setSegment(Number(e.target.value))}/></label><label>重叠 <input className="number" type="number" min="2" max="50" value={overlap} onChange={e => setOverlap(Number(e.target.value))}/></label>
        <button disabled={busy || task.running || !source || !spec?.ready} onClick={() => { setBusy(true); setError(''); void api<{id: string}>('/separation', json({source_id: source, model_id: model, segment_size: segment, overlap})).then(j=>{task.track(j);setMessage('分离任务已提交。');}).catch(e => setError(e.message)).finally(() => setBusy(false)); }}>开始处理</button>
      </div>
      <JobProgress id={task.id} onComplete={()=>void refresh().catch(e=>setError(e.message))}/>
    </article>
    <article className="card"><h3>分离产物</h3>{items.filter(i => i.metadata.kind === 'separated_audio').map(i => <div className="clip" key={i.id}><strong>{i.name}</strong><audio controls preload="none" src={`/api/artifacts/${i.id}/file`}/><a href={`/api/artifacts/${i.id}/file`} download>下载音轨</a></div>)}</article>
  </section>;
}
