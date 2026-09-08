const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const scopes={project:"作品",volume:"卷",chapter:"章节",selection:"选段"};
const jobNames={queued:"等待启动",running:"正在生成",cancelling:"正在取消",cancelled:"已取消",failed:"执行失败",failed_validation:"回答未通过校验",completed:"生成完成",rejected_output_boundary:"输出越界，未采用",interrupted:"运行中断"};

export function connectDiscussion({app,get,post,notify}) {
 let projectId=null, activeJob=null, pollTimer=null, adoptionPending=false;
 const suggestionDrafts=new Map();
 const $=id=>document.getElementById(id);
 async function refreshHistory(id) {
   if(!$("discussion-history"))return;
   projectId=id;const {turns}=await get(`/api/projects/${id}/discussions`);
   const history=$("discussion-history"),focused=document.activeElement;
   const focusState=history.contains(focused)&&focused.matches("[data-adopt-text]")?{id:focused.dataset.adoptText,start:focused.selectionStart,end:focused.selectionEnd}:null;
   history.querySelectorAll("[data-suggestion-key]").forEach(field=>suggestionDrafts.set(field.dataset.suggestionKey,field.value));
   $("discussion-history").innerHTML=turns.map(t=>`<section class="discussion-turn"><p class="small muted">${scopes[t.scope_key.scope]}${t.scope_key.chapter?` · 第 ${t.scope_key.chapter} 章`:""}${t.stale?" · 依据已失效":""}</p><p class="question-message">${esc(t.question)}</p>${t.selection?`<blockquote class="selection-quote">第 ${t.selection.chapter_number} 章：${esc(t.selection.quote)}</blockquote>`:""}${t.response?`<div class="answer-message">${esc(t.response)}</div>${t.stale?`<p class="notice warn">来源已变化：${t.stale_sources.map(esc).join("、")}</p>`:`<label>保留的建议<textarea data-adopt-text="${t.id}" aria-label="要保留的建议">${esc(t.response)}</textarea></label><div class="button-row"><button class="secondary" data-adopt="${t.id}" data-kind="memo">存为备忘</button><button class="secondary" data-adopt="${t.id}" data-kind="intent_draft">意图草稿</button><button class="secondary" data-adopt="${t.id}" data-kind="planning_proposal">规划提案</button></div>`}`:`<p class="small muted">尚无已校验回答</p><button data-run-discussion="${t.id}" data-status="${esc(t.task_status)}" data-task="${esc(t.task_id)}" ${t.stale?"disabled":""}>${["submitted","validated"].includes(t.task_status)?"重新校验并记录回答":"执行顾问任务"}</button>`}${t.adoptions.length?`<p class="small muted">已保存 ${t.adoptions.length} 条建议，等待相应创作流程采用。</p>`:""}<details><summary>参考材料</summary>${t.bindings.map(b=>`<p class="small">${esc(b.label)}</p>`).join("")}</details></section>`).join("")||'<p class="small muted">讨论会保留问题、引文、回答和采用记录。</p>';
   for(const turn of turns){
     let field=history.querySelector(`[data-adopt-text="${turn.id}"]`);
     const key=`studio:${id}:suggestion:${turn.id}:${turn.response_sha256}`;
     let draft=suggestionDrafts.get(key);try{draft??=localStorage.getItem(key)}catch{}
     if(!field&&turn.stale&&draft!==null&&draft!==undefined){
       const section=history.querySelectorAll(".discussion-turn")[turns.indexOf(turn)],details=document.createElement("details"),summary=document.createElement("summary");
       summary.textContent="查看本机保留的修改文字（依据已失效）";field=document.createElement("textarea");field.dataset.adoptText=turn.id;field.readOnly=true;details.append(summary,field);section.append(details);
     }
     if(field){field.dataset.suggestionKey=key;if(draft!==null&&draft!==undefined)field.value=draft;field.oninput=()=>{suggestionDrafts.set(key,field.value);try{localStorage.setItem(key,field.value)}catch{notify("本机存储不可用，请复制尚未保存的建议文字。")}};}
   }
   if(focusState){const field=history.querySelector(`[data-adopt-text="${focusState.id}"]`);if(field){field.focus({preventScroll:true});field.setSelectionRange(focusState.start,focusState.end)}}
   $("discussion-history").querySelectorAll("[data-run-discussion]").forEach(b=>b.onclick=()=>run(b.dataset.runDiscussion,b.dataset.task,b.dataset.status).catch(e=>notify(e.message)));
   $("discussion-history").querySelectorAll("[data-adopt]").forEach(b=>b.onclick=async()=>{if(adoptionPending)return;adoptionPending=true;$("discussion-history").querySelectorAll("[data-adopt]").forEach(button=>button.disabled=true);try{const text=$("discussion-history").querySelector(`[data-adopt-text="${b.dataset.adopt}"]`).value;await post(`/api/projects/${id}/discussions/${b.dataset.adopt}/adopt`,{kind:b.dataset.kind,text});notify("建议已保存到创作沙盒，正式采用仍需进入对应审批。");await refreshHistory(id)}catch(e){notify(e.message)}finally{adoptionPending=false;$("discussion-history")?.querySelectorAll("[data-adopt]").forEach(button=>button.disabled=false)}});
   return turns;
 }
 async function run(turnId,taskId,status) {
   if(["submitted","validated"].includes(status)){await post(`/api/projects/${projectId}/discussions/${turnId}/record`,{});await refreshHistory(projectId);notify("回答已校验并记录。");return;}
   if(activeJob){notify("请等待当前顾问任务结束。");return}
   const started=await post(`/api/projects/${projectId}/agent-jobs`,{task_id:taskId});
   activeJob=started.result.job_id;
   sessionStorage.setItem(`studio:${projectId}:discussion-job`,JSON.stringify({job:activeJob,turn:turnId}));
   await poll(turnId);
 }
 async function poll(turnId) {
   if(!activeJob||!$("discussion-state"))return;
   try {
     const state=await get(`/api/projects/${projectId}/agent-jobs/${activeJob}`);
     $("discussion-state").textContent=jobNames[state.status]||state.status;
     let preview=$("discussion-preview");if(!preview){preview=document.createElement("div");preview.id="discussion-preview";preview.className="answer-message";$("discussion-state").after(preview)}
     const messages=(state.events||[]).filter(e=>e.text);preview.textContent=messages.length?"生成中 · 尚未校验\n"+messages[messages.length-1].text:"";
     if(["queued","running","cancelling"].includes(state.status)) {
       let cancel=$("cancel-discussion");if(!cancel){cancel=document.createElement("button");cancel.id="cancel-discussion";cancel.className="secondary";cancel.textContent="取消生成";$("discussion-state").after(cancel);cancel.onclick=async()=>{cancel.disabled=true;try{await post(`/api/projects/${projectId}/agent-jobs/${activeJob}/cancel`,{});await poll(turnId)}catch(error){notify(`取消未完成：${error.message}`);await poll(turnId)}finally{cancel.disabled=false}}}
       clearTimeout(pollTimer);pollTimer=setTimeout(()=>poll(turnId),1600);return;
     }
     if(state.status==="completed") {try{await post(`/api/projects/${projectId}/discussions/${turnId}/record`,{});preview.textContent="";$("discussion-state").textContent="回答已校验并记录，可以继续追问。"}catch(error){$("discussion-state").textContent=`回答已生成，但记录未通过：${error.message}。请检查来源并重新校验。`;}}
     else if(state.error)$("discussion-state").textContent+=`：${state.error}`;
     $("cancel-discussion")?.remove();activeJob=null;sessionStorage.removeItem(`studio:${projectId}:discussion-job`);await refreshHistory(projectId);
   } catch(e){$("discussion-state").textContent=`连接暂时中断：${e.message}。稍后自动重连。`;clearTimeout(pollTimer);pollTimer=setTimeout(()=>poll(turnId),4000)}
 }
 app.addEventListener("studio-reader-ready",async e=>{try{
   const turns=await refreshHistory(e.detail.projectId),key=`studio:${projectId}:discussion-job`;
   let saved=JSON.parse(sessionStorage.getItem(key)||"null");
   if(saved&&!turns.some(turn=>turn.id===saved.turn)){saved=null;sessionStorage.removeItem(key)}
   if(!saved){const {jobs}=await get(`/api/projects/${projectId}/agent-jobs`);const active=jobs.find(job=>["queued","running","cancelling"].includes(job.status)&&turns.some(turn=>turn.task_id===job.task_id));if(active){saved={job:active.job_id,turn:turns.find(turn=>turn.task_id===active.task_id).id};sessionStorage.setItem(key,JSON.stringify(saved))}}
   if(saved){activeJob=saved.job;await poll(saved.turn)}
 }catch(e){notify(e.message)}});
 app.addEventListener("studio-discuss",async e=>{const d=e.detail;try{if(activeJob)throw Error("请等待当前顾问任务结束。");projectId=d.projectId;$("discussion-state").dataset.status="pending";$("ask-advisor").disabled=true;$("discussion-state").textContent="正在绑定讨论范围和有效历史…";const turn=await post(`/api/projects/${projectId}/discussions`,{scope:d.scope,chapter:d.chapter||null,selection:d.selection||null,question:d.question,document_ids:d.document_ids||[]});await refreshHistory(projectId);await run(turn.result.id,turn.result.task_id)}catch(e){$("discussion-state").dataset.status="failed";$("discussion-state").textContent=e.message}finally{$("ask-advisor").disabled=false}});
}
