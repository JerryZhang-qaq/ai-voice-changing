import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { CachePanel } from './CachePanel';
import { DatasetPanel } from './DatasetPanel';
import { JobsPanel } from './JobsPanel';
import { SeparationPanel } from './SeparationPanel';
import { RvcPanel } from './RvcPanel';
import { MixPanel } from './MixPanel';
import { ResourcesPanel } from './ResourcesPanel';
import { ConversionPanel } from './ConversionPanel';
import './style.css';

function App() {
  const [page, setPage] = useState('datasets');
  const pages = { datasets: '训练素材与数据集', separation: '人声与和声分离', rvc: '训练与音色模型', conversion:'转换素材与翻唱', mixing: '混音与导出', resources: '引擎与基础模型', jobs: '任务中心', cache: '缓存与存储' };
  return <div className="shell"><aside><h1>声作坊</h1><p>AI 翻唱工作台</p><nav>{Object.entries(pages).map(([id, label]) => <button aria-label={label} className={page === id ? 'active' : ''} key={id} onClick={() => setPage(id)}><span className="nav-icon" aria-hidden="true">{Object.keys(pages).indexOf(id)+1}</span><span className="nav-label">{label}</span></button>)}</nav><small>早期测试版 · 0.0.3</small></aside><main><header><span>工作台 / {pages[page as keyof typeof pages]}</span><span className="badge">自托管</span></header>{page === 'cache' ? <CachePanel/> : page === 'jobs' ? <JobsPanel/> : page === 'separation' ? <SeparationPanel/> : page === 'rvc' ? <RvcPanel/> : page === 'conversion' ? <ConversionPanel/> : page === 'mixing' ? <MixPanel/> : page === 'resources' ? <ResourcesPanel/> : <DatasetPanel/>}</main></div>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
