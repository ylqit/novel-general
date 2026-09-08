/* Real browser clicks against a live backend. Preparation fixtures are labelled;
 * no business API calls, network interception, or fabricated responses. */
const {captureBuildMetadata}=require('./web_test_metadata.cjs');const buildAtStart=captureBuildMetadata();
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const args = Object.fromEntries(process.argv.slice(2).reduce((rows,v,i,a)=>i%2?rows:[...rows,[v.replace(/^--/,''),a[i+1]]],[]));
const {chromium} = require(args.playwright);
const evidenceRoot = path.resolve(args.evidence);
const server = JSON.parse(fs.readFileSync(path.join(evidenceRoot,'server.json'),'utf8'));

const evidence=path.join(evidenceRoot,'attempt-'+Date.now());fs.mkdirSync(evidence);
const sessionFile=path.join(evidenceRoot,'browser-session.json');
const savedSession=fs.existsSync(sessionFile)?JSON.parse(fs.readFileSync(sessionFile,'utf8')):null;
(async()=>{
 const browser = await chromium.launch({executablePath:args.browser,headless:true});
 const context = await browser.newContext({viewport:{width:1440,height:1000},locale:'zh-CN',...(savedSession?.pid===server.pid?{storageState:{...savedSession.state,origins:[]}}:{})});
 await context.tracing.start({screenshots:true,snapshots:true,sources:true});
 const page = await context.newPage();
 const errors=[];const steps=[];const expectedRejections=[];
 page.on('pageerror',e=>errors.push({message:e.message,url:''}));
 page.on('console',m=>{if(m.type()==='error')errors.push({message:m.text(),url:m.location().url})});
 page.on('response',async response=>{if(response.status()===403||response.status()===409){const body=await response.text().catch(()=>'');if(response.url().endsWith('/edit-draft')&&body.includes('draft_revision_conflict'))expectedRejections.push({url:response.url(),status:response.status(),body})}});
 page.on('dialog',d=>d.accept());
 page.setDefaultTimeout(30000);
 async function step(name,work){await work();steps.push({name,passed:true,time:new Date().toISOString()})}
 try {
  await page.goto(savedSession?.pid===server.pid?server.base_url:server.bootstrap_url,{waitUntil:'networkidle',timeout:90000});
  await step('书架读取实际隔离项目',async()=>{
   await page.getByRole('heading',{name:'作品书架',exact:true}).waitFor();
   await page.getByRole('link',{name:'阅读作品',exact:true}).first().waitFor();
   await page.screenshot({path:path.join(evidence,'shelf.png'),fullPage:true});
  });
  await step('打开历史正式正文',async()=>{
   await page.locator('.project-card').filter({has:page.getByText('已有正式章节可阅读',{exact:true})}).getByRole('link',{name:'阅读作品',exact:true}).click();
   await page.locator('#prose').getByText('第 1 章 阅读夹具',{exact:true}).waitFor();
   assert.match(await page.locator('#source-state').innerText(),/正式/);
   assert.match(await page.locator('#prose').innerText(),/补充字符/);
   await page.screenshot({path:path.join(evidence,'reader-light.png'),fullPage:true});
  });
  if(args.visual==='true'){
   await page.getByRole('button',{name:'阅读设置',exact:true}).click();
   for(const theme of ['light','dark'])await step('正文主题 '+theme+' 的实际文字对比度',async()=>{
    await page.locator('#reading-theme').selectOption(theme);
    const ratios=await page.evaluate(()=>{
     const luminance=color=>color.match(/[\d.]+/g).slice(0,3).map(v=>{const c=Number(v)/255;return c<=.04045?c/12.92:((c+.055)/1.055)**2.4}).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
     return ['body','#manuscript','#toggle-directory'].map(selector=>{const el=document.querySelector(selector),style=getComputedStyle(el);let bg=style.backgroundColor,parent=el;while(/rgba.*,[ ]*0\)/.test(bg)&&parent.parentElement){parent=parent.parentElement;bg=getComputedStyle(parent).backgroundColor}const a=luminance(style.color),b=luminance(bg);return {selector,foreground:style.color,background:bg,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)}});
    });
    for(const row of ratios)assert.ok(row.ratio>=4.5,JSON.stringify(row));
    fs.writeFileSync(path.join(evidence,'contrast-'+theme+'.json'),JSON.stringify(ratios,null,2));
   });
   await step('减少动态效果与可见键盘焦点',async()=>{
    await page.emulateMedia({reducedMotion:'reduce'});
    await page.locator('#toggle-directory').focus();await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'toggle-discussion');
    const style=await page.locator('#toggle-discussion').evaluate(el=>({outline:getComputedStyle(el).outlineWidth,transition:getComputedStyle(el).transitionDuration}));
    assert.ok(parseFloat(style.outline)>=2);assert.equal(style.transition,'0s');
   });
   for(const width of [375,768,1280,1440,1920])await step('只读视觉检查 '+width+'px',async()=>{await page.setViewportSize({width,height:1000});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:path.join(evidence,'visual-'+width+'.png'),fullPage:false})});
   await page.waitForLoadState('networkidle');assert.deepEqual(errors,[]);return;
  }
  await step('前后章与跨卷点击',async()=>{
   await page.getByRole('button',{name:'下一章',exact:true}).click();
   await page.locator('#prose h1').filter({hasText:'第 2 章'}).waitFor({state:'attached'});
   await page.locator('[data-chapter="30"]').click();
   await page.locator('#prose h1').filter({hasText:'第 30 章'}).waitFor();
   await page.getByRole('button',{name:'下一章',exact:true}).click();
   await page.locator('#prose h1').filter({hasText:'第 31 章'}).waitFor();
  });
  await step('阅读设置与刷新恢复',async()=>{
   await page.getByRole('button',{name:'阅读设置',exact:true}).click();
   await page.locator('#reading-theme').selectOption('dark');
   await page.locator('#reading-size').selectOption('24');
   await page.locator('#reading-line').selectOption('2.1');
   await page.locator('#reading-font').selectOption('sans');
   assert.match(await page.locator('#manuscript').getAttribute('class'),/dark/);
   await page.reload({waitUntil:'networkidle'});
   assert.match(await page.locator('#manuscript').getAttribute('class'),/dark/);
   assert.equal(await page.locator('#reading-size').inputValue(),'24');
  });
  await step('修改稿保存并刷新恢复',async()=>{
   await page.getByRole('button',{name:'修改',exact:true}).click();
   const editor=page.getByRole('textbox',{name:'本章修改稿'});
   const original=await editor.inputValue();
   await editor.fill(original+'\n这行只属于修改稿。\n');
   await page.getByRole('button',{name:'保存修改稿',exact:true}).click();
   await page.locator('#edit-state').getByText(/修改稿已保存/).waitFor();
   await page.reload({waitUntil:'networkidle'});
   await page.getByRole('button',{name:'修改',exact:true}).click();
   assert.match(await editor.inputValue(),/这行只属于修改稿/);
   assert.doesNotMatch(await page.locator('#prose').innerText(),/这行只属于修改稿/);
   await page.getByRole('button',{name:'阅读',exact:true}).click();
  });
  await step('正文搜索并定位旧章节',async()=>{
   await page.getByRole('searchbox',{name:'搜索正文与资料'}).fill('测试段落 1：');
   await page.getByRole('button',{name:'搜索',exact:true}).click();
   await page.locator('.search-result').first().click();
   await page.locator('#prose mark').waitFor();
   assert.equal(await page.locator('#prose mark').innerText(),'测试段落 1：');
  });
  await step('读取历史版本且不恢复生产状态',async()=>{
   await page.getByText('历史版本与差异',{exact:true}).click();
   await page.getByLabel('基准版本',{exact:true}).waitFor();
   await page.locator('.version-prose').first().getByText(/陆照/).first().waitFor();
   assert.match(await page.locator('.version-prose').first().innerText(),/阅读夹具/);
   if(args.extended==='true'){
    const archive=page.getByLabel('对照版本',{exact:true}).locator('option').filter({hasText:'归档'}).first();
    await page.getByLabel('对照版本',{exact:true}).selectOption(await archive.getAttribute('value'));
    await page.locator('.version-prose').last().getByText(/旧版本夹具/).waitFor();
    assert.doesNotMatch(await page.locator('#prose').innerText(),/旧版本夹具/);
    assert.ok(await page.locator('.diff-added').count());
   }
  });
  if(args.extended==='true')await step('四百章目录分批加载并定位远章',async()=>{
   while(await page.locator('#more-chapters').isVisible()){
    const before=await page.locator('[data-chapter]').count();await page.locator('#more-chapters').click();
    await page.waitForFunction(before=>document.querySelectorAll('[data-chapter]').length>before,before);
   }
   const numbers=await page.locator('[data-chapter]').evaluateAll(rows=>rows.map(row=>row.dataset.chapter));
   assert.equal(numbers.length,400);assert.equal(new Set(numbers).size,400);
   await page.locator('[data-chapter="400"]').click();await page.locator('#prose h1').filter({hasText:'第 400 章'}).waitFor();
   await page.getByRole('searchbox',{name:'搜索正文与资料'}).fill('第 257 章');await page.getByRole('button',{name:'搜索',exact:true}).click();
   await page.locator('.search-result').filter({hasText:'257'}).first().click();await page.locator('#prose h1').filter({hasText:'第 257 章'}).waitFor();
   await page.locator('[data-chapter="1"]').click();await page.locator('#prose h1').filter({hasText:'第 1 章'}).waitFor();
  });
  if(args.extended==='true'){
   await step('中文输入法提交后刷新可恢复文字',async()=>{
    await page.getByRole('button',{name:'修改',exact:true}).click();const editor=page.getByRole('textbox',{name:'本章修改稿'});
    await editor.focus();await editor.press('Control+End');const cdp=await context.newCDPSession(page);
    await cdp.send('Input.imeSetComposition',{text:'炉',selectionStart:1,selectionEnd:1});
    await cdp.send('Input.imeSetComposition',{text:'炉火中文输入',selectionStart:6,selectionEnd:6});
    await cdp.send('Input.insertText',{text:'炉火中文输入已确认。'});await cdp.detach();
    assert.match(await editor.inputValue(),/炉火中文输入已确认/);await page.reload({waitUntil:'networkidle'});
    await page.getByRole('button',{name:'修改',exact:true}).click();assert.match(await editor.inputValue(),/炉火中文输入已确认/);
    await page.getByRole('button',{name:'阅读',exact:true}).click();
   });
   await step('专注阅读、滚动位置恢复与键盘焦点',async()=>{
    await page.locator('#focus-reader').click();assert.equal(await page.locator('#toggle-directory').getAttribute('aria-expanded'),'false');assert.equal(await page.locator('#toggle-discussion').getAttribute('aria-expanded'),'false');
    await page.mouse.move(800,650);await page.mouse.wheel(0,700);await page.waitForTimeout(250);const position=await page.evaluate(()=>scrollY);assert.ok(position>200);
    await page.reload({waitUntil:'networkidle'});await page.waitForFunction(y=>Math.abs(scrollY-y)<80,position);
    assert.equal(await page.locator('#toggle-directory').getAttribute('aria-expanded'),'false');
    await page.locator('#focus-reader').click();await page.locator('#toggle-directory').focus();await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'toggle-discussion');
   });
  }
  await step('未保存文字经历导航和刷新仍可恢复',async()=>{
   await page.getByRole('button',{name:'修改',exact:true}).click();
   const editor=page.getByRole('textbox',{name:'本章修改稿'});
   await editor.fill((await editor.inputValue())+'\n尚未保存的中文输入：炉火未灭。');
   await page.getByRole('button',{name:'下一章',exact:true}).click();
   await page.locator('#prose h1').filter({hasText:'第 2 章'}).waitFor({state:'attached'});
   await page.getByRole('button',{name:'上一章',exact:true}).click();
   await page.locator('#prose h1').filter({hasText:'第 1 章'}).waitFor({state:'attached'});
   assert.match(await editor.inputValue(),/炉火未灭/);
   await page.reload({waitUntil:'networkidle'});
   await page.getByRole('button',{name:'修改',exact:true}).click();
   assert.match(await editor.inputValue(),/炉火未灭/);
  });
  await step('多标签页保存冲突及明确比较处理',async()=>{
   const other=await context.newPage();other.on('dialog',dialog=>dialog.accept());
   await other.goto(page.url(),{waitUntil:'networkidle'});
   await other.getByRole('button',{name:'修改',exact:true}).click();
   const second=other.getByRole('textbox',{name:'本章修改稿'});
   await second.fill((await second.inputValue())+'\n另一标签页的修改。');
   await other.getByRole('button',{name:'保存修改稿',exact:true}).click();
   await other.locator('#edit-state').getByText(/修改稿已保存/).waitFor();
   const before=await page.getByRole('textbox',{name:'本章修改稿'}).inputValue();
   assert.doesNotMatch(before,/另一标签页的修改/);
   await page.getByRole('button',{name:'保存修改稿',exact:true}).click();
   await page.locator('#edit-state').getByText(/draft_revision_conflict/).waitFor();
   assert.equal(await page.getByRole('textbox',{name:'本章修改稿'}).inputValue(),before);
   await page.getByRole('button',{name:'处理保存冲突',exact:true}).click();
   await page.locator('#before-edit').getByText(/另一标签页/).waitFor();
   await page.getByRole('button',{name:'已比较，保留我的文字并重新保存',exact:true}).click();
   await page.locator('#edit-state').getByText(/按刚刚比较的版本保存/).waitFor();
   await other.close();
   await page.getByRole('button',{name:'阅读',exact:true}).click();
  });
  await step('圈选与跨章引用、问题草稿恢复',async()=>{
   const paragraph=page.locator('#prose p').first();await paragraph.scrollIntoViewIfNeeded();
   const box=await paragraph.boundingBox();
   await page.mouse.move(box.x+3,box.y+12);await page.mouse.down();await page.mouse.move(box.x+225,box.y+12,{steps:12});await page.mouse.up();
   await page.getByRole('button',{name:'引用选中原文',exact:true}).click();
   const quote=await page.locator('#selection-quote').innerText();assert.ok(quote.length>3);
   await page.getByRole('button',{name:'下一章',exact:true}).click();
   await page.locator('#prose h1').filter({hasText:'第 2 章'}).waitFor({state:'attached'});
   assert.match(await page.locator('#selection-quote').innerText(),/第 1 章/);
   await page.getByRole('textbox',{name:'你的问题',exact:true}).fill('这段对白应保留人物的回避方式。');
   await page.reload({waitUntil:'networkidle'});
   assert.equal(await page.getByRole('textbox',{name:'你的问题',exact:true}).inputValue(),'这段对白应保留人物的回避方式。');
   assert.match(await page.locator('#selection-quote').innerText(),/第 1 章/);
   await page.getByRole('button',{name:'移除引用',exact:true}).click();
   assert.equal(await page.locator('#selection-quote').isVisible(),false);
  });
  await step('备忘保存、资料搜索与原文定位',async()=>{
   await page.getByRole('button',{name:'保存为备忘',exact:true}).click();
   await page.locator('#toast').getByText(/备忘已保存/).waitFor();
   await page.getByRole('searchbox',{name:'搜索正文与资料'}).fill('对白应保留人物');
   await page.getByRole('button',{name:'搜索',exact:true}).click();
   await page.locator('.search-result').first().click();
   await page.locator('#document-text mark').waitFor();
   assert.equal(await page.locator('#document-text mark').innerText(),'对白应保留人物');
   await page.getByRole('link',{name:'阅读作品',exact:true}).click();
   await page.locator('#prose h1').waitFor();
  });
  await step('实际总纲资料展示',async()=>{
   await page.getByRole('link',{name:'查看规划',exact:true}).click();
   await page.locator('.document-button').first().waitFor();
   await page.locator('.document-button').filter({hasText:'全书主干'}).click();
   await page.waitForFunction(()=>document.querySelector('#document-text')?.textContent.length>100);
   assert.ok((await page.locator('#document-text').innerText()).length>100);
   await page.screenshot({path:path.join(evidence,'planning.png'),fullPage:true});
   await page.getByRole('link',{name:'阅读作品',exact:true}).click();
   await page.locator('#prose h1').waitFor();
  });
  for(const width of [375,768,1280,1440,1920])await step(`响应式 ${width}px`,async()=>{
   await page.setViewportSize({width,height:1000});
   if(width<=800){
    await page.waitForFunction(()=>document.getElementById('toggle-directory')?.getAttribute('aria-expanded')==='false'&&document.getElementById('toggle-discussion')?.getAttribute('aria-expanded')==='false');
    await page.locator('#toggle-directory').click();await page.locator('.reader-directory').waitFor();
    await page.locator('#toggle-directory').click();await page.locator('.reader-directory').waitFor({state:'hidden'});
   }
   await page.screenshot({path:path.join(evidence,`reader-${width}.png`),fullPage:false});
   const dimensions=await page.evaluate(()=>({width:innerWidth,body:document.documentElement.scrollWidth}));
   assert.ok(dimensions.body<=dimensions.width+1,`horizontal overflow: ${JSON.stringify(dimensions)}`);
  });
  await page.setViewportSize({width:1440,height:1000});
  const created=[];
  for(const suffix of ['甲'+Date.now(),'乙'+Date.now()])await step(`页面创建与作品隔离 ${suffix}`,async()=>{
   await page.getByRole('link',{name:'创建小说',exact:true}).click();
   await page.getByLabel('书名',{exact:true}).fill(`点击验收${suffix}`);
   await page.getByLabel('项目 slug',{exact:true}).fill(`click-${Date.now()}`);
   await page.getByRole('button',{name:'确认创建并开书',exact:true}).click();
   await page.getByRole('heading',{name:`点击验收${suffix}`,exact:true}).waitFor({timeout:90000});
   created.push(page.url());
   assert.match(await page.locator('#app').innerText(),/准备创作设计|等待 Codex|创作进度/);
   await page.getByRole('link',{name:'作品书架',exact:true}).click();
   await page.getByLabel('查找作品',{exact:true}).fill(`点击验收${suffix}`);
   assert.equal(await page.locator('.project-card').count(),1);
   await page.getByRole('link',{name:'阅读作品',exact:true}).click();
   await page.getByText('尚无章节。',{exact:false}).first().waitFor();
   assert.doesNotMatch(await page.locator('#prose').innerText(),/阅读夹具|测试段落/);
  });
  assert.notEqual(created[0],created[1]);
  await page.screenshot({path:path.join(evidence,'new-project-reader.png'),fullPage:true});
  const unexpected=errors.filter(error=>!expectedRejections.some(rejection=>error.url===rejection.url&&error.message.includes(String(rejection.status))));
  assert.deepEqual(unexpected,[],'Unexpected browser errors');
  assert.equal(expectedRejections.length,1,'one deliberate edit conflict');
 } catch(error) {
  steps.push({name:'failure',passed:false,error:error.stack});
  await page.screenshot({path:path.join(evidence,'failure.png'),fullPage:true}).catch(()=>{});
  process.exitCode=1;
 } finally {
  fs.writeFileSync(sessionFile,JSON.stringify({pid:server.pid,state:await context.storageState()}));
  await context.tracing.stop({path:path.join(evidence,'clicks.trace.zip')});
  if(errors.filter(error=>!expectedRejections.some(item=>error.url===item.url&&error.message.includes(String(item.status)))).length)process.exitCode=1;
  fs.writeFileSync(path.join(evidence,'clicks.json'),JSON.stringify({passed:!process.exitCode,run_id:server.run_id,engine_version:server.engine_version,build_at_start:buildAtStart,build_at_finish:captureBuildMetadata(),fixture:server.fixture,real_model:false,steps,browser_errors:errors,expected_http_rejections:expectedRejections},null,2));
  console.log(JSON.stringify({steps,errors},null,2));
  await browser.close();
 }
})().catch(e=>{console.error(e);process.exitCode=1});
