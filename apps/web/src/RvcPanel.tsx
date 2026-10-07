import { useEffect, useState } from 'react';
import { api, json, upload } from './api';

import { JobProgress, UploadProgress, useTask } from './JobProgress';

interface Artifact { id: string; name: string; role: string; availability?:{accepted_count:number;missing_count:number}; metadata: {kind?: string; model_id?: string; sample_rate?: number; dataset_id?: string; rate?: string; summary?: {accepted_count: number}; }; }
interface Engines { rvc: {ready: boolean; environment: {reason?: string; gpu_name?: string}; missing_base_files: string[]; training_rates: Record<string, boolean>; }; separation: {models: {id: string; name: string; task: string; ready: boolean}[]}; }

export function RvcPanel() {
  const task=useTask('rvc:train');
  const [uploadProgress,setUploadProgress]=useState<{done:number;total:number}|null>(null);
  const [items, setItems] = useState<Artifact[]>([]);
  const [engines, setEngines] = useState<Engines | null>(null);
  const [modelFile, setModelFile] = useState<File | null>(null);
  const [indexFile, setIndexFile] = useState<File | null>(null);
  const [dataset, setDataset] = useState('');
  const [rate, setRate] = useState('40k');
  const [epochs, setEpochs] = useState(200);
  const [batch, setBatch] = useState(4);
  const [saveEvery,setSaveEvery]=useState(10);
  const [resume, setResume] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  async function refresh() { const [a,e] = await Promise.all([api<{items: Artifact[]}>('/artifacts'),api<Engines>('/engines')]); setItems(a.items);setEngines(e); }
  useEffect(() => { refresh().catch(e => setError(e.message)); }, []);
  async function action(fn: () => Promise<void>) { setBusy(true);setError('');try { await fn(); } catch(e) { setError((e as Error).message); } finally { setBusy(false); } }
  async function submit(path:string,body:object){task.track(await api<{id:string}>(path,json(body)));}
  const models = items.filter(i => i.metadata.kind === 'rvc_model');
  return <section><div className="section-head"><div><h2>训练与音色模型</h2><p>使用审核后的数据集训练，或导入已有 RVC 音色模型。转换素材在独立页面导入。</p></div><button disabled={busy} onClick={() => void action(refresh)}>刷新</button></div>
    {error && <p className="error" role="alert">{error}</p>}{message && <p className="notice" role="status">{message}</p>}
    {engines && !engines.rvc.ready && <p className="notice">RVC 未就绪：{engines.rvc.environment.reason ?? '上游版本或基础权重不完整'}。真实训练和转换需要 GPU 与完整引擎环境。</p>}
    <article className="card"><h3>导入音色模型</h3><p>上传 .pth 推理权重及对应 .index。导入会在独立引擎进程中检查结构与索引维度。</p><div className="toolbar"><label>权重 <input type="file" accept=".pth" onChange={e => setModelFile(e.target.files?.[0] ?? null)}/></label><label>索引 <input type="file" accept=".index" onChange={e => setIndexFile(e.target.files?.[0] ?? null)}/></label><button disabled={busy || !modelFile} onClick={() => void action(async () => {const form=new FormData();form.append('model',modelFile!);if(indexFile)form.append('index',indexFile);setUploadProgress({done:0,total:0});try{await upload('/models/import',form,setUploadProgress);await refresh();setMessage('模型已导入。');}finally{setUploadProgress(null);}})}>导入模型</button></div><UploadProgress value={uploadProgress}/>
    {models.map(m => <div className="toolbar" key={m.id}><span>{m.name} · {m.metadata.sample_rate} Hz</span><a href={`/api/artifacts/${m.id}/file`} download>下载权重</a>{items.filter(i=>i.metadata.model_id===m.id).map(i=><a key={i.id} href={`/api/artifacts/${i.id}/file`} download>下载索引</a>)}</div>)}</article>
    <article className="card"><h3>训练专属音色</h3><div className="toolbar"><select aria-label="训练数据集" value={dataset} onChange={e=>{setDataset(e.target.value);setResume('');}}><option value="">选择已审查数据集</option>{items.filter(i=>i.metadata.kind==='dataset_manifest' && (i.availability?.accepted_count??i.metadata.summary?.accepted_count??0)>=2).map(i=><option key={i.id} value={i.id}>{i.name.replace(/\.json$/,'')} · {i.id.slice(0,8)} · 接受 {i.availability?.accepted_count??i.metadata.summary?.accepted_count} 段</option>)}</select><select value={rate} onChange={e=>setRate(e.target.value)} aria-label="训练采样率">{['32k','40k','48k'].map(r=><option key={r}>{r}</option>)}</select></div>
      <div className="toolbar"><label>总轮数 <input className="number" type="number" min="1" max="10000" value={epochs} onChange={e=>setEpochs(Number(e.target.value))}/></label><label>批大小 <input className="number" type="number" min="1" max="64" value={batch} onChange={e=>setBatch(Number(e.target.value))}/></label><label>保存间隔 <input className="number" type="number" min="1" max="1000" value={saveEvery} onChange={e=>setSaveEvery(Number(e.target.value))}/> 轮</label><select aria-label="继续训练检查点" value={resume} onChange={e=>setResume(e.target.value)}><option value="">从预训练权重开始</option>{items.filter(i=>i.metadata.kind==='workspace' && i.metadata.dataset_id===dataset && i.metadata.rate===rate).map(i=><option key={i.id} value={i.id}>继续 {i.id.slice(0,12)} 的检查点</option>)}</select><button disabled={busy || task.running || !dataset || !engines?.rvc.ready || !engines.rvc.training_rates[rate]} onClick={()=>void action(()=>submit('/training',{dataset_id:dataset,rate,epochs,batch_size:batch,save_every:saveEvery,resume_id:resume||null}))}>开始训练</button></div><p>需要至少两个已接受片段；可用时长、音域与纯净度仍决定质量。继续训练的总轮数应大于已完成轮数。</p>
      <JobProgress id={task.id} onComplete={()=>void action(refresh)}/>
    </article>
  </section>;
}
