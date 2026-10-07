export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败 (${response.status})`);
  return data as T;
}

export const json = (body: unknown, method = 'POST'): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

export function upload<T>(path: string, body: FormData, progress: (value: { done: number; total: number }) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('POST', `/api${path}`);
    request.upload.onprogress = event => progress({ done: event.loaded, total: event.lengthComputable ? event.total : 0 });
    request.onerror = () => reject(new Error('上传连接失败，请检查工作台是否运行'));
    request.onload = () => { try { const data = JSON.parse(request.responseText); if (request.status >= 200 && request.status < 300) resolve(data); else reject(new Error(typeof data.detail === 'string' ? data.detail : `上传失败 (${request.status})`)); } catch { reject(new Error('服务器未返回有效的上传结果')); } };
    request.send(body);
  });
}

export function bytes(n: number) {
  if (n < 1024) return `${n} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < units.length - 1);
  return `${n.toFixed(1)} ${units[i]}`;
}
