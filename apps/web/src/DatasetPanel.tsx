import { useEffect, useState } from 'react';
import { api, bytes, json } from './api';

interface Artifact { id: string; name: string; role: string; size: number; metadata: { kind?: string; stem?: string; summary?: { accepted_count: number; clip_count: number }; }; }
interface Clip { artifact_id: string; source_id: string; duration: number; status: string; reasons: string[]; available: boolean; start_sample: number; sample_rate: number; valid_start_sample:number; valid_end_sample:number; decision?:{origin:string}; near_duplicate_of?:string[]; }
interface CleanupComparison { input_id:string; candidate_id:string; report_id:string; input_available:boolean;candidate_available:boolean;report_available:boolean;task:string; decision:string; reasons:string[]; metrics:{duration_delta_seconds?:number;median_level_change_db?:number}; }
interface Dataset { clips: Clip[]; sources?:{id:string; status?:string; reasons?:string[]; processing?:{stages:CleanupComparison[];harmony?:{status:string;reasons:string[];metrics?:{independent_overlap_seconds?:number};lead_id:string;backing_id:string;lead_available:boolean;backing_available:boolean}}}[]; processor_version: string; split?: {warning:string|null;linked_source_count?:number}; summary: { clip_count: number; accepted_count: number; review_count: number; excluded_count: number; train_count?:number;validation_count?:number;near_duplicate_count?:number;cleanup_fallback_count?:number;excluded_source_count?:number }; }
const reasons: Record<string, string> = { SHORT_CLIP: '片段较短', UNSAFE_BOUNDARY: '连续演唱中的强制边界', VOCAL_PURITY_UNVERIFIED: '人声纯净度尚未自动验证', IDENTITY_UNVERIFIED: '歌手身份尚未自动验证', CLIPPING_DETECTED: '发现削波', EXACT_DUPLICATE: '完全重复片段', NEAR_DUPLICATE:'疑似相同录音，请检查重复版本', LOW_SIGNAL: '信号过弱', PHASE_CANCELLATION: '立体声相位抵消风险', MULTICHANNEL_DOWNMIX: '多声道下混需检查', CLEANUP_COMPARISON_UNCALIBRATED:'清洗检查规则尚待真实歌唱校准', CLEANUP_DURATION_CHANGED:'清洗改变了音频时长', NEW_CLIPPING:'清洗后出现新的削波风险', EXTRACTION_CONTENT_CHANGED:'提取人声会改变能量和频谱，需试听', INPUT_ACTIVITY_UNCERTAIN:'有效声音过少，无法可靠比较', CLEANUP_SIGNAL_COLLAPSE:'清洗后信号严重丢失', POSSIBLE_ACTIVITY_DAMAGE:'部分声音疑似被误删', WEAK_ACTIVITY_LOSS:'弱声或气声可能被误删', POSSIBLE_HIGH_FREQUENCY_DAMAGE:'高频成分可能被误删', CLEANUP_TIMING_SHIFT:'清洗后出现时间错位', CLEANUP_FALLBACK_REQUIRES_REVIEW:'已回退处理前版本，仍需复核', INVALID_CLEANUP_OUTPUT:'清洗结果无效，已回退' };
Object.assign(reasons,{COMPLEX_HARMONY:'复杂和声，弃用整份素材',HARMONY_CHECK_REQUIRED:'尚未筛查和声',SOLO_DECLARATION_REQUIRED:'尚未确认独唱素材',VOCAL_CONTENT_UNCERTAIN:'歌唱成分证据不足，请试听',SEVERE_CLIPPING:'严重平顶削波',MANUAL_BOUNDARY_REVIEW:'边界已编辑，请重新试听确认',HARMONY_INPUT_UNCERTAIN:'和声检查的有效演唱不足',HARMONY_UNCERTAIN:'疑似叠唱或模型泄漏，请试听',HARMONY_STEMS_MISALIGNED:'和声候选时间不一致，需复核',POSSIBLE_BACKING_VOCAL:'疑似叠唱或同音和声，请试听'});
const tasks:Record<string,string>={vocals:'提取人声',lead_backing:'剥离和声',dereverb:'去混响',denoise:'降噪'};
const statuses: Record<string, string> = { accepted: '接受', review: '待复核', excluded: '排除' };

export function DatasetPanel() {
  const [items, setItems] = useState<Artifact[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [kind, setKind] = useState('dry_vocal');
  const [files, setFiles] = useState<FileList | null>(null);
  const [target, setTarget] = useState(8);
  const [max, setMax] = useState(15);
  const [forceSeparation,setForceSeparation]=useState(false);
  const [backing,setBacking]=useState(true);
  const [mode,setMode]=useState('automatic');
  const [solo,setSolo]=useState(false);
  const [denoise,setDenoise]=useState(false);
  const [min,setMin]=useState(2);
  const [silence,setSilence]=useState(.3);
  const [padding,setPadding]=useState(.15);
  const [threshold,setThreshold]=useState('');
  const [dereverb,setDereverb]=useState(false);
  const [manifestId, setManifestId] = useState('');
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [decisions, setDecisions] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  async function refresh() { setItems((await api<{ items: Artifact[] }>('/artifacts')).items); }
  useEffect(() => { refresh().catch(e => setError(e.message)); }, []);
  async function action(fn: () => Promise<void>) {
    setBusy(true); setError('');
    try { await fn(); } catch(e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function loadManifest(id: string) {
    if (!id) { setDataset(null); setManifestId(''); return; }
    const next = await api<Dataset>(`/datasets/${id}`);
    setManifestId(id); setDataset(next); setDecisions({});
  }
  return <section>
    <div className="section-head"><div><h2>素材与数据集</h2><p>导入独唱素材，自动筛查和声、清洗、切片；有风险的片段留待复核。</p></div><button disabled={busy} onClick={() => void action(refresh)}>刷新</button></div>
    {error && <p className="error" role="alert">{error}</p>}{message && <p className="notice" role="status">{message}</p>}
    <article className="card"><h3>导入训练素材</h3><p>支持常见音频格式，单文件不超过 512 MB、30 分钟。原始文件始终保留。</p>
      <div className="toolbar"><input type="file" multiple accept="audio/*,.flac,.m4a,.ogg" onChange={e => setFiles(e.target.files)} disabled={busy}/>
      <select value={kind} onChange={e => setKind(e.target.value)}><option value="dry_vocal">纯人声 / 干声</option><option value="song">带伴奏歌曲</option></select>
      <button disabled={busy || !files?.length} onClick={() => void action(async () => {
        for (const file of Array.from(files ?? [])) {
          const form = new FormData(); form.append('file', file); form.append('kind', kind);
          await api('/sources', { method: 'POST', body: form });
        }
        setMessage('素材已导入。请选择素材开始处理。'); await refresh();
      })}>{busy ? '处理中…' : '上传素材'}</button></div>
    </article>
    <article className="card"><h3>自动准备数据</h3><p>歌曲先提取人声，再检查和声。检测到复杂和声会弃用整份素材。自动模式接受规则通过的片段，保留不确定片段供试听；模型处理需要 GPU 引擎就绪。</p>
      <div className="table-wrap"><table><thead><tr><th>选择</th><th>素材</th><th>输入类型</th><th>大小</th></tr></thead><tbody>{items.filter(i => i.role === 'source' || ['vocals', 'lead', 'dry', 'clean'].includes(i.metadata.stem ?? '')).map(i => <tr key={i.id}>
        <td><input type="checkbox" aria-label={`选择素材 ${i.name}`} disabled={busy} checked={selected.includes(i.id)} onChange={e => setSelected(e.target.checked ? [...selected, i.id] : selected.filter(id => id !== i.id))}/></td>
        <td>{i.name}</td><td>{i.metadata.kind === 'dry_vocal' || i.metadata.stem ? '人声' : '歌曲'}</td><td>{bytes(i.size)}</td>
      </tr>)}</tbody></table></div>
      <div className="toolbar"><label>处理模式 <select value={mode} onChange={e=>setMode(e.target.value)}><option value="automatic">自动准备</option><option value="review">全部人工复核</option></select></label><label><input type="checkbox" checked={solo} onChange={e=>setSolo(e.target.checked)}/> 我确认所选素材均为同一歌手独唱</label></div>
      <div className="toolbar"><label>最短 <input className="number" type="number" min=".5" max="30" step=".5" value={min} onChange={e=>setMin(Number(e.target.value))}/> 秒</label><label>目标长度 <input className="number" type="number" min="2" max="30" value={target} onChange={e => setTarget(Number(e.target.value))}/> 秒</label><label>最长 <input className="number" type="number" min="2" max="30" value={max} onChange={e => setMax(Number(e.target.value))}/> 秒</label>
      <label><input type="checkbox" checked={forceSeparation} onChange={e=>setForceSeparation(e.target.checked)}/> 干声也执行伴奏去除</label><label><input type="checkbox" checked={backing||mode==='automatic'} disabled={mode==='automatic'} onChange={e=>setBacking(e.target.checked)}/> 筛查复杂和声（自动模式必选）</label><label><input type="checkbox" checked={dereverb} onChange={e=>setDereverb(e.target.checked)}/> 温和去混响</label>
      <label><input type="checkbox" checked={denoise} onChange={e=>setDenoise(e.target.checked)}/> 模型降噪</label></div>
      <details><summary>切片高级参数</summary><div className="toolbar"><label>停顿长度 <input className="number" type="number" min=".05" max="2" step=".05" value={silence} onChange={e=>setSilence(Number(e.target.value))}/> 秒</label><label>边缘余量 <input className="number" type="number" min="0" max=".5" step=".05" value={padding} onChange={e=>setPadding(Number(e.target.value))}/> 秒</label><label>活动阈值 <input className="number" type="number" min="-80" max="-20" placeholder="自动" value={threshold} onChange={e=>setThreshold(e.target.value)}/> dBFS</label></div></details><div className="toolbar">
      <button disabled={busy || !selected.length || (mode==='automatic'&&!solo)} onClick={() => void action(async () => {
        const job = await api<{id: string}>('/datasets/prepare', json({ source_ids: selected, target_seconds: target, max_seconds: max, force_vocal_separation:forceSeparation, check_harmony:backing,dereverb,denoise,mode,solo_confirmed:solo,min_seconds:min,silence_seconds:silence,padding_seconds:padding,threshold_db:threshold?Number(threshold):null }));
        setMessage(`任务 ${job.id.slice(0, 12)} 已提交，请在任务中心查看进度。`);
      })}>准备所选素材</button></div>
    </article>
    <article className="card"><h3>数据集审查</h3><div className="toolbar"><select aria-label="选择数据集" disabled={busy} value={manifestId} onChange={e => void action(() => loadManifest(e.target.value))}><option value="">选择数据集版本</option>{items.filter(i => i.metadata.kind === 'dataset_manifest').map(i => <option key={i.id} value={i.id}>{i.name.replace(/\.json$/,'')} · {i.id.slice(0, 8)} · {i.metadata.summary?.clip_count ?? 0} 段 · 接受 {i.metadata.summary?.accepted_count ?? 0}</option>)}</select></div>
    {dataset && <><p>共 {dataset.summary.clip_count} 段：接受 {dataset.summary.accepted_count}，待复核 {dataset.summary.review_count}，排除 {dataset.summary.excluded_count}。新版本保存后，接受的音频会受到清理保护。</p>
      {dataset.split && <p>训练 {dataset.summary.train_count} 段，按来源保留验证 {dataset.summary.validation_count} 段。{dataset.split.warning ? '去重后的素材只有一个独立来源组，无法建立独立验证集。' : ''}{dataset.split.linked_source_count ? ` 已关联 ${dataset.split.linked_source_count} 个重复来源。` : ''}</p>}
      <p className="notice">自动筛查仍处于首测阶段，不能保证检出所有和声与伴奏残留。歌手身份由素材提供者确认；“待复核”片段不能直接训练。</p>
      <p>因复杂和声弃用来源 {dataset.summary.excluded_source_count??0} 份。</p>
      {dataset.sources?.filter(s=>s.processing?.harmony).map(s=><details className="card" key={s.id}><summary>和声检查 · {s.processing!.harmony!.status==='rejected'?'弃用来源':s.processing!.harmony!.status==='passed'?'通过规则':'待复核'}</summary><p>{s.processing!.harmony!.reasons.map(r=>reasons[r]??r).join(' · ')}</p>{s.processing!.harmony!.lead_available&&<div><p>主唱候选</p><audio controls preload="none" src={`/api/artifacts/${s.processing!.harmony!.lead_id}/file`}/></div>}{s.processing!.harmony!.backing_available&&<div><p>和声候选（检查泄漏或叠唱）</p><audio controls preload="none" src={`/api/artifacts/${s.processing!.harmony!.backing_id}/file`}/></div>}</details>)}
      {!!dataset.summary.near_duplicate_count && <p>发现 {dataset.summary.near_duplicate_count} 个疑似重复片段。它们没有被自动删除；对应来源会归入同一训练/验证组。</p>}
      {dataset.sources?.some(s=>s.processing?.stages.length) && <details className="card"><summary>清洗前后对比 · {dataset.summary.cleanup_fallback_count ?? 0} 次自动回退</summary>
        {dataset.sources.flatMap(s=>s.processing?.stages ?? []).map((stage,i)=><div className="clip" key={`${stage.report_id}-${i}`}><strong>{tasks[stage.task] ?? stage.task} · {stage.decision==='fallback_input'?'已回退处理前版本':'使用处理后候选'}</strong>
          <p>{stage.reasons.map(r=>reasons[r] ?? r).join(' · ')}</p>
          <div><p>处理前</p>{stage.input_available?<audio controls preload="none" src={`/api/artifacts/${stage.input_id}/file`}/>:<small>对比音频已清理</small>}</div>
          <div><p>处理后候选</p>{stage.candidate_available?<audio controls preload="none" src={`/api/artifacts/${stage.candidate_id}/file`}/>:<small>对比音频已清理</small>}</div>
          <small>{stage.metrics.duration_delta_seconds !== undefined ? `时长变化 ${(stage.metrics.duration_delta_seconds*1000).toFixed(0)} 毫秒。` : ''}{stage.metrics.median_level_change_db !== undefined ? `活动段电平变化 ${stage.metrics.median_level_change_db.toFixed(1)} dB。` : ''}</small>
          {stage.report_available && <a href={`/api/artifacts/${stage.report_id}/file`} download>下载对比记录</a>}
        </div>)}
      </details>}
      <div className="clips">{dataset.clips.map((c, index) => <div className="clip" key={c.artifact_id}><div><strong>片段 {index + 1}</strong><small>{(c.start_sample / c.sample_rate).toFixed(2)} 秒起 · {c.duration.toFixed(2)} 秒</small></div>
        {c.available ? <audio controls preload="none" src={`/api/artifacts/${c.artifact_id}/file`}/> : <p className="error">片段已清理，请重新准备。</p>}
        <select disabled={!c.available || busy} aria-label={`片段 ${index + 1} 审查决定`} value={decisions[c.artifact_id] ?? c.status} onChange={e => setDecisions({...decisions, [c.artifact_id]: e.target.value})}>{Object.entries(statuses).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
        <small>{c.decision?.origin==='automatic'?'自动规则接受':c.decision?.origin==='manual'?'人工审查':'规则标记'}</small>
        <ClipEditor clip={c} manifestId={manifestId} busy={busy} onSubmit={(start,end)=>action(async()=>{const job=await api<{id:string}>(`/datasets/${manifestId}/edit`,json({clip_id:c.artifact_id,start_seconds:start,end_seconds:end}));setMessage(`边界编辑任务 ${job.id.slice(0,12)} 已提交，完成后刷新并选择新版本。`);})}/>
        <p className="clip-reasons">{c.reasons.map(r => reasons[r] ?? r).join(' · ')}{c.near_duplicate_of?.length ? ` · 相关片段 ${c.near_duplicate_of.map(id=>dataset.clips.findIndex(other=>other.artifact_id===id)+1).join('、')}` : ''}</p></div>)}</div>
      <div className="toolbar"><button disabled={busy || !Object.keys(decisions).length} onClick={() => void action(async () => {
        const next = await api<{id: string}>(`/datasets/${manifestId}/review`, json({ decisions })); await refresh(); await loadManifest(next.id); setMessage('已保存新的数据集版本，原版本仍可追溯。');
      })}>保存审查为新版本</button>
      <button disabled={busy || !dataset.summary.accepted_count} onClick={()=>void action(async()=>{const job=await api<{id:string}>(`/datasets/${manifestId}/export`,json({}));setMessage(`数据集导出任务 ${job.id.slice(0,12)} 已提交。`);})}>导出已接受数据集</button></div>
    </>}
    </article>
    <article className="card"><h3>数据集导出包</h3>{items.filter(i=>i.metadata.kind==='dataset_export').map(i=><div className="toolbar" key={i.id}><span>{i.name} · {i.id.slice(0,12)}</span><a href={`/api/artifacts/${i.id}/file`} download>下载 ZIP</a></div>)}</article>
  </section>;
}

function ClipEditor({clip,manifestId,busy,onSubmit}:{clip:Clip;manifestId:string;busy:boolean;onSubmit:(a:number,b:number)=>Promise<void>}) {
  const [open,setOpen]=useState(false),[peaks,setPeaks]=useState<number[]>([]),[error,setError]=useState('');
  const [start,setStart]=useState(clip.valid_start_sample/clip.sample_rate),[end,setEnd]=useState(clip.valid_end_sample/clip.sample_rate);
  async function show(){setOpen(!open);if(!open&&!peaks.length){try{setPeaks((await api<{peaks:number[]}>(`/datasets/${manifestId}/waveform/${clip.artifact_id}`)).peaks);}catch(e){setError((e as Error).message);}}}
  return <div><button disabled={busy||!clip.available} onClick={()=>void show()}>{open?'收起边界编辑':'波形与边界编辑'}</button>{open&&<div>{error&&<small className="error">{error}</small>}<svg viewBox="0 0 500 64" width="100%" height="64" role="img" aria-label="音频波形">{peaks.map((p,i)=><line key={i} x1={i*500/peaks.length} x2={i*500/peaks.length} y1={32-30*Math.min(1,p)} y2={32+30*Math.min(1,p)} stroke="currentColor"/>)}</svg><div className="toolbar"><label>核心起点 <input className="number" type="number" step=".01" min="0" value={start} onChange={e=>setStart(Number(e.target.value))}/></label><label>核心终点 <input className="number" type="number" step=".01" min="0" value={end} onChange={e=>setEnd(Number(e.target.value))}/></label><button disabled={busy||end<=start} onClick={()=>void onSubmit(start,end)}>另存边界版本</button></div><small>时间以来源工作母版为基准；自动保留边缘余量。编辑后需再次试听接受。</small></div>}</div>;
}
