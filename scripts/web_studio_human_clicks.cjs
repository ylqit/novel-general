/* Authorized simulated-human revision of an actual model candidate. Normal Web
 * save, isolated review, lock and submission remain mandatory. Never writes final. */
const {captureBuildMetadata}=require('./web_test_metadata.cjs');const buildAtStart=captureBuildMetadata();
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const args=Object.fromEntries(process.argv.slice(2).reduce((rows,v,i,a)=>i%2?rows:[...rows,[v.replace(/^--/,''),a[i+1]]],[]));
const {chromium}=require(args.playwright),root=path.resolve(args.evidence),server=JSON.parse(fs.readFileSync(path.join(root,'server.json'),'utf8'));
const novel=path.resolve(args['project-root']),spec=JSON.parse(fs.readFileSync(args.record,'utf8')),origin=JSON.parse(fs.readFileSync(path.join(novel,'00_governance/execution_origin.json'),'utf8'));
assert.ok(novel.startsWith(path.resolve(server.workspace)+path.sep));assert.equal(origin.run_id,server.run_id);assert.equal(origin.simulated_human,true);assert.equal(spec.simulated_human,true);
assert.ok(Number.isInteger(spec.chapter_number)&&spec.chapter_number>=1&&spec.chapter_number<=5);
const jobs=fs.readdirSync(path.join(novel,'70_runtime/agent_jobs'),{withFileTypes:true}).filter(p=>p.isDirectory()&&p.name.startsWith('job_')).map(p=>JSON.parse(fs.readFileSync(path.join(novel,'70_runtime/agent_jobs',p.name,'job.json'),'utf8')));
const job=jobs.find(j=>j.task_id===spec.candidate_task_id&&j.status==='completed');assert.ok(job,'Revision must come from a completed actual Web model job');assert.ok(['chapter_coedit_rewrite','repair'].includes(job.task_type));
const candidateFile=path.resolve(novel,job.allowed_output_path);assert.ok(candidateFile.startsWith(path.join(novel,'50_workbench')+path.sep));
const bytes=fs.readFileSync(candidateFile),hash=crypto.createHash('sha256').update(bytes).digest('hex');assert.equal(hash,job.result_sha256);
const normalized=bytes.toString('utf8').replaceAll('\r\n','\n').replaceAll('\r','\n'),text=normalized.endsWith('\n')?normalized:normalized+'\n',savedHash=crypto.createHash('sha256').update(text).digest('hex');assert.ok(text.length>100);assert.ok(spec.changes.length>=2);assert.match(spec.human_statement,/模拟/);assert.match(spec.lock_statement,/模拟/);
const configRelative=path.relative(server.workspace,path.join(novel,'project.yaml')).replaceAll('\\','/').toLowerCase(),projectId='project_'+crypto.createHash('sha256').update(configRelative).digest('hex').slice(0,20);
const sessionFile=path.join(root,'browser-session.json'),saved=fs.existsSync(sessionFile)?JSON.parse(fs.readFileSync(sessionFile,'utf8')):null;
const run=path.join(root,'human-'+Date.now());fs.mkdirSync(run);
(async()=>{
 const browser=await chromium.launch({executablePath:args.browser,headless:true}),context=await browser.newContext({viewport:{width:1440,height:1100},locale:'zh-CN',...(saved?.pid===server.pid?{storageState:saved.state}:{})});
 await context.tracing.start({screenshots:true,snapshots:true,sources:true});const page=await context.newPage(),steps=[],errors=[];
 page.setDefaultTimeout(60000);page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text()+' '+m.location().url)});page.on('dialog',d=>d.accept());
 async function record(name){steps.push({name,passed:true,time:new Date().toISOString()});console.log(name)}
 try{
  await page.goto(saved?.pid===server.pid?server.base_url:server.bootstrap_url,{waitUntil:'networkidle'});
  await page.locator(`a[href="/projects/${projectId}?view=read"]`).click();await page.locator(`[data-chapter="${spec.chapter_number}"]`).click();await page.locator('#review-mode').click();await page.locator('#manuscript').waitFor();
  if(await page.locator('#revisionPrepare').isEnabled()){await page.locator('#revisionPrepare').click();await page.locator('#revisionNaturalForm').waitFor()}
  const source=await page.locator('#revisionSource').inputValue();assert.notEqual(source,text);
  await page.locator('#revisionText').fill(text);
  while(await page.locator('#revisionChanges .revision-change').count()<spec.changes.length)await page.locator('#addRevisionChange').click();
  assert.equal(await page.locator('#revisionChanges .revision-change').count(),spec.changes.length);
  for(const [index,change] of spec.changes.entries()){
   const row=page.locator('#revisionChanges .revision-change').nth(index);
   for(const [content,quote] of [[source,change.before],[text,change.after]]){assert.ok(quote.length>2&&content.includes(quote));assert.equal(content.indexOf(quote),content.lastIndexOf(quote),'Evidence must locate one exact passage')}
   for(const field of ['dimension','intent_ref'])await row.locator(`[data-revision-field="${field}"]`).selectOption(change[field]);
   for(const field of ['before','after','intent','reader_effect'])await row.locator(`[data-revision-field="${field}"]`).fill(change[field]);
   await row.locator('[data-revision-field="must_preserve"]').fill(change.must_preserve.join('\n'));
  }
  for(const [key,note] of Object.entries(spec.protected_confirmations)){await page.locator('#protect-'+key).check();await page.locator('#protect-note-'+key).fill(note)}
  await page.locator('#revisionHumanStatement').fill(spec.human_statement);await page.locator('#revisionLockStatement').fill(spec.lock_statement);
  const unrelated='未提交的追问：保留我的讨论草稿。';await page.locator('#question').fill(unrelated);await page.locator('#reload').click();assert.equal(await page.locator('#revisionText').inputValue(),text);
  const saveResponse=page.waitForResponse(r=>r.url().endsWith('/human-revision/save')&&r.request().method()==='POST');await page.locator('#revisionSave').click();const response=await saveResponse;assert.equal(response.status(),200);assert.equal((await response.json()).result.candidate_sha256,savedHash);
  await page.waitForFunction(hash=>document.getElementById('candidate')?.textContent===hash,savedHash);assert.equal(await page.locator('#question').inputValue(),unrelated);
  await record('真实模型候选通过中文表单保存，准确引用由界面绑定，未提交追问保留');
  await page.locator('#revisionValidate').click();let previous='';const deadline=Date.now()+30*60*1000;
  while(Date.now()<deadline){const status=await page.locator('#revisionStatus').innerText();if(status!==previous){console.log(status);previous=status}if(status.includes('独立复核已通过'))break;if(/执行失败|未通过|未完成记录|无效|缺失|请先|changed|stale|invalid|missing/.test(status))throw Error(status);await page.waitForTimeout(1600)}
  await page.waitForFunction(()=>!document.getElementById('revisionSubmit')?.disabled);await record('真实独立语义复核通过，控制面绑定结果并锁定模拟人工终稿');
  await page.screenshot({path:path.join(run,'locked-revision.png'),fullPage:true});
  const submitted=page.waitForResponse(r=>r.url().endsWith('/human-revision/submit')&&r.request().method()==='POST');await page.locator('#revisionSubmit').click();assert.equal((await submitted).status(),200);await record('通过正常人工终稿提交入口进入完整复审');
  await page.locator('#readerBack').click();await page.getByRole('link',{name:'作品概览',exact:true}).click();await page.locator('.action-box').waitFor();assert.deepEqual(errors,[]);
 }catch(error){steps.push({name:'failure',passed:false,error:error.stack});process.exitCode=1;await page.screenshot({path:path.join(run,'failure.png'),fullPage:true}).catch(()=>{})}
 finally{fs.writeFileSync(sessionFile,JSON.stringify({pid:server.pid,state:await context.storageState()}));await context.tracing.stop({path:path.join(run,'human.trace.zip')});fs.writeFileSync(path.join(run,'human.json'),JSON.stringify({run_id:server.run_id,engine_version:server.engine_version,build_at_start:buildAtStart,build_at_finish:captureBuildMetadata(),fixture:!!origin.fixture,simulated_human:true,formal_literary_eligible:false,real_model_candidate_task_id:job.task_id,model_output_sha256:hash,candidate_sha256:savedHash,chapter_number:spec.chapter_number,steps,errors},null,2));console.log(JSON.stringify({steps,errors}));await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
