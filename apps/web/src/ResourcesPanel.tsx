import { useEffect, useState } from 'react';
import { api, bytes, json } from './api';

import { JobProgress, useTask } from './JobProgress';

interface Resource { id:string; name:string; size:number; ready:boolean; source:string; license:string; files:{path:string;present:boolean;verified:boolean}[]; }
interface Engine { ready:boolean; environment:{ready:boolean;reason?:string;gpu_name?:string}; }

export function ResourcesPanel() {
  const task=useTask('resources');
  const [modal,setModal]=useState(false);
  const [items,setItems]=useState<Resource[]>([]);
  const [engines,setEngines]=useState<{rvc:Engine;separation:{environment:Engine['environment']}}|null>(null);
  const [selected,setSelected]=useState(['rvc_base','rvc_40k','vocals_melband_unwa','lead_melband_aufr33']);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
  async function refresh(){const [r,e]=await Promise.all([api<{items:Resource[]}>('/resources'),api<typeof engines>('/engines')]);setItems(r.items);setEngines(e);}
  useEffect(()=>{void refresh().catch(e=>setError(e.message));},[]);
  async function action(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function submit(verify_only:boolean){const job=await api<{id:string}>('/resources',json({resource_ids:selected,verify_only}));task.track(job);setModal(true);setMessage('资源任务已提交，进度显示在此页面。');}
  return <section><div className="section-head"><div><h2>引擎与基础模型</h2><p>首次使用下载基础资源，之后可以离线训练和制作翻唱。</p></div><button disabled={busy} onClick={()=>void action(refresh)}>刷新状态</button></div>
    {error&&<p className="error" role="alert">{error}</p>}{message&&<p className="notice" role="status">{message}</p>}
    <article className="card"><h3>运行环境</h3><p>RVC：{engines?.rvc.ready?'就绪':engines?.rvc.environment.reason??'等待工作进程'}</p><p>分离引擎：{engines?.separation.environment.ready?'就绪':engines?.separation.environment.reason??'等待工作进程'}</p><p>需要 Windows 10/11、Python 3.12 和 NVIDIA RTX 40 系及以后显卡。请先运行安装程序；下载权重可以在没有 GPU 的环境进行。</p></article>
    <article className="card"><h3>基础资源</h3><p>默认选择训练 40k 模型、人声提取和复杂和声筛查所需资源。去混响与降噪可按需下载，每种约 913 MB。</p>
      <div className="table-wrap"><table><thead><tr><th>选择</th><th>资源</th><th>大小</th><th>完整性</th><th>来源</th></tr></thead><tbody>{items.map(r=><tr key={r.id}><td><input type="checkbox" aria-label={`选择资源 ${r.name}`} checked={selected.includes(r.id)} onChange={e=>setSelected(e.target.checked?[...selected,r.id]:selected.filter(id=>id!==r.id))}/></td><td>{r.name}</td><td>{bytes(r.size)}</td><td>{r.ready?'已校验':r.files.some(f=>f.present)?'待校验 / 不完整':'未下载'}</td><td><a href={r.source} target="_blank" rel="noreferrer">发布来源</a></td></tr>)}</tbody></table></div>
      <div className="toolbar"><span>所选 {bytes(items.filter(r=>selected.includes(r.id)).reduce((n,r)=>n+r.size,0))}</span><button disabled={busy||task.running||!selected.length} onClick={()=>void action(()=>submit(false))}>下载 / 校验所选资源</button><button disabled={busy||task.running||!selected.length} onClick={()=>void action(()=>submit(true))}>仅校验已有文件</button></div>
      <p>下载完成后会保留校验记录。基础权重与用户训练模型不属于中间缓存。</p>
      <JobProgress id={task.id} onComplete={()=>void action(refresh)}/>{task.id&&<button onClick={()=>setModal(true)}>查看下载进度弹窗</button>}
    </article>{modal&&<div className="dialog-backdrop"><div className="dialog download-dialog" role="dialog" aria-modal="true" aria-labelledby="download-title"><h3 id="download-title">资源下载与校验</h3><p>任务已提交。排队、下载、校验和完成状态将在下面实时显示。</p><JobProgress id={task.id}/><button autoFocus onClick={()=>setModal(false)}>关闭弹窗，继续后台任务</button></div></div>}</section>;
}
