import { useEffect, useState } from 'react';
import { api, bytes, json } from './api';

interface Artifact {
  id: string; name: string; role: string; job_id: string | null; size: number;
  cleanable: boolean; protection: string[]; retained: boolean; exists: boolean;
}
interface Inventory { items: Artifact[]; total_bytes: number; cleanable_bytes: number; disk_free_bytes: number; }
interface Preview { reclaimable_bytes: number; cleanable_count: number; }
interface Result { deleted_ids: string[]; reclaimed_bytes: number; errors: { id: string; error: string }[]; }
const roles: Record<string, string> = { source: '原始音频', cache: '中间缓存', dataset: '训练数据', model: '模型', export: '成品' };
const protections: Record<string, string> = { permanent: '长期保留', retained: '手动保留', active_job: '任务使用中', unsafe_path: '文件路径异常' };

export function CachePanel() {
  const [data, setData] = useState<Inventory | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [job, setJob] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [scope, setScope] = useState<object>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  async function refresh() {
    const inventory = await api<Inventory>('/artifacts');
    setData(inventory);
    setSelected(previous => previous.filter(id => inventory.items.some(a => a.id === id && a.cleanable)));
  }
  useEffect(() => { refresh().catch(e => setError(e.message)); }, []);
  async function action(fn: () => Promise<void>) {
    setBusy(true); setError('');
    try { await fn(); } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  function inspect(body: object) {
    void action(async () => { setScope(body); setPreview(await api<Preview>('/cache/preview', json(body))); });
  }
  return <section>
    <div className="section-head"><div><h2>缓存与存储</h2><p>清理中间产物，保留原始素材、训练数据、模型和成品。</p></div><button disabled={busy} onClick={() => void action(refresh)}>刷新</button></div>
    {error && <p className="error" role="alert">{error}</p>}
    {message && <p className="notice" role="status">{message}</p>}
    <div className="stats">
      <article><span>已登记文件</span><strong>{bytes(data?.total_bytes ?? 0)}</strong></article>
      <article><span>可清理缓存</span><strong>{bytes(data?.cleanable_bytes ?? 0)}</strong></article>
      <article><span>磁盘剩余空间</span><strong>{bytes(data?.disk_free_bytes ?? 0)}</strong></article>
    </div>
    <div className="toolbar">
      <button disabled={busy || !data?.items.some(i=>i.cleanable)} onClick={() => inspect({})}>清理全部缓存</button>
      <button disabled={busy || !selected.length} onClick={() => inspect({ artifact_ids: selected })}>清理所选 ({selected.length})</button>
      <label>任务 <select value={job} onChange={e => setJob(e.target.value)}><option value="">选择任务</option>{[...new Set(data?.items.flatMap(i => i.job_id ? [i.job_id] : []))].map(id => <option key={id} value={id}>{id.slice(0, 12)}</option>)}</select></label>
      <button disabled={busy || !job} onClick={() => inspect({ job_id: job })}>清理任务缓存</button>
    </div>
    <div className="table-wrap"><table><thead><tr><th>选择</th><th>文件</th><th>类型</th><th>大小</th><th>状态</th><th>保留</th></tr></thead><tbody>
      {data?.items.map(a => <tr key={a.id}>
        <td><input type="checkbox" aria-label={`选择 ${a.name}`} disabled={!a.cleanable || busy} checked={selected.includes(a.id)} onChange={e => setSelected(e.target.checked ? [...selected, a.id] : selected.filter(id => id !== a.id))} /></td>
        <td>{a.name}<small>{a.id.slice(0, 12)}</small></td><td>{roles[a.role] ?? a.role}</td><td>{bytes(a.size)}</td>
        <td>{!a.exists ? '文件缺失或异常' : a.cleanable ? '可清理' : a.protection.map(p => protections[p] ?? p).join(' · ')}</td>
        <td>{a.role === 'cache' && <input type="checkbox" aria-label={`保留 ${a.name}`} checked={a.retained} disabled={busy} onChange={e => void action(async () => { await api(`/artifacts/${a.id}/retention`, json({ retained: e.target.checked }, 'PATCH')); await refresh(); })} />}</td>
      </tr>)}
    </tbody></table></div>
    {data && !data.items.length && <div className="empty">还没有音频或中间产物。处理任务完成后，可在这里管理缓存。</div>}
    {preview && <div className="dialog-backdrop"><div className="dialog" role="dialog" aria-modal="true" aria-labelledby="cleanup-title">
      <h3 id="cleanup-title">确认清理缓存</h3><p>预计清理 {preview.cleanable_count} 项缓存，释放 {bytes(preview.reclaimable_bytes)}。</p>
      <p>清理后，相应中间步骤需要重新计算。训练工作目录中的检查点也会删除；正在使用的文件会自动跳过。</p>
      <div className="toolbar"><button disabled={busy} onClick={() => setPreview(null)}>取消</button><button className="danger" disabled={busy || !preview.cleanable_count} onClick={() => void action(async () => {
        const result = await api<Result>('/cache/cleanup', json(scope));
        setPreview(null);
        setMessage(`已清理 ${result.deleted_ids.length} 项缓存，移除 ${bytes(result.reclaimed_bytes)}。${result.errors.length ? ` ${result.errors.length} 项清理失败，请重试。` : ''}`);
        if (result.errors.length) setError(result.errors.map(e => e.error).join('；'));
        await refresh();
      })}>{busy ? '处理中…' : '确认清理'}</button></div>
    </div></div>}
  </section>;
}
