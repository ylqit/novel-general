/* Explicitly authorized simulated-human decisions, entered through normal forms.
 * The input contains judgments and exact quotes; it never contains gate reports,
 * manufactured model reviews, final paths, or hashes for the agent to fill in. */
const {captureBuildMetadata}=require('./web_test_metadata.cjs');const buildAtStart=captureBuildMetadata();
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const args=Object.fromEntries(process.argv.slice(2).reduce((rows,v,i,a)=>i%2?rows:[...rows,[v.replace(/^--/,''),a[i+1]]],[]));
const {chromium}=require(args.playwright),root=path.resolve(args.evidence),server=JSON.parse(fs.readFileSync(path.join(root,'server.json'),'utf8'));
const novel=path.resolve(args['project-root']),spec=JSON.parse(fs.readFileSync(args.record,'utf8')),origin=JSON.parse(fs.readFileSync(path.join(novel,'00_governance/execution_origin.json'),'utf8'));
assert.ok(novel.startsWith(path.resolve(server.workspace)+path.sep));assert.equal(origin.run_id,server.run_id);assert.equal(origin.simulated_human,true);assert.equal(spec.simulated_human,true);
assert.ok(Number.isInteger(spec.chapter_number)&&spec.chapter_number>=1&&spec.chapter_number<=5);
assert.ok(['review','events','reader_promises','author_voice'].includes(spec.phase));
const relative=path.relative(server.workspace,path.join(novel,'project.yaml')).replaceAll('\\','/').toLowerCase(),projectId='project_'+crypto.createHash('sha256').update(relative).digest('hex').slice(0,20);
const sessionFile=path.join(root,'browser-session.json'),saved=fs.existsSync(sessionFile)?JSON.parse(fs.readFileSync(sessionFile,'utf8')):null;
const run=path.join(root,'confirm-'+Date.now());fs.mkdirSync(run);
(async()=>{
 const browser=await chromium.launch({executablePath:args.browser,headless:true}),context=await browser.newContext({viewport:{width:1440,height:1100},locale:'zh-CN',...(saved?.pid===server.pid?{storageState:saved.state}:{})});
 await context.tracing.start({screenshots:true,snapshots:true,sources:true});const page=await context.newPage(),steps=[],errors=[];
 page.setDefaultTimeout(60000);page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text()+' '+m.location().url)});page.on('dialog',d=>d.accept());
 async function record(name){steps.push({name,passed:true,time:new Date().toISOString()});console.log(name)}
 const note=text=>{assert.ok(typeof text==='string'&&text.includes('模拟'));return text};
 try{
  await page.goto(saved?.pid===server.pid?server.base_url:server.bootstrap_url,{waitUntil:'networkidle'});
  if(spec.phase==='review'){
   await page.locator(`a[href="/projects/${projectId}?view=read"]`).click();await page.locator(`[data-chapter="${spec.chapter_number}"]`).click();await page.locator('#review-mode').click();await page.locator('#manuscript').waitFor();
   const prepared=page.waitForResponse(r=>r.url().endsWith('/human-review/prepare')&&r.request().method()==='POST');await page.locator('#reviewPrepare').click();assert.equal((await prepared).status(),200);
   await page.locator('#coverageNaturalForm').waitFor();
   const text=await page.locator('#manuscript').inputValue();
   for(const item of spec.evidence){
    assert.ok(text.includes(item.quote));assert.equal(text.indexOf(item.quote),text.lastIndexOf(item.quote));
    const details=page.locator('#exactEvidenceQuote').locator('xpath=ancestor::details[1]');if(!await details.getAttribute('open'))await details.locator('summary').click();
    await page.locator('#exactEvidenceQuote').fill(item.quote);await page.locator('#exactEvidenceKind').selectOption(item.kind);await page.locator('#evidenceNote').fill(note(item.note));await page.locator('#exactEvidenceAdd').click();await page.getByText('原文已定位并登记，请填写对应的阅读判断。',{exact:true}).waitFor();
   }
   assert.deepEqual(new Set(spec.evidence.map(x=>x.kind)),new Set(['key_turn','character_choice_or_emotion','reader_gain']));
   for(const item of spec.coverage){
    const status=page.locator(`[data-coverage="${item.id}"][data-field="status"]`),details=status.locator('xpath=ancestor::details[1]');if(!await details.getAttribute('open'))await details.locator('summary').first().click();
    await status.selectOption(item.status);await page.locator(`[data-coverage="${item.id}"][data-field="coverage_source"]`).selectOption(item.source);await page.locator(`[data-coverage="${item.id}"][data-field="reason"]`).fill(note(item.reason));
   }
   const findingRows=page.locator('.finding-resolution');assert.equal(await findingRows.count(),spec.findings.length,'Every frozen finding needs its own explicit resolution');
   for(const [index,item] of spec.findings.entries()){
    const row=findingRows.nth(index);await row.locator('summary').first().click();await row.locator('[data-field="disposition"]').selectOption(item.disposition);await row.locator('[data-field="reason"]').fill(note(item.reason));await row.locator('[data-field="must_preserve"]').fill(item.must_preserve.join('\n'));
   }
   await page.locator('#decision').selectOption(spec.decision);await page.locator('#gainNote').fill(note(spec.reader_gain));await page.locator('#reviewReason').fill(note(spec.reason));
   const validating=page.waitForResponse(r=>r.url().endsWith('/human-review/validate')&&r.request().method()==='POST');await page.locator('#reviewValidate').click();const validationResponse=await validating;assert.equal(validationResponse.status(),200);const validation=await validationResponse.json();assert.equal(validation.result.ok,true,JSON.stringify(validation.result));
   await page.waitForFunction(()=>!document.getElementById('reviewApply')?.disabled);await record('准确原文、核心阅读判断与逐项问题处置经正常领域校验通过');
   await page.screenshot({path:path.join(run,'validated-decision.png'),fullPage:true});
   await page.locator('#reviewApplyAck').check();const applying=page.waitForResponse(r=>r.url().endsWith('/human-review/apply')&&r.request().method()==='POST');await page.locator('#reviewApply').click();assert.equal((await applying).status(),200);await record('明确模拟确认并通过正常入口采用当前人工审稿决定');
   await page.locator('#readerBack').click();await page.getByRole('link',{name:'作品概览',exact:true}).click();
  }else{
   await page.locator(`a[href="/projects/${projectId}"]`).click();await page.locator('.chapter-confirmation-form').waitFor();
   const form=page.locator('.chapter-confirmation-form');
   if(spec.phase==='author_voice'){
    await form.locator('[data-field="change"]').selectOption(String(spec.change_index));
    for(const key of ['purpose','abstract_principle'])await form.locator(`[data-field="${key}"]`).fill(note(spec[key]));
    if(spec.pov_name){const option=form.locator('[data-field="pov_character_id"] option').filter({hasText:new RegExp('^'+spec.pov_name+'$')});assert.equal(await option.count(),1);await form.locator('[data-field="pov_character_id"]').selectOption(await option.getAttribute('value'))}
    await form.locator('[data-field="scene_kind"]').fill(spec.scene_kind||'');
   }else{
    const rows=form.locator('fieldset');assert.equal(await rows.count(),spec.items.length);
    for(const [index,item] of spec.items.entries()){
     const row=rows.nth(index);if(spec.phase==='events')await row.locator('[data-field="state"]').selectOption(item.state);
     if(await row.locator('[data-field="quote"]').isVisible())await row.locator('[data-field="quote"]').fill(item.quote);
     await row.locator('[data-field="reason"]').fill(note(item.reason));
    }
   }
   const prefix=spec.phase==='events'?'event':spec.phase==='author_voice'?'voice':'promise',action=spec.phase==='events'?'confirm-events':spec.phase==='author_voice'?'approve-author-voice':'confirm-promises',button=spec.phase==='events'?'confirm-events':spec.phase==='author_voice'?'approve-voice':'confirm-promises';
   await page.screenshot({path:path.join(run,'confirmation-form.png'),fullPage:true});await page.locator('#'+prefix+'-check').check();const applying=page.waitForResponse(r=>r.url().endsWith('/'+action)&&r.request().method()==='POST');await page.locator('[data-'+button+']').click();const response=await applying;assert.equal(response.status(),200,await response.text());await page.waitForLoadState('networkidle');await record('模拟逐项确认，经正常事务登记'+spec.phase);
  }
  assert.deepEqual(errors,[]);
 }catch(error){steps.push({name:'failure',passed:false,error:error.stack});process.exitCode=1;await page.screenshot({path:path.join(run,'failure.png'),fullPage:true}).catch(()=>{})}
 finally{fs.writeFileSync(sessionFile,JSON.stringify({pid:server.pid,state:await context.storageState()}));await context.tracing.stop({path:path.join(run,'confirmation.trace.zip')});fs.writeFileSync(path.join(run,'confirmation.json'),JSON.stringify({run_id:server.run_id,engine_version:server.engine_version,build_at_start:buildAtStart,build_at_finish:captureBuildMetadata(),fixture:!!origin.fixture,simulated_human:true,formal_literary_eligible:false,phase:spec.phase,chapter_number:spec.chapter_number,steps,errors},null,2));console.log(JSON.stringify({steps,errors}));await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
