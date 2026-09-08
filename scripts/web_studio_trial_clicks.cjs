/* User-authorized automatic rehearsal. Every action uses the normal visible UI.
 * This script never fabricates prose, model reviews, final files or gate reports. */
const {captureBuildMetadata}=require('./web_test_metadata.cjs');const buildAtStart=captureBuildMetadata();
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const args=Object.fromEntries(process.argv.slice(2).reduce((rows,v,i,a)=>i%2?rows:[...rows,[v.replace(/^--/,''),a[i+1]]],[]));
const {chromium}=require(args.playwright),root=path.resolve(args.evidence);
const server=JSON.parse(fs.readFileSync(path.join(root,'server.json'),'utf8'));
if(!server.automated_rehearsal||server.fixture)throw Error('Trial requires a dedicated automatic-rehearsal workspace without synthetic prose fixtures');
const run=path.join(root,'trial-'+Date.now());fs.mkdirSync(run);
const sessionFile=path.join(root,'browser-session.json'),projectFile=path.join(root,'trial-project.json');
(async()=>{
 const saved=fs.existsSync(sessionFile)?JSON.parse(fs.readFileSync(sessionFile,'utf8')):null;
 const browser=await chromium.launch({executablePath:args.browser,headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1100},locale:'zh-CN',...(saved?.pid===server.pid?{storageState:saved.state}:{})});
 await context.tracing.start({screenshots:true,snapshots:true,sources:true});const page=await context.newPage(),steps=[],errors=[];
 page.setDefaultTimeout(60000);page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text()+' '+m.location().url)});page.on('dialog',dialog=>dialog.accept());
 async function navigate(button){
  const loaded=page.waitForEvent('domcontentloaded',{timeout:20*60*1000});loaded.catch(()=>{});
  const response=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().startsWith(server.base_url),{timeout:20*60*1000});response.catch(()=>{});
  await button.click();const actual=await response;
  assert.ok(actual.ok(),`网页动作返回 HTTP ${actual.status()}`);
  await Promise.race([loaded,page.locator('#action-status .danger').waitFor().then(async()=>{throw Error(await page.locator('#action-status').innerText())})]);await page.waitForLoadState('networkidle');
  await page.getByRole('heading',{name:'借火入山',exact:true}).waitFor();
 }
 async function record(action,details={}){steps.push({action,simulated_human:true,time:new Date().toISOString(),...details});fs.writeFileSync(path.join(run,'steps.json'),JSON.stringify(steps,null,2));console.log(action);}
 async function writeDecision(decision){
  if(typeof decision!=='string'||!decision.includes('本记录由已授权的隔离测试流程模拟填写'))throw Error('A decision must carry the explicit simulated-human provenance');
  if(!await page.locator('#design-editor').getAttribute('open'))await page.locator('#design-editor > summary').click();
  const original=await page.locator('#design-text').inputValue(),start=original.search(/^##\s*人工决定/m);
  assert.ok(start>=0,'Candidate must have its normal human decision section');assert.ok(original.length>100);
  const tail=original.slice(start+1),next=tail.search(/^##\s/m);
  const revised=original.slice(0,start)+'## 人工决定\n\n'+decision.trim()+'\n'+(next>=0?'\n'+tail.slice(next):'');
  assert.notEqual(revised,original);await page.locator('#design-text').fill(revised);await navigate(page.locator('#design-save'));
  assert.ok(await page.locator('[data-approve-task]').count());await record('通过设计表单记录模拟人工选择并保存独立候选版本');
 }
 try{
  await page.goto(saved?.pid===server.pid?server.base_url:server.bootstrap_url,{waitUntil:'networkidle'});
  assert.match(await page.locator('#app').innerText(),/自动演练/);
  if(args.phase==='create'){
   if(fs.existsSync(projectFile))throw Error('The trial project already exists; resume it');
   await page.getByRole('navigation',{name:'工作区',exact:true}).getByRole('link',{name:'创建小说',exact:true}).click();
   const fields={title:'借火入山',slug:'jiehuo-rushan',target_audience:'喜欢玄幻穿越、宗门底层成长与有限能力的中文长篇读者',writing_style:'第三人称有限视角，以陆照的感知为主。克制解释腔；人物用行动、回避和对白策略表达立场。',core_promise:'现代人陆照穿越成小宗门炉房杂役，面临炉毁追责。以有限的余火感知和有代价的行动争取生存、修炼与同伴信任。天赋必须接触实物，受污染与经验不足影响，只能辅助验材或短时护身，过用伤及经脉；不能替代修为，其他人物拥有利益、怀疑与拒绝权。',main_question:'一个凭残留火息辨材的炉房杂役，如何从追责中活下来，并在不把别人当工具的前提下取得进入山门深处的资格？开篇方向：第一章身份危机与追责；第二章一次有后果的天赋误判；第三章与同门协商；第四章外出任务挑战判断；第五章解决当前一项危机并承担新的关系与责任。第五章不结束全书。',ending_direction:'长篇最终完成炉火来源与宗门责任的主线，陆照付出真实代价，获得自己选择道路的能力。前五章只作为长篇开篇，不压缩成完整短篇。',forbidden_experience:'旁人无理由信任主角或降智\n穿越知识直接替代修为\n能力没有代价与反制\n重复解释、空泛升华与整齐套话\n第五章强行完成全书',target_total_characters:'600000',chapter_target_characters:'3000',volume_target_characters:'90000',planning_horizon:'20',refill_threshold:'8'};
   for(const [name,value] of Object.entries(fields))await page.locator(`[name="${name}"]`).fill(value);
   await page.getByRole('button',{name:'确认创建并开书',exact:true}).click();await page.getByRole('heading',{name:'借火入山',exact:true}).waitFor();
   fs.writeFileSync(projectFile,JSON.stringify({run_id:server.run_id,title:'借火入山',project_path:new URL(page.url()).pathname,simulated_human:true,formal_literary_eligible:false,created_at:new Date().toISOString(),requested_chapters:5,requested_chinese_characters_per_chapter:[2500,3500]},null,2));
   await record('网页创建原创玄幻穿越自动演练项目');
  }else{
   const project=JSON.parse(fs.readFileSync(projectFile,'utf8'));
   await page.locator(`a[href="${project.project_path}"]`).click();await page.getByRole('heading',{name:'借火入山',exact:true}).waitFor();
   if(args.phase==='cancel'){
    const cancel=page.locator('[data-cancel-job]');assert.equal(await cancel.count(),1,'Cancel only the single active job');
    const id=await cancel.getAttribute('data-cancel-job');await navigate(cancel);
    await page.waitForFunction(id=>{const b=document.querySelector(`[data-cancel-job="${id}"]`);return !b},id,{timeout:120000});
    await page.reload({waitUntil:'networkidle'});await page.locator('#job-list .job').first().waitFor();
    const status=await page.locator('#job-list .job').first().locator('.badge').getAttribute('data-status');
    assert.equal(status,'cancelled');await record('通过网页明确取消当前任务，未采用生成内容',{job_id:id});
   }else if(args.phase==='rebuild-ideation'){
    await navigate(page.locator('#rebuild-ideation'));await page.locator('[data-start-job]').waitFor();
    await record('通过网页重建当前开书轮次，保留此前工作单和候选');
   }else if(args.phase==='design-decision'){
    await writeDecision(fs.readFileSync(args['decision-file'],'utf8'));
   }else if(args.phase==='design-revision'){
    const text=fs.readFileSync(args['design-file'],'utf8');assert.match(text,/隔离测试流程模拟|测试执行器模拟人类/);
    if(!await page.locator('#design-editor').getAttribute('open'))await page.locator('#design-editor > summary').click();
    assert.ok(text.length>100);assert.notEqual(text,await page.locator('#design-text').inputValue());
    await page.locator('#design-text').fill(text);await navigate(page.locator('#design-save'));await page.locator('[data-approve-task]').waitFor();
    await record('通过网页保存宿主模型起草、模拟人工填写的完整设计修改稿');
   }else if(args.phase==='intent'){
    const intent=JSON.parse(fs.readFileSync(args['intent-file'],'utf8'));
    if(!Number.isInteger(intent.chapter_number)||intent.chapter_number<1||intent.chapter_number>5)throw Error('Only the authorized first five chapters may receive rehearsal intent');
    const link=page.getByRole('link',{name:'编辑本章人工意图',exact:true});assert.ok((await link.getAttribute('href')).includes(`/chapters/${intent.chapter_number}?intent=1`));
    await link.click();await page.locator('#intent-create, #intent-form').first().waitFor();
    if(await page.locator('#intent-create').count()){await page.locator('#intent-create').click();await page.locator('#intent-form').waitFor();}
    for(const name of ['story_intent','key_character_choice','emotional_truth','pov_voice_intent']){assert.ok(typeof intent[name]==='string'&&intent[name].trim());await page.locator(`#intent-form [name="${name}"]`).fill(intent[name]);}
    const pov=page.locator('#intent-pov option').filter({hasText:/^陆照(?:（本章参与）)?$/});assert.equal(await pov.count(),1,'Choose the explicit named POV, never the first character');
    await page.locator('#intent-pov').selectOption(await pov.getAttribute('value'));await page.locator('#intent-scene').fill(intent.scene_kind||'');
    await page.locator('#intent-protected').fill(intent.protected_items.join('\n'));await page.getByRole('button',{name:'保存并校验草稿',exact:true}).click();
    await page.locator('#intent-result').getByText('草稿已保存，校验通过。',{exact:true}).waitFor();
    await page.locator('#intent-confirm').check();const applied=page.waitForResponse(r=>r.url().endsWith('/intent/apply')&&r.request().method()==='POST');
    await page.locator('#intent-apply').click();assert.equal((await applied).status(),200);await page.locator('#intent-current-state').getByText(/已有批准意图有效/).waitFor();
    await page.getByRole('link',{name:'作品概览',exact:true}).click();await page.getByRole('heading',{name:'借火入山',exact:true}).waitFor();await record('通过正常网页表单模拟填写、校验和批准本章意图',{chapter_number:intent.chapter_number});
   }else if(args.phase==='planning'){
    await page.getByRole('link',{name:'总纲与规划',exact:true}).click();
    await page.locator('#planning-job-status').waitFor({state:'attached'});
    if(args.rebuild==='true'){const execute=page.locator('#planning-execute'),before=await execute.count()?await execute.getAttribute('data-task-id'):null;await page.locator('[data-planning="rebuild"]').click();await page.waitForFunction(before=>{const button=document.getElementById('planning-execute');return button&&button.dataset.taskId!==before},before);await page.locator('#planning-execute').waitFor();await record('通过正常失败入口重建规划，保留旧候选并绑定当前模板');}
    for(let index=0;index<Number(args.actions||6);index++){
     const create=page.locator('[data-planning="create"]'),execute=page.locator('#planning-execute'),approve=page.locator('#planning-approve');
     if(await page.getByText('本轮规划已批准并应用。',{exact:true}).count()){await record('当前规划已应用');break;}
     if(await create.count()){await create.click();await page.locator('#planning-execute').waitFor();await record('通过网页准备正常规划任务');}
     else if(await execute.count()){
      const executingId=await execute.getAttribute('data-task-id');if(await execute.isEnabled())await execute.click();let previous='',finished=false;const deadline=Date.now()+30*60*1000;
      while(Date.now()<deadline){const status=await page.locator('#planning-job-status').innerText();if(status!==previous){console.log(status);previous=status;}const outcome=await page.locator('#planning-result').innerText();if(/失败|未通过|无效|不存在|stale|missing|invalid|budget_exceeded|不可执行|已有运行|结构校验/.test(status+outcome))throw Error(status+' '+outcome);if(!status&&((await page.locator("#planning-execute").count()&&await page.locator("#planning-execute").getAttribute("data-task-id")!==executingId)||(await approve.count()&&await approve.isEnabled()))){finished=true;break;}await page.waitForTimeout(2000);}
      assert.ok(finished,'规划任务等待超时，不能计为完成');await record('真实规划任务执行与领域校验');
     }else if(await approve.count()&&await approve.isEnabled()){
      const rows=page.locator('#planning-approval [data-node-id]');
      for(const row of await rows.all()){await row.locator('[name="decision"]').selectOption('approve');await row.locator('[name="reason"]').fill('自动演练的模拟人工决定：保留当前节点所示行动与读者影响，用正文和独立审稿继续检验其落实情况。');}
      await page.locator('#planning-reason').fill('自动演练的模拟人工批准：以已通过独立证据审查的当前规划作为试写依据，保留能力限制、人物拒绝权和后果延续；这不是文学质量通过结论。');await page.locator('#planning-confirm').check();await approve.click();await page.getByText('本轮规划已批准并应用。',{exact:true}).waitFor();await record('模拟逐节点批准并通过正常事务应用规划');break;
     }else{await record('规划仍需处理',{state:await page.locator('#planning-result').innerText()});break;}
    }
   }else
   for(let index=0;index<Number(args.actions||1);index++){
    if(fs.existsSync(path.join(server.workspace,'jiehuo-rushan/30_state/chapter_closures/ch005.json'))){await record('第五章关闭记录已存在，本次不再启动任何章节任务');break;}
    const job=page.locator('#job-list .job').first();
    if(await job.count()&&/queued|running|cancelling/.test(await job.locator('.badge').getAttribute('data-status'))){
     let previous='';const deadline=Date.now()+30*60*1000;
     while(Date.now()<deadline){const status=await job.locator('.badge').getAttribute('data-status');if(status!==previous){console.log('模型任务：'+status);previous=status;}if(!/queued|running|cancelling/.test(status)){if(status!=='completed')throw Error(await job.innerText());break;}await page.waitForTimeout(2000);}
     assert.equal(await job.locator('.badge').getAttribute('data-status'),'completed');await record('真实模型任务完成');await page.reload({waitUntil:'networkidle'});await page.getByRole('heading',{name:'借火入山',exact:true}).waitFor();
    }
    const start=page.locator('[data-start-job]'),advance=page.locator('[data-advance]'),approve=page.locator('[data-approve-task]'),apply=page.locator('[data-apply-canonical]'),rebuild=page.locator('[data-rebuild-compile]');
    if(await rebuild.count()){await navigate(rebuild);await record('按正常失败路径重建设计编译任务，保留旧输出');}
    else if(await page.locator('[data-rebuild-review]').count()){await navigate(page.locator('[data-rebuild-review]'));await record('通过当前审稿失败入口重建任务，保留失败证据');}
    else if(await start.count()&&await start.isEnabled()){
     await record('点击执行当前真实模型任务',{task_id:await start.getAttribute('data-start-job')});await navigate(start);await page.locator('#job-list .job').first().waitFor();index-=1;
    }else if(await advance.count()){
     const before=await page.locator('.action-box').innerText();await navigate(advance);await page.waitForFunction(before=>{const box=document.querySelector('.action-box');return box&&box.textContent.trim()!==before.trim()},before);await record('点击正常控制面准备与校验');
    }else if(await page.locator('[data-record-review]').count()){
     const button=page.locator('[data-record-review]'),details=button.locator('..').locator('details');await details.locator('summary').click();
     assert.ok((await details.locator('pre').innerText()).length>100);await navigate(button);await record('通过网页记录已校验的独立审稿结果');
    }else if(await approve.count()){
     const taskId=await approve.getAttribute('data-approve-task');
     if(taskId.startsWith('book_ideation:')&&!(await page.locator('#design-text').inputValue()).includes('本记录由已授权的隔离测试流程模拟填写')){
      const decision=args.decisions?JSON.parse(fs.readFileSync(args.decisions,'utf8'))[taskId.match(/:round(\d+):/)?.[1]]:null;
      if(!decision){await record('设计候选等待模拟人工明确选择');break;}
      await writeDecision(decision);continue;
     }
     if(await page.getByText('页面预览已截断',{exact:false}).count())throw Error('Full candidate must be readable before simulated approval');
     const subject={task_id:await approve.getAttribute('data-approve-task'),sha256:await approve.getAttribute('data-sha')};assert.ok((await approve.locator('..').locator('.design-preview').innerText()).length>100);await page.locator('#approve-check').check();await navigate(approve);await record('模拟人工批准当前精确设计候选',subject);await page.waitForLoadState('networkidle');
    }else if(await apply.count()){
     const subject={task_id:await apply.getAttribute('data-apply-canonical'),sha256:await apply.getAttribute('data-sha')};assert.ok((await apply.locator('..').locator('pre').innerText()).length>100);await page.locator('#canonical-check').check();await navigate(apply);await record('模拟人工确认已校验设计语义应用',subject);await page.waitForLoadState('networkidle');
    }else if(await page.locator('[data-finalize]').count()){
     const button=page.locator('[data-finalize]'),chapter=Number(await button.getAttribute('data-finalize'));assert.ok(chapter>=1&&chapter<=5);
     await page.locator('#finalize-check').check();await navigate(button);await record('模拟明确确认并按正常领域规则定稿',{chapter_number:chapter});
    }else if(await page.locator('[data-semantic-apply]').count()){
     const button=page.locator('[data-semantic-apply]'),chapter=Number(await button.getAttribute('data-chapter'));assert.ok(chapter>=1&&chapter<=5);
     assert.ok((await button.locator('..').locator('pre').innerText()).length>100);await page.locator('#semantic-check').check();await navigate(button);await record('模拟确认当前证据绑定的语义候选并正常应用',{chapter_number:chapter});
    }else if(await page.locator('[data-close]').count()){
     const button=page.locator('[data-close]'),chapter=Number(await button.getAttribute('data-close'));assert.ok(chapter>=1&&chapter<=5);
     await page.locator('#close-check').check();await navigate(button);await record('正常关闭当前章节并读取后续准备状态',{chapter_number:chapter});
     if(chapter===5){await record('已到第六章准备边界，本次不生成第六章');break;}
    }else{await record('到达需要下一类操作的正常生产状态',{state:await page.locator('.action-box').innerText()});break;}
    if(await page.locator('#action-status .danger').count())throw Error(await page.locator('#action-status').innerText());
   }
  }
  await page.screenshot({path:path.join(run,'current-page.png'),fullPage:true});assert.deepEqual(errors,[]);
 }catch(error){await record('failure',{error:error.stack});process.exitCode=1;await page.screenshot({path:path.join(run,'failure.png'),fullPage:true}).catch(()=>{});}
 finally{fs.writeFileSync(sessionFile,JSON.stringify({pid:server.pid,state:await context.storageState()}));await context.tracing.stop({path:path.join(run,'trial.trace.zip')});fs.writeFileSync(path.join(run,'run.json'),JSON.stringify({run_id:server.run_id,engine_version:server.engine_version,build_at_start:buildAtStart,build_at_finish:captureBuildMetadata(),codex_runtime:server.codex_runtime,actual_model:null,fixture:false,simulated_human:true,formal_literary_eligible:false,steps,errors},null,2));console.log(JSON.stringify({steps,errors}));await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
