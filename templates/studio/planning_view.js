const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fields={premise:"故事前提",central_conflict:"核心冲突",ending_boundary:"结局边界",protagonist_arc:"主角变化",reader_value:"读者价值",protected_invariants:"保护项",chapter_duty:"章节职责",observable_change:"可观察变化",topology:"章节结构",entry_state:"进入状态",exit_state:"离开状态",event_graph:"事件关系",character_arcs:"人物弧",promise_threads:"承诺",foreshadow_threads:"伏笔",flex_zones:"调整空间",tiers:"规划层次"};
export function planningOverview(state){
  const approved=state.approved;
  if(!approved||approved.status!=="current")return `<section class="card"><h3>已批准的故事规划</h3><p class="notice warn">${esc(approved?.issues?.join("；")||"正在读取批准依据")}</p><p>下方候选通过独立审查和人工决定后，才会成为写作依据。</p></section>`;
  const spine=approved.book_spine,volume=approved.active_volume_plan,tiers=approved.rolling_window.tiers;
  const chapter=approved.chapter_contracts.map(c=>`<article class="card"><h4>第 ${c.chapter_number} 章</h4><p>${esc(c.chapter_duty)}</p><p>${esc(c.observable_change)}</p><p class="muted">读者获得：${esc(c.reader_value)}</p></article>`).join("");
  return `<section class="card"><span class="badge ok">批准依据有效 · 计划不代表已发生</span><h3>${esc(spine.premise)}</h3><p>${esc(spine.central_conflict)}</p><p>读者价值：${esc(spine.reader_value)}</p><details><summary>结局与全书卷结构</summary><p>${esc(spine.ending_boundary)}</p>${approved.volume_skeletons.items.map(v=>`<h4>${esc(v.title)} · 第 ${v.chapter_range.join("–")} 章</h4><p>${esc(v.volume_goal)}</p>`).join("")}</details><h3>当前卷 · 第 ${volume.chapter_range.join("–")} 章</h3><p>${esc(volume.entry_state)} → ${esc(volume.exit_state)}</p><h3>近期已确定章节</h3><div class="grid planning-chapters">${chapter}</div><h3>后续方向与远期展望</h3><p>方向层：第 ${esc(tiers.directional?.join("–")||"未设置")} 章 · 展望层：第 ${esc(tiers.horizon?.join("–")||"未设置")} 章</p>${["directional","horizon"].map(tier=>`<details><summary>${tier==="directional"?"展开后续方向":"展开远期展望"}</summary>${approved.chapter_forecasts.filter(c=>c.tier===tier).map(c=>`<h4>第 ${c.chapter_number} 章 · ${esc(c.chapter_duty)}</h4><p>${esc(c.likely_change)}</p><p>读者价值：${esc(c.reader_value)}</p><p>调整空间：${esc(c.flexibility)}</p>`).join("")||'<p class="muted">尚无可核验版本的后续说明。</p>'}</details>`).join("")}<p class="muted">后续章节仍须滚动细化为合同；不能越过当前章关闭和逐节点人工批准。</p></section>`;
}
export function planningChanges(state){
  if(!state.bundle||state.status==="applied")return "";
  const prior=state.approved?.status==="current"?state.approved:null;
  const sections=["book_spine","volume_skeletons","active_volume_plan","chapter_contracts","chapter_forecasts","plot_node_tables","rolling_window"];
  const control=new Set(["schema","lifecycle","approved_by","basis_sha256","candidate_sha256","human_decision"]);
  const compare=value=>Array.isArray(value)?value.map(compare):value&&typeof value==="object"?Object.fromEntries(Object.keys(value).filter(key=>!control.has(key)).sort().map(key=>[key,compare(value[key])])):value;
  let rows=[];
  for(const section of sections){const before=prior?.[section],after=state.bundle[section];
    if(Array.isArray(after)||Array.isArray(before)){
      const numbers=[...new Set([...(before||[]),...(after||[])].map(ch=>ch.chapter_number))].sort((a,b)=>a-b);
      for(const number of numbers){const old=before?.find(c=>c.chapter_number===number),next=after?.find(c=>c.chapter_number===number);
        if(JSON.stringify(compare(old))!==JSON.stringify(compare(next)))rows.push([`第 ${number} 章 · ${section==="chapter_contracts"?"完整合同与保护项":section==="plot_node_tables"?"情节节点与依赖":"后续方向"}${next?"":"（本轮移除）"}`,old,next]);
      }
    }else for(const key of new Set([...Object.keys(before||{}),...Object.keys(after||{})])){
      if(!control.has(key)&&JSON.stringify(compare(before?.[key]))!==JSON.stringify(compare(after?.[key])))rows.push([fields[key]||(section==="volume_skeletons"?"全书卷结构":key),before?.[key],after?.[key]]);
    }
  }
  const text=value=>typeof value==="string"?value:JSON.stringify(value??"未提供",null,2);
  return `<section class="card"><h3>本轮候选改动与影响</h3><p class="notice warn">这 ${rows.length} 项内容变化仍待批准。受影响的章节合同、节点与故事工作单必须按现有流程重新校验；正文事实不会随候选改变。</p>${rows.length?rows.map(([label,before,after])=>`<details><summary>${esc(label)}</summary><div class="planning-diff"><section><h4>已批准内容</h4><pre>${esc(text(before))}</pre></section><section><h4>本轮候选</h4><pre>${esc(text(after))}</pre></section></div></details>`).join(""):'<p>这些主要创作字段没有变化；请继续核对下方完整节点和审查证据。</p>'}</section>`;
}
