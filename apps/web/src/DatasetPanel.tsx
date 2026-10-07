import { useEffect, useState } from 'react';
import { api, bytes, json, upload } from './api';
import { JobProgress, UploadProgress, useTask } from './JobProgress';
import type { Job } from './JobProgress';
import { SeparationProfile } from './SeparationProfile';
import type { Profile } from './SeparationProfile';

interface Artifact { id: string; name: string; role: string; size: number; created_at: number; exists: boolean; availability?:{missing_count:number;available_count:number;accepted_count:number}; metadata: { kind?: string; singer?:string; stem?: string; summary?: { accepted_count: number; clip_count: number }; }; }
interface Folder {singer:string;source_ids:string[];items:Artifact[];bytes:number;}
interface HarmonyEvidence { status:string; scope?:string; reasons?:string[]; lead_id?:string; backing_id?:string; report_id?:string; lead_available?:boolean; backing_available?:boolean; report_available?:boolean; }
interface Clip { artifact_id: string; name?:string; source_id: string; duration: number; status: string; reasons: string[]; available: boolean; start_sample: number; sample_rate: number; valid_start_sample:number; valid_end_sample:number; decision?:{origin:string}; near_duplicate_of?:string[]; harmony?:HarmonyEvidence; }
interface CleanupComparison { input_id:string; candidate_id:string; report_id:string; input_available:boolean;candidate_available:boolean;report_available:boolean;task:string; decision:string; reasons:string[]; metrics:{duration_delta_seconds?:number;median_level_change_db?:number}; }
interface Dataset { clips: Clip[]; sources?:{id:string; status?:string; reasons?:string[]; processing?:{stages:CleanupComparison[]}}[]; schema_version?:number; harmony_review?:{scope:string;enabled:boolean}; processor_version: string; split?: {warning:string|null;linked_source_count?:number}; summary: { clip_count: number; accepted_count: number; review_count: number; excluded_count: number; train_count?:number;validation_count?:number;near_duplicate_count?:number;cleanup_fallback_count?:number;excluded_source_count?:number;harmony_review_count?:number }; }
const reasons: Record<string, string> = { SHORT_CLIP: '片段较短', UNSAFE_BOUNDARY: '连续演唱中的强制边界', VOCAL_PURITY_UNVERIFIED: '人声纯净度尚未自动验证', IDENTITY_UNVERIFIED: '歌手身份尚未自动验证', CLIPPING_DETECTED: '发现削波', EXACT_DUPLICATE: '完全重复片段', NEAR_DUPLICATE:'疑似相同录音，请检查重复版本', LOW_SIGNAL: '信号过弱', PHASE_CANCELLATION: '立体声相位抵消风险', MULTICHANNEL_DOWNMIX: '多声道下混需检查', CLEANUP_COMPARISON_UNCALIBRATED:'清洗检查规则尚待真实歌唱校准', CLEANUP_DURATION_CHANGED:'清洗改变了音频时长', NEW_CLIPPING:'清洗后出现新的削波风险', EXTRACTION_CONTENT_CHANGED:'提取人声会改变能量和频谱，需试听', INPUT_ACTIVITY_UNCERTAIN:'有效声音过少，无法可靠比较', CLEANUP_SIGNAL_COLLAPSE:'清洗后信号严重丢失', POSSIBLE_ACTIVITY_DAMAGE:'部分声音疑似被误删', WEAK_ACTIVITY_LOSS:'弱声或气声可能被误删', POSSIBLE_HIGH_FREQUENCY_DAMAGE:'高频成分可能被误删', CLEANUP_TIMING_SHIFT:'清洗后出现时间错位', CLEANUP_FALLBACK_REQUIRES_REVIEW:'已回退处理前版本，仍需复核', INVALID_CLEANUP_OUTPUT:'清洗结果无效，已回退' };
Object.assign(reasons,{CLIP_UNDER_ONE_SECOND:'切片不足 1 秒，直接排除',COMPLEX_HARMONY:'此片段疑似复杂和声，请试听决定',HARMONY_CHECK_REQUIRED:'尚未筛查和声，请试听',HARMONY_NOT_CHECKED:'未运行和声辅助检查，请试听确认',SOLO_DECLARATION_REQUIRED:'尚未确认素材所属歌手',VOCAL_CONTENT_UNCERTAIN:'歌唱成分证据不足，请试听',SEVERE_CLIPPING:'严重平顶削波',MANUAL_BOUNDARY_REVIEW:'边界已编辑，请重新试听确认',HARMONY_INPUT_UNCERTAIN:'和声检查的有效演唱不足',HARMONY_UNCERTAIN:'疑似叠唱或模型泄漏，请试听',HARMONY_STEMS_MISALIGNED:'和声候选时间不一致，需复核',POSSIBLE_BACKING_VOCAL:'疑似叠唱或同音和声，请试听'});
const tasks:Record<string,string>={vocals:'提取人声',lead_backing:'剥离和声',dereverb:'去混响',denoise:'降噪'};
const statuses: Record<string, string> = { accepted: '接受', review: '待复核', excluded: '排除' };

export function DatasetPanel() {
  const [items, setItems] = useState<Artifact[]>([]);
  const [folders,setFolders]=useState<Folder[]>([]),[folder,setFolder]=useState(''),[singer,setSinger]=useState('');
  const [uploadProgress,setUploadProgress]=useState<{done:number;total:number}|null>(null);
  const prepareTask=useTask('dataset:prepare'), exportTask=useTask('dataset:export');
  const [kind, setKind] = useState('song');
  const [files, setFiles] = useState<File[]>([]);
  const [target, setTarget] = useState(8);
  const [max, setMax] = useState(15);
  const [forceSeparation,setForceSeparation]=useState(false);
  const [checkHarmony,setCheckHarmony]=useState(false);
  const [profile,setProfile]=useState<Profile>('balanced'),[reuseQualityCache,setReuseQualityCache]=useState(true);
  const mode='review';
  const [solo,setSolo]=useState(false);
  const [denoise,setDenoise]=useState(false);
  const [min,setMin]=useState(2);
  const [silence,setSilence]=useState(.3);
  const [padding,setPadding]=useState(.15);
  const [threshold,setThreshold]=useState('');
  const [dereverb,setDereverb]=useState(false);
  const [manifestId, setManifestId] = useState('');
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [clipPage,setClipPage]=useState(0);
  const [decisions, setDecisions] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  async function refresh() { const [a,f]=await Promise.all([api<{items:Artifact[]}>('/artifacts'),api<{items:Folder[]}>('/training/folders')]);setItems(a.items);setFolders(f.items); }
  useEffect(() => { refresh().catch(e => setError(e.message)); }, []);
  async function action(fn: () => Promise<void>) {
    setBusy(true); setError('');
    try { await fn(); } catch(e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function loadManifest(id: string) {
    if (!id) { setDataset(null); setManifestId(''); return; }
    const next = await api<Dataset>(`/datasets/${id}`);
    setManifestId(id); setDataset(next); setClipPage(0); setDecisions({});
  }
  return <section>
    <div className="section-head"><div><h2>训练素材与数据集</h2><p>整夹导入歌曲，提取人声、切片，再逐片试听审核并保存训练版本。</p></div><button disabled={busy} onClick={() => void action(refresh)}>刷新</button></div>
    {error && <p className="error" role="alert">{error}</p>}{message && <p className="notice" role="status">{message}</p>}
    <article className="card"><h3>1. 导入训练文件夹</h3><p>选择一个只包含音源文件的文件夹。全部素材归入指定歌手，歌曲和干声都支持；单个文件限 512 MB、30 分钟。</p>
      <div className="toolbar"><label>音源文件夹 <input aria-label="训练音源文件夹" type="file" multiple ref={node=>{node?.setAttribute('webkitdirectory','');node?.setAttribute('directory','');}} disabled={busy} onChange={e=>{const next=Array.from(e.target.files??[]);setFiles(next);if(next.length)setSinger(next[0].webkitRelativePath.split('/')[0]||'');}}/></label><label>歌手名字 <input aria-label="训练歌手名字" value={singer} onChange={e=>setSinger(e.target.value)}/></label><label>文件夹内音源类型 <select value={kind} onChange={e=>setKind(e.target.value)}><option value="song">带伴奏歌曲</option><option value="dry_vocal">纯人声 / 干声</option></select></label></div>
      {!!files.length&&<details><summary>已选择 {files.length} 个文件</summary>{files.map((f,i)=><small className="file-list" key={i}>{f.webkitRelativePath} · {bytes(f.size)}</small>)}</details>}
      <button className="primary" disabled={busy||!files.length||!singer.trim()} onClick={()=>void action(async()=>{const form=new FormData();files.forEach(f=>form.append('files',f,f.name));form.append('paths',JSON.stringify(files.map(f=>f.webkitRelativePath)));form.append('singer',singer);form.append('kind',kind);setUploadProgress({done:0,total:0});try{const result=await upload<{singer:string;imported_count:number;reused_count:number}>('/training/import-folder',form,setUploadProgress);await refresh();setFolder(result.singer);setMessage(`已导入 ${result.imported_count} 个音源，复用 ${result.reused_count} 个已有音源。整个文件夹已选中，可以批量处理。`);}finally{setUploadProgress(null);}})}>导入整个文件夹</button>
      <UploadProgress value={uploadProgress}/>
    </article>
    <article className="card"><h3>2. 批量自动处理</h3><p>所有歌曲先提取人声，再切片，最后逐片审核。复杂和声只提示相关片段，由你决定接受或排除。切片不足 1 秒直接排除。</p>
      <div className="toolbar"><label>训练素材文件夹 <select aria-label="训练素材文件夹" value={folder} onChange={e=>setFolder(e.target.value)}><option value="">选择歌手文件夹</option>{folders.map(f=><option key={f.singer} value={f.singer}>{f.singer} · {f.source_ids.length} 个音源 · {bytes(f.bytes)}</option>)}</select></label><label><input type="checkbox" checked={solo} onChange={e=>setSolo(e.target.checked)}/> 我确认文件夹内素材均属于该歌手</label></div>
      {folder&&<details><summary>查看整夹音源</summary>{folders.find(f=>f.singer===folder)?.items.map(i=><small className="file-list" key={i.id}>{i.name} · {i.metadata.kind==='song'?'歌曲':'干声'}{!i.exists?' · 文件缺失':''}</small>)}</details>}
      <div className="toolbar"><SeparationProfile value={profile} onChange={setProfile}/><label><input type="checkbox" checked={reuseQualityCache} onChange={e=>setReuseQualityCache(e.target.checked)}/> 优先复用已有保真分离缓存</label></div>
      <div className="toolbar"><label>最短目标 <input className="number" type="number" min="1" max="30" step=".5" value={min} onChange={e=>setMin(Number(e.target.value))}/> 秒</label><label>目标长度 <input className="number" type="number" min="2" max="30" value={target} onChange={e=>setTarget(Number(e.target.value))}/> 秒</label><label>最长 <input className="number" type="number" min="2" max="30" value={max} onChange={e=>setMax(Number(e.target.value))}/> 秒</label><label><input type="checkbox" checked={forceSeparation} onChange={e=>setForceSeparation(e.target.checked)}/> 干声也去除伴奏</label><label><input type="checkbox" checked={checkHarmony} onChange={e=>setCheckHarmony(e.target.checked)}/> 切片和声辅助检查（可选，增加耗时）</label><label><input type="checkbox" checked={dereverb} onChange={e=>setDereverb(e.target.checked)}/> 温和去混响</label><label><input type="checkbox" checked={denoise} onChange={e=>setDenoise(e.target.checked)}/> 降噪</label></div>
      <details><summary>切片高级参数</summary><div className="toolbar"><label>停顿长度 <input className="number" type="number" min=".05" max="2" step=".05" value={silence} onChange={e=>setSilence(Number(e.target.value))}/> 秒</label><label>边缘余量 <input className="number" type="number" min="0" max=".5" step=".05" value={padding} onChange={e=>setPadding(Number(e.target.value))}/> 秒</label><label>活动阈值 <input className="number" type="number" min="-80" max="-20" placeholder="自动" value={threshold} onChange={e=>setThreshold(e.target.value)}/> dBFS</label></div></details>
      <button className="primary" disabled={busy||prepareTask.running||!folder||!solo} onClick={()=>void action(async()=>{const job=await api<{id:string}>('/datasets/prepare',json({folder_singer:folder,target_seconds:target,max_seconds:max,force_vocal_separation:forceSeparation,check_harmony:checkHarmony,profile,reuse_quality_cache:reuseQualityCache,dereverb,denoise,mode,solo_confirmed:solo,min_seconds:min,silence_seconds:silence,padding_seconds:padding,threshold_db:threshold?Number(threshold):null}));prepareTask.track(job);setMessage('已开始整夹批量处理，完成后会自动打开切片审查。');})}>批量自动处理整个文件夹</button>
      <JobProgress id={prepareTask.id} onComplete={job=>void action(async()=>{await refresh();if(job.metadata.result_id)await loadManifest(job.metadata.result_id);setMessage('切片处理完成，请用 Y / N 审核，并保存审查为新版本。');})}/>
    </article>
    <article className="card"><h3>3. 人工审核与保存</h3><div className="toolbar"><select aria-label="选择数据集" disabled={busy} value={manifestId} onChange={e => void action(() => loadManifest(e.target.value))}><option value="">选择数据集版本</option>{items.filter(i => i.metadata.kind === 'dataset_manifest').map(i => <option key={i.id} value={i.id}>{i.name.replace(/\.json$/,'')} · {i.metadata.singer??'未分类歌手'} · {new Date(i.created_at*1000).toLocaleString()} · {i.id.slice(0,8)} · 可用 {i.availability?.available_count??0} 段 · 接受 {i.availability?.accepted_count??0}{i.availability?.missing_count?` · 缺失 ${i.availability.missing_count} 段，需重新准备`:''}</option>)}</select></div>
    {dataset && <><p>共 {dataset.summary.clip_count} 段：接受 {dataset.summary.accepted_count}，待复核 {dataset.summary.review_count}，排除 {dataset.summary.excluded_count}。新版本保存后，接受的音频会受到清理保护。</p>
      {dataset.split && <p>训练 {dataset.summary.train_count} 段，按来源保留验证 {dataset.summary.validation_count} 段。{dataset.split.warning ? '去重后的素材只有一个独立来源组，无法建立独立验证集。' : ''}{dataset.split.linked_source_count ? ` 已关联 ${dataset.split.linked_source_count} 个重复来源。` : ''}</p>}
      <p className="notice">请逐片试听和声、伴奏残留及清洗损伤，用 Y / N 决定是否用于训练。和声辅助检查仅提供提示；“待复核”片段不能直接训练。</p>
      {(dataset.schema_version??0)<5&&!!dataset.summary.excluded_source_count&&<p className="notice">这是旧版整曲和声筛查生成的版本，曾跳过 {dataset.summary.excluded_source_count} 首歌曲。请重新批量处理原文件夹，生成全部歌曲的切片供审核。</p>}
      {!!dataset.summary.harmony_review_count&&<p>{dataset.summary.harmony_review_count} 个片段提示复杂和声，请逐片试听决定，其余片段照常保留。</p>}
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
      <div className="clips">{dataset.clips.slice(clipPage*50,(clipPage+1)*50).map((c, localIndex) => { const index=clipPage*50+localIndex; return <div className="clip" key={c.artifact_id}><div><strong>片段 {index + 1} · {c.name??'音频切片'}</strong><small>{(c.start_sample / c.sample_rate).toFixed(2)} 秒起 · {c.duration.toFixed(2)} 秒</small></div>
        {c.available ? <audio controls preload="none" src={`/api/artifacts/${c.artifact_id}/file`}/> : <p className="error">片段已清理，请重新准备。</p>}
        <div className="review-buttons" role="group" aria-label={`片段 ${index+1} 审查决定`}><button disabled={!c.available||busy||c.duration<1} aria-pressed={(decisions[c.artifact_id]??c.status)==='accepted'} className="review-yes" onClick={()=>setDecisions({...decisions,[c.artifact_id]:'accepted'})}>Y · 接受</button><button disabled={!c.available||busy} aria-pressed={(decisions[c.artifact_id]??c.status)==='excluded'} className="review-no" onClick={()=>setDecisions({...decisions,[c.artifact_id]:'excluded'})}>N · 排除</button><small>{statuses[decisions[c.artifact_id]??c.status]}</small></div>
        <small>{c.decision?.origin==='automatic'?'自动规则接受':c.decision?.origin==='manual'?'人工审查':'规则标记'}</small>
        {c.harmony?.report_id&&<details><summary>此片段的和声检查与试听</summary><p>{c.harmony.reasons?.map(r=>reasons[r]??r).join(' · ')||'规则未发现明显和声，仍请试听确认。'}</p>{c.harmony.lead_available&&<div><p>主唱候选</p><audio controls preload="none" src={`/api/artifacts/${c.harmony.lead_id}/file`}/></div>}{c.harmony.backing_available&&<div><p>和声候选（检查泄漏或叠唱）</p><audio controls preload="none" src={`/api/artifacts/${c.harmony.backing_id}/file`}/></div>}{c.harmony.report_available&&<a href={`/api/artifacts/${c.harmony.report_id}/file`} download>下载此片段检查记录</a>}</details>}
        <ClipEditor clip={c} manifestId={manifestId} busy={busy} onSubmit={(start,end)=>api<{id:string}>(`/datasets/${manifestId}/edit`,json({clip_id:c.artifact_id,start_seconds:start,end_seconds:end}))} onComplete={job=>void action(async()=>{await refresh();if(job.metadata.result_id)await loadManifest(job.metadata.result_id);})}/>
        <p className="clip-reasons">{c.reasons.map(r => reasons[r] ?? r).join(' · ')}{c.near_duplicate_of?.length ? ` · 相关片段 ${c.near_duplicate_of.map(id=>dataset.clips.findIndex(other=>other.artifact_id===id)+1).join('、')}` : ''}</p></div>;})}</div>
      {dataset.clips.length>50&&<div className="toolbar"><button disabled={clipPage===0} onClick={()=>setClipPage(clipPage-1)}>上一页</button><span>第 {clipPage+1} / {Math.ceil(dataset.clips.length/50)} 页 · 每页 50 段</span><button disabled={(clipPage+1)*50>=dataset.clips.length} onClick={()=>setClipPage(clipPage+1)}>下一页</button></div>}
      <div className="toolbar"><button disabled={busy || !Object.keys(decisions).length} onClick={() => void action(async () => {
        const next = await api<{id: string}>(`/datasets/${manifestId}/review`, json({ decisions })); await refresh(); await loadManifest(next.id); setMessage('已保存新的数据集版本，原版本仍可追溯。');
      })}>保存审查为新版本</button>
      <button disabled={busy || exportTask.running || !dataset.summary.accepted_count} onClick={()=>void action(async()=>{const job=await api<{id:string}>(`/datasets/${manifestId}/export`,json({}));exportTask.track(job);})}>导出已接受数据集</button><button disabled={busy||exportTask.running||!dataset.clips.some(c=>c.available&&c.status!=='excluded'&&c.duration>=1)} onClick={()=>void action(async()=>{exportTask.track(await api<{id:string}>(`/datasets/${manifestId}/export-prepared`,json({})));})}>下载处理后干声合集</button></div><JobProgress id={exportTask.id} onComplete={()=>void action(refresh)}/>
    </>}
    </article>
    <article className="card"><h3>数据集导出包</h3>{items.filter(i=>['dataset_export','dataset_prepared_export'].includes(i.metadata.kind??'')).map(i=><div className="toolbar" key={i.id}><span>{i.name} · {i.id.slice(0,12)}</span><a href={`/api/artifacts/${i.id}/file`} download>下载 ZIP</a></div>)}</article>
  </section>;
}

function ClipEditor({clip,manifestId,busy,onSubmit,onComplete}:{clip:Clip;manifestId:string;busy:boolean;onSubmit:(a:number,b:number)=>Promise<{id:string}>;onComplete:(job:Job)=>void}) {
  const task=useTask(`edit:${manifestId}:${clip.artifact_id}`);
  const [submitting,setSubmitting]=useState(false);
  async function submit(){setSubmitting(true);setError('');try{task.track(await onSubmit(start,end));}catch(e){setError((e as Error).message);}finally{setSubmitting(false);}}
  const [open,setOpen]=useState(false),[peaks,setPeaks]=useState<number[]>([]),[error,setError]=useState('');
  const [start,setStart]=useState(clip.valid_start_sample/clip.sample_rate),[end,setEnd]=useState(clip.valid_end_sample/clip.sample_rate);
  async function show(){setOpen(!open);if(!open&&!peaks.length){try{setPeaks((await api<{peaks:number[]}>(`/datasets/${manifestId}/waveform/${clip.artifact_id}`)).peaks);}catch(e){setError((e as Error).message);}}}
  return <div><button disabled={busy||!clip.available} onClick={()=>void show()}>{open?'收起边界编辑':'波形与边界编辑'}</button>{open&&<div>{error&&<small className="error">{error}</small>}<svg viewBox="0 0 500 64" width="100%" height="64" role="img" aria-label="音频波形">{peaks.map((p,i)=><line key={i} x1={i*500/peaks.length} x2={i*500/peaks.length} y1={32-30*Math.min(1,p)} y2={32+30*Math.min(1,p)} stroke="currentColor"/>)}</svg><div className="toolbar"><label>核心起点 <input className="number" type="number" step=".01" min="0" value={start} onChange={e=>setStart(Number(e.target.value))}/></label><label>核心终点 <input className="number" type="number" step=".01" min="0" value={end} onChange={e=>setEnd(Number(e.target.value))}/></label><button disabled={busy||submitting||task.running||end<=start} onClick={()=>void submit()}>另存边界版本</button></div><small>时间以来源工作母版为基准；自动保留边缘余量。编辑后需再次试听接受。</small></div>}<JobProgress id={task.id} onComplete={onComplete}/></div>;
}
