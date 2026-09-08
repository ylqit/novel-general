// Human choices and exact quotations compile into existing domain applications.
const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const states={realized:"已发生",partially_realized:"部分发生",deferred:"延期",cancelled:"取消",contradicted:"正文与计划相矛盾"};
export function bindChapterConfirmation(state,{post,notify}){
 const c=state.chapter_confirmation;if(!c)return;
 const raw=document.getElementById(c.kind==="events"?"event-observations":c.kind==="author_voice"?"voice-record":"promise-evidence");
 if(!raw)return;
 const section=raw.closest("section"),form=document.createElement("div");form.className="chapter-confirmation-form";
 const draftKey=`studio:${state.project.id}:confirmation:${c.kind}:${c.final_sha256}`;
 function quoteField(value=""){return `<label>正文证据原文<textarea data-field="quote" required>${esc(value)}</textarea></label><p class="small muted">粘贴能够唯一定位的完整句段，系统计算准确位置。</p>`}
 if(c.kind==="events"){
  const saved=c.draft_application?.observations||[];
  form.innerHTML=c.events.map((event,i)=>{const observation=saved.find(row=>row.event_id===event.event_id);return `<fieldset data-index="${i}"><legend>事件 ${i+1}</legend><p>${esc(event.description||event.summary||event.action_or_exchange||event.intended_change||event.event_id)}</p><label>实际发生情况<select data-field="state" required><option value="">请选择</option>${Object.entries(states).map(([key,label])=>`<option value="${key}" ${observation?.state===key?"selected":""}>${label}</option>`).join("")}</select></label>${quoteField(observation?.evidence?.excerpt)}<label>判断理由<textarea data-field="reason" required>${esc(observation?.semantic_reason||"")}</textarea></label></fieldset>`}).join("");
 }else if(c.kind==="reader_promises"){
  const saved=c.draft_application?.evidence||[];
  form.innerHTML=c.actions.filter(action=>action.action!=="defer").map((action,i)=>{const evidence=saved.find(row=>row.promise_id===action.promise_id);return `<fieldset data-index="${i}"><legend>读者承诺 ${i+1}</legend><p>${esc(action.intended_reader_gain)}</p>${quoteField(evidence?.excerpt)}<label>为何这段兑现了本章承诺<textarea data-field="reason" required>${esc(evidence?.semantic_reason||"")}</textarea></label></fieldset>`}).join("")||'<p>本章只有已批准的延期动作，无需补填正文证据。</p>';
 }else{
  form.innerHTML=`<label>选择本章已验证的修改<select data-field="change" required>${(c.changes||[]).map((change,i)=>`<option value="${i}">${esc(change.intent||`修改 ${i+1}`)}</option>`).join("")}</select></label><div class="version-comparison"><pre id="voice-before" class="candidate"></pre><pre id="voice-after" class="candidate"></pre></div><label>这项修改的用途<textarea data-field="purpose" required></textarea></label><label>今后可复用的表达原则<textarea data-field="abstract_principle" required></textarea></label><label>适用人物<select data-field="pov_character_id"><option value="">通用声例，不按人物筛选</option>${(c.characters||[]).map(character=>`<option value="${esc(character.id)}">${esc(character.name)}</option>`).join("")}</select></label><label>适用场景（可选）<input data-field="scene_kind"></label>`;
 }
 raw.before(form);
 const detail=document.createElement("details");detail.innerHTML='<summary>协议与高级字段</summary>';raw.before(detail);detail.append(raw);
 if(c.kind==="events")for(const id of ["event-discovered","event-divergences"]){const input=document.getElementById(id);if(input.previousElementSibling?.tagName==="LABEL")detail.append(input.previousElementSibling);detail.append(input)}
 const fields=()=>Array.from(form.querySelectorAll("input,textarea,select"));
 try{const values=JSON.parse(localStorage.getItem(draftKey)||"null");if(values)fields().forEach((field,i)=>field.value=values[i]||"")}catch{}
 const $=name=>form.querySelector(`[data-field="${name}"]`);
 function update(){
  try{localStorage.setItem(draftKey,JSON.stringify(fields().map(field=>field.value)))}catch{notify("浏览器无法保存这份确认草稿，请保留文字。")}
  if(c.kind==="events")for(const row of form.querySelectorAll("fieldset")){const required=["realized","partially_realized","contradicted"].includes(row.querySelector('[data-field="state"]').value);row.querySelector('[data-field="quote"]').required=required;row.querySelector('[data-field="quote"]').closest("label").hidden=!required;}
  if(c.kind==="author_voice"){const change=(c.changes||[])[Number($("change").value)];document.getElementById("voice-before").textContent=change?.before?.text||"";document.getElementById("voice-after").textContent=change?.after?.text||"";}
 }
 form.addEventListener("input",update);form.addEventListener("change",update);update();
 function exact(quote){const offset=c.final_text.indexOf(quote);if(!quote||offset<0||c.final_text.indexOf(quote,offset+1)>=0)throw Error("证据原文必须在终稿中唯一出现，请扩大引用范围。");return {start:Array.from(c.final_text.slice(0,offset)).length,end:Array.from(c.final_text.slice(0,offset+quote.length)).length,excerpt:quote}}
 const action=c.kind==="events"?"confirm-events":c.kind==="author_voice"?"approve-author-voice":"confirm-promises";
 const button=section.querySelector(`[data-${c.kind==="events"?"confirm-events":c.kind==="author_voice"?"approve-voice":"confirm-promises"}]`);
 const check=section.querySelector(`#${c.kind==="events"?"event":c.kind==="author_voice"?"voice":"promise"}-check`);
 // Submit only the current source-bound natural-language choices.
 button.addEventListener("click",async event=>{
  event.stopImmediatePropagation();
  try{
   if(!check.checked)throw Error("请先阅读并明确确认本次操作。");
   for(const field of fields())if(!field.reportValidity())return;
   const payload={chapter_number:c.chapter_number,expected_final_sha256:c.final_sha256};
   if(c.kind==="events"){
    payload.observations=Array.from(form.querySelectorAll("fieldset"),(row,i)=>{const value=name=>row.querySelector(`[data-field="${name}"]`).value,phase=value("state");return {event_id:c.events[i].event_id,state:phase,evidence:["realized","partially_realized","contradicted"].includes(phase)?exact(value("quote")):null,semantic_reason:value("reason")}});
    payload.discovered_causal_nodes=JSON.parse(document.getElementById("event-discovered").value);payload.realized_major_divergences=JSON.parse(document.getElementById("event-divergences").value);payload.confirmed_by="human";payload.acknowledge_event_apply=true;
   }else if(c.kind==="reader_promises"){
    const actions=c.actions.filter(item=>item.action!=="defer");payload.evidence=Array.from(form.querySelectorAll("fieldset"),(row,i)=>({promise_id:actions[i].promise_id,...exact(row.querySelector('[data-field="quote"]').value),semantic_reason:row.querySelector('[data-field="reason"]').value}));payload.confirmed_by="human";payload.acknowledge_promise_apply=true;
   }else{
    const change=c.changes[Number($("change").value)];payload.record={...c.record,before:change.before,after:change.after,purpose:$("purpose").value,abstract_principle:$("abstract_principle").value,pov_character_id:$("pov_character_id").value,scene_kind:$("scene_kind").value};payload.approved_by="human";payload.acknowledge_voice_apply=true;
   }
   button.disabled=true;await post(`/api/projects/${state.project.id}/chapters/${c.chapter_number}/${action}`,payload);localStorage.removeItem(draftKey);location.href=`/projects/${state.project.id}`;
  }catch(error){notify(error.message);button.disabled=false;}
 });
}
