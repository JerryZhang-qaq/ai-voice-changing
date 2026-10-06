import { useEffect,useState } from 'react';
import { api,json } from './api';

interface Artifact {id:string;name:string;role:string;metadata:{kind?:string};}
export function MixPanel() {
  const [items,setItems]=useState<Artifact[]>([]),[vocal,setVocal]=useState(''),[inst,setInst]=useState('');
  const [vocalDb,setVocalDb]=useState(0),[instDb,setInstDb]=useState(0),[format,setFormat]=useState('wav');
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
  const refresh=()=>api<{items:Artifact[]}>('/artifacts').then(d=>setItems(d.items));
  useEffect(()=>{refresh().catch(e=>setError(e.message));},[]);
  const audio=items.filter(i=>i.role==='source'||['separated_audio','converted_audio'].includes(i.metadata.kind??''));
  return <section><div className="section-head"><div><h2>混音与导出</h2><p>调整人声与伴奏，无需重新运行分离和转换。</p></div><button onClick={()=>{void refresh().catch(e=>setError(e.message));}}>刷新</button></div>
  {error&&<p className="error" role="alert">{error}</p>}{message&&<p className="notice" role="status">{message}</p>}
  <article className="card"><div className="toolbar"><label>人声 <select value={vocal} onChange={e=>setVocal(e.target.value)}><option value="">选择人声音轨</option>{audio.map(i=><option key={i.id} value={i.id}>{i.name}</option>)}</select></label><label>伴奏 <select value={inst} onChange={e=>setInst(e.target.value)}><option value="">选择伴奏音轨</option>{audio.map(i=><option key={i.id} value={i.id}>{i.name}</option>)}</select></label></div>
    <div className="toolbar"><label>人声音量 <input className="number" type="number" min="-40" max="12" value={vocalDb} onChange={e=>setVocalDb(Number(e.target.value))}/> dB</label><label>伴奏音量 <input className="number" type="number" min="-40" max="12" value={instDb} onChange={e=>setInstDb(Number(e.target.value))}/> dB</label><select value={format} onChange={e=>setFormat(e.target.value)} aria-label="导出格式">{['wav','flac','mp3'].map(f=><option key={f}>{f}</option>)}</select><button disabled={busy||!vocal||!inst} onClick={()=>{setBusy(true);setError('');void api<{id:string}>('/mixing',json({vocal_id:vocal,instrumental_id:inst,vocal_db:vocalDb,instrumental_db:instDb,format})).then(j=>setMessage(`混音任务 ${j.id.slice(0,12)} 已提交。`)).catch(e=>setError(e.message)).finally(()=>setBusy(false));}}>混音导出</button></div><p>输出保持立体声；必要时对整首统一衰减，避免采样峰值削波。音轨时长差异超过 100 毫秒时停止，提示检查对齐。</p>
  </article><article className="card"><h3>成曲</h3>{items.filter(i=>i.metadata.kind==='mixed_audio').map(i=><div className="clip" key={i.id}><strong>{i.name}</strong><audio controls preload="none" src={`/api/artifacts/${i.id}/file`}/><a href={`/api/artifacts/${i.id}/file`} download>下载</a></div>)}</article></section>;
}
