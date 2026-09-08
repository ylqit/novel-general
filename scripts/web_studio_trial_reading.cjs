/* Read the actual five-chapter rehearsal through visible UI, then check its
 * final hashes read-only. No API substitution, fixtures, or chapter creation. */
const {captureBuildMetadata}=require('./web_test_metadata.cjs');
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const args=Object.fromEntries(process.argv.slice(2).reduce((rows,v,i,a)=>i%2?rows:[...rows,[v.replace(/^--/,''),a[i+1]]],[]));
const {chromium}=require(args.playwright),root=path.resolve(args.evidence),novel=path.resolve(args['project-root']);
const server=JSON.parse(fs.readFileSync(path.join(root,'server.json'),'utf8'));
assert.ok(novel.startsWith(path.resolve(server.workspace)+path.sep));assert.ok(server.automated_rehearsal&&!server.fixture);
const origin=JSON.parse(fs.readFileSync(path.join(novel,'00_governance/execution_origin.json'),'utf8'));
assert.equal(origin.run_id,server.run_id);assert.equal(origin.simulated_human,true);assert.ok(!origin.fixture);
const project=JSON.parse(fs.readFileSync(path.join(root,'trial-project.json'),'utf8'));
const projectPath=project.project_path.split('?')[0];
const run=path.join(root,'five-chapter-reading-'+Date.now());fs.mkdirSync(run);
const sessionFile=path.join(root,'browser-session.json'),saved=fs.existsSync(sessionFile)?JSON.parse(fs.readFileSync(sessionFile,'utf8')):null;
const buildAtStart=captureBuildMetadata(),chapters=[];
(async()=>{
 const browser=await chromium.launch({executablePath:args.browser,headless:true}),context=await browser.newContext({viewport:{width:1440,height:1100},locale:'zh-CN',...(saved?.pid===server.pid?{storageState:{...saved.state,origins:[]}}:{})});
 await context.tracing.start({screenshots:true,snapshots:true,sources:true});const page=await context.newPage(),steps=[],errors=[];
 page.setDefaultTimeout(60000);page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text()+' '+m.location().url)});
 async function step(name,work){await work();steps.push({name,passed:true,time:new Date().toISOString()});console.log(name)}
 try{
  // Read-only post-production evidence; failure remains visible in this run.
  for(let number=1;number<=5;number++){
   const filename=`ch${String(number).padStart(3,'0')}`,bytes=fs.readFileSync(path.join(novel,'40_manuscript/final',filename+'.md'));
   const hash=crypto.createHash('sha256').update(bytes).digest('hex'),text=bytes.toString('utf8').replaceAll('\r\n','\n');
   const closure=JSON.parse(fs.readFileSync(path.join(novel,'30_state/chapter_closures',filename+'.json'),'utf8'));
   assert.equal(closure.final_sha256,hash);assert.equal(closure.chapter_number,number);
   const chinese=(text.match(/[\u3400-\u9fff]/g)||[]).length;assert.ok(chinese>=2500&&chinese<=3500,`第 ${number} 章有 ${chinese} 个中文字符`);
   chapters.push({number,sha256:hash,chinese_characters:chinese,text,first_paragraph:text.split('\n').find(line=>line.trim().length>6&&!line.startsWith('#'))});
  }
  assert.equal(new Set(chapters.map(chapter=>chapter.sha256)).size,5);
  await page.goto(saved?.pid===server.pid?server.base_url:server.bootstrap_url,{waitUntil:'networkidle'});
  await page.locator(`a[href="${projectPath}?view=read"]`).click();await page.locator('[data-chapter="1"]').click();
  for(const chapter of chapters)await step(`目录连续阅读第 ${chapter.number} 章正式正文`,async()=>{
   await page.waitForFunction(number=>document.getElementById('reading-heading')?.textContent.includes(`第 ${number} 章`),chapter.number);
   await page.locator('#prose').getByText(chapter.first_paragraph,{exact:true}).waitFor();assert.match(await page.locator('#source-state').innerText(),/正式/);
   await page.screenshot({path:path.join(run,`chapter-${chapter.number}.png`),fullPage:false});
   if(chapter.number<5)await page.getByRole('button',{name:'下一章',exact:true}).click();
  });
  await step('从第五章搜索回第一章并核对精确原文',async()=>{
   const quote=chapters[0].first_paragraph.slice(0,18);await page.getByRole('searchbox',{name:'搜索正文与资料'}).fill(quote);await page.getByRole('button',{name:'搜索',exact:true}).click();
   await page.locator('.search-result').filter({hasText:'第 1 章'}).first().click();await page.locator('#prose mark').waitFor();assert.equal(await page.locator('#prose mark').innerText(),quote);
  });
  await step('将第一章圈选原文带回第五章讨论并保存备忘',async()=>{
   await page.locator('#prose p').first().click({clickCount:3});await page.getByRole('button',{name:'引用选中原文',exact:true}).click();
   const quote=await page.locator('#selection-quote').innerText();assert.ok(quote.trim().length>3);
   await page.locator('[data-chapter="5"]').click();await page.waitForFunction(()=>document.getElementById('reading-heading')?.textContent.includes('第 5 章'));
   assert.match(await page.locator('#selection-quote').innerText(),/第 1 章/);
   await page.getByRole('textbox',{name:'你的问题',exact:true}).fill('自动演练的连续性备忘：核对第一章的身份压力与第五章的责任变化；这里保存引用，不代替独立文学评价。');
   await page.getByRole('button',{name:'保存为备忘',exact:true}).click();await page.locator('#toast').getByText(/备忘已保存/).waitFor();
   await page.reload({waitUntil:'networkidle'});await page.locator('#prose').getByText(chapters[4].first_paragraph,{exact:true}).waitFor();assert.match(await page.locator('#selection-quote').innerText(),/第 1 章/);
   await page.screenshot({path:path.join(run,'cross-chapter-reference.png'),fullPage:true});
  });
  assert.deepEqual(errors,[]);assert.ok(!fs.existsSync(path.join(novel,'40_manuscript/final/ch006.md')));
 }catch(error){steps.push({name:'failure',passed:false,error:error.stack});process.exitCode=1;await page.screenshot({path:path.join(run,'failure.png'),fullPage:true}).catch(()=>{})}
 finally{fs.writeFileSync(sessionFile,JSON.stringify({pid:server.pid,state:await context.storageState()}));await context.tracing.stop({path:path.join(run,'reading.trace.zip')});fs.writeFileSync(path.join(run,'reading.json'),JSON.stringify({run_id:server.run_id,engine_version:server.engine_version,build_at_start:buildAtStart,build_at_finish:captureBuildMetadata(),fixture:false,simulated_human:true,formal_literary_eligible:false,chapters:chapters.map(({text,first_paragraph,...chapter})=>chapter),steps,errors},null,2));console.log(JSON.stringify({steps,errors}));await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
