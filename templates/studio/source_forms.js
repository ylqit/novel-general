// Natural forms use the source console's existing API, validation and approval flow.
const sourceEscape=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let sourceIngestPlan=null,sourceFormsReady=false,sourcePreviewRequest=0;
const sourceDraftKey=`studio:${location.pathname}:source-forms`;
function sourceSelectPanel(name){for(const button of $("nav").querySelectorAll("button"))if(button.textContent===name){button.click();break;}}
function sourceSaveDraft(){
 const fields={};for(const field of document.querySelectorAll("input[id]:not([type=file]),textarea[id],select[id]"))fields[field.id]=field.type==="checkbox"?field.checked:field.value;
 try{localStorage.setItem(sourceDraftKey,JSON.stringify({fields,batch:lastBatch,plan:sourceIngestPlan,job:lastJob}))}catch{show("groupResult","浏览器无法保存表单，请保留正在填写的文字。")}
}
function sourceSyncGroups(){
 if(!sourceIngestPlan)return;
 const groups=sourceIngestPlan.groups.map((group,index)=>({...group,name:$("source-group-name-"+index).value.trim(),approved:$("source-group-approve-"+index).checked,file_ids:[]}));
 for(const field of document.querySelectorAll("[data-source-file]"))groups[Number(field.value)].file_ids.push(field.dataset.sourceFile);
 $("groupPlan").value=JSON.stringify(groups.filter(group=>group.file_ids.length),null,2);
 sourceSaveDraft();
}
async function sourcePreview(offset=0){
 const request=++sourcePreviewRequest,item=$("processItem").value;
 if(!item){show("source-preview-text","请先选择资料。");return;}
 show("source-preview-text","正在读取证据摘录…");
 try{
  const response=await fetch(`/api/evidence/preview?item_id=${encodeURIComponent(item)}&offset=${offset}&limit=40`,{credentials:"same-origin"});
  const result=await response.json();if(!response.ok)throw Error(result.error||"无法读取原文");
  if(request!==sourcePreviewRequest||item!==$("processItem").value)return;
  const data=result.result||result;
  $("source-preview-text").innerHTML=`<p class="muted">证据摘录 ${offset+1}—${Math.min(offset+40,data.total)} / ${data.total}，尚未成为批准的原著基线。</p>`+(data.segments||[]).map((segment,index)=>`<article><h3>片段 ${offset+index+1}</h3><p>${sourceEscape(segment.normalized_text)}</p>${segment.text_truncated?'<p class="muted">此处仅展示片段预览。</p>':''}</article>`).join("")+`<div class="button-row"><button id="source-preview-prev" ${offset===0?"disabled":""}>上一组摘录</button><button id="source-preview-next" ${data.has_more?"":"disabled"}>下一组摘录</button></div>`;
  $("source-preview-prev").onclick=()=>sourcePreview(Math.max(0,offset-40));$("source-preview-next").onclick=()=>sourcePreview(offset+40);
 }catch(error){if(request===sourcePreviewRequest)show("source-preview-text",error.message)}
}
function sourceRenderGroups(plan){
 sourceIngestPlan=plan;let form=$("source-group-form");if(!form){form=document.createElement("div");form.id="source-group-form";($("groupPlan").closest("details")||$("groupPlan").closest("label")).before(form);}
 const groups=plan.groups||[];
 form.innerHTML=`<h3>资料分组</h3><p class="muted">核对名称和文件归属，勾选需要导入的分组。未采用的文件仍保留在分组记录中。</p>${groups.map((group,index)=>`<fieldset><legend>分组 ${index+1}</legend><label>资料名称<input id="source-group-name-${index}" value="${sourceEscape(group.name)}"></label><label><input id="source-group-approve-${index}" type="checkbox" ${group.approved?"checked":""}> 采用这个分组</label></fieldset>`).join("")}<h3>文件归属</h3>${(plan.files||[]).map(file=>`<label>${sourceEscape(file.relative_path)}<select id="source-file-${sourceEscape(file.file_id)}" data-source-file="${sourceEscape(file.file_id)}">${groups.map((group,index)=>`<option value="${index}" ${group.file_ids.includes(file.file_id)?"selected":""}>分组 ${index+1} · ${sourceEscape(group.name)}</option>`).join("")}</select></label>`).join("")}`;
 form.oninput=sourceSyncGroups;form.onchange=sourceSyncGroups;sourceSaveDraft();
}
function sourceEnhance(){
 const catalog=state.catalog,works=catalog.works||[],items=catalog.items||[];
 $("project").textContent=state.project.title+" · "+({original:"原创小说",fanfiction:"同人小说"}[state.project.creation_mode]||"创作资料");
 if(!sourceFormsReady){
  sourceFormsReady=true;
  const register=document.createElement("form");register.id="source-register-form";register.className="card";
  register.innerHTML='<h3>登记资料所属作品</h3><div class="form-grid"><label>作品名称<input id="source-work-name" required></label><label>作者或创作方<input id="source-work-creator" required></label><label>版本名称<input id="source-work-version" required></label><label>其他名称（每行一个）<textarea id="source-work-aliases"></textarea></label></div><button type="submit">保存作品资料</button><p id="source-register-result" role="status"></p>';
  $("catalog").before(register);
  register.onsubmit=async event=>{event.preventDefault();const button=register.querySelector("button");button.disabled=true;try{const work=await api("/api/work/register",{name:$("source-work-name").value,creator:$("source-work-creator").value,versions:[$("source-work-version").value],aliases:$("source-work-aliases").value.split("\n").map(value=>value.trim()).filter(Boolean),approved_by:"human"});await load();$("uploadWork").value=work.work_id;show("source-register-result","作品资料已保存，可以选择文件导入。");sourceSaveDraft()}catch(error){show("source-register-result",error.message)}finally{button.disabled=false}};
  for(const id of ["uploadWork","processItem"]){const input=$(id),select=document.createElement("select");select.id=id;input.replaceWith(select);select.parentElement.firstChild.textContent=id==="uploadWork"?"所属作品":"选择资料";}
  const format=document.createElement("label");format.innerHTML='选择方式<select id="source-file-mode"><option value="files">选择文件</option><option value="directory">选择文件夹</option></select>';$("files").closest("label").before(format);$("files").removeAttribute("webkitdirectory");$("source-file-mode").onchange=()=>$("files").toggleAttribute("webkitdirectory",$("source-file-mode").value==="directory");
  for(const [id,label] of [["catalog","资料库索引详情"],["capabilities","本机处理能力详情"],["groupPlan","高级分组字段"],["uploadResult","上传记录详情"],["processResult","处理记录详情"],["groupResult","导入记录详情"]]){const element=$(id),target=id==="groupPlan"?element.closest("label"):element,detail=document.createElement("details");detail.innerHTML=`<summary>${label}</summary>`;target.before(detail);detail.append(target);if(["processResult","groupResult","uploadResult"].includes(id)){const summary=document.createElement("p");summary.id=id+"-summary";summary.setAttribute("role","status");detail.before(summary);}}
  const preview=document.createElement("button");preview.type="button";preview.className="secondary";preview.id="source-preview";preview.textContent="查看处理后的原文";$("processRun").after(preview);const body=document.createElement("div");body.id="source-preview-text";body.className="document-content";($("processResult").closest("details")||$("processResult")).after(body);
  preview.onclick=()=>sourcePreview();
  $("processItem").onchange=()=>{sourcePreviewRequest++;lastJob=null;show("source-preview-text","");show("processResult","");sourceSaveDraft()};
  for(const id of ["upload","confirmGroups","applyIngest","processPlan","processRun"]){const button=$(id),handler=button.onclick;button.onclick=async event=>{button.disabled=true;try{await handler(event)}finally{button.disabled=false}};}
  document.addEventListener("input",sourceSaveDraft);document.addEventListener("change",sourceSaveDraft);
 }
 for(const [id,rows,key] of [["uploadWork",works,"work_id"],["processItem",items,"item_id"]]){const select=$(id),previous=select.value;select.innerHTML='<option value="">请选择</option>'+rows.map(row=>`<option value="${sourceEscape(row[key])}">${sourceEscape(row.name||row[key])}</option>`).join("");if(rows.some(row=>row[key]===previous))select.value=previous;}
 let cards=$("source-library-cards");if(!cards){cards=document.createElement("div");cards.id="source-library-cards";cards.className="grid";$("catalog").closest("details").before(cards);}
 cards.innerHTML=items.map(item=>`<article class="card"><h3>${sourceEscape(item.name)}</h3><p>${sourceEscape(works.find(work=>work.work_id===item.work_id)?.name||"")}</p><p class="muted">${item.normalization_sha256?"已有处理后的原文":"等待处理"}</p><button data-process-source="${sourceEscape(item.item_id)}">打开资料</button></article>`).join("")||'<p class="empty">尚无导入资料。先登记所属作品，再选择文件。</p>';
 cards.querySelectorAll("[data-process-source]").forEach(button=>button.onclick=()=>{$("processItem").value=button.dataset.processSource;sourceSelectPanel("资料处理")});
 if(!sourceEnhance.restored){
  sourceEnhance.restored=true;
  try{const saved=JSON.parse(localStorage.getItem(sourceDraftKey)||"null");if(saved){lastBatch=saved.batch;lastJob=saved.job;if(saved.plan)sourceRenderGroups(saved.plan);for(const [id,value] of Object.entries(saved.fields||{})){const field=$(id);if(field){if(field.type==="checkbox")field.checked=value;else field.value=value}}}}catch{}
  sourceSelectPanel("原著资料库");sourceSaveDraft();
 }
}

function sourceSummarize(id,value){
 const summary=$(id+"-summary");if(!summary)return;
 if(typeof value==="string"){summary.textContent=value;return;}
 summary.textContent=value.schema==="source_processing_job_v1"?"处理任务已准备，可以运行。":value.schema==="source_normalization_manifest_v1"?"处理记录已生成，请查看证据摘录与详细记录。":value.status==="applied"?"资料已导入，可以打开资料并处理。":value.approved_group_count?`已确认 ${value.approved_group_count} 个分组，下一步导入。`:value.schema==="source_ingest_batch_v1"?"上传完成，请核对下方文件分组。":"记录已更新，请查看详情。";
}
