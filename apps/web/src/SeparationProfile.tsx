export type Profile = 'balanced' | 'quality' | 'fast';

export function SeparationProfile({value,onChange}:{value:Profile;onChange:(value:Profile)=>void}) {
  return <div><label>分离速度与质量 <select aria-label="分离速度与质量" value={value} onChange={e=>onChange(e.target.value as Profile)}>
    <option value="balanced">均衡 · 推荐</option><option value="quality">保真 · 较慢</option><option value="fast">快速 · 先试听</option>
  </select></label><small className="file-list">{value==='quality'?'沿用 FP32 和高重叠处理，优先保真。':value==='fast'?'混合精度与更少重叠，适合快速预览；请检查伴奏残留和片段边缘。':'混合精度与适度降低重叠；保留清洗检查，数值异常时自动回退。'}</small></div>;
}
