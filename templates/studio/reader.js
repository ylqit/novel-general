// Author views only. Domain actions stay on the server and retain their approvals.
import {showVersions} from "./versions.js";
import {markdownView} from "./document_view.js";
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const readLocal = (key, fallback) => {try{return JSON.parse(localStorage.getItem(key)) ?? fallback}catch{return fallback}};
const saveLocal = (key, value) => {try{localStorage.setItem(key, JSON.stringify(value));return true}catch{return false}};
const labels = {final:"正式", draft:"草稿", planned:"计划"};
const base = id => `/api/projects/${id}`;

export async function renderBookReader({projectId, chapterNumber, app, get, post, notify}) {
  const query = new URLSearchParams(location.search);
  const scope = `studio:${projectId}`;
  const stored = readLocal(`${scope}:position`, {});
  const [catalogue, documentIndex, identity] = await Promise.all([get(`${base(projectId)}/catalogue`), get(`${base(projectId)}/documents`), get(`${base(projectId)}/identity`)]);
  document.getElementById("workspace").textContent=identity.title||"正文阅读";
  let selected = chapterNumber || stored.chapter || catalogue.chapters.find(c => c.status !== "planned")?.number;
  let preferences = readLocal("studio:reader-preferences", {dark:false, size:20, line:"1.9", sans:false, directory:true, discussion:true});
  const desktopPanels={directory:preferences.directory,discussion:preferences.discussion};
  let panelMode="";
  let current = null, edit = null, changed = false, composing = false, chapterRequest = 0;
  let selection = null;
  let catalogueRows = [...catalogue.chapters];
  app.className = "";
  app.innerHTML = `${catalogue.origin?.simulated_human?'<p class="notice warn">自动演练 · 人工步骤由测试流程模拟，文学质量未经真实人工验收。</p>':""}<div class="reader-toolbar"><a href="/projects/${projectId}">作品概览</a><span class="muted">/</span><strong class="reading-book-name">${esc(identity.title||"作品")}</strong><span id="reading-heading">正文阅读</span><button class="secondary" id="toggle-directory" aria-expanded="true">目录</button><button class="secondary" id="toggle-discussion" aria-expanded="true">讨论</button><button class="secondary" id="focus-reader">专注阅读</button><a class="button secondary" href="/projects/${projectId}/planning">查看规划</a></div>
  <div class="reader-shell"><aside class="card reader-directory" aria-label="卷章目录"><h3>作品目录</h3><div id="directory-issues"></div><nav id="chapter-directory"></nav><button id="more-chapters" class="secondary" hidden>加载更多章节</button><hr class="divider"><form id="reader-search"><label for="search-query">搜索正文与资料</label><input id="search-query" type="search" maxlength="120" required placeholder="人物、情节或关键词"><button class="secondary">搜索</button></form><div id="search-results" aria-live="polite"></div></aside>
  <section class="reader-center" aria-label="章节内容"><div class="reader-toolbar"><button id="read-mode" class="secondary" aria-pressed="true">阅读</button><button id="edit-mode" class="secondary" aria-pressed="false">修改</button><a id="review-mode" class="button secondary">审稿</a><select id="text-version" aria-label="正文版本"><option value="preferred">优先正式稿</option><option value="final">正式稿</option><option value="draft">已提交草稿</option></select><button id="settings-toggle" class="secondary" aria-expanded="false">阅读设置</button></div><div class="card reader-settings" hidden><div class="form-grid"><label>正文主题<select id="reading-theme"><option value="light">浅色正文</option><option value="dark">深色正文</option></select></label><label>字号<select id="reading-size">${[18,20,22,24,26].map(n=>`<option>${n}</option>`).join("")}</select></label><label>行距<select id="reading-line"><option>1.7</option><option>1.9</option><option>2.1</option></select></label><label>字体<select id="reading-font"><option value="serif">宋体 / 衬线</option><option value="sans">黑体 / 无衬线</option></select></label></div></div><p id="source-state" class="muted small"></p><div id="manuscript" class="manuscript"><article class="manuscript-inner" id="prose"></article></div><section id="editor-panel" hidden><p id="edit-state" role="status"></p><textarea id="reader-editor" class="reader-editor" aria-label="本章修改稿" spellcheck="false"></textarea><div class="button-row"><button id="save-edit">保存修改稿</button><button id="compare-edit" class="secondary">查看原稿与修改稿</button></div><details id="edit-diff"><summary>修改对照</summary><div class="form-grid"><pre class="candidate" id="before-edit"></pre><pre class="candidate" id="after-edit"></pre></div></details></section><nav class="reader-footer" aria-label="连续阅读"><button class="secondary" id="previous-chapter">上一章</button><button class="secondary" id="next-chapter">下一章</button></nav></section>
  <aside class="card discussion" aria-label="创作讨论"><h3>创作讨论</h3><p class="small muted" id="discussion-context">先选择讨论范围与参考内容。</p><label>讨论范围<select id="discussion-scope"><option value="project">整部作品</option><option value="volume">当前卷</option><option value="chapter" selected>当前章节</option><option value="selection">选中段落</option></select></label><blockquote id="selection-quote" class="selection-quote" hidden></blockquote><button id="cite-selection" class="secondary">引用选中原文</button><label for="discussion-question">你的问题</label><textarea id="discussion-question" placeholder="例如：这段对白是否解释得太满？"></textarea><div class="button-row"><button id="ask-advisor">提交讨论</button><button id="save-memo" class="secondary">保存为备忘</button></div><p id="discussion-state" role="status"></p><div id="discussion-history"></div></aside></div>`;
  const $ = id => document.getElementById(id);
  const clearReference=document.createElement("button");clearReference.className="secondary";clearReference.textContent="移除引用";
  $("selection-quote").after(clearReference);
  clearReference.onclick=()=>{selection=null;localStorage.removeItem(`${scope}:reference`);$("selection-quote").hidden=true;if($("discussion-scope").value==="selection")$("discussion-scope").value="chapter"};
  const materials=document.createElement("details");materials.innerHTML=`<summary>选择参考资料</summary><div class="reference-picker">${documentIndex.documents.map(d=>`<label><input type="checkbox" value="${d.id}">${esc(d.title)}</label>`).join("")}</div>`;
  $("discussion-question").before(materials);
  const questionDraft=readLocal(`${scope}:discussion-draft`,null);
  if(questionDraft){$("discussion-question").value=questionDraft.question;$("discussion-scope").value=questionDraft.scope;materials.querySelectorAll("input").forEach(input=>input.checked=questionDraft.documents?.includes(input.value))}
  const keepQuestion=()=>saveLocal(`${scope}:discussion-draft`,{question:$("discussion-question").value,scope:$("discussion-scope").value,documents:Array.from(materials.querySelectorAll("input:checked"),input=>input.value)});
  $("discussion-question").addEventListener("input",keepQuestion);$("discussion-scope").addEventListener("change",keepQuestion);materials.addEventListener("change",keepQuestion);
  const compareLatest=document.createElement("button");compareLatest.className="secondary";compareLatest.textContent="处理保存冲突";
  $("save-edit").after(compareLatest);
  compareLatest.onclick=async()=>{try{
    const n=selected;const [source,saved]=await Promise.all([get(`${base(projectId)}/chapters/${n}/text`),get(`${base(projectId)}/chapters/${n}/edit-draft`)]);
    if(n!==selected)return;
    $("before-edit").textContent=saved.revision?saved.text:source.text;$("after-edit").textContent=$("reader-editor").value;$("edit-diff").open=true;
    let confirm=$("confirm-rebase");if(!confirm){confirm=document.createElement("button");confirm.id="confirm-rebase";confirm.textContent="已比较，保留我的文字并重新保存";$("edit-diff").append(confirm)}
    confirm.onclick=async()=>{try{
      if(n!==selected)throw Error("章节已切换，请重新比较。");
      const result=await post(`${base(projectId)}/chapters/${n}/edit-draft`,{revision:saved.revision,source_sha256:source.sha256,text:$("reader-editor").value});
      edit=result.result;current=source;changed=false;localStorage.removeItem(`${scope}:edit:${n}`);renderProse(source.text);$("save-edit").disabled=false;$("edit-state").textContent="已按刚刚比较的版本保存。";confirm.remove();
    }catch(e){report(e)}};
  }catch(e){report(e)}};
  const shell = app.querySelector(".reader-shell");
  function applyPreferences() {
    preferences.panels??={};
    preferences.panels[panelMode]={directory:preferences.directory,discussion:preferences.discussion};
    if(!preferences.directory&&document.activeElement?.closest('.reader-directory'))$("toggle-directory").focus();
    if(!preferences.discussion&&document.activeElement?.closest('.discussion'))$("toggle-discussion").focus();
    shell.classList.toggle("hide-directory", !preferences.directory);
    shell.classList.toggle("hide-discussion", !preferences.discussion);
    $("toggle-directory").setAttribute("aria-expanded", String(preferences.directory));
    $("toggle-discussion").setAttribute("aria-expanded", String(preferences.discussion));
    $("manuscript").classList.toggle("dark", preferences.dark);
    $("manuscript").classList.toggle("sans", preferences.sans);
    // CSSOM declarations contain only validated numeric preferences.
    $("manuscript").style.setProperty("--reading-size", `${[18,20,22,24,26].includes(Number(preferences.size)) ? preferences.size : 20}px`);
    $("manuscript").style.setProperty("--reading-line", ["1.7","1.9","2.1"].includes(preferences.line) ? preferences.line : "1.9");
    $("reading-theme").value = preferences.dark ? "dark" : "light";
    $("reading-size").value = preferences.size;
    $("reading-line").value = preferences.line;
    $("reading-font").value = preferences.sans ? "sans" : "serif";
    saveLocal("studio:reader-preferences", preferences);
  }
  function drawDirectory() {
    $("directory-issues").innerHTML = catalogue.issues.map(t=>`<p class="small notice warn">${esc(t)}</p>`).join("");
    const link = c => `<a class="chapter-link" data-chapter="${c.number}" href="/projects/${projectId}/chapters/${c.number}" ${c.number===selected?'aria-current="page"':""}><span class="badge">${labels[c.status]}</span>${esc(c.title)}</a>`;
    $("chapter-directory").innerHTML = catalogue.volumes.map(v=>`<details open><summary>${esc(v.title)} <span class="small muted">${v.chapter_range.join("–")}</span></summary>${catalogueRows.filter(c=>c.volume_id===v.volume_id).map(link).join("")||'<p class="small muted">本页尚无章节；可继续加载目录</p>'}</details>`).join("") + catalogueRows.filter(c=>!c.volume_id).map(link).join("");
    $("more-chapters").hidden = catalogue.next_offset === null;
    $("chapter-directory").querySelectorAll("a").forEach(a=>a.addEventListener("click",e=>{e.preventDefault();openChapter(Number(a.dataset.chapter)).catch(report)}));
  }
  function report(error) {notify(error.message || String(error));}
  function persistLocal() {
    if (!current || !changed || composing) return;
    if (!saveLocal(`${scope}:edit:${selected}`, {text:$("reader-editor").value, source_sha256:current.sha256, revision:edit.revision})) {
      $("edit-state").textContent="浏览器存储不可用，请保存修改稿后离开。";
    }
  }
  function renderProse(text, highlight) {
    const article = $("prose"); article.replaceChildren();
    const chunks = [...text.matchAll(/[^\n]+(?:\n(?!\n)[^\n]+)*/g)];
    for (const match of chunks) {
      const raw = match[0], heading = /^#{1,3} /.test(raw);
      const node = document.createElement(heading ? "h1" : "p");
      const prefix = heading ? raw.match(/^#+ /)[0].length : 0;
      node.dataset.start = match.index + prefix;
      node.className = "scroll-anchor";
      const display = raw.slice(prefix);
      const start = highlight ? highlight.start - Array.from(text.slice(0,match.index + prefix)).length : -1;
      const end = highlight ? highlight.end - Array.from(text.slice(0,match.index + prefix)).length : -1;
      const points = Array.from(display);
      if (start >= 0 && end <= points.length) {
        node.append(document.createTextNode(points.slice(0,start).join("")));
        const mark = document.createElement("mark"); mark.textContent=points.slice(start,end).join(""); node.append(mark,document.createTextNode(points.slice(end).join("")));
      } else node.textContent = display;
      article.append(node);
    }
  }
  async function openChapter(n, highlight) {
    persistLocal();
    const request = ++chapterRequest;
    const oldPosition = readLocal(`${scope}:position`, {});
    const [text, draft] = await Promise.all([get(`${base(projectId)}/chapters/${n}/text?version=${$("text-version").value}`),get(`${base(projectId)}/chapters/${n}/edit-draft`)]);
    if (request !== chapterRequest) return;
    selected = n; current=text; edit=draft; changed=false;
    selection=readLocal(`${scope}:reference`,null);
    $("selection-quote").hidden=!selection;
    if(selection)$("selection-quote").textContent=`第 ${selection.chapter_number} 章：${selection.quote}`;
    $("reading-heading").textContent=`第 ${n} 章`;
    $("review-mode").href=`/projects/${projectId}/chapters/${n}?mode=review`;
    $("source-state").textContent=text.available ? `${labels[text.version]} · ${text.characters.toLocaleString()} 字${text.closed ? " · 已关闭" : ""}${text.closure_issue ? ` · ${text.closure_issue}` : ""}` : "本章尚无这个版本的正文，可在规划页查看创作安排。";
    $("discussion-context").textContent=`当前阅读：第 ${n} 章${text.available ? ` · ${labels[text.version]}` : " · 尚无正文"}`;
    renderProse(text.text || "尚无正文。");
    const local=readLocal(`${scope}:edit:${n}`, null);
    if (local) {edit={...draft,revision:local.revision}; changed=true;}
    $("reader-editor").value=local?.text ?? (draft.revision ? draft.text : text.text);
    $("edit-state").textContent=local ? "已恢复本机未提交修改。" : draft.revision ? `已载入修改稿，第 ${draft.revision} 次保存。` : "修改稿单独保存，正式采用请进入审稿。";
    if ((local?.source_sha256 && local.source_sha256 !== text.sha256) || (draft.source_sha256 && draft.source_sha256 !== text.sha256)) {
      $("edit-state").textContent="正文来源已变化；已保留修改文字，请比较来源后重新处理。";
      $("save-edit").disabled=true;
    } else $("save-edit").disabled=!text.available;
    $("previous-chapter").disabled=!text.previous; $("next-chapter").disabled=!text.next;
    $("previous-chapter").onclick=()=>openChapter(text.previous).catch(report);
    $("next-chapter").onclick=()=>openChapter(text.next).catch(report);
    history.replaceState({},"",`/projects/${projectId}/chapters/${n}`);
    drawDirectory();
    let versions=$("chapter-versions");
    if(!versions){versions=document.createElement("section");versions.id="chapter-versions";versions.className="card";app.querySelector(".reader-footer").before(versions)}
    await showVersions({container:versions,projectId,chapter:n,get});
    if (highlight && highlight.sha256 !== text.sha256) notify("搜索结果来源已变化，请重新搜索。");
    else if (highlight) {renderProse(text.text,highlight);requestAnimationFrame(()=>$("prose").querySelector("mark")?.scrollIntoView({block:"center"}));}
    else if (oldPosition.chapter===n && oldPosition.sha256===text.sha256) requestAnimationFrame(()=>window.scrollTo(0,oldPosition.scroll || 0));
    else window.scrollTo(0,0);
    saveLocal(`${scope}:position`,{chapter:n,sha256:text.sha256,scroll:oldPosition.chapter===n ? oldPosition.scroll : 0});
  }
  function resizePanels(){
    if(!shell.isConnected){window.removeEventListener('resize',resizePanels);return}
    const mode=innerWidth<=800?'phone':innerWidth<=1200?'tablet':'desktop';
    if(mode===panelMode)return;
    panelMode=mode;
    const panels=preferences.panels?.[mode]||(mode==='desktop'?desktopPanels:{directory:mode==='tablet',discussion:false});
    Object.assign(preferences,panels);applyPreferences();
  }
  drawDirectory();resizePanels();window.addEventListener('resize',resizePanels);
  $("more-chapters").onclick=async()=>{const button=$("more-chapters");if(button.disabled)return;button.disabled=true;try{const next=await get(`${base(projectId)}/catalogue?offset=${catalogue.next_offset}`);if(JSON.stringify(next.volumes)!==JSON.stringify(catalogue.volumes))throw Error("卷目录已变化，请刷新后继续加载。");catalogueRows.push(...next.chapters);catalogue.next_offset=next.next_offset;drawDirectory()}catch(e){report(e)}finally{button.disabled=false}};
  $("toggle-directory").onclick=()=>{preferences.directory=!preferences.directory;applyPreferences()};
  $("toggle-discussion").onclick=()=>{preferences.discussion=!preferences.discussion;applyPreferences()};
  $("focus-reader").onclick=()=>{const active=preferences.directory||preferences.discussion;preferences.directory=!active;preferences.discussion=!active;applyPreferences()};
  $("settings-toggle").onclick=()=>{const box=app.querySelector(".reader-settings");box.hidden=!box.hidden;$("settings-toggle").setAttribute("aria-expanded",String(!box.hidden))};
  for(const id of ["reading-theme","reading-size","reading-line","reading-font"]) $(id).onchange=()=>{preferences={...preferences,dark:$("reading-theme").value==="dark",size:Number($("reading-size").value),line:$("reading-line").value,sans:$("reading-font").value==="sans"};applyPreferences()};
  $("text-version").onchange=()=>openChapter(selected).catch(report);
  function setMode(editing) {$("manuscript").hidden=editing;$("editor-panel").hidden=!editing;$("read-mode").setAttribute("aria-pressed",String(!editing));$("edit-mode").setAttribute("aria-pressed",String(editing));}
  $("read-mode").onclick=()=>setMode(false); $("edit-mode").onclick=()=>setMode(true);
  $("reader-editor").addEventListener("compositionstart",()=>{composing=true});
  $("reader-editor").addEventListener("compositionend",()=>{composing=false;changed=true;persistLocal()});
  $("reader-editor").addEventListener("input",()=>{changed=true;persistLocal();$("edit-state").textContent="有修改，已保留本机恢复副本。"});
  window.addEventListener("beforeunload",e=>{persistLocal();if(changed){e.preventDefault();e.returnValue=""}});
  window.addEventListener("scroll",()=>{if(current)saveLocal(`${scope}:position`,{chapter:selected,sha256:current.sha256,scroll:window.scrollY})},{passive:true});
  window.addEventListener("storage",e=>{if(e.key===`${scope}:edit:${selected}`)$("edit-state").textContent="另一标签页修改了恢复副本；当前输入保持原样，保存时将核对版本。"});
  $("save-edit").onclick=async()=>{try{const response=await post(`${base(projectId)}/chapters/${selected}/edit-draft`,{revision:edit.revision,source_sha256:current.sha256,text:$("reader-editor").value});edit=response.result;changed=false;localStorage.removeItem(`${scope}:edit:${selected}`);$("edit-state").textContent=`修改稿已保存，第 ${edit.revision} 次。`;}catch(e){$("edit-state").textContent=e.message;persistLocal()}};
  $("compare-edit").onclick=()=>{$("before-edit").textContent=current?.text || "";$("after-edit").textContent=$("reader-editor").value;$("edit-diff").open=true};
  $("cite-selection").onclick=()=>{
    const s=window.getSelection(); if(!current?.available||!s?.rangeCount||s.isCollapsed){notify("请先在正文中选中需要讨论的原文。");return}
    const range=s.getRangeAt(0), article=$("prose");
    if(!article.contains(range.startContainer)||!article.contains(range.endContainer)){notify("请选择同一章正文中的文字。");return}
    const point=(container,offset)=>{const block=(container.nodeType===Node.ELEMENT_NODE?container:container.parentElement).closest("[data-start]");if(!block)return null;const before=document.createRange();before.selectNodeContents(block);before.setEnd(container,offset);return Array.from(current.text.slice(0,Number(block.dataset.start))).length+Array.from(before.toString()).length};
    const start=point(range.startContainer,range.startOffset),end=point(range.endContainer,range.endOffset);
    if(start===null||end===null||end<=start)return;
    selection={chapter_number:selected,start,end,quote:Array.from(current.text).slice(start,end).join(""),sha256:current.sha256,version:current.version};
    $("selection-quote").textContent=selection.quote;$("selection-quote").hidden=false;$("discussion-scope").value="selection";
    saveLocal(`${scope}:reference`,selection);
  };
  const savedReference=readLocal(`${scope}:reference`,null);
  if(savedReference){selection=savedReference;$("selection-quote").textContent=`第 ${savedReference.chapter_number} 章：${savedReference.quote}`;$("selection-quote").hidden=false;}
  $("save-memo").onclick=async()=>{const question=$("discussion-question").value.trim();if(!question){notify("先写下讨论内容。");return}try{await post(`${base(projectId)}/notes`,{title:question.slice(0,60),text:`讨论范围：${$("discussion-scope").selectedOptions[0].textContent}${selected?` · 第 ${selected} 章`:""}\n\n${question}${selection?`\n\n原文引用（第 ${selection.chapter_number} 章，版本 ${selection.sha256}）：\n${selection.quote}`:""}`});notify("备忘已保存，可在人与故事资料中查看。")}catch(e){notify(e.message)}};
  $("ask-advisor").onclick=()=>{keepQuestion();app.dispatchEvent(new CustomEvent("studio-discuss",{detail:{projectId,chapter:selected,scope:$("discussion-scope").value,selection,question:$("discussion-question").value,document_ids:Array.from(materials.querySelectorAll("input:checked"),input=>input.value)}}))};
  let searchEpoch=0;
  $("reader-search").onsubmit=async event=>{
    event.preventDefault();const epoch=++searchEpoch,query=$("search-query").value.trim(),box=$("search-results");box.textContent="正在搜索…";
    async function load(offset=0){
      try{const results=await get(`${base(projectId)}/search?q=${encodeURIComponent(query)}&offset=${offset}`);if(epoch!==searchEpoch)return;
        box.querySelector("[data-more-results]")?.remove();
        if(!offset)box.innerHTML=`<p class="small muted">找到 ${results.total} 处</p>`;
        for(const hit of results.results){const button=document.createElement("button");button.className="search-result";
          button.innerHTML=`${hit.kind==="chapter"?`第 ${hit.id} 章`:esc(documentIndex.documents.find(d=>d.id===hit.id)?.title||hit.title)}<br><span class="small">${esc(hit.excerpt)}</span>`;
          button.onclick=()=>{if(hit.kind==="chapter"){$("text-version").value="final";openChapter(hit.id,hit).catch(report)}else location.href=`/projects/${projectId}/knowledge?document=${hit.id}&start=${hit.start}&end=${hit.end}&sha256=${hit.sha256}`};box.append(button);
        }
        if(results.next_offset!==null){const more=document.createElement("button");more.dataset.moreResults="";more.className="secondary";more.textContent="加载更多搜索结果";more.onclick=()=>{more.disabled=true;load(results.next_offset)};box.append(more)}
        if(results.issues.length){const warning=document.createElement("p");warning.className="notice warn";warning.textContent="部分资料无法读取："+results.issues.join("；");box.append(warning)}
      }catch(error){if(epoch===searchEpoch){report(error);const retry=box.querySelector("[data-more-results]");if(retry)retry.disabled=false;}}
    }
    await load();
  };
  if(selected)await openChapter(selected, query.has("start") ? {start:Number(query.get("start")),end:Number(query.get("end")),sha256:query.get("sha256")} : null);
  else {$("prose").textContent="尚无章节。完成开书和规划后，这里会出现作品目录。";$("editor-panel").hidden=true;$("edit-mode").disabled=true;$("review-mode").hidden=true;}
  app.dispatchEvent(new CustomEvent("studio-reader-ready",{detail:{projectId}}));
}

const fieldNames={schema:"资料协议",title:"标题",summary:"摘要",items:"内容",chapter_range:"章节范围",volume_id:"卷",volume_goal:"本卷目标",entry_state:"入卷状态",exit_state:"卷末计划状态",character_arcs:"人物弧",chapter_duty:"章节职责",observable_change:"可观察变化",reader_value:"阅读价值",premise:"故事前提",central_conflict:"核心冲突",ending_boundary:"结局边界",goal:"目标",flaw:"缺陷",arc_stages:"成长阶段",source_id:"来源",source_ids:"适用来源",host_source_id:"宿主世界",payload_kinds:"跨界载荷",activation_condition:"生效条件",cost:"代价",limitations:"限制",local_countermeasures:"当地反制",perception_bias:"感知偏向",decision_habits:"决策习惯",dialogue_strategy:"对白策略",emotional_leakage:"情绪泄露",event_graph:"事件关系",nodes:"节点",edges:"关系",claims:"语义主张",text:"内容",description:"说明",source_refs:"来源依据",dependency_refs:"依赖依据",approved_by:"批准人",lifecycle:"规划状态",characters:"人物",name:"姓名",content:"内容",status:"状态",state:"实现状态",tiers:"规划层级",firm:"近期详细规划",directional:"中期方向",horizon:"远期展望",reader_expectation:"读者期待",expected_changes:"计划变化",reader_effect:"预期阅读影响",realization_evidence:"实现依据",chapter_digest:"本章事实摘要",causal_change:"实际因果变化",reader_payoff:"本章阅读回报",threads:"伏笔条目",plant_chapter:"计划埋设章节",payoff_window:"回收范围"};
Object.assign(fieldNames,{body:"内容说明",document_type:"资料类型",continuity:"连续性",uncertainties:"尚待确认",extensions:"适用规则与补充依据",crossover:"跨界安排",topology:"跨界模式",adapter:"适配器",adapters:"实际适配器",transfers:"跨界转移",default_host_source_id:"默认宿主世界",activation:"激活方式",replenishment:"补充方式",countermeasures:"反制方式",consequences:"后果",from_chapter:"开始章节",to_chapter:"结束章节",applicability:"适用范围",statement:"主张",allowed_elements:"使用范围"});
function structured(value,depth=0,field="",sourceNames={}) {
  if(value===null)return '<span class="muted">未记录</span>';
  if(["source_id","host_source_id","default_host_source_id"].includes(field)&&typeof value==="string")return esc(sourceNames[value]||`来源尚未登记（${value}）`);
  if(field==="topology")return esc({fixed_host:"固定宿主",fusion_world:"世界融合",sequential_worlds:"顺序诸天"}[value]||value);
  if(field==="payload_kinds"&&Array.isArray(value))return value.map(kind=>esc({character:"人物",ability:"能力",item:"物品",knowledge:"知识"}[kind]||kind)).join("、");
  if(typeof value!=="object"){const states={planned:"计划中",active:"当前有效",realized:"已有正文实现依据",cancelled:"已撤销",resolved:"已解决",superseded:"已被新版本替代",stale:"来源已变化",validated:"校验通过",applied:"已应用",pending:"待处理"};return esc(["status","state"].includes(field)?states[value]||value:value);}
  if(depth>7)return `<pre>${esc(JSON.stringify(value,null,2))}</pre>`;
  if(Array.isArray(value))return value.length?`<ol>${value.map(v=>`<li>${structured(v,depth+1,"",sourceNames)}</li>`).join("")}</ol>`:'<span class="muted">尚无条目</span>';
  const entries=Object.entries(value),technical=entries.filter(([key])=>!["source_id","host_source_id","default_host_source_id"].includes(key)&&/^(schema|schema_version|artifact|human_decision|id|source_refs|dependency_refs|evidence|provenance|created_at|updated_at)$|(_sha256|_hash|_path|_id|_ids)$/.test(key));
  const normal=entries.filter(([key])=>!technical.some(([name])=>name===key));
  return `<dl>${normal.map(([k,v])=>`<dt>${esc(fieldNames[k]||k)}</dt><dd>${structured(v,depth+1,k,sourceNames)}</dd>`).join("")}</dl>${technical.length?`<details><summary>来源、版本与协议详情</summary><pre>${esc(JSON.stringify(Object.fromEntries(technical),null,2))}</pre></details>`:""}`;
}
export async function renderDocuments({projectId,section,container,get}) {
  const {documents}=await get(`${base(projectId)}/documents`);
  const query=new URLSearchParams(location.search), requested=query.get("document");
  const rows=documents.filter(d=>d.group===section||requested===d.id||(section==="sources"&&d.group==="fanfiction")||(section==="knowledge"&&d.group==="notes"));
  container.innerHTML=`<div class="document-list"><div><label>查找资料<input id="document-filter" type="search" placeholder="人物、卷纲或章节"></label><nav aria-label="创作资料" id="document-index"></nav></div><section><h3 id="document-title">选择一份资料</h3><p class="muted small" id="document-basis"></p><article id="document-text" class="document-content"></article></section></div>`;
  let documentRequest=0;
  async function open(id) {
    const requestNumber=++documentRequest;
    const d=await get(`${base(projectId)}/documents/${id}`);
    if(requestNumber!==documentRequest)return;
    container.querySelector('.consequence-history')?.remove();
    container.querySelector("#document-title").textContent=d.title;
    container.querySelector("#document-basis").textContent=d.relative + (d.notice?" · "+d.notice:"");
    const article=container.querySelector("#document-text");article.classList.remove("graph-view");
    if(id===requested&&query.has("start")){
      if(query.get("sha256")!==d.sha256){article.textContent="搜索依据已变化，请重新搜索。\n\n"+d.text}
      else{const points=Array.from(d.text),start=Number(query.get("start")),end=Number(query.get("end"));article.replaceChildren(document.createTextNode(points.slice(0,start).join("")));const mark=document.createElement("mark");mark.textContent=points.slice(start,end).join("");article.append(mark,document.createTextNode(points.slice(end).join("")));requestAnimationFrame(()=>mark.scrollIntoView({block:"center"}))}
    }else if(d.format==="json"){try{const data=JSON.parse(d.text);if(d.relative==="30_state/story_graph.json"){
      const {renderGraphView}=await import("./graph_view.js");if(requestNumber!==documentRequest)return;renderGraphView(article,data);
    }else article.innerHTML=structured(data,0,"",d.source_names||{});}catch(error){article.replaceChildren();const warning=document.createElement("p");warning.className="notice warn";warning.textContent="资料视图无法展开，保留原文供核对："+error.message;const raw=document.createElement("pre");raw.textContent=d.text;article.append(warning,raw)}}else article.innerHTML=markdownView(d.text);
    if(d.consequence_history){
      const panel=document.createElement('section');panel.className='card consequence-history';
      const data=d.consequence_history;
      panel.innerHTML=`<h3>跨卷后果：规划与实际</h3><p class="muted">实际状态核对至第 ${data.through_chapter} 章正式正文；采用最近一次有证据的状态，解除后的后果不继续算作限制。</p>${data.issues.map(message=>`<p class="notice warn">证据不完整，暂不推断实际后果：${esc(message)}</p>`).join('')}${data.items.map(item=>`<section class="card"><h4>规划要求</h4><p>${esc(item.plan)}</p><details><summary>规划适用范围</summary>${structured(item.scope,0,'',d.source_names||{})}</details><h4>正文已证实的最新状态</h4>${item.actual?`<p>${esc(item.actual.value)}</p><blockquote>${esc(item.actual.evidence.excerpt)}</blockquote><a class="button secondary" href="/projects/${projectId}/chapters/${item.actual.chapter_number}?start=${item.actual.evidence.start}&end=${item.actual.evidence.end}&sha256=${item.actual.source.sha256}">阅读第 ${item.actual.chapter_number} 章证据</a>`:'<p class="muted">尚无正文实现依据，不能把规划当作已经发生。</p>'}</section>`).join('')}`;
      article.before(panel);
    }
    history.replaceState({},"",`${location.pathname}?document=${id}`);
  }
  let documentLimit=60;
  function drawIndex(){
    const q=container.querySelector("#document-filter").value.trim().toLocaleLowerCase(),visible=rows.filter(d=>(d.title+" "+d.relative).toLocaleLowerCase().includes(q));
    const nav=container.querySelector("#document-index");nav.innerHTML=visible.slice(0,documentLimit).map(d=>`<button class="document-button" data-document="${d.id}">${esc(d.title)}</button>`).join("")||'<p class="muted">没有匹配资料。</p>';
    nav.querySelectorAll("[data-document]").forEach(b=>b.onclick=()=>open(b.dataset.document).catch(e=>{container.querySelector("#document-text").textContent=e.message}));
    if(visible.length>documentLimit){const more=document.createElement("button");more.className="secondary";more.textContent="加载更多资料";more.onclick=()=>{documentLimit+=60;drawIndex()};nav.append(more)}
  }
  container.querySelector("#document-filter").oninput=()=>{documentLimit=60;drawIndex()};drawIndex();
  if(requested||rows[0])await open(requested||rows[0].id);
}

export async function renderOperations({projectId,section,container,get,post,notify}) {
  const endpoint=`/api/projects/${projectId}/${section}`;
  const state=await get(endpoint+"/state");
  if(section==="publication") {
    container.innerHTML=`<p class="muted">生成本机预检报告和发布材料，核对后再自行发布。</p>${state.origin.simulated_human?'<p class="notice warn">自动演练材料，不计入真实人工文学或平台验收。</p>':""}<label>目标平台<select id="publication-target"><option value="qidian_male">起点中文网</option><option value="fanqie_free">番茄小说</option></select></label><div class="button-row"><button id="publication-preflight">生成平台预检</button><button id="publication-export" class="secondary">导出本机发布材料</button></div><div id="operation-result" role="status"></div><details><summary>现有预检证据</summary><pre class="candidate">${esc(JSON.stringify(state.targets,null,2))}</pre></details><hr class="divider"><div id="publication-documents"></div>`;
    for(const action of ["preflight","export"]){const button=container.querySelector(`#publication-${action}`);button.onclick=async()=>{button.disabled=true;try{const result=await post(endpoint+"/action",{action,target:container.querySelector("#publication-target").value});container.querySelector("#operation-result").innerHTML=`<p class="notice">${action==="preflight"?"预检已生成，请查看实际待处理项。":state.origin.simulated_human?"自动演练阅读材料已生成，保留模拟来源说明；未通过真实人工文学或平台验收。":"发布材料已生成在本作品导出目录，请按预检待处理项核对。"}</p><details><summary>结果详情</summary><pre class="candidate">${esc(JSON.stringify(result.result,null,2))}</pre></details>`;await renderDocuments({projectId,section,container:container.querySelector("#publication-documents"),get})}catch(e){container.querySelector("#operation-result").textContent=e.message}finally{button.disabled=false}};}
    await renderDocuments({projectId,section,container:container.querySelector("#publication-documents"),get});return;
  }
  const choices={recoverable_rollback:{action:"rollback",label:"回滚未完成写入"},recoverable_discard:{action:"discard",label:"丢弃未应用准备"},recoverable_cleanup:{action:"cleanup",label:"清理已提交快照"}};
  const recoveryReason={transaction_never_reached_prepared_state:"准备过程已中断，尚未开始正式写入。",committed_snapshot_cleanup_required:"写入已经提交，尚有事务快照需要清理。",prepared_transaction_requires_rollback:"未完成写入的恢复依据已核对，可以恢复先前状态。",transaction_preparation_snapshot_missing_or_unsafe:"准备快照缺失或位置异常，需要核对恢复依据。",transaction_snapshot_path_unsafe:"快照位置异常，需要人工核对。",transaction_recovery_failed:"上次恢复未完成，请先核对失败记录。",transaction_report_invalid:"事务记录无法读取，请展开详情核对。",untrusted_transaction_inventory:"事务使用的旧记录不足以支持自动恢复。"};
  container.innerHTML=`<p class="notice ${state.blocked?"warn":""}">${state.blocked?"存在需要处理的运行状态。恢复前请核对目标和原因。":"没有待恢复的阻断，已有正文可正常阅读。"}</p><button id="refresh-recovery" class="secondary">重新检查恢复状态</button>${state.lock.state==="confirmed_dead"?'<div class="card"><p>检测到已确认退出的进程锁。</p><label><input id="lock-ack" type="checkbox"> 我确认回收当前已失去所属进程的锁</label><button id="reclaim-lock" disabled>回收失效锁</button></div>':""}<div id="recovery-items">${state.transactions.filter(r=>r.state!=="terminal").map((r,i)=>`<section class="card spaced-top"><h3>${esc(r.command||"未完成事务")}</h3><p>${esc(recoveryReason[r.reason]||"恢复依据需要核对，请展开详情查看。")}</p><details><summary>事务与证据详情</summary><pre class="candidate">${esc(JSON.stringify(r,null,2))}</pre></details>${choices[r.state]?`<label><input id="recovery-ack-${i}" type="checkbox"> 我已核对并确认${choices[r.state].label}</label><button data-recovery="${i}" disabled>${choices[r.state].label}</button>`:'<p class="notice warn">当前证据不足，不能自动恢复。</p>'}</section>`).join("")}</div><div id="operation-result" role="status"></div><details><summary>全部事务记录（${state.transactions.length}）</summary><pre class="candidate">${esc(JSON.stringify(state.transactions,null,2))}</pre></details>`;
  let pending=false;
  const updateRecoveryControls=()=>{container.querySelectorAll("[data-recovery]").forEach(button=>button.disabled=pending||!container.querySelector(`#recovery-ack-${button.dataset.recovery}`).checked);const reclaim=container.querySelector("#reclaim-lock");if(reclaim)reclaim.disabled=pending||!container.querySelector("#lock-ack").checked;container.querySelector("#refresh-recovery").disabled=pending;};
  container.querySelectorAll('input[type="checkbox"]').forEach(input=>input.addEventListener("change",updateRecoveryControls));
  const execute=async payload=>{if(pending)return;pending=true;updateRecoveryControls();try{await post(endpoint+"/action",payload);notify("恢复动作已完成并记录审计。");await renderOperations({projectId,section,container,get,post,notify})}catch(e){pending=false;container.querySelector("#operation-result").textContent=e.message;updateRecoveryControls()}};
  container.querySelector("#refresh-recovery").onclick=()=>renderOperations({projectId,section,container,get,post,notify}).catch(e=>notify(e.message));
  container.querySelector("#reclaim-lock")?.addEventListener("click",()=>execute({action:"reclaim_lock",id:"project_lock",expected_sha256:state.lock.sha256,acknowledge:container.querySelector("#lock-ack").checked}));
  container.querySelectorAll("[data-recovery]").forEach(b=>b.onclick=()=>{const i=Number(b.dataset.recovery),row=state.transactions.filter(r=>r.state!=="terminal")[i];return execute({action:choices[row.state].action,id:row.id,expected_sha256:row.sha256,acknowledge:container.querySelector(`#recovery-ack-${i}`).checked})});
}
