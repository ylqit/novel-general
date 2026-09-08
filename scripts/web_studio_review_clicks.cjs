/* Review desk browser checks on labelled protocol fixtures. No API substitutes. */
const {captureBuildMetadata}=require('./web_test_metadata.cjs');const buildAtStart=captureBuildMetadata();
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const args=Object.fromEntries(process.argv.slice(2).reduce((rows,v,i,a)=>i%2?rows:[...rows,[v.replace(/^--/,''),a[i+1]]],[]));
const {chromium}=require(args.playwright),root=path.resolve(args.evidence);
const server=JSON.parse(fs.readFileSync(path.join(root,'server.json'),'utf8'));
const trial=args['project-root']?path.resolve(args['project-root']):null,chapter=Number(args.chapter||1);
let projectId=null,questions=null;
if(trial){
 assert.ok(server.automated_rehearsal&&!server.fixture);assert.ok(trial.startsWith(path.resolve(server.workspace)+path.sep));assert.ok(Number.isInteger(chapter)&&chapter>=1&&chapter<=5);
 const origin=JSON.parse(fs.readFileSync(path.join(trial,'00_governance/execution_origin.json'),'utf8'));assert.equal(origin.run_id,server.run_id);assert.equal(origin.simulated_human,true);assert.ok(!origin.fixture);
 const relative=path.relative(server.workspace,path.join(trial,'project.yaml')).replaceAll('\\','/').toLowerCase();projectId='project_'+crypto.createHash('sha256').update(relative).digest('hex').slice(0,20);
 if(args.model==='true'){questions=JSON.parse(fs.readFileSync(args.questions,'utf8'));assert.ok(Array.isArray(questions)&&questions.length>=1&&questions.every(q=>typeof q==='string'&&q.length>10));}
}
const evidence=path.join(root,'review-'+Date.now());fs.mkdirSync(evidence);
const sessionFile=path.join(root,'browser-session.json');
(async()=>{
 const saved=fs.existsSync(sessionFile)?JSON.parse(fs.readFileSync(sessionFile,'utf8')):null;
 const browser=await chromium.launch({executablePath:args.browser,headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'zh-CN',...(saved?.pid===server.pid?{storageState:{...saved.state,origins:[]}}:{})});
 await context.tracing.start({screenshots:true,snapshots:true,sources:true});const page=await context.newPage(),steps=[],errors=[];
 page.setDefaultTimeout(45000);page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text()+" "+m.location().url)});page.on('dialog',dialog=>dialog.accept());
 async function step(name,work){await work();steps.push({name,passed:true,time:new Date().toISOString()});console.log(name)}
 try{
  await page.goto(saved?.pid===server.pid?server.base_url:server.bootstrap_url,{waitUntil:'networkidle'});
  await (trial?page.locator(`a[href="/projects/${projectId}?view=read"]`):page.getByRole('link',{name:'阅读作品',exact:true})).click();
  if(trial)await page.locator(`[data-chapter="${chapter}"]`).click();await page.locator('#prose h1').waitFor();
  await step('从所选章节进入审稿台',async()=>{await page.getByRole('link',{name:'审稿',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#manuscript')?.value.length>100);if(!trial)assert.match(await page.locator('#manuscript').inputValue(),/第一章 山门/);else assert.match(await page.locator('#title').innerText(),new RegExp(`第 ${chapter} 章`))});
  if(args['expect-readonly']==='true')await step('已提交人工修改稿保持只读，旧建议只能回看',async()=>{
   assert.equal(args.inspect,'true','Readonly inspection cannot start editing or model work');
   if(trial)await page.locator('#reviewOrigin').getByText(/自动演练/).waitFor();
   await page.locator('#consultPhase').getByText(/人工终稿阶段/).waitFor();
   assert.equal(await page.locator('#revisionText').getAttribute('readonly'),'');
   for(const id of ['revisionPrepare','revisionSave','revisionValidate','revisionSubmit','coeditRewrite','coeditCandidateValidate'])assert.ok(await page.locator('#'+id).isDisabled(),id);
   const options=page.locator('#consultHistory button').filter({hasText:/^选择 OPTION-/});assert.ok(await options.count());
   for(const option of await options.all())assert.ok(await option.isDisabled());
   const before=await page.locator('#revisionText').inputValue();await page.locator('#reload').click();assert.equal(await page.locator('#revisionText').inputValue(),before);
   await page.reload({waitUntil:'networkidle'});await page.waitForFunction(()=>document.getElementById('revisionText')?.readOnly===true);assert.equal(await page.locator('#revisionText').inputValue(),before);
  });
  await step('证据与批注元数据按需展开，不占据日常阅读区',async()=>{
   assert.ok(await page.getByText('粘贴准确原文登记证据',{exact:true}).isVisible(),'The ordinary evidence form remains outside technical details');
   for(const [id,label] of [['evidenceView','已登记证据详情'],['annotationView','批注协议详情']]){
    const details=page.locator(`#${id}`).locator('..');assert.equal(await details.evaluate(el=>el.tagName),'DETAILS');assert.equal(await details.getAttribute('open'),null);
    await details.getByText(label,{exact:true}).click();assert.ok(await page.locator(`#${id}`).isVisible());
    await details.getByText(label,{exact:true}).click();assert.equal(await details.getAttribute('open'),null);
   }
  });
  if(args.inspect!=='true'){
  await step('刷新状态保留人工输入',async()=>{
   await page.locator('#question').fill('这是未提交的审稿咨询文字，不应因刷新状态丢失。');
   await page.locator('#reload').click();assert.match(await page.locator('#question').inputValue(),/不应因刷新/);
   await page.reload({waitUntil:'networkidle'});assert.match(await page.locator('#question').inputValue(),/不应因刷新/);
  });
  if(args.model==='true')await step('真实顾问建议与完整修改候选',async()=>{
   async function waitJob(expected){let previous='';const deadline=Date.now()+20*60*1000;while(Date.now()<deadline){const status=await page.locator('#consultStatus').innerText();if(status!==previous){console.log(status);previous=status}if(expected.test(status))return;if(/执行失败|校验失败|未通过|越界|未完成记录|无效|不允许/.test(status))throw Error(status);await page.waitForTimeout(1500)}throw Error('real model timeout')}
   for(const question of questions||['这是协议测试的合成草稿。请针对开头提出两到三个可选方案，保留林迟夺路、铜符转交及同伴拒绝的已批准事实，通过行动和对白降低重复解释。']){
    const manuscript=page.locator('#manuscript');await page.waitForFunction(()=>document.getElementById('manuscript')?.value.length>100);
    const text=await manuscript.inputValue(),line=text.split('\n').find(value=>value.trim().length>3&&!value.trim().startsWith('#'));assert.ok(line);
    const offset=text.indexOf(line),lineNumber=text.slice(0,offset).split('\n').length-1;
    await manuscript.scrollIntoViewIfNeeded();await manuscript.click({position:{x:20,y:20}});await manuscript.press('Control+Home');
    const metrics=await manuscript.evaluate(el=>{const css=getComputedStyle(el);return {left:parseFloat(css.paddingLeft),top:parseFloat(css.paddingTop),line:parseFloat(css.lineHeight),scrollTop:el.scrollTop}});
    assert.equal(metrics.scrollTop,0);assert.ok(Number.isFinite(metrics.line));
    await manuscript.click({position:{x:metrics.left+25,y:metrics.top+(lineNumber+.5)*metrics.line},clickCount:3});
    const quote=await manuscript.evaluate(el=>el.value.slice(el.selectionStart,el.selectionEnd));
    if(quote.trim()!==line.trim())console.log('圈选诊断：'+JSON.stringify(await manuscript.evaluate(el=>({start:el.selectionStart,end:el.selectionEnd,focused:document.activeElement===el,readOnly:el.readOnly,disabled:el.disabled,scrollTop:el.scrollTop,chars:el.value.length}))));
    assert.equal(quote.trim(),line.trim(),'Mouse selection must match one actual prose paragraph');
    await page.locator('#question').fill(question);await page.locator('#consultTask').click();await waitJob(/回答已校验并记录/);
    await page.reload({waitUntil:'networkidle'});await page.locator('#consultHistory').getByText(question,{exact:true}).last().waitFor();
   }
   await page.locator('#consultHistory section').last().getByRole('button').first().click();
   assert.ok(await page.locator('#optionId').inputValue());await page.locator('#coeditRewrite').click();
   await waitJob(/完整修改候选已校验/);
   await page.getByText('阅读完整修改候选与原稿',{exact:true}).last().click();
   await page.locator('#consultHistory .version-prose').last().waitFor();
   const texts=await page.locator('#consultHistory .version-prose').allTextContents();assert.ok(texts.length>=2);assert.notEqual(texts.at(-1),texts.at(-2));
   await page.screenshot({path:path.join(evidence,'coedit-candidate.png'),fullPage:true});
  });
  await step('准备实质修改并展示自然语言表单',async()=>{
   if(await page.locator('#revisionPrepare').isEnabled())await page.locator('#revisionPrepare').click();await page.locator('#revisionNaturalForm').waitFor();
   await page.getByRole('button',{name:'添加一项实质修改',exact:true}).waitFor();
   await page.locator('#revisionText').fill((await page.locator('#manuscript').inputValue())+'\n这段文字是尚未提交的修改草稿。');
   await page.locator('#reload').click();assert.match(await page.locator('#revisionText').inputValue(),/尚未提交的修改草稿/);
   await page.screenshot({path:path.join(evidence,'review-natural-form.png'),fullPage:true});
  });
  }
  for(const width of [375,768,1280,1440,1920])await step(`审稿布局 ${width}px`,async()=>{await page.setViewportSize({width,height:1000});await page.locator("#reload").focus();await page.keyboard.press("Control+Home");assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));if(width<=768){assert.equal(await page.locator("#toggleReviewContext").getAttribute("aria-expanded"),"false");assert.equal(await page.locator("#toggleReviewDecisions").getAttribute("aria-expanded"),"false");assert.ok((await page.locator("#manuscript").boundingBox()).y<350,"Mobile review opens directly on prose")} await page.screenshot({path:path.join(evidence,`review-${width}.png`),fullPage:false})});
  await step('键盘展开与收起审稿面板，设置在刷新后恢复',async()=>{
   await page.setViewportSize({width:1440,height:1000});await page.locator('#toggleReviewContext').focus();await page.keyboard.press('Enter');assert.equal(await page.locator('#toggleReviewContext').getAttribute('aria-expanded'),'false');await page.reload({waitUntil:'networkidle'});assert.equal(await page.locator('#toggleReviewContext').getAttribute('aria-expanded'),'false');await page.locator('#toggleReviewContext').click();assert.equal(await page.locator('#toggleReviewContext').getAttribute('aria-expanded'),'true');
   await page.setViewportSize({width:375,height:1000});await page.locator('#toggleReviewDecisions').click();assert.equal(await page.locator('#toggleReviewDecisions').getAttribute('aria-expanded'),'true');assert.ok(await page.locator('#question').isVisible());await page.locator('#toggleReviewDecisions').click();assert.equal(await page.locator('#toggleReviewDecisions').getAttribute('aria-expanded'),'false');
  });
  assert.deepEqual(errors,[]);
 }catch(error){steps.push({name:'failure',passed:false,error:error.stack});process.exitCode=1;await page.screenshot({path:path.join(evidence,'failure.png'),fullPage:true}).catch(()=>{});}
 finally{fs.writeFileSync(sessionFile,JSON.stringify({pid:server.pid,state:await context.storageState()}));await context.tracing.stop({path:path.join(evidence,'review.trace.zip')});if(errors.length)process.exitCode=1;
  fs.writeFileSync(path.join(evidence,'review.json'),JSON.stringify({passed:!process.exitCode,run_id:server.run_id,engine_version:server.engine_version,build_at_start:buildAtStart,build_at_finish:captureBuildMetadata(),fixture:!trial,simulated_human:!!trial,formal_literary_eligible:false,chapter_number:chapter,real_model:args.model==='true',steps,errors},null,2));console.log(JSON.stringify({steps,errors}));await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
