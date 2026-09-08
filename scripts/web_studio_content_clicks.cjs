/* Actual reading views over a labelled closed fixture. Faults are restricted to
 * its planning index and restored byte-for-byte; no business API substitutes. */
const {captureBuildMetadata}=require('./web_test_metadata.cjs');const buildAtStart=captureBuildMetadata();
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const args=Object.fromEntries(process.argv.slice(2).reduce((rows,v,i,a)=>i%2?rows:[...rows,[v.replace(/^--/,''),a[i+1]]],[]));
const {chromium}=require(args.playwright),root=path.resolve(args.evidence),server=JSON.parse(fs.readFileSync(path.join(root,'server.json'),'utf8'));
assert.ok(server.fixture&&server.automated_rehearsal);
const novel=path.resolve(server.workspace,'literary-fixture','novel'),origin=JSON.parse(fs.readFileSync(path.join(novel,'00_governance/execution_origin.json'),'utf8'));
assert.equal(origin.fixture,true);assert.equal(origin.run_id,server.run_id);
const sessionFile=path.join(root,'browser-session.json'),saved=fs.existsSync(sessionFile)?JSON.parse(fs.readFileSync(sessionFile,'utf8')):null;
const run=path.join(root,'content-'+Date.now());fs.mkdirSync(run);
(async()=>{
 const browser=await chromium.launch({executablePath:args.browser,headless:true}),context=await browser.newContext({viewport:{width:1440,height:1100},locale:'zh-CN',...(saved?.pid===server.pid?{storageState:saved.state}:{})});
 await context.tracing.start({screenshots:true,snapshots:true,sources:true});const page=await context.newPage(),steps=[],errors=[];
 page.setDefaultTimeout(45000);page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text()+' '+m.location().url)});page.on('dialog',d=>d.accept());
 async function step(name,work){await work();steps.push({name,passed:true,time:new Date().toISOString()});console.log(name)}
 async function document(relative,expected){
  const id='doc_'+crypto.createHash('sha256').update(relative).digest('hex').slice(0,24);
  await page.locator('#document-filter').fill(relative);await page.locator(`[data-document="${id}"]`).click();
  await page.waitForFunction(relative=>document.getElementById('document-basis')?.textContent.startsWith(relative),relative);
  assert.ok((await page.locator('#document-text').innerText()).includes(expected));
 }
 try{
  await page.goto(saved?.pid===server.pid?server.base_url:server.bootstrap_url,{waitUntil:'networkidle'});
  await page.getByRole('link',{name:'继续创作',exact:true}).first().click();
  await step('开书与世界实际资料可读',async()=>{
   await page.getByRole('link',{name:'开书与设计',exact:true}).click();await document('00_governance/idea_seed.md','中文长篇小说读者');
   assert.ok((await page.locator('.project-name').innerText()).trim());assert.equal(await page.locator('#workspace').innerText(),await page.locator('.project-name').innerText());
   await page.getByRole('link',{name:'知识与伏笔',exact:true}).click();await document('10_bible/world.md','Memory edits always leave physical evidence.');
  });
  await step('总纲、卷纲与滚动规划分开呈现',async()=>{
   await page.getByRole('link',{name:'总纲与规划',exact:true}).click();await document('20_outline/book_outline.md','Ten escalating evidence arcs.');
   const volume=JSON.parse(fs.readFileSync(path.join(novel,'20_outline/volumes/vol001.json'),'utf8'));
   await document('20_outline/volumes/vol001.json',volume.entry_state);
   assert.match(await page.locator('#document-basis').innerText(),/规划内容，不代表正文中已发生/);
   await document('20_outline/rolling_window.json','近期详细规划');
   await page.locator('#document-filter').fill('第 1 章');assert.ok(await page.locator('#document-index [data-document]').count()>=3);
   await page.screenshot({path:path.join(run,'planning-documents.png'),fullPage:true});
  });
  await step('人物声音、承诺与关系图谱详情',async()=>{
   await page.getByRole('link',{name:'知识与伏笔',exact:true}).click();await document('10_bible/characters.json','Ari');
   await document('10_bible/character_expression.json','protect the border archive');
   await document('30_state/reader_promise_ledger.json','The opening conflict produces a usable clue and changed alliance.');
   await document('30_state/foreshadowing_state.json','计划中');await document('30_state/narrative_events/ch001.json','已有正文实现依据');await document('30_state/semantic_ledger/ch001.json','The protagonist\'s action changes the next available decision.');
   await document('30_state/story_graph.json','个对象');await page.locator('#graph-filter').fill('Ari');await page.locator('[data-entity="lead_ari"]').click();
   await page.locator('#graph-detail h3').getByText('Ari',{exact:true}).waitFor();assert.match(await page.locator('#graph-detail').innerText(),/Mira/);
   assert.match(await page.locator('#graph-detail').innerText(),/同盟/);assert.match(await page.locator('#graph-detail').innerText(),/计划中/);
   await page.getByText('展开依据与有效范围',{exact:true}).first().click();assert.match(await page.locator('#graph-detail').innerText(),/lead_ari/);
   await page.screenshot({path:path.join(run,'graph-details.png'),fullPage:true});
  });
  for(const width of [375,768,1280,1440,1920])await step(`资料和图谱布局 ${width}px`,async()=>{
   await page.setViewportSize({width,height:1000});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:path.join(run,`content-${width}.png`)});
  });
  await page.setViewportSize({width:1440,height:1100});await page.getByRole('link',{name:'阅读作品',exact:true}).click();await page.locator('#prose h1').waitFor();
  assert.equal(await page.locator('#workspace').innerText(),await page.locator('.reading-book-name').innerText());
  const final=path.join(novel,'40_manuscript/final/ch001.md'),finalBytes=fs.readFileSync(final),body=await page.locator('#prose').innerText();
  const skeleton=path.join(novel,'20_outline/volume_skeletons.json'),basis=path.join(novel,'30_state/planning_basis.json'),originalSkeleton=fs.readFileSync(skeleton),originalBasis=fs.readFileSync(basis);
  await step('卷范围冲突不猜测归属，正式正文仍可阅读',async()=>{
   try{
    const s=JSON.parse(originalSkeleton),b=JSON.parse(originalBasis);assert.ok(s.items.length>=2);s.items[1].chapter_range=[...s.items[0].chapter_range];
    const replacement=Buffer.from(JSON.stringify(s));fs.writeFileSync(skeleton,replacement);
    const binding=b.source_files.find(row=>row.path==='20_outline/volume_skeletons.json');assert.ok(binding);binding.sha256=crypto.createHash('sha256').update(replacement).digest('hex');fs.writeFileSync(basis,JSON.stringify(b));
    await page.reload({waitUntil:'networkidle'});await page.locator('#directory-issues').getByText(/卷范围冲突/).waitFor();await page.locator('#prose h1').waitFor();assert.equal(await page.locator('#prose').innerText(),body);
    assert.equal(await page.locator('#chapter-directory details [data-chapter="1"]').count(),0);assert.equal(await page.locator('#chapter-directory > [data-chapter="1"]').count(),1);
    await page.screenshot({path:path.join(run,'overlapping-volumes.png'),fullPage:true});
   }finally{fs.writeFileSync(skeleton,originalSkeleton);fs.writeFileSync(basis,originalBasis)}
  });
  await step('批准依据失效时仍然阅读已完成正文',async()=>{
   try{
    fs.writeFileSync(basis,'{}');await page.reload({waitUntil:'networkidle'});await page.getByText('卷目录批准绑定已失效，请重新批准规划',{exact:true}).waitFor();await page.locator('#prose h1').waitFor();assert.equal(await page.locator('#prose').innerText(),body);
    await page.getByRole('link',{name:'作品概览',exact:true}).click();await page.locator('.action-box').waitFor();assert.match(await page.locator('.action-box').innerText(),/规划|依据|planning|need|阻断|修复|处理/);
    await page.getByRole('link',{name:'阅读作品与目录',exact:true}).click();await page.locator('#prose h1').waitFor();assert.equal(await page.locator('#prose').innerText(),body);
   }finally{fs.writeFileSync(basis,originalBasis)}
   assert.deepEqual(fs.readFileSync(final),finalBytes);assert.deepEqual(fs.readFileSync(skeleton),originalSkeleton);assert.deepEqual(fs.readFileSync(basis),originalBasis);
  });
  assert.deepEqual(errors,[]);
 }catch(error){steps.push({name:'failure',passed:false,error:error.stack});process.exitCode=1;await page.screenshot({path:path.join(run,'failure.png'),fullPage:true}).catch(()=>{})}
 finally{fs.writeFileSync(sessionFile,JSON.stringify({pid:server.pid,state:await context.storageState()}));await context.tracing.stop({path:path.join(run,'content.trace.zip')});if(errors.length)process.exitCode=1;
  fs.writeFileSync(path.join(run,'content.json'),JSON.stringify({passed:!process.exitCode,run_id:server.run_id,engine_version:server.engine_version,build_at_start:buildAtStart,build_at_finish:captureBuildMetadata(),fixture:true,steps,errors},null,2));console.log(JSON.stringify({steps,errors}));await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
