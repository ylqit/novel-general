const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
export async function renderPlanning({projectId,container,get,post,notify}) {
  const endpoint=`/api/projects/${projectId}/planning`;
  let state=await get(endpoint+"/state"), job=null, timer=null;
  const key=`studio:${projectId}:planning-job`;
  const $=selector=>container.querySelector(selector);
  const action=async(name,body={})=>{const result=await post(`${endpoint}/${name}`,body);state=result.result;draw()};
  function draw(){
    const bundle=state.bundle, applied=state.status==="applied";
    const reviewer=state.reviewer, task=reviewer||state.author;
    container.innerHTML=`<section class="card"><h3>规划创作与批准</h3><p class="muted">先生成候选，再独立审查，最后逐项确认本轮情节决定。批准的实际卷范围决定章节归属。</p><div id="planning-job-status" role="status"></div><div class="button-row">${!task||applied?'<button data-planning="create">准备当前滚动规划</button>':`<button data-planning="rebuild" class="secondary">重建当前规划任务</button>${["awaiting_agent","invalid"].includes(task.status)?`<button id="planning-execute" data-task-id="${esc(task.task_id)}">执行当前规划任务</button>`:""}${!reviewer?'<button data-planning="prepare-review">校验候选并准备独立审查</button>':'<button data-planning="review-validate">核对独立审查证据</button>'}`}</div><p id="planning-result" role="status">${applied?"本轮规划已批准并应用。":(state.stale_reasons?.length||state.output_errors?.length)?esc([...(state.stale_reasons||[]),...(state.output_errors||[])].join("；")):""}</p></section>${bundle?`<section class="card"><h3>${esc(bundle.book_spine.premise)}</h3><p>${esc(bundle.book_spine.central_conflict)}</p><p>${esc(bundle.book_spine.reader_value)}</p><details><summary>全书边界与卷结构</summary><p>${esc(bundle.book_spine.ending_boundary)}</p>${bundle.volume_skeletons.items.map(v=>`<h4>${esc(v.title)} · 第 ${v.chapter_range.join("–")} 章</h4><p>${esc(v.volume_goal)}</p><p>${esc(v.entry_state)} → ${esc(v.exit_state)}</p>`).join("")}</details></section><section class="card"><h3>本轮章节与情节决定</h3><form id="planning-approval">${bundle.chapter_contracts.map(ch=>`<section><h4>第 ${ch.chapter_number} 章 · ${esc(ch.chapter_duty)}</h4><p>${esc(ch.observable_change)}</p><p class="muted">读者获得：${esc(ch.reader_value)}</p>${(bundle.plot_node_tables.find(t=>t.chapter_number===ch.chapter_number)?.nodes||[]).map(node=>`<div class="revision-change" ${node.node_kind==="state_change"?`data-node-id="${esc(node.node_id)}"`:""}><p>${esc(node.action_or_exchange)}</p><p class="muted">${esc(node.reader_effect)}</p>${node.node_kind==="state_change"&&!applied?'<label>本项决定<select name="decision" required><option value="">请选择</option><option value="approve">保留</option><option value="adjust">调整</option><option value="reject">拒绝</option><option value="defer">暂缓</option></select></label><label>决定理由<input name="reason" required></label><label>调整内容（选择调整时填写）<textarea name="adjustment"></textarea></label>':""}</div>`).join("")}</section>`).join("")}${!applied?'<label>整体批准理由<textarea id="planning-reason" required></textarea></label><label><input type="checkbox" id="planning-confirm" required> 我已逐项阅读并确认这些规划决定</label><button id="planning-approve">批准并应用规划</button>':""}</form></section>`:""}${state.review?`<section class="card"><h3>独立审查</h3><p>${({pass:"审查通过",repair:"需要修订",need_human:"需要人工决定",insufficient_evidence:"证据不足"}[state.review.verdict]||"尚无有效结论")}</p>${(state.review.findings||[]).map(f=>`<p class="notice warn">${esc(f.diagnosis)}<br>${esc(f.reader_impact)}</p>`).join("")}<details><summary>证据与覆盖范围</summary><pre>${esc(JSON.stringify(state.review,null,2))}</pre></details></section>`:""}`;
    container.querySelectorAll("[data-planning]").forEach(button=>button.onclick=async()=>{button.disabled=true;try{const name=button.dataset.planning;await action(name,name==="prepare-review"?{task_id:state.author_task_id}:name==="review-validate"?{task_id:state.reviewer_task_id}:{})}catch(error){$("#planning-result").textContent=error.message;button.disabled=false}});
    $("#planning-execute")?.addEventListener("click",async()=>{try{if(job)throw Error("请等待当前任务结束。");const started=await post(`/api/projects/${projectId}/agent-jobs`,{task_id:task.task_id});job={id:started.result.job_id,task_id:task.task_id,type:task.task_type};sessionStorage.setItem(key,JSON.stringify(job));await poll()}catch(error){$("#planning-result").textContent=error.message}});
    const form=$("#planning-approval"), draftKey=`studio:${projectId}:planning-decisions:${state.bundle_sha256}`;
    if(form&&!applied){
      const fields=()=>Array.from(form.querySelectorAll("input:not([type=checkbox]),textarea,select"));
      try{const values=JSON.parse(localStorage.getItem(draftKey)||"null");if(values)fields().forEach((field,i)=>field.value=values[i]||"")}catch{}
      form.addEventListener("input",()=>localStorage.setItem(draftKey,JSON.stringify(fields().map(field=>field.value))));
      form.onsubmit=async event=>{event.preventDefault();try{const decisions=Array.from(form.querySelectorAll("[data-node-id]"),row=>({node_id:row.dataset.nodeId,decision:row.querySelector('[name="decision"]').value,reason:row.querySelector('[name="reason"]').value,adjustment:row.querySelector('[name="adjustment"]').value}));await action("approve",{expected_sha256:state.bundle_sha256,reason:$("#planning-reason").value,decisions,acknowledge:$("#planning-confirm").checked});localStorage.removeItem(draftKey);notify("规划已通过事务应用，可继续本章人工意图。")}catch(error){$("#planning-result").textContent=error.message}};
      $("#planning-approve").disabled=state.review_validation?.state!=="semantic_passed"||!state.review_validation?.ok||Boolean(state.stale_reasons?.length);
    }
  }
  async function poll(){
    if(!job||!$("#planning-job-status"))return;
    let result;
    try{result=await get(`/api/projects/${projectId}/agent-jobs/${job.id}`)}catch(error){$("#planning-job-status").textContent=`连接中断，正在重连：${error.message}`;timer=setTimeout(poll,4000);return}
    $("#planning-job-status").dataset.status=result.status;container.setAttribute("aria-busy",String(["queued","running","cancelling"].includes(result.status)));
    if($("#planning-execute"))$("#planning-execute").disabled=["queued","running","cancelling"].includes(result.status);
    $("#planning-job-status").textContent=({queued:"等待执行",running:"正在生成规划结果",cancelling:"正在取消",cancelled:"已取消",failed:"生成失败",completed:"生成完成，正在校验",failed_validation:"输出校验未通过",rejected_output_boundary:"输出超出允许范围，未采用",interrupted:"任务已中断"}[result.status]||result.status)+(result.error?`：${result.error}`:"");
    const messages=(result.events||[]).filter(event=>event.text);
    if(messages.length){let preview=$("#planning-progress");if(!preview){preview=document.createElement("pre");preview.id="planning-progress";preview.className="answer-message";$("#planning-job-status").after(preview)}preview.textContent="生成过程 · 尚未作为批准规划\n"+messages.at(-1).text;}
    if(["queued","running","cancelling"].includes(result.status)){
      if(!$("#planning-cancel")){const cancel=document.createElement("button");cancel.id="planning-cancel";cancel.className="secondary";cancel.textContent="取消生成";cancel.onclick=()=>post(`/api/projects/${projectId}/agent-jobs/${job.id}/cancel`,{}).catch(error=>notify(error.message));$("#planning-job-status").after(cancel)}
      clearTimeout(timer);timer=setTimeout(poll,1800);return;
    }
    const completed=job;job=null;sessionStorage.removeItem(key);$("#planning-cancel")?.remove();
    if(result.status==="completed")try{await action(completed.type==="planning_generation"?"prepare-review":"review-validate",{task_id:completed.task_id})}catch(error){$("#planning-result").textContent=error.message}
  }
  draw();try{
    job=JSON.parse(sessionStorage.getItem(key)||"null");
    const task=state.reviewer||state.author;
    if(job&&job.task_id!==task?.task_id){job=null;sessionStorage.removeItem(key)}
    if(!job&&task){const {jobs}=await get(`/api/projects/${projectId}/agent-jobs`);const active=jobs.find(row=>row.task_id===task.task_id&&["queued","running","cancelling"].includes(row.status));if(active){job={id:active.job_id,task_id:active.task_id,type:active.task_type};sessionStorage.setItem(key,JSON.stringify(job))}}
    if(job)await poll();
  }catch(error){notify(error.message)}
}
