import { useEffect, useRef, useState } from 'react';
import { api, bytes, json } from './api';

export interface Job {
  id: string; kind: string; status: string; created_at: number; updated_at: number; error: string | null;
  metadata: { stage?: string; done?: number | null; total?: number | null; unit?: string; result_id?: string; outputs?: Record<string, string>; cancel_requested?: boolean; started_at?: number; eta_seconds?: number | null; resume_id?: string; checkpoint?: string;
    training?: { epoch?: number; epochs?: number; batch?: number; batches?: number; loss_g?: number; loss_d?: number; gpu?: { utilization: number; used_mb: number; total_mb: number } | null };
    loss_history?: { epoch: number; batch?: number; loss_g?: number; loss_d?: number }[];
    batch_progress?: {done:number;total:number;completed_steps:number;phases:string[];phase_index?:number;phase_label?:string;phase_done?:number;phase_total?:number;source_index?:number;source_total:number;source_name?:string;clip_index?:number|null;clip_total?:number|null;phase_eta_seconds?:number|null};
    engine_progress?: {done?:number|null;total?:number|null;state?:string;unit?:string;eta_seconds?:number|null;cache_hit?:boolean;model_reused?:boolean;fallback_reason?:string|null;reason?:string;parameters?:{profile?:string;precision?:string;overlap?:number;segment_size?:number};inference_seconds?:number;load_seconds?:number;peak_vram_mb?:number|null} | null;
    cleanup_result?:{deleted_ids:string[];reclaimed_bytes:number;errors:{id:string;error:string}[];skipped:{id:string}[]};
  };
}
const statuses: Record<string, string> = { queued: '排队中', running: '处理中', completed: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断' };
export const jobNames: Record<string, string> = { dataset_prepare: '批量准备训练数据', dataset_edit: '切片边界编辑', dataset_export: '数据集导出', resources: '资源下载与校验', song_separation: '人声、伴奏与和声分离', separation: '音轨分离', rvc_train: 'RVC 训练', rvc_convert: '音色转换', mix: '混音导出', cover: '制作翻唱' };
const active = (job: Job) => ['queued', 'running'].includes(job.status);
Object.assign(jobNames,{storage_cleanup:'手动清理缓存',storage_delete:'永久删除文件'});
const duration = (n: number) => n < 60 ? `${Math.floor(n)} 秒` : `${Math.floor(n / 60)} 分 ${Math.floor(n % 60)} 秒`;

export function useTask(key: string) {
  const [id, setId] = useState(() => localStorage.getItem(`task:${key}`) ?? '');
  const [running,setRunning]=useState(()=>['queued','running'].includes(localStorage.getItem(`task-status:${id}`)??''));
  useEffect(()=>{function update(event:Event){const value=(event as CustomEvent<{id:string;running:boolean}>).detail;if(value.id===id)setRunning(value.running);}window.addEventListener('workbench-task-state',update);return()=>window.removeEventListener('workbench-task-state',update);},[id]);
  function track(job: { id: string }) { localStorage.setItem(`task:${key}`, job.id);localStorage.setItem(`task-status:${job.id}`,'queued');setRunning(true);setId(job.id); }
  return { id, track, running };
}

export function UploadProgress({ value }: { value: { done: number; total: number } | null }) {
  if (!value) return null;
  const uploaded = value.total > 0 && value.done >= value.total;
  return <div className="task-progress" role="status"><strong>{uploaded ? '文件已上传，正在校验…' : '正在上传文件'}</strong><progress aria-label="上传进度" max={value.total || undefined} value={value.total > 0 && !uploaded ? value.done : undefined} /><small>{bytes(value.done)}{value.total > 0 ? ` / ${bytes(value.total)}` : ''}</small></div>;
}

export function JobProgress({ id, job: supplied, onComplete }: { id?: string; job?: Job; onComplete?: (job: Job) => void }) {
  const [loaded, setLoaded] = useState<Job | null>(null), [error, setError] = useState(''), [log, setLog] = useState<string | null>(null), [now, setNow] = useState(Date.now() / 1000),[liveLog,setLiveLog]=useState(false);
  const callback = useRef(onComplete); callback.current = onComplete;
  const job = supplied ?? loaded;
  useEffect(()=>{if(job){localStorage.setItem(`task-status:${job.id}`,job.status);window.dispatchEvent(new CustomEvent('workbench-task-state',{detail:{id:job.id,running:active(job)}}));}},[job?.id,job?.status]);
  useEffect(() => {
    if (!id || supplied) return;
    let live = true, timer: ReturnType<typeof setTimeout>;
    setLoaded(null); setError(''); setLog(null); setLiveLog(false);
    async function refresh() {
      try {
        const next = await api<Job>(`/jobs/${id}`);
        if (!live) return;
        setLoaded(next); setError('');
        if (next.status === 'completed' && callback.current && !localStorage.getItem(`task-seen:${next.id}`)) { localStorage.setItem(`task-seen:${next.id}`, '1'); callback.current(next); }
        if (active(next)) timer = setTimeout(() => void refresh(), 1000);
      } catch (e) { if (live) { setError((e as Error).message); timer = setTimeout(() => void refresh(), 3000); } }
    }
    void refresh(); return () => { live = false; clearTimeout(timer); };
  }, [id, supplied]);
  useEffect(() => { if (!job || !active(job)) return; const timer = setInterval(() => setNow(Date.now() / 1000), 1000); return () => clearInterval(timer); }, [job?.status]);
  useEffect(()=>{if(!job||!liveLog)return;let live=true;const jobId=job.id;async function refresh(){try{const data=await api<{text:string}>(`/jobs/${jobId}/logs`);if(live)setLog(data.text||'引擎正在启动，等待日志…');}catch(e){if(live)setError((e as Error).message);}}void refresh();const timer=active(job)?setInterval(()=>void refresh(),2000):undefined;return()=>{live=false;if(timer)clearInterval(timer);};},[job?.id,job?.status,liveLog]);
  if (!id && !supplied) return null;
  if (!job) return <div className="task-progress" role="status"><p>{error || '任务已提交，正在读取进度…'}</p><progress aria-label="任务进度" /></div>;
  const m = job.metadata, training = m.training,batch=m.batch_progress,engine=m.engine_progress;
  const measured = batch??m;
  const measurable = typeof measured.total === 'number' && measured.total > 0 && typeof measured.done === 'number';
  const percent = job.status === 'completed' ? 100 : measurable ? Math.max(0, Math.min(100, 100 * measured.done! / measured.total!)) : null;
  const enginePercent=engine&&typeof engine.done==='number'&&typeof engine.total==='number'&&engine.total>0?Math.max(0,Math.min(100,100*engine.done/engine.total)):null;
  const phases = ['生成训练音频', 'RMVPE 音高提取', 'HuBERT 内容特征提取', 'RVC 训练', '校验训练模型导出', 'FAISS 索引构建'];
  const phase = job.status === 'completed' ? phases.length : phases.indexOf(m.stage ?? '');
  return <div className={`task-progress task-${job.status}`} data-job-id={job.id}>
    <div className="section-head"><strong>{jobNames[job.kind] ?? job.kind}</strong><span className="badge">{statuses[job.status]}</span></div>
    <p role="status">{job.status === 'queued' ? job.kind === 'resources' ? '等待下载，正在排队' : '等待工作进程' : job.status==='completed'?'完成':batch?'整批流程':m.stage || '引擎正在处理'}{percent !== null ? ` · ${percent.toFixed(1)}%` : ''}</p>
    <progress aria-label={`${jobNames[job.kind] ?? '任务'}进度`} max="100" value={percent ?? undefined} />
    <div className="progress-details"><span>{m.started_at ? `已用 ${duration(Math.max(0, (active(job) ? now : job.updated_at) - m.started_at))}` : '尚未开始'}</span><span>{active(job) ? batch?batch.phase_eta_seconds!=null?`当前阶段剩余约 ${duration(batch.phase_eta_seconds)}`:'当前阶段剩余时间待估算':m.eta_seconds != null ? `预计剩余 ${duration(m.eta_seconds)}` : '剩余时间待估算' : statuses[job.status]}</span>{measurable && m.unit !== 'epochs' && <span>{batch?`已完成 ${batch.completed_steps} / ${batch.total} 个步骤`:job.kind === 'resources' ? `${bytes(m.done!)} / ${bytes(m.total!)}` : `${m.done} / ${m.total}`}</span>}</div>
    {batch&&<div className="batch-current"><ol className="training-phases">{batch.phases.map((name,i)=><li key={name} className={job.status==='completed'||i<(batch.phase_index??0)?'done':i===batch.phase_index?'current':''}>{name}</li>)}</ol><strong>{job.status==='completed'?`已处理全部 ${batch.source_total} 首`:`第 ${batch.source_index??1} / ${batch.source_total} 首 · ${batch.source_name??'准备素材'}`}</strong><p>{batch.phase_label} · 本阶段已完成 {batch.phase_done??0} / {batch.phase_total??batch.source_total} 首</p><small>{m.stage}{batch.clip_total?` · 第 ${batch.clip_index} / ${batch.clip_total} 个切片`:''}</small>{active(job)&&<><progress aria-label="当前步骤进度" max="100" value={enginePercent??undefined}/><div className="progress-details"><span>{engine?.cache_hit?'已复用分离缓存':engine?.state==='loading'?'正在加载模型':engine?.state==='fallback'?'已回退计算方式':enginePercent!==null?`当前模型 ${enginePercent.toFixed(1)}% · ${engine?.done} / ${engine?.total} 块`:'当前步骤处理中'}</span>{engine?.eta_seconds!=null&&<span>当前步骤剩余约 {duration(engine.eta_seconds)}</span>}</div></>}{engine?.parameters&&<small>{({balanced:'均衡',quality:'保真',fast:'快速'} as Record<string,string>)[engine.parameters.profile??'']??'执行参数'} · {engine.parameters.precision==='amp_fp16'?'混合精度':'FP32'} · 重叠 {engine.parameters.overlap}{engine.model_reused?' · 复用已加载模型':''}</small>}{(engine?.fallback_reason||engine?.reason)&&<p className="notice">自动回退：{engine.fallback_reason||engine.reason}</p>}</div>}
    {job.kind === 'resources' && job.status === 'running' && <p className="notice">{m.stage?.startsWith('下载资源') ? '下载已开始，请保持工作台运行。' : '正在检查、校验或安装资源。'}</p>}
    {job.kind === 'rvc_train' && <><ol className="training-phases">{phases.map((name, i) => <li key={name} className={i < phase ? 'done' : i === phase ? 'current' : ''}>{name}{m.resume_id && (i === 1 || i === 2) ? '（复用特征）' : ''}</li>)}</ol><div className="stats compact"><article><span>训练轮次</span><strong>{training?.epoch ?? '—'} / {training?.epochs ?? '—'}</strong></article><article><span>当前批次</span><strong>{training?.batch ?? '—'} / {training?.batches ?? '—'}</strong></article><article><span>GPU / 显存</span><strong>{training?.gpu ? `${training.gpu.utilization}%` : '—'}</strong><small>{training?.gpu ? `${training.gpu.used_mb.toFixed(0)} / ${training.gpu.total_mb.toFixed(0)} MB` : '等待设备采样'}</small></article></div><LossCurve points={m.loss_history ?? []} />{m.checkpoint && <small>检查点已写入：{m.checkpoint}</small>}</>}
    {job.error && <p className="error" role="alert">{job.error}</p>}
    {m.cleanup_result&&<div><p>已删除 {m.cleanup_result.deleted_ids.length} 项，释放 {bytes(m.cleanup_result.reclaimed_bytes)}；跳过 {m.cleanup_result.skipped.length} 项。</p>{m.cleanup_result.errors.map(e=><p className="error" key={e.id}>{e.id.slice(0,12)}：{e.error}</p>)}</div>}
    {m.outputs && <div className="toolbar">{Object.entries(m.outputs).filter(([key]) => key !== 'workspace').map(([key, value]) => <a key={key} href={`/api/artifacts/${value}/file`} download>下载 {({ model: '模型', index: '索引', instrumental: '伴奏', backing: '和声', lead: '主唱', vocals: '人声', converted: '转换人声' } as Record<string, string>)[key] ?? key}</a>)}</div>}
    <div className="toolbar"><small>{job.id.slice(0, 12)}</small><button onClick={() => void api<{ text: string }>(`/jobs/${job.id}/logs`).then(data => {setLog(data.text || '暂无引擎日志。');setLiveLog(true);}).catch(e => setError(e.message))}>查看日志</button>{log!==null&&active(job)&&<button onClick={()=>setLiveLog(!liveLog)}>{liveLog?'暂停日志刷新':'继续日志刷新'}</button>}{active(job) && <button disabled={m.cancel_requested} onClick={() => void api<Job>(`/jobs/${job.id}/cancel`, json({})).then(setLoaded).catch(e => setError(e.message))}>{m.cancel_requested ? '等待停止…' : '取消任务'}</button>}</div>{error && <p className="error">{error}</p>}{log !== null && <pre className="job-log">{log}</pre>}
  </div>;
}

function LossCurve({ points }: { points: NonNullable<Job['metadata']['loss_history']> }) {
  const valid = points.filter(p => Number.isFinite(p.loss_g) && Number.isFinite(p.loss_d));
  if (!valid.length) return <small>损失曲线等待训练批次数据。</small>;
  const values = valid.flatMap(p => [p.loss_g!, p.loss_d!]), low = Math.min(...values), high = Math.max(...values);
  const path = (key: 'loss_g' | 'loss_d') => valid.map((p, i) => `${10 + 580 * i / Math.max(1, valid.length - 1)},${145 - 130 * (p[key]! - low) / Math.max(1e-8, high - low)}`).join(' ');
  return <figure className="loss-curve"><figcaption>实际训练损失 · <span className="loss-g">生成器</span> / <span className="loss-d">判别器</span><small>{low.toFixed(3)} — {high.toFixed(3)} · 最近 {valid.length} 个观测点</small></figcaption><svg viewBox="0 0 600 160" role="img" aria-label="训练损失曲线"><polyline points={path('loss_g')} fill="none" stroke="#007aff" strokeWidth="2"/><polyline points={path('loss_d')} fill="none" stroke="#a855f7" strokeWidth="2"/></svg></figure>;
}
