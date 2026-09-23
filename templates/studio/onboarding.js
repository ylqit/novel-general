// Owns the creation form's navigation, local draft lifecycle and final review.
export function configureOnboarding(form, {setMode, sourceRow, readSources, refreshRequirements, notify}) {
  const key = "studio:new-project-wizard:v1";
  const audienceDefaults = {qidian:"起点中文网长篇读者", fanqie:"番茄小说长篇读者", general_cn:"中文长篇读者"};
  const labels = {title:"暂定书名", slug:"项目标识", target_platform:"目标平台", target_audience:"目标读者", writing_style:"视角与风格", core_promise:"核心阅读承诺", main_question:"全书问题", ending_direction:"结局方向", forbidden_experience:"禁止体验", target_total_characters:"目标总字数", chapter_target_characters:"单章字数", volume_target_characters:"单卷字数", planning_horizon:"详细规划跨度", refill_threshold:"规划补充阈值"};
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  let step = 0, audienceEdited = false, submitting = false;
  form.noValidate = true;
  const panels = ["作品方向", "故事要求", "原著资料", "复核创建"].map((title, i) => {
    const panel = document.createElement("section");
    panel.dataset.step = i;
    panel.className = "onboarding-step";
    panel.innerHTML = `<h3 tabindex="-1">${title}</h3>`;
    form.append(panel);
    return panel;
  });
  const advanced = document.createElement("details");
  advanced.innerHTML = '<summary>高级设置：项目标识、篇幅与规划参数</summary><div class="form-grid"></div>';
  panels[3].append(advanced);
  for (const [name] of Object.entries(labels)) {
    const field = form.elements.namedItem(name).closest(".field");
    if (["title","target_platform","target_audience","writing_style"].includes(name)) panels[0].append(field);
    else if (["core_promise","main_question","ending_direction","forbidden_experience"].includes(name)) panels[1].append(field);
    else advanced.lastElementChild.append(field);
  }
  const automation = form.elements.namedItem("automation_level");
  automation.closest(".field").hidden = true;
  form.append(automation.closest(".field"));
  panels[0].insertAdjacentHTML("beforeend", '<p class="muted">创作由你决定：生成候选后仍需校验、阅读与明确批准。</p>');
  panels[1].insertAdjacentHTML("afterbegin", '<p class="muted">写下这本书自己的方向。示例只是提示，不会替你填写或批准设计；创建后仍可通过开书构思任务细化。</p>');
  const sourcePanel = document.getElementById("fanfiction-fields");
  panels[2].append(sourcePanel);
  const preview = document.createElement("div");
  preview.className = "creation-review";
  panels[3].querySelector("h3").after(preview);
  const confirm = document.createElement("label");
  confirm.innerHTML = '<input id="creation-review-confirm" type="checkbox"> 我已核对以上实际内容，确认创建项目并开始开书。';
  panels[3].append(confirm);
  const steps = document.createElement("ol");
  steps.className = "wizard-progress";
  steps.setAttribute("aria-label", "开书步骤");
  form.prepend(steps);
  const bar = document.createElement("div");
  bar.className = "button-row";
  bar.innerHTML = '<button type="button" class="secondary" id="creation-back">上一步</button><button type="button" id="creation-next">下一步</button>';
  const submit = document.getElementById("create-submit");
  const cancel = submit.nextElementSibling;
  bar.append(submit, cancel);
  form.append(bar, document.getElementById("create-status"));
  form.querySelectorAll(":scope > .form-grid, :scope > hr, :scope > .button-row:empty").forEach(e => e.remove());
  const isFan = () => !form.elements.namedItem("continuity_mode").disabled;
  const order = () => isFan() ? [0,1,2,3] : [0,1,3];
  const mode = () => isFan() ? form.elements.namedItem("continuity_mode").value === "crossover" ? "crossover" : "fanfiction" : "original";
  function save() {
    if (submitting) return;
    confirm.firstElementChild.checked = false;
    const fields = Object.fromEntries([...form.querySelectorAll("[name]")].map(e => [e.name, e.value]));
    try { localStorage.setItem(key, JSON.stringify({fields, sources:readSources(), mode:mode(), step, audienceEdited})); }
    catch { notify("浏览器无法保存开书草稿，请保留重要内容。"); }
  }
  function review() {
    const fields = Object.entries(labels).map(([name,label]) => `<dt>${label}</dt><dd>${esc(form.elements.namedItem(name).tagName === "SELECT" ? form.elements.namedItem(name).selectedOptions[0]?.textContent : form.elements.namedItem(name).value)}</dd>`).join("");
    preview.innerHTML = `<dl class="kv">${fields}</dl>${isFan() ? `<h4>原著声明</h4>${readSources().map(s => `<p>${esc(s.title)} · ${esc(s.creator)} · ${esc(s.canon_cutoff)}</p><p class="small">${esc(s.rights_status)} / ${esc(s.retention_mode)} · ${s.commercial_intent?"商业用途":"非商业用途"}</p><p>${esc(s.allowed_elements.join("、"))} · ${esc(s.platform_policy_url || "未填写政策链接")}</p>`).join("")}` : ""}`;
  }
  function draw(focus = true) {
    const active = order();
    if (!active.includes(step)) step = 0;
    panels.forEach((panel,i) => panel.hidden = i !== step);
    document.querySelector(".mode-picker").hidden = step !== 0;
    steps.innerHTML = active.map((i,n) => `<li ${i===step?'aria-current="step"':""}>${n+1}. ${panels[i].querySelector("h3").textContent}</li>`).join("");
    document.getElementById("creation-back").hidden = step === 0;
    document.getElementById("creation-next").hidden = step === 3;
    submit.hidden = step !== 3;
    review();
    if (focus) panels[step].querySelector("h3").focus();
  }
  function validate(index) {
    for (const control of panels[index].querySelectorAll("input,textarea,select")) {
      if (control.disabled || control.id === "creation-review-confirm") continue;
      if (!control.checkValidity() || control.required && !control.value.trim()) {
        step = index; draw(false);
        const detail = control.closest("details"); if (detail) detail.open = true;
        control.focus(); control.reportValidity();
        document.getElementById("create-status").textContent = "请完成当前步骤的必填内容。";
        return false;
      }
    }
    return true;
  }
  document.getElementById("creation-next").onclick = () => {if(validate(step)){step=order()[order().indexOf(step)+1];save();draw();}};
  document.getElementById("creation-back").onclick = () => {step=order()[order().indexOf(step)-1];save();draw();};
  form.addEventListener("submit", event => {
    if (submitting || step !== 3 || !order().every(validate) || !confirm.firstElementChild.checked) {
      event.preventDefault(); event.stopImmediatePropagation();
      if (step === 3) document.getElementById("create-status").textContent = "请核对内容并勾选创建确认。";
      return;
    }
    submitting = true;
  }, true);
  // A failed request re-enables the submit button; preserve the current review.
  new MutationObserver(() => {if (!submit.disabled) submitting=false;}).observe(submit,{attributes:true,attributeFilter:["disabled"]});
  form.addEventListener("input", event => {if(event.target===confirm.firstElementChild)return;if(event.target===form.elements.namedItem("target_audience")) audienceEdited=true; save(); if(step===3)review();});
  form.addEventListener("change", event => {if(event.target!==confirm.firstElementChild)save();});
  form.elements.namedItem("target_platform").addEventListener("change", () => {if(!audienceEdited)form.elements.namedItem("target_audience").value=audienceDefaults[form.elements.namedItem("target_platform").value]; save();});
  try {
    const saved = JSON.parse(localStorage.getItem(key) || "null");
    if (saved?.fields) {
      setMode(saved.mode === "original" ? "original" : "fanfiction");
      for (const [name,value] of Object.entries(saved.fields)) {const field=form.elements.namedItem(name);if(field && typeof value==="string")field.value=value;}
      if (Array.isArray(saved.sources) && saved.sources.length) {
        const list=document.getElementById("source-list"); list.innerHTML="";
        for(const source of saved.sources){list.insertAdjacentHTML("beforeend",sourceRow());for(const field of list.lastElementChild.querySelectorAll("[data-source]")){const value=source[field.dataset.source];if(field.type==="checkbox")field.checked=Boolean(value);else if(value!==undefined)field.value=Array.isArray(value)?value.join("、"):value;}}
      }
      audienceEdited=Boolean(saved.audienceEdited); step=Number(saved.step)||0;
      refreshRequirements();
    }
  } catch { notify("上次开书草稿无法恢复，请重新填写。"); }
  // Consent is deliberately not restored from browser storage.
  document.getElementById("rights-ack").checked=false;
  if(!form.elements.namedItem("slug").value)form.elements.namedItem("slug").value="book-"+crypto.randomUUID().replaceAll("-","").slice(0,12);
  sourcePanel.querySelectorAll("input,select,textarea").forEach(control=>control.disabled=!isFan());
  function reflectMode(){document.querySelectorAll("[data-mode]").forEach(button=>{const active=button.dataset.mode===mode();button.classList.toggle("active",active);button.setAttribute("aria-pressed",String(active));});}
  form.addEventListener("modechange", () => {save();reflectMode();draw(false);});
  document.querySelector(".mode-picker").addEventListener("click",()=>{save();reflectMode();draw(false);});
  document.getElementById("add-source").addEventListener("click",save);
  form.elements.namedItem("continuity_mode").addEventListener("change",reflectMode);
  save();reflectMode();
  draw(false);
}
