// Workspace-only job controls. The standalone review desk remains a read-only
// advisor handoff surface; it does not gain its own process launcher.
const reviewProject=location.pathname.match(/^\/projects\/(project_[0-9a-f]{20})\/chapters\/([1-9][0-9]*)$/);
let reviewJob=null, reviewJobTimer=null, chosenConsultTurn=null, checkedRunningReviewJob=false;
const reviewJobKey="studio-review-job:"+location.pathname;
async function startReviewJob(taskId,kind,turn) {
  if(!reviewProject)throw Error("请从工作区审稿页执行顾问任务。");
  if(reviewJob)throw Error("请等待当前任务结束。");
  const result=await api(`/api/projects/${reviewProject[1]}/agent-jobs`,{task_id:taskId});
  reviewJob={job_id:result.job_id,kind,turn,phase:state.consultation_candidate.phase};
  const status=$(kind==="revision_semantic"?"revisionStatus":"consultStatus");status.after($("reviewJobCancel"),$("reviewJobPreview"));$("reviewJobCancel").hidden=false;
  sessionStorage.setItem(reviewJobKey,JSON.stringify(reviewJob));
  await pollReviewJob();
}
async function pollReviewJob() {
  if(!reviewJob)return;
  let job;
  try {
    const response=await fetch(`/api/projects/${reviewProject[1]}/agent-jobs/${reviewJob.job_id}`,{credentials:"same-origin"});
    job=await response.json();if(!response.ok)throw Error(job.error||"无法读取任务状态");
  }catch(error){show(reviewJob.kind==="revision_semantic"?"revisionStatus":"consultStatus",`连接暂时中断：${error.message}，稍后重连。`,"error");clearTimeout(reviewJobTimer);reviewJobTimer=setTimeout(pollReviewJob,4000);return}
  const statusId=reviewJob.kind==="revision_semantic"?"revisionStatus":"consultStatus";
  show(statusId,({queued:"等待执行",running:"正在生成",cancelling:"正在取消",cancelled:"已取消",failed:"执行失败",completed:"生成完成",failed_validation:"未通过输出校验",rejected_output_boundary:"输出超出允许范围，未采用",interrupted:"任务已中断"}[job.status]||job.status)+(job.error?`：${job.error}`:""));
  const preview=$("reviewJobPreview"), messages=(job.events||[]).filter(event=>event.text);
  preview.textContent=messages.length?"生成中 · 尚未校验\n"+messages.at(-1).text:"";
  if(["queued","running","cancelling"].includes(job.status)){clearTimeout(reviewJobTimer);reviewJobTimer=setTimeout(pollReviewJob,1600);return}
  const completed=reviewJob;reviewJob=null;sessionStorage.removeItem(reviewJobKey);$("reviewJobCancel").hidden=true;
  if(job.status!=="completed")return;
  try {
    if(completed.kind==="revision_semantic"){
      if(["revisionText","revisionRecord"].some(id=>dirtyFields.has(id)))throw Error("独立复核已结束，但页面存在未保存修改。请先核对并保存，再执行语义复核。");
      const checked=await api("/api/human-revision/validate",{expected_draft_sha256:completed.turn.expected_draft_sha256});
      if(!checked.ok)throw Error((checked.errors||[]).join("；")||"人工修订未通过独立复核");
      show("revisionStatus","独立复核已通过，已保存的人工终稿已锁定。","ok");
    }else if(completed.kind==="consult"){
      const checked=await api("/api/consult/validate",{phase:completed.phase,response_file:completed.turn.response_file});
      if(checked.ok===false)throw Error((checked.errors||[]).join("；")||"回答未通过领域校验");
      await api("/api/consult/record",{phase:completed.phase,response_file:completed.turn.response_file});
      show("consultStatus","回答已校验并记录，可选择方案或继续追问。","ok");
    }else{
      const checked=await api("/api/coedit/candidate-validate",{candidate_file:completed.turn.candidate_file});
      if(checked.ok===false)throw Error((checked.errors||[]).join("；")||"完整候选校验失败");
      acknowledgedCandidateHash=checked.candidate_sha256||"";
      show("consultStatus","完整修改候选已校验，请阅读差异后决定是否提交。","ok");
    }
    preview.textContent="";await load();
  }catch(error){show(statusId,`生成已结束，但结果未完成记录：${error.message}。请核对后使用本区域的校验按钮重试。`,"error")}
}
function renderConsultJobs(){
  if(!reviewProject)return;
  const turns=consultSessions().flatMap(session=>(session.turns||[]).map(turn=>({...turn,session_id:session.session_id,phase:session.phase,stale:session.phase!==state.consultation_candidate.phase||(session.effective_status||session.status)==="stale"||Boolean(turn.response_sha256&&!turn.response_current)||turn.candidate_current===false})));
  if(!checkedRunningReviewJob&&!reviewJob){
    checkedRunningReviewJob=true;
    fetch(`/api/projects/${reviewProject[1]}/agent-jobs`,{credentials:"same-origin"}).then(async response=>{if(!response.ok)throw Error("无法恢复当前任务状态");return response.json()}).then(async ({jobs})=>{
      if(reviewJob)return;
      const active=jobs.find(job=>["queued","running","cancelling"].includes(job.status));if(!active)return;
      const turn=turns.find(turn=>turn.task_id===active.task_id||turn.rewrite_task_id===active.task_id);
      if(turn)reviewJob={job_id:active.job_id,kind:turn.task_id===active.task_id?"consult":"rewrite",turn:turn.task_id===active.task_id?turn:{...turn,candidate_file:turn.rewrite_candidate_file},phase:turn.phase};
      else if(active.task_type==="prose_revision_semantic_review"&&state.human_author_revision.record_file&&active.declared_input_files.includes(state.human_author_revision.record_file))reviewJob={job_id:active.job_id,kind:"revision_semantic",turn:{expected_draft_sha256:state.draft.sha256},phase:state.consultation_candidate.phase};
      if(reviewJob){sessionStorage.setItem(reviewJobKey,JSON.stringify(reviewJob));$("reviewJobCancel").hidden=false;await pollReviewJob()}
    }).catch(error=>show("consultStatus",error.message,"error"));
  }
  $("consultHistory").querySelectorAll("section").forEach((section,index)=>{
    const turn=turns[index];if(!turn)return;
    section.querySelectorAll("button").forEach(button=>button.addEventListener("click",()=>{chosenConsultTurn=turn}));
    if(!turn.response_sha256){const button=document.createElement("button");button.textContent="执行本轮顾问任务";button.disabled=turn.stale;button.onclick=()=>startReviewJob(turn.task_id,"consult",turn).catch(error=>show("consultStatus",error.message,"error"));section.append(button)}
    if(turn.rewrite_candidate_sha256){
      const details=document.createElement("details"), summary=document.createElement("summary");summary.textContent="阅读完整修改候选与原稿";details.append(summary);section.append(details);
      let loaded=false;
      details.ontoggle=async()=>{if(!details.open||loaded)return;loaded=true;try{
        const base=`/api/projects/${reviewProject[1]}/chapters/${reviewProject[2]}`;
        const versions=await fetch(base+"/versions",{credentials:"same-origin"}).then(r=>r.json());
        const version=versions.versions.find(row=>row.relative===turn.rewrite_candidate_file);
        if(!version||version.sha256!==turn.rewrite_candidate_sha256)throw Error("候选版本已变化或不再可读。");
        const candidate=await fetch(`${base}/versions/${version.id}`,{credentials:"same-origin"}).then(r=>r.json());
        const sourceVersion=versions.versions.find(row=>row.sha256===turn.candidate_sha256);
        if(!sourceVersion)throw Error("当时的原稿版本暂不可读，不能用当前正文替代历史对照。");
        const source=await fetch(`${base}/versions/${sourceVersion.id}`,{credentials:"same-origin"}).then(r=>r.json());
        if(source.sha256!==turn.candidate_sha256||candidate.sha256!==turn.rewrite_candidate_sha256)throw Error("对照版本在读取期间已变化，请刷新后重试。");
        const grid=document.createElement("div");grid.className="version-comparison";
        for(const text of [source.text,candidate.text]){const pre=document.createElement("pre");pre.className="version-prose";pre.textContent=text||"";grid.append(pre)}
        const label=document.createElement("label"), ack=document.createElement("input");ack.type="checkbox";label.append(ack,document.createTextNode("已阅读完整对照，确认提交为本章新草稿"));
        const submit=document.createElement("button");submit.textContent="采用完整候选并重新审稿";submit.disabled=!turn.rewrite_current||state.consultation_candidate.phase!=="coedit";
        submit.onclick=async()=>{try{await api("/api/coedit/submit",{task_id:turn.rewrite_task_id,expected_draft_sha256:state.draft.sha256,expected_candidate_sha256:version.sha256,acknowledge:ack.checked});acknowledgedCandidateHash=version.sha256;await load();show("consultStatus","已提交替代稿，须重新完成审稿与人工终稿流程。","ok")}catch(error){show("consultStatus",error.message,"error")}};
        details.append(grid,label,submit);
      }catch(error){const warning=document.createElement("p");warning.textContent=error.message;details.append(warning)}};
    }
  });
}
if(reviewProject){
  const preview=document.createElement("pre");preview.id="reviewJobPreview";preview.className="answer-message";
  const cancel=document.createElement("button");cancel.id="reviewJobCancel";cancel.textContent="取消正在生成的任务";cancel.className="secondary";cancel.hidden=true;
  cancel.onclick=async()=>{if(reviewJob){await api(`/api/projects/${reviewProject[1]}/agent-jobs/${reviewJob.job_id}/cancel`,{});await pollReviewJob()}};
  $("consultStatus").after(cancel,preview);
  $("revisionValidate").textContent="执行独立复核并锁定已保存稿";
  $("revisionValidate").onclick=async()=>{const button=$("revisionValidate");button.disabled=true;try{
    if(reviewJob)throw Error("请等待当前任务结束。");
    if(["revisionText","revisionRecord"].some(id=>dirtyFields.has(id)))throw Error("请先保存当前完整改稿与修改说明。");
    const result=await api("/api/human-revision/validate",{expected_draft_sha256:state.draft.sha256});
    if(result.stage==="semantic_review_pending"&&result.semantic_task_id)await startReviewJob(result.semantic_task_id,"revision_semantic",{expected_draft_sha256:state.draft.sha256});
    else{show("revisionStatus",result.ok?"独立复核已通过，已保存的人工终稿已锁定。":(result.errors||[]).join("；"),result.ok?"ok":"error");await load();}
  }catch(error){show("revisionStatus",error.message,"error")}finally{button.disabled=false}};
  $("consultTask").textContent="提交问题并生成建议";
  $("consultTask").onclick=async()=>{const button=$("consultTask");button.disabled=true;try{if(reviewJob)throw Error("请等待当前任务结束。");const selection=capture();const turn=await api("/api/consult/task",{phase:state.consultation_candidate.phase,expected_candidate_sha256:state.consultation_candidate.sha256,start:selection.start,end:selection.end,question:$("question").value});await load();cancel.hidden=false;await startReviewJob(turn.task_id,"consult",turn)}catch(error){show("consultStatus",error.message,"error")}finally{button.disabled=false}};
  $("coeditRewrite").textContent="按所选方案生成完整候选";
  $("coeditRewrite").onclick=async()=>{const button=$("coeditRewrite");button.disabled=true;try{const turn=chosenConsultTurn||latestTurn();if(!turn)throw Error("请先选择已记录方案。");const result=await api("/api/coedit/rewrite-task",{session_id:turn.session_id,turn_number:turn.turn_number,option_id:$("optionId").value,adjustment:$("optionAdjustment").value});cancel.hidden=false;await startReviewJob(result.task_id,"rewrite",result)}catch(error){show("consultStatus",error.message,"error")}finally{button.disabled=false}};
  try{reviewJob=JSON.parse(sessionStorage.getItem(reviewJobKey)||"null");if(reviewJob){cancel.hidden=false;setTimeout(pollReviewJob,0)}}catch{sessionStorage.removeItem(reviewJobKey)}
}
