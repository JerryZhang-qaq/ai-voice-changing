import { useEffect, useState } from 'react';
import { api, json } from './api';

interface Artifact { id: string; name: string; role: string; metadata: {kind?: string; model_id?: string; sample_rate?: number; dataset_id?: string; rate?: string; summary?: {accepted_count: number}; }; }
interface Engines { rvc: {ready: boolean; environment: {reason?: string; gpu_name?: string}; missing_base_files: string[]; training_rates: Record<string, boolean>; }; separation: {models: {id: string; name: string; task: string; ready: boolean}[]}; }

export function RvcPanel() {
  const [items, setItems] = useState<Artifact[]>([]);
  const [engines, setEngines] = useState<Engines | null>(null);
  const [modelFile, setModelFile] = useState<File | null>(null);
  const [indexFile, setIndexFile] = useState<File | null>(null);
  const [dataset, setDataset] = useState('');
  const [rate, setRate] = useState('40k');
  const [epochs, setEpochs] = useState(200);
  const [batch, setBatch] = useState(4);
  const [saveEvery,setSaveEvery]=useState(10);
  const [rmsMix,setRmsMix]=useState(1);
  const [vocalDb,setVocalDb]=useState(0);
  const [instrumentalDb,setInstrumentalDb]=useState(0);
  const [format,setFormat]=useState('wav');
  const [resume, setResume] = useState('');
  const [model, setModel] = useState('');
  const [index, setIndex] = useState('');
  const [audio, setAudio] = useState('');
  const [pitch, setPitch] = useState(0);
  const [indexRate, setIndexRate] = useState(.5);
  const [protect, setProtect] = useState(.33);
  const [separation, setSeparation] = useState('vocals_melband_unwa');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  async function refresh() { const [a,e] = await Promise.all([api<{items: Artifact[]}>('/artifacts'),api<Engines>('/engines')]); setItems(a.items);setEngines(e); }
  useEffect(() => { refresh().catch(e => setError(e.message)); }, []);
  async function action(fn: () => Promise<void>) { setBusy(true);setError('');try { await fn(); } catch(e) { setError((e as Error).message); } finally { setBusy(false); } }
  async function submit(path: string, body: object) { const j = await api<{id: string}>(path,json(body));setMessage(`任务 ${j.id.slice(0,12)} 已提交，请在任务中心查看进度。`); }
  const models = items.filter(i => i.metadata.kind === 'rvc_model');
  const indexes = items.filter(i => i.metadata.kind === 'rvc_index' && i.metadata.model_id === model);
  const audios = items.filter(i => i.role === 'source' || ['separated_audio','converted_audio'].includes(i.metadata.kind ?? ''));
  return <section><div className="section-head"><div><h2>RVC 模型与翻唱</h2><p>管理音色模型，训练专属音色，转换干声或自动制作翻唱。</p></div><button disabled={busy} onClick={() => void action(refresh)}>刷新</button></div>
    {error && <p className="error" role="alert">{error}</p>}{message && <p className="notice" role="status">{message}</p>}
    {engines && !engines.rvc.ready && <p className="notice">RVC 未就绪：{engines.rvc.environment.reason ?? '上游版本或基础权重不完整'}。真实训练和转换需要 GPU 与完整引擎环境。</p>}
    <article className="card"><h3>导入音色模型</h3><p>上传 .pth 推理权重及对应 .index。导入会在独立引擎进程中检查结构与索引维度。</p><div className="toolbar"><label>权重 <input type="file" accept=".pth" onChange={e => setModelFile(e.target.files?.[0] ?? null)}/></label><label>索引 <input type="file" accept=".index" onChange={e => setIndexFile(e.target.files?.[0] ?? null)}/></label><button disabled={busy || !modelFile} onClick={() => void action(async () => {const form=new FormData();form.append('model',modelFile!);if(indexFile)form.append('index',indexFile);await api('/models/import',{method:'POST',body:form});await refresh();setMessage('模型已导入。');})}>导入模型</button></div>
    {models.map(m => <div className="toolbar" key={m.id}><span>{m.name} · {m.metadata.sample_rate} Hz</span><a href={`/api/artifacts/${m.id}/file`} download>下载权重</a>{items.filter(i=>i.metadata.model_id===m.id).map(i=><a key={i.id} href={`/api/artifacts/${i.id}/file`} download>下载索引</a>)}</div>)}</article>
    <article className="card"><h3>训练专属音色</h3><div className="toolbar"><select aria-label="训练数据集" value={dataset} onChange={e=>{setDataset(e.target.value);setResume('');}}><option value="">选择已审查数据集</option>{items.filter(i=>i.metadata.kind==='dataset_manifest' && (i.metadata.summary?.accepted_count ?? 0)>=2).map(i=><option key={i.id} value={i.id}>{i.name.replace(/\.json$/,'')} · {i.id.slice(0,8)} · 接受 {i.metadata.summary?.accepted_count} 段</option>)}</select><select value={rate} onChange={e=>setRate(e.target.value)} aria-label="训练采样率">{['32k','40k','48k'].map(r=><option key={r}>{r}</option>)}</select></div>
      <div className="toolbar"><label>总轮数 <input className="number" type="number" min="1" max="10000" value={epochs} onChange={e=>setEpochs(Number(e.target.value))}/></label><label>批大小 <input className="number" type="number" min="1" max="64" value={batch} onChange={e=>setBatch(Number(e.target.value))}/></label><label>保存间隔 <input className="number" type="number" min="1" max="1000" value={saveEvery} onChange={e=>setSaveEvery(Number(e.target.value))}/> 轮</label><select aria-label="继续训练检查点" value={resume} onChange={e=>setResume(e.target.value)}><option value="">从预训练权重开始</option>{items.filter(i=>i.metadata.kind==='workspace' && i.metadata.dataset_id===dataset && i.metadata.rate===rate).map(i=><option key={i.id} value={i.id}>继续 {i.id.slice(0,12)} 的检查点</option>)}</select><button disabled={busy || !dataset || !engines?.rvc.ready || !engines.rvc.training_rates[rate]} onClick={()=>void action(()=>submit('/training',{dataset_id:dataset,rate,epochs,batch_size:batch,save_every:saveEvery,resume_id:resume||null}))}>开始训练</button></div><p>需要至少两个已接受片段；可用时长、音域与纯净度仍决定质量。继续训练的总轮数应大于已完成轮数。</p>
    </article>
    <article className="card"><h3>音色转换与一键翻唱</h3><div className="toolbar"><select aria-label="转换输入音频" value={audio} onChange={e=>setAudio(e.target.value)}><option value="">选择歌曲或干声</option>{audios.map(i=><option key={i.id} value={i.id}>{i.name}</option>)}</select><select aria-label="目标音色模型" value={model} onChange={e=>{setModel(e.target.value);setIndex('');}}><option value="">选择音色模型</option>{models.map(i=><option key={i.id} value={i.id}>{i.name}</option>)}</select><select aria-label="检索索引" value={index} onChange={e=>setIndex(e.target.value)}><option value="">不使用索引</option>{indexes.map(i=><option key={i.id} value={i.id}>{i.name}</option>)}</select></div>
      <div className="toolbar"><label>变调 <input className="number" type="number" min="-24" max="24" value={pitch} onChange={e=>setPitch(Number(e.target.value))}/> 半音</label><label>索引比例 <input className="number" type="number" min="0" max="1" step=".05" value={indexRate} onChange={e=>setIndexRate(Number(e.target.value))}/></label><label>清辅音保护 <input className="number" type="number" min="0" max=".5" step=".01" value={protect} onChange={e=>setProtect(Number(e.target.value))}/></label></div>
      <div className="toolbar"><label>响度包络混合 <input className="number" type="number" min="0" max="1" step=".05" value={rmsMix} onChange={e=>setRmsMix(Number(e.target.value))}/></label><label>人声增益 <input className="number" type="number" min="-40" max="12" value={vocalDb} onChange={e=>setVocalDb(Number(e.target.value))}/> dB</label><label>伴奏增益 <input className="number" type="number" min="-40" max="12" value={instrumentalDb} onChange={e=>setInstrumentalDb(Number(e.target.value))}/> dB</label><label>成品格式 <select value={format} onChange={e=>setFormat(e.target.value)}><option value="wav">WAV</option><option value="flac">FLAC</option><option value="mp3">MP3 320k</option></select></label></div>
      <p>仅转换干声会保留演唱旋律与节奏。变调影响人声；一键翻唱暂不改变伴奏调性。</p>
      <div className="toolbar"><button disabled={busy || !audio || !model || !engines?.rvc.ready} onClick={()=>void action(()=>submit('/conversion',{audio_id:audio,model_id:model,index_id:index||null,pitch,index_rate:indexRate,protect,rms_mix:rmsMix}))}>转换干声</button><select aria-label="翻唱分离模型" value={separation} onChange={e=>setSeparation(e.target.value)}>{engines?.separation.models.filter(m=>m.task==='vocals').map(m=><option key={m.id} value={m.id}>{m.name}</option>)}</select><button disabled={busy || !audio || !model || !engines?.rvc.ready || !engines.separation.models.some(m=>m.id===separation&&m.ready)} onClick={()=>void action(()=>submit('/covers',{source_id:audio,model_id:model,index_id:index||null,pitch,index_rate:indexRate,protect,rms_mix:rmsMix,vocal_db:vocalDb,instrumental_db:instrumentalDb,format,separation_model:separation}))}>分离 → 转换 → 混音</button></div>
    </article>
    <article className="card"><h3>转换人声与成品</h3>{items.filter(i=>['converted_audio','mixed_audio'].includes(i.metadata.kind??'')).map(i=><div className="clip" key={i.id}><strong>{i.name}</strong><audio controls preload="none" src={`/api/artifacts/${i.id}/file`}/><a href={`/api/artifacts/${i.id}/file`} download>下载</a></div>)}</article>
  </section>;
}
