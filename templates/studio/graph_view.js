// Derived relationships remain a source-linked reading view, never an editor.
export function renderGraphView(container,graph){
 if(!graph||!['entities','relationships','events'].every(key=>Array.isArray(graph[key])))throw Error('图谱目录不完整，请检查派生资料');
 const {entities,relationships,events}=graph;
 if(entities.some(item=>!item||typeof item.id!=='string'||!item.id)||new Set(entities.map(item=>item.id)).size!==entities.length)throw Error('图谱对象缺少标识或存在重复标识');
 const names=new Map(entities.map(item=>[item.id,item.name||item.id]));
 const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const types={character:'人物',location:'地点',item:'物品',organization:'组织',faction:'势力',ability:'能力',foreshadowing:'伏笔',world_rule:'世界规则'};
 const relationNames={ally_of:'同盟',alliance:'同盟',enemy_of:'敌对',member_of:'所属',located_in:'位于',located_at:'位于',owns:'持有',knows:'知晓',trusts:'信任',distrusts:'不信任',owes:'负有债务',师徒:'师徒'};
 const statusNames={planned:'计划中',active:'当前有效',realized:'已有正文实现依据',resolved:'已解决',cancelled:'已撤销',stale:'来源已变化'};
 container.classList.add('graph-view');
 container.innerHTML=`<p class="muted">${entities.length} 个对象 · ${relationships.length} 项关系 · ${events.length} 个事件。此页展示可重建的资料视图，具体事实以终稿和批准材料为依据。</p><label>查找人物、地点或物品<input id="graph-filter" type="search" placeholder="输入姓名或资料关键词"></label><div class="graph-browser"><nav aria-label="关系图谱对象" id="graph-entities"></nav><section id="graph-detail"><p class="muted">选择一个对象，查看关系、参与事件与来源。</p></section></div>`;
 let limit=60;
 const list=container.querySelector('#graph-entities'),detail=container.querySelector('#graph-detail'),filter=container.querySelector('#graph-filter');
 function select(entity){
  const linked=relationships.filter(row=>row.source===entity.id||row.target===entity.id),involved=events.filter(row=>(row.participants||[]).includes(entity.id));
  detail.innerHTML=`<h3>${esc(entity.name||entity.id)}</h3><p>${esc(types[entity.type]||entity.type||'资料对象')} · ${esc(statusNames[entity.status]||entity.status||'未记录状态')}</p>${entity.description?`<p>${esc(entity.description)}</p>`:''}<h4>相关关系</h4>${linked.length?'<div class="document-table"><table><thead><tr><th>对象</th><th>关系与状态</th><th>对象</th><th>依据</th></tr></thead><tbody>'+linked.map(row=>`<tr><td>${esc(names.get(row.source)||`未找到对象：${row.source}`)}</td><td>${esc(relationNames[row.type||row.relation]||row.type||row.relation)}<p class="small muted">${esc(statusNames[row.status]||row.status||'未标记实现状态')}</p></td><td>${esc(names.get(row.target)||`未找到对象：${row.target}`)}</td><td><details><summary>展开依据与有效范围</summary><pre>${esc(JSON.stringify(row,null,2))}</pre></details></td></tr>`).join('')+'</tbody></table></div>':'<p class="muted">暂无已记录关系。</p>'}<h4>参与事件</h4>${involved.map(event=>`<details><summary>${event.chapter_number?`第 ${esc(event.chapter_number)} 章 · `:''}${esc(event.title||event.id)}</summary><p class="muted">${esc(statusNames[event.status]||event.status||'未标记实现状态')}</p><p>${esc(event.consequences||event.description||'尚无后果说明')}</p><pre>${esc(JSON.stringify(event,null,2))}</pre></details>`).join('')||'<p class="muted">暂无已记录事件。</p>'}<details><summary>对象资料与来源详情</summary><pre>${esc(JSON.stringify(entity,null,2))}</pre></details>`;
  list.querySelectorAll('[data-entity]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.entity===String(entity.id))));
 }
 function draw(){
  const query=filter.value.trim().toLocaleLowerCase(),matches=entities.filter(item=>`${item.name||''} ${types[item.type]||item.type||''} ${item.description||''}`.toLocaleLowerCase().includes(query));
  list.innerHTML=matches.slice(0,limit).map(item=>`<button type="button" class="document-button" data-entity="${esc(item.id)}" aria-pressed="false">${esc(item.name||item.id)} <span class="muted small">${esc(types[item.type]||item.type||'')}</span></button>`).join('')||'<p class="muted">没有匹配对象。</p>';
  list.querySelectorAll('[data-entity]').forEach(button=>button.onclick=()=>select(matches.find(item=>String(item.id)===button.dataset.entity)));
  if(matches.length>limit){const more=document.createElement('button');more.textContent='加载更多对象';more.onclick=()=>{limit+=60;draw()};list.append(more)}
 }
 filter.oninput=()=>{limit=60;draw()};draw();
}
