/* Explicitly simulated assessment of a labelled closed protocol fixture.
 * All business actions use visible UI; the only file mutation is a bounded
 * stale-source fault, restored in finally. No model or literary score is claimed. */
const {captureBuildMetadata}=require('./web_test_metadata.cjs');const buildAtStart=captureBuildMetadata();
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const args=Object.fromEntries(process.argv.slice(2).reduce((rows,v,i,a)=>i%2?rows:[...rows,[v.replace(/^--/,''),a[i+1]]],[]));
const {chromium}=require(args.playwright),root=path.resolve(args.evidence),server=JSON.parse(fs.readFileSync(path.join(root,'server.json'),'utf8'));
if(!server.fixture||!server.automated_rehearsal)throw Error('A labelled closed rehearsal fixture is required');
const run=path.join(root,'literary-'+Date.now());fs.mkdirSync(run);
const sessionFile=path.join(root,'browser-session.json'),saved=fs.existsSync(sessionFile)?JSON.parse(fs.readFileSync(sessionFile,'utf8')):null;
(async()=>{
 const browser=await chromium.launch({executablePath:args.browser,headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1100},locale:'zh-CN',acceptDownloads:true,...(saved?.pid===server.pid?{storageState:saved.state}:{})});
 await context.tracing.start({screenshots:true,snapshots:true,sources:true});
 const page=await context.newPage(),steps=[],errors=[],expected=[],reviewContexts=[];
 const watch=p=>{p.setDefaultTimeout(60000);p.on('dialog',d=>d.accept());p.on('pageerror',e=>errors.push({message:e.message,url:''}));p.on('console',m=>{if(m.type()==='error')errors.push({message:m.text(),url:m.location().url})});p.on('response',async r=>{if([403,409].includes(r.status())){const body=await r.text().catch(()=>'');if(/匿名包已失效|评审入口|入口凭证|not authenticated|forbidden|session/i.test(body))expected.push({url:r.url(),status:r.status(),body})}})};
 watch(page);
 async function step(name,work){await work();steps.push({name,passed:true,time:new Date().toISOString()});fs.writeFileSync(path.join(run,'steps.json'),JSON.stringify(steps,null,2));console.log(name)}
 try{
  await page.goto(saved?.pid===server.pid?server.base_url:server.bootstrap_url,{waitUntil:'networkidle'});await page.getByRole('link',{name:'继续创作',exact:true}).first().click();
  const projectPath=new URL(page.url()).pathname;
  await page.getByRole('link',{name:'质量评测',exact:true}).click();await page.locator('#create').waitFor();
  const trial='protocol-'+Date.now(),links=[];
  await step('正常闭环证据核对与演练匿名包创建',async()=>{
   await page.locator('[name="trial_id"]').fill(trial);await page.locator('[name="stage"]').selectOption('rehearsal');await page.locator('#samples .end').fill('1');
   await page.getByRole('button',{name:'核对证据并创建匿名包',exact:true}).click();await page.locator(`[data-report="${trial}"]`).waitFor();
   assert.match(await page.locator('#message').innerText(),/演练匿名包/);
  });
  await step('创建三个独立匿名入口',async()=>{
   for(let index=0;index<3;index++){
    const form=page.locator(`form.add-reviewer[data-trial="${trial}"]`);await form.locator('[name="reviewer_id"]').fill('simulated-'+index);
    await form.getByRole('button',{name:'创建独立评审入口',exact:true}).click();
    const link=page.getByRole('link',{name:'打开匿名评审入口 simulated-'+index,exact:true});await link.waitFor();links.push(await link.getAttribute('href'));
   }
  });
  for(let index=0;index<3;index++)await step(`匿名入口 ${index+1} 阅读、草稿与模拟提交`,async()=>{
   const c=await browser.newContext({viewport:{width:1440,height:1100},locale:'zh-CN',acceptDownloads:true});reviewContexts.push(c);
   await c.tracing.start({screenshots:true,snapshots:true,sources:true});const p=await c.newPage();watch(p);
   await p.goto(links[index],{waitUntil:'networkidle'});await p.locator('#review-form').waitFor();
   assert.match(await p.locator('#app').innerText(),/自动演练/);assert.equal((await c.cookies()).length,0);
   assert.doesNotMatch(await p.locator('#app').innerText(),/阅读报告与处理分歧/);assert.ok(!(await p.locator('#app').innerText()).includes(server.workspace));assert.equal(await p.locator('#create,[data-report],.add-reviewer').count(),0);
   for(const input of await p.locator('[data-metric]').all())await input.fill('4');
   await p.locator('[data-metric="prose_naturalness"]').fill(String([2,4,5][index]));
   await p.locator('[data-notes="0"]').fill(`模拟评审 ${index+1}：这是协议评分夹具，不是人类文学判断。`);
   await p.locator('#human-id').fill('synthetic-person-'+index);await p.locator('#independent').check();
   await p.locator('#attestation').fill('用户授权的隔离测试流程模拟填写；本入口不读取其他入口草稿，所有评分仅用于工程复算。');
   if(index===0){
    await p.reload({waitUntil:'networkidle'});await p.locator('#review-form').waitFor();assert.match(await p.locator('[data-notes="0"]').inputValue(),/模拟评审 1/);
    assert.ok((await p.locator('#prose').inputValue()).length>100);
    await p.locator('#severity').selectOption('major');await p.locator('#finding-note').fill('模拟严重问题，用于验证精确引文和分歧处理；不声称此段实际存在文学缺陷。');
    await p.locator('#prose').scrollIntoViewIfNeeded();const area=await p.locator('#prose').boundingBox();await p.mouse.move(area.x+32,area.y+47);await p.mouse.down();await p.mouse.move(area.x+175,area.y+47,{steps:12});await p.mouse.up();assert.ok(await p.locator('#prose').evaluate(el=>el.selectionEnd>el.selectionStart));
    await p.locator('#add-finding').click();await p.waitForTimeout(100);if(!await p.locator('.findings blockquote').count())throw Error(await p.locator('#message').innerText());
   }
   await p.locator('#save').click();await p.getByText('个人草稿已保存。',{exact:true}).waitFor();
   const downloadEvent=p.waitForEvent('download');await p.locator('#export-draft').click();const download=await downloadEvent;
   const file=path.join(run,`reviewer-${index}.json`);await download.saveAs(file);
   assert.equal(JSON.parse(fs.readFileSync(file,'utf8')).reviewer_kind,'simulated');
   await p.locator('#import-draft').setInputFiles(file);await p.getByText('个人评分草稿已导入，请核对后独立提交。',{exact:true}).waitFor();
   await p.getByRole('button',{name:'提交并锁定模拟评分',exact:true}).click();await p.getByText('模拟评分已提交并锁定，不是真人评价。',{exact:true}).waitFor();
   assert.ok(await p.locator('[data-metric]').first().isDisabled());assert.equal(await p.locator('#minutes').inputValue(),'');
   if(index===0)for(const width of [375,768,1280,1440,1920]){await p.setViewportSize({width,height:1000});assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await p.screenshot({path:path.join(run,`anonymous-${width}.png`)})}
   await c.tracing.stop({path:path.join(run,`reviewer-${index}.trace.zip`)});c.traceSaved=true;
  });
  await step('三份模拟评分复算、分歧处理和报告导出',async()=>{
   await page.reload({waitUntil:'networkidle'});await page.locator(`[data-report="${trial}"]`).click();await page.locator('#resolve').waitFor();
   const scoreRow=page.locator('#report table').first().getByRole('row').filter({hasText:'语言自然度'});assert.match(await scoreRow.innerText(),/2 \/ 4 \/ 5/);
   const before=await scoreRow.innerText();
   for(const select of await page.locator('#resolve select').all())if(await select.locator('option[value="not_substantiated"]').count())await select.selectOption('not_substantiated');
   for(const input of await page.locator('#resolve textarea[name^="reason-"]').all())await input.fill('协议演练预置的评分差异及引文，保留全部原始评分；本例不作真实文学判断。');
   for(const input of await page.locator('#resolve textarea[name^="follow-"]').all())await input.fill('真实文学质量仍需真实样本与三人独立评阅，本次只核对证据、处理流程与分数未被改写。');
   await page.locator('#resolve [name="decided_by"]').fill('simulated-chair');await page.getByRole('button',{name:'保存处理意见并复算',exact:true}).click();
   await page.getByText('处理意见已保存，评分保持原值。',{exact:true}).waitFor();assert.equal(await scoreRow.innerText(),before);
   const event=page.waitForEvent('download');await page.locator('#export-report').click();const file=path.join(run,'report.json');await(await event).saveAs(file);
   const report=JSON.parse(fs.readFileSync(file,'utf8'));assert.equal(report.status,'protocol_complete');assert.equal(report.formal_acceptance,false);assert.equal(report.evaluation_kind,'simulated_protocol');
   assert.ok(report.entries[0].scores.prose_naturalness.median===4);
   await page.screenshot({path:path.join(run,'resolved-report.png'),fullPage:true});
  });
  await step('导出匿名包且正文变化使原评测失效',async()=>{
   const event=page.waitForEvent('download');await page.locator(`a[href$="/${trial}/export-pack"]`).click();await(await event).saveAs(path.join(run,'anonymous-pack.zip'));
   const projectRoot=path.resolve(server.workspace,'literary-fixture','novel'),final=path.resolve(projectRoot,'40_manuscript/final/ch001.md');
   assert.ok(final.startsWith(projectRoot+path.sep));const origin=JSON.parse(fs.readFileSync(path.join(projectRoot,'00_governance/execution_origin.json'),'utf8'));
   assert.equal(origin.fixture,true);assert.equal(origin.run_id,server.run_id);const original=fs.readFileSync(final);
   try{
    fs.writeFileSync(final,Buffer.concat([original,Buffer.from('\n')]));
    await page.reload({waitUntil:'networkidle'});await page.getByRole('heading',{name:trial+' · 已失效，请重建评测',exact:true}).waitFor();assert.equal(await page.locator(`[data-report="${trial}"]`).count(),0);
    const p=await reviewContexts[0].newPage();watch(p);await p.goto(links[0],{waitUntil:'networkidle'});await p.getByText('暂时无法读取评测。',{exact:true}).waitFor();assert.equal(await p.locator('#prose').count(),0);
   }finally{fs.writeFileSync(final,original)}
   await page.reload({waitUntil:'networkidle'});await page.locator(`[data-report="${trial}"]`).waitFor();assert.match(await page.locator('#app').innerText(),/正式试写验收：未验证/);
   const privatePage=await reviewContexts[0].newPage();watch(privatePage);await privatePage.goto(server.base_url+projectPath,{waitUntil:'networkidle'});assert.equal(await privatePage.getByRole('heading',{name:'作品书架',exact:true}).count(),0);
  });
  assert.deepEqual(errors.filter(e=>!expected.some(r=>r.url===e.url&&e.message.includes(String(r.status)))),[]);
 }catch(error){steps.push({name:'failure',passed:false,error:error.stack});process.exitCode=1;await page.screenshot({path:path.join(run,'failure.png'),fullPage:true}).catch(()=>{})}
 finally{
  fs.writeFileSync(sessionFile,JSON.stringify({pid:server.pid,state:await context.storageState()}));
  await context.tracing.stop({path:path.join(run,'literary.trace.zip')});for(const [index,c] of reviewContexts.entries()){if(!c.traceSaved){await c.pages().at(-1)?.screenshot({path:path.join(run,`reviewer-${index}-failure.png`),fullPage:true}).catch(()=>{});await c.tracing.stop({path:path.join(run,`reviewer-${index}.trace.zip`)}).catch(()=>{})}await c.close();}
  if(errors.filter(error=>!expected.some(item=>error.url===item.url&&error.message.includes(String(item.status)))).length)process.exitCode=1;
  fs.writeFileSync(path.join(run,'literary.json'),JSON.stringify({passed:!process.exitCode,run_id:server.run_id,engine_version:server.engine_version,build_at_start:buildAtStart,build_at_finish:captureBuildMetadata(),fixture:true,real_model:false,simulated_reviews:true,formal_literary_eligible:false,steps,errors,expected_rejections:expected},null,2));
  console.log(JSON.stringify({steps,errors,expected}));await browser.close();
 }
})().catch(e=>{console.error(e);process.exitCode=1});
