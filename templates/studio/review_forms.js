// Natural-language forms preserve the existing evidence record and domain checks.
const revisionDimensions={scene_causality:"场景因果",character_voice_or_emotion:"人物声音与情绪",reader_payoff_or_exit:"读者收益与章末",relationship_logic:"关系逻辑",pacing_or_information:"节奏与信息",prose_naturalness:"语言自然度"};
const intentNames={story_intent:"故事意图",key_character_choice:"人物关键选择",emotional_truth:"情绪真相",pov_voice_intent:"视角与声音"};
const protectionNames={chapter_contract:"章节合同",knowledge_boundaries:"人物知识边界",ability_costs:"能力代价",relationship_stage:"关系阶段",protected_outcomes:"应保留的结果"};
const safeText=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let revisionFormBinding="";
function options(values,current){return (Object.hasOwn(values,current)?"":'<option value="" selected>请选择</option>')+Object.entries(values).map(([k,v])=>`<option value="${k}" ${current===k?"selected":""}>${v}</option>`).join("")}
function renderHumanForms(){
 renderReviewFindings();
 const revision=state.human_author_revision||{};
 if(!revision.available){$("revisionNaturalForm")?.remove();revisionFormBinding="";}
 if(revision.available&&(!revisionFormBinding||revisionFormBinding!==revision.record_sha256&&!dirtyFields.has("revisionRecord"))){
  revisionFormBinding=revision.record_sha256;
  let form=$("revisionNaturalForm");if(!form){form=document.createElement("div");form.id="revisionNaturalForm";$("revisionRecord").parentElement.before(form)}
  const record=JSON.parse($("revisionRecord").value||"{}");
  form.innerHTML=`<h3>修改说明</h3><p class="muted">填写修改前后准确原文、目的和阅读影响。重复片段可在冻结原稿和修改稿中选择准确位置，再使用下方引用按钮；也可扩展引用。自然度修改须保留人物知识、能力代价、关系和关键结果。候选变化后，旧审稿失效，须完成整轮复审。</p><div id="revisionChanges"></div><button type="button" id="addRevisionChange" class="secondary">添加一项实质修改</button><h3>保护项核对</h3>${Object.entries(protectionNames).map(([k,v])=>`<label><input id="protect-${k}" type="checkbox" ${record.protected_confirmations?.[k]?.preserved?"checked":""}> 已保留${v}<textarea id="protect-note-${k}" placeholder="依据与说明">${safeText(record.protected_confirmations?.[k]?.note||"")}</textarea></label>`).join("")}<label>本次人工修改说明<textarea id="revisionHumanStatement">${safeText(record.human_confirmation?.statement||"")}</textarea></label><label>终稿确认说明<textarea id="revisionLockStatement">${safeText(record.final_lock_confirmation?.statement||"")}</textarea></label>`;
  (record.changes?.length?record.changes:[{}]).forEach(addRevisionChange);
  $("addRevisionChange").onclick=()=>{addRevisionChange({});syncRevisionRecord(false);dirtyFields.add("revisionRecord");preserveLocal()};
  form.oninput=()=>{try{syncRevisionRecord(false)}catch{}dirtyFields.add("revisionRecord");preserveLocal()};form.onchange=form.oninput;
 }
 for(const control of $("revisionNaturalForm")?.querySelectorAll("input,textarea,select,button")||[]){
  if(control.matches("textarea,input:not([type=checkbox])"))control.readOnly=revision.editable===false;
  else control.disabled=revision.editable===false;
 }
 if(!$("coverageNaturalForm")||!dirtyFields.has("coverageJson"))renderCoverageForm();
 if(!$("findingNaturalForm")||!dirtyFields.has("findingJson"))renderFindingForm();
}
function renderReviewFindings(){
 const barrier=state.review_barrier, current=state.consultation_candidate;
 const box=$("findings");box.replaceChildren();
 for(const finding of barrier.findings||[]){
  const row=document.createElement("section");row.className="finding";
  const title=document.createElement("strong");title.textContent=`${finding.severity} · ${finding.diagnosis}`;row.append(title);
  const impact=document.createElement("p");impact.textContent=finding.reader_impact||"";row.append(impact);
  for(const id of finding.evidence_ids||[]){
   const match=/^(.*)@(\d+):(\d+)$/.exec(id);
   if(!match||![barrier.candidate_path,state.draft.path].includes(match[1]))continue;
   const start=Number(match[2]),end=Number(match[3]),points=Array.from(current.text);
   const button=document.createElement("button");button.type="button";button.className="secondary";
   const valid=barrier.candidate_sha256===current.sha256&&end>start&&start>=0&&end<=points.length;
   button.textContent=valid?"定位问题原文":"该意见依据已变化，请重新审稿";button.disabled=!valid;
   if(valid){const quote=document.createElement("blockquote");quote.textContent=points.slice(start,end).join("");row.append(quote);button.onclick=()=>{const manuscript=$("manuscript");manuscript.focus();manuscript.setSelectionRange(points.slice(0,start).join("").length,points.slice(0,end).join("").length);manuscript.scrollIntoView({block:"center"});};}
   row.append(button);
  }
  if(finding.preserve?.length){const protectedItems=document.createElement("p");protectedItems.textContent="修订保护项："+finding.preserve.join("；");row.append(protectedItems);}
  box.append(row);
 }
 if(!box.children.length)box.textContent="当前审稿没有待处理问题；定稿仍须完成整轮审稿和人工确认。";
}
function addRevisionChange(change={}){
 const index=$("revisionChanges").children.length;
 const row=document.createElement("div");row.className="revision-change";row.dataset.change=change.change_id||`edit-${Date.now()}-${index}`;
 row.innerHTML=`<label>修改影响<select data-revision-field="dimension">${options(revisionDimensions,change.dimension)}</select></label><label>修改前原文<textarea data-revision-field="before" aria-label="修改前原文">${safeText(change.before?.text||"")}</textarea></label><label>修改后原文<textarea data-revision-field="after" aria-label="修改后原文">${safeText(change.after?.text||"")}</textarea></label><label>对应人工意图<select data-revision-field="intent_ref">${options(intentNames,change.intent_ref)}</select></label><label>为什么这样改<textarea data-revision-field="intent">${safeText(change.intent||"")}</textarea></label><label>预期读者感受<textarea data-revision-field="reader_effect">${safeText(change.reader_effect||"")}</textarea></label><label>本项修改要保留什么<textarea data-revision-field="must_preserve" placeholder="每行一项">${safeText((change.must_preserve||[]).join("\n"))}</textarea></label><button type="button" class="secondary">移除此项</button>`;
 row.querySelector("button").onclick=()=>{row.remove();syncRevisionRecord(false);dirtyFields.add("revisionRecord");preserveLocal()};
 row.evidenceBindings={};
 for(const [side,id] of [["before","revisionSource"],["after","revisionText"]]){
  const source=$(id), existing=change[side];
  const persisted=state.human_author_revision.record?.changes?.find(item=>item.change_id===change.change_id)?.[side];
  const savedSource=side==="before"?state.human_author_revision.source_text:state.human_author_revision.text;
  if(existing?.text&&JSON.stringify(persisted)===JSON.stringify(existing)&&source.value===savedSource&&Array.from(source.value).slice(existing.start,existing.end).join("")===existing.text)row.evidenceBindings[side]={source:source.value,...existing};
  const button=document.createElement("button");button.type="button";button.className="secondary";button.textContent=side==="before"?"引用冻结原稿选区":"引用修改稿选区";
  row.querySelector(`[data-revision-field="${side}"]`).after(button);
  button.onclick=()=>{
   const quote=source.value.slice(source.selectionStart,source.selectionEnd);
   if(!quote){show("revisionStatus","请先在对应的全文中选中一段原文，再点击引用。","error");return;}
   row.evidenceBindings[side]={source:source.value,start:Array.from(source.value.slice(0,source.selectionStart)).length,end:Array.from(source.value.slice(0,source.selectionEnd)).length,text:quote};
   row.querySelector(`[data-revision-field="${side}"]`).value=quote;
   syncRevisionRecord(false);dirtyFields.add("revisionRecord");preserveLocal();
   show("revisionStatus","已绑定所选位置；修改目的、读者影响与保护项仍由你填写。","ok");
  };
 }
 $("revisionChanges").append(row);
}
function locateExact(source,quote,strict,binding){
 if(quote&&binding?.source===source&&binding.text===quote&&Array.from(source).slice(binding.start,binding.end).join("")===quote)return {start:binding.start,end:binding.end,text:quote};
 const start=source.indexOf(quote);
 if(!quote||start<0||source.indexOf(quote,start+1)>=0){if(strict)throw Error("原文不存在、重复或选区已过期。请重新选择准确位置，或扩大引用片段。");return {start:0,end:0,text:quote}}
 return {start:Array.from(source.slice(0,start)).length,end:Array.from(source.slice(0,start+quote.length)).length,text:quote};
}
function syncRevisionRecord(strict=true){
 if(!$("revisionNaturalForm"))return;
 const record=JSON.parse($("revisionRecord").value||"{}");
 record.changes=[...$("revisionChanges").children].map(row=>{const values=Object.fromEntries([...row.querySelectorAll("[data-revision-field]")].map(el=>[el.dataset.revisionField,el.value]));return {change_id:row.dataset.change,dimension:values.dimension,before:locateExact(state.human_author_revision.source_text||state.draft.text,values.before,strict,row.evidenceBindings?.before),after:locateExact($("revisionText").value,values.after,strict,row.evidenceBindings?.after),intent_ref:values.intent_ref,intent:values.intent,reader_effect:values.reader_effect,must_preserve:values.must_preserve.split("\n").map(s=>s.trim()).filter(Boolean)}});
 record.impact_dimensions=[...new Set(record.changes.map(c=>c.dimension))];
 record.protected_confirmations=Object.fromEntries(Object.keys(protectionNames).map(k=>[k,{preserved:$("protect-"+k).checked,note:$("protect-note-"+k).value}]));
 record.human_confirmation={confirmed_by:"human",statement:$("revisionHumanStatement").value};record.final_lock_confirmation={locked_by:"human",statement:$("revisionLockStatement").value};
 $("revisionRecord").value=JSON.stringify(record,null,2);
}
function renderCoverageForm(){
 let form=$("coverageNaturalForm");if(!form){form=document.createElement("div");form.id="coverageNaturalForm";$("coverageJson").parentElement.before(form)}
 const coverage=JSON.parse($("coverageJson").value||"{}");
 form.innerHTML=state.review_checks.map(c=>{const item=coverage[c.id]||{};return `<details><summary>${safeText(c.label)}</summary><label>判断<select data-coverage="${c.id}" data-field="status">${options({confirmed:"已核对",covered:"由独立审稿覆盖",accepted_p2:"接受一般建议",repair:"需要修改",redirect:"调整方向"},item.status)}</select></label><label>判断依据<select data-coverage="${c.id}" data-field="coverage_source">${options({human_core:"本人阅读正文",independent_review:"独立审稿证据",human_resolution:"本人处理意见"},item.coverage_source)}</select></label><label>阅读理由<textarea data-coverage="${c.id}" data-field="reason">${safeText(item.reason||"")}</textarea></label><details><summary>证据引用详情</summary><textarea data-coverage="${c.id}" data-field="evidence_refs">${safeText((item.evidence_refs||[]).join("\n"))}</textarea></details></details>`}).join("");
 form.oninput=()=>{const result=JSON.parse($("coverageJson").value||"{}");for(const el of form.querySelectorAll("[data-coverage]")){result[el.dataset.coverage]??={};result[el.dataset.coverage][el.dataset.field]=el.dataset.field==="evidence_refs"?el.value.split("\n").filter(Boolean):el.value}$("coverageJson").value=JSON.stringify(result,null,2);dirtyFields.add("coverageJson");preserveLocal()};form.onchange=form.oninput;
}
$("revisionSave").addEventListener("click",event=>{try{syncRevisionRecord()}catch(error){event.stopImmediatePropagation();show("revisionStatus",error.message,"error")}},true);

// Keyboard and narrow-screen alternative to dragging a selection. Both controls
// use the same evidence actions, and the server still verifies the exact span.
const exactEvidence=document.createElement("details");
exactEvidence.innerHTML='<summary>粘贴准确原文登记证据</summary><label>引用原文<textarea id="exactEvidenceQuote" placeholder="粘贴能够唯一定位的句段"></textarea></label><label>证据用途<select id="exactEvidenceKind"><option value="key_turn">关键转折</option><option value="character_choice_or_emotion">人物选择与情绪</option><option value="reader_gain">读者收益</option></select></label><button type="button" id="exactEvidenceAdd">登记这段证据</button><p id="exactEvidenceStatus" role="status"></p>';
$("evidenceNote").closest("label").after(exactEvidence);
$("exactEvidenceAdd").onclick=()=>{
 try{
  const quote=$("exactEvidenceQuote").value,manuscript=$("manuscript");
  locateExact(manuscript.value,quote,true);
  const start=manuscript.value.indexOf(quote);
  manuscript.setSelectionRange(start,start+quote.length);
  document.querySelector(`[data-evidence="${$("exactEvidenceKind").value}"]`).click();
  show("exactEvidenceStatus","原文已定位并登记，请填写对应的阅读判断。","ok");
 }catch(error){show("exactEvidenceStatus",error.message,"error")}
};

function renderFindingForm(){
 let form=$("findingNaturalForm");if(!form){form=document.createElement("div");form.id="findingNaturalForm";$("findingJson").parentElement.before(form)}
 const records=JSON.parse($("findingJson").value||"[]"),findings=state.review_barrier?.findings||[];
 form.innerHTML='<h3>逐项处理审稿问题</h3>'+(records.map((record,index)=>{
  const finding=findings.find(item=>item.finding_id===record.finding_id)||{};
  const choices=record.severity==="P2"?{accept_p2:"接受一般建议并说明理由",repair:"修改后重新审稿",redirect:"调整创作方向"}:{repair:"修改后重新审稿",redirect:"调整创作方向"};
  return `<details class="finding-resolution" data-index="${index}"><summary>${safeText(record.severity)} · ${safeText(finding.diagnosis||"待处理审稿问题")}</summary><label>处理方式<select data-field="disposition">${options(choices,record.disposition)}</select></label><label>采用或修改的理由<textarea data-field="reason">${safeText(record.reason||"")}</textarea></label><label>必须保留的内容（每行一项）<textarea data-field="must_preserve">${safeText((record.must_preserve||[]).join("\n"))}</textarea></label><details><summary>原始审稿与证据</summary><pre>${safeText(JSON.stringify(finding,null,2))}</pre></details></details>`;
 }).join("")||'<p class="muted">当前冻结审稿中没有待处理问题。</p>');
 form.oninput=()=>{for(const row of form.querySelectorAll("[data-index]")){const item=records[Number(row.dataset.index)];for(const field of row.querySelectorAll("[data-field]"))item[field.dataset.field]=field.dataset.field==="must_preserve"?field.value.split("\n").map(x=>x.trim()).filter(Boolean):field.value}$("findingJson").value=JSON.stringify(records,null,2);dirtyFields.add("findingJson");preserveLocal()};form.onchange=form.oninput;
}
