/* Synthetic source display fixtures plus a normally validated baseline. This
 * proves UI navigation and source separation, never crossover literary quality. */
const {captureBuildMetadata}=require('./web_test_metadata.cjs');const buildAtStart=captureBuildMetadata();
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const args=Object.fromEntries(process.argv.slice(2).reduce((rows,v,i,a)=>i%2?rows:[...rows,[v.replace(/^--/,''),a[i+1]]],[]));
const {chromium}=require(args.playwright),root=path.resolve(args.evidence),server=JSON.parse(fs.readFileSync(path.join(root,'server.json'),'utf8'));
assert.ok(server.fixture&&server.automated_rehearsal);
const run=path.join(root,'fanfiction-'+Date.now());fs.mkdirSync(run);
const sessionFile=path.join(root,'browser-session.json'),saved=fs.existsSync(sessionFile)?JSON.parse(fs.readFileSync(sessionFile,'utf8')):null;
(async()=>{
 const browser=await chromium.launch({executablePath:args.browser,headless:true}),context=await browser.newContext({viewport:{width:1440,height:1100},locale:'zh-CN',...(saved?.pid===server.pid?{storageState:saved.state}:{})});
 await context.tracing.start({screenshots:true,snapshots:true,sources:true});const page=await context.newPage(),steps=[],errors=[];
 page.setDefaultTimeout(60000);page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text()+' '+m.location().url)});
 async function step(name,work){await work();steps.push({name,passed:true,time:new Date().toISOString()});console.log(name)}
 async function openBook(title){await page.getByRole('navigation',{name:'工作区',exact:true}).getByRole('link',{name:'作品书架',exact:true}).click();await page.locator('#shelf-query').fill(title);await page.locator('.project-card').filter({has:page.getByRole('heading',{name:title,exact:true})}).getByRole('link',{name:'继续创作',exact:true}).click();await page.getByRole('link',{name:'同人设定',exact:true}).click();await page.locator('#document-index').waitFor();assert.equal(await page.locator('.project-name').innerText(),title)}
 async function openDocument(relative){const id='doc_'+crypto.createHash('sha256').update(relative).digest('hex').slice(0,24);await page.locator('#document-filter').fill(relative);await page.locator(`[data-document="${id}"]`).click();await page.waitForFunction(relative=>document.getElementById('document-basis')?.textContent.startsWith(relative),relative)}
 try{
  await page.goto(saved?.pid===server.pid?server.base_url:server.bootstrap_url,{waitUntil:'networkidle'});
  await step('正常资料提取、覆盖和批准后的原著基线可读',async()=>{
   await openBook('原著基线 · 正常批准协议夹具');await openDocument('10_bible/fanfiction/source_canon.json');assert.match(await page.locator('#document-text').innerText(),/林舟|青铜门/);assert.doesNotMatch(await page.locator('#document-basis').innerText(),/尚未批准|无效/);await page.screenshot({path:path.join(run,'approved-baseline.png'),fullPage:true});
  });
  for(const [topology,title] of [['fixed_host','固定宿主'],['fusion_world','世界融合'],['sequential_worlds','顺序诸天']])await step(title+'的宿主、来源及规则范围可读且夹具标识明确',async()=>{
   await openBook(title+' · 资料阅读夹具');await openDocument('10_bible/fanfiction/source_canon.json');assert.match(await page.locator('#document-basis').innerText(),/合成资料阅读夹具/);assert.match(await page.locator('#document-text').innerText(),/山门世界/);
   await openDocument('10_bible/fanfiction/fanfiction_bible.json');const text=await page.locator('#document-text').innerText();for(const part of [title,'山门世界','来访世界','接触实物','伤及经脉','隔热封印','能力'])assert.ok(text.includes(part),part);assert.match(await page.locator('#document-basis').innerText(),/不代表已批准原著或真实小说事实/);await page.screenshot({path:path.join(run,topology+'.png'),fullPage:true});
  });
  await step('跨卷后果区分未发生、持续与解除，并定位正式原文',async()=>{
   const panel=page.locator('.consequence-history');await panel.waitFor();const text=await panel.innerText();
   for(const part of ['右手伤势已恢复，行动限制解除。','仍欠同伴一次搬运。','尚无正文实现依据，不能把规划当作已经发生。'])assert.ok(text.includes(part),part);
   const injury=panel.locator('section').filter({hasText:'换卷仍要处理右手伤势'});assert.doesNotMatch(await injury.innerText(),/右手伤势尚未恢复/);
   await injury.getByRole('link',{name:'阅读第 2 章证据',exact:true}).click();await page.locator('#prose mark').waitFor();assert.equal(await page.locator('#prose mark').innerText(),'右手伤势已恢复，行动限制解除。');
   await page.getByRole('button',{name:'上一章',exact:true}).click();await page.locator('#prose h1').filter({hasText:'第 1 章'}).waitFor();await page.getByRole('button',{name:'下一章',exact:true}).click();await page.locator('#prose h1').filter({hasText:'第 2 章'}).waitFor();
   await openBook('顺序诸天 · 资料阅读夹具');await openDocument('10_bible/fanfiction/fanfiction_bible.json');await page.screenshot({path:path.join(run,'consequence-states.png'),fullPage:true});
  });
  for(const width of [375,768,1280,1440,1920])await step(`同人资料布局 ${width}px`,async()=>{await page.setViewportSize({width,height:1000});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:path.join(run,`fanfiction-${width}.png`)})});
  await step('正式正文变化使后果证据失效，资料原文仍可读',async()=>{
   const file=path.join(server.workspace,'fanfiction-sequential_worlds-fixture/novel/40_manuscript/final/ch002.md'),before=fs.readFileSync(file);
   try{fs.writeFileSync(file,Buffer.concat([before,Buffer.from('\n限定故障注入。')]));await openDocument('10_bible/fanfiction/fanfiction_bible.json');await page.locator('.consequence-history .warn').waitFor();assert.match(await page.locator('.consequence-history').innerText(),/证据不完整/);assert.equal(await page.locator('.consequence-history a').count(),0);assert.match(await page.locator('#document-text').innerText(),/接触实物/)}finally{fs.writeFileSync(file,before)}
   await openDocument('10_bible/fanfiction/fanfiction_bible.json');await page.locator('.consequence-history a').first().waitFor();
  });
  await step('批准内容遭改变时提示绑定无效，原文仍可核对',async()=>{
   const file=path.join(server.workspace,'fanfiction-approved-fixture/project-fanfiction/10_bible/fanfiction/source_canon.json'),before=fs.readFileSync(file);
   try{const modified=JSON.parse(before);modified.body+='限定故障注入：改变正文而不改变批准绑定。';fs.writeFileSync(file,JSON.stringify(modified));await openBook('原著基线 · 正常批准协议夹具');await openDocument('10_bible/fanfiction/source_canon.json');assert.match(await page.locator('#document-basis').innerText(),/绑定无效/);assert.match(await page.locator('#document-text').innerText(),/限定故障注入/);await page.screenshot({path:path.join(run,'invalid-source-binding.png'),fullPage:true})}finally{fs.writeFileSync(file,before)}
   assert.deepEqual(fs.readFileSync(file),before);
  });
  assert.deepEqual(errors,[]);
 }catch(error){steps.push({name:'failure',passed:false,error:error.stack});process.exitCode=1;await page.screenshot({path:path.join(run,'failure.png'),fullPage:true}).catch(()=>{})}
 finally{fs.writeFileSync(sessionFile,JSON.stringify({pid:server.pid,state:await context.storageState()}));await context.tracing.stop({path:path.join(run,'fanfiction.trace.zip')});if(errors.length)process.exitCode=1;
  fs.writeFileSync(path.join(run,'fanfiction.json'),JSON.stringify({passed:!process.exitCode,run_id:server.run_id,engine_version:server.engine_version,build_at_start:buildAtStart,build_at_finish:captureBuildMetadata(),fixture:true,real_model:false,formal_literary_eligible:false,scope:'source and topology display plus final-bound consequence history on synthetic fixtures; no production or literary quality claim',steps,errors},null,2));console.log(JSON.stringify({steps,errors}));await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
