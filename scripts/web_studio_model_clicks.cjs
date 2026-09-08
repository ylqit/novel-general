/* Real model diagnostic through visible Studio controls. No business API calls. */
const {captureBuildMetadata}=require('./web_test_metadata.cjs');const buildAtStart=captureBuildMetadata();
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const args=Object.fromEntries(process.argv.slice(2).reduce((rows,v,i,a)=>i%2?rows:[...rows,[v.replace(/^--/,''),a[i+1]]],[]));
const {chromium}=require(args.playwright);
const root=path.resolve(args.evidence),server=JSON.parse(fs.readFileSync(path.join(root,'server.json'),'utf8'));
assert.equal(server.fixture,true,'Discussion diagnostics require labelled synthetic fixtures');
const evidence=path.join(root,'model-'+Date.now());fs.mkdirSync(evidence);
const sessionFile=path.join(root,'browser-session.json');
(async()=>{
 const saved=fs.existsSync(sessionFile)?JSON.parse(fs.readFileSync(sessionFile,'utf8')):null;
 const browser=await chromium.launch({executablePath:args.browser,headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'zh-CN',...(saved?.pid===server.pid?{storageState:{...saved.state,origins:[]}}:{})});
 await context.tracing.start({screenshots:true,snapshots:true,sources:true});
 let page=await context.newPage();const steps=[],errors=[],expectedNetworkErrors=[];let offline=false;page.setDefaultTimeout(45000);
 page.on('pageerror',error=>errors.push(error.message));page.on('console',message=>{if(message.type()==='error'){const text=message.text();if(offline&&/ERR_INTERNET_DISCONNECTED|Failed to fetch/.test(text))expectedNetworkErrors.push(text);else errors.push(text)}});
 page.on('dialog',dialog=>dialog.accept());
 try{
  await page.goto(saved?.pid===server.pid?server.base_url:server.bootstrap_url,{waitUntil:'networkidle'});
  await page.locator('.project-card').filter({has:page.getByText('已有正式章节可阅读',{exact:true})}).getByRole('link',{name:'阅读作品',exact:true}).click();
  await page.locator('#prose h1').waitFor();
  for(const scope of ['project','volume','chapter','selection']){
   await page.locator('#discussion-scope').selectOption(scope);
   assert.equal(await page.locator('#discussion-scope').inputValue(),scope);
  }
  const questions=['stale','adopt'].includes(args.phase)?[]:[
   {scope:'project',question:'请分析已声明材料的叙述问题，并明确说明这些界面夹具不能证明小说文学质量。给出两项针对语言表达的具体建议。'},
   {scope:'project',question:'承接上一轮提出的两项建议：哪一项更适合平静过渡场景？请先指出上一轮的建议，再解释适用边界。'},
   ...(args.full==='true'?[
    {scope:'volume',question:'只依据当前卷绑定的批准规划，说明本卷计划的变化与正文已经发生的事情有什么区别。不要把界面夹具当作文学作品。'},
    {scope:'chapter',question:'检查当前章节的重复解释，指出一个需要修改的具体位置，以及有必要保留概述的边界。这是合成界面材料，不能得到文学合格结论。'},
    {scope:'selection',question:'围绕这段引文，建议怎样用行动呈现信息，并说明哪些内容不能因润色而改变。请引用本轮实际选段。'}]:[]),
  ].filter(item=>!args['resume-scope']||['project','volume','chapter','selection'].indexOf(item.scope)>=['project','volume','chapter','selection'].indexOf(args['resume-scope']));
  let suggestionDraft=null,suggestionId=null;
  for(const [index,{scope,question}] of questions.entries()){
   if(scope==='selection'){
    await page.locator('#prose p').first().scrollIntoViewIfNeeded();const rect=await page.locator('#prose p').first().boundingBox();await page.mouse.move(rect.x+2,rect.y+8);await page.mouse.down();await page.mouse.move(rect.x+Math.min(280,rect.width-5),rect.y+8,{steps:15});await page.mouse.up();
    await page.locator('#cite-selection').click();assert.ok((await page.locator('#selection-quote').innerText()).length>3);
   }
   await page.locator('#discussion-scope').selectOption(scope);
   const before=await page.locator('.discussion-turn .answer-message').count();
   await page.getByRole('textbox',{name:'你的问题',exact:true}).fill(question);
   await page.locator('#ask-advisor').click();
   if(args.full==='true'&&index===0){
    await page.locator('#cancel-discussion').waitFor();await page.locator('#cancel-discussion').click();await page.locator('#discussion-state').getByText('已取消',{exact:true}).waitFor();
    assert.equal(await page.locator('.discussion-turn .answer-message').count(),before);await page.locator('.discussion-turn [data-run-discussion]').last().click();
    steps.push({action:'取消真实任务后从原轮次重试，取消不计为回答成功',time:new Date().toISOString(),passed:true});
    await page.locator('#cancel-discussion').waitFor();const unsaved='尚未提交的文字：恢复连接后继续比较。';await page.locator('#discussion-question').fill(unsaved);
    offline=true;await context.setOffline(true);await page.locator('#discussion-state').getByText(/连接暂时中断/).waitFor();assert.equal(await page.locator('#discussion-question').inputValue(),unsaved);
    await context.setOffline(false);await page.waitForFunction(()=>!document.getElementById('discussion-state')?.textContent.includes('连接暂时中断'));offline=false;
    assert.equal(await page.locator('#discussion-question').inputValue(),unsaved);steps.push({action:'限定断网后自动重连，未提交问题保留',time:new Date().toISOString(),passed:true});
    const previousUrl=page.url();await page.close();page=await context.newPage();page.setDefaultTimeout(45000);page.on('pageerror',error=>errors.push(error.message));page.on('console',message=>{if(message.type()==='error')errors.push(message.text())});page.on('dialog',dialog=>dialog.accept());await page.goto(previousUrl,{waitUntil:'networkidle'});await page.locator('#cancel-discussion').waitFor();assert.equal(await page.locator('#discussion-question').inputValue(),unsaved);steps.push({action:'关闭原标签页后重新打开，从服务端接回同一运行任务和问题草稿',time:new Date().toISOString(),passed:true});
   }
   let prior='';const deadline=Date.now()+20*60*1000;
   while(Date.now()<deadline){
    const status=await page.locator('#discussion-state').innerText();
    if(status!==prior){console.log(status);prior=status;}
    if(await page.locator('.discussion-turn .answer-message').count()>before)break;
    if(await page.locator('#discussion-state').getAttribute('data-status')==='failed'||/未通过|失败|已取消|越界|不可执行|无效|禁止|不允许|planning_context_/.test(status))throw Error(status);
    await page.waitForTimeout(1500);
   }
   assert.equal(await page.locator('.discussion-turn .answer-message').count(),before+1,'real model answer recorded');
   if(suggestionId)assert.equal(await page.locator(`[data-adopt-text="${suggestionId}"]`).inputValue(),suggestionDraft,'Generating the next answer must preserve edits to an earlier suggestion');
   assert.ok((await page.locator('.discussion-turn .answer-message').last().innerText()).length>80);
   steps.push({action:'真实模型问答',scope,question,time:new Date().toISOString(),passed:true});
   await page.screenshot({path:path.join(evidence,`answer-${steps.length}.png`),fullPage:true});
   await page.reload({waitUntil:'networkidle'});
   assert.equal(await page.locator('.discussion-turn .answer-message').count(),before+1);
   if(index===0){const field=page.locator('[data-adopt-text]').last();suggestionId=await field.getAttribute('data-adopt-text');suggestionDraft='我对这项建议的未提交修改：保留必要概述，只处理重复解释。';await field.fill(suggestionDraft);await page.reload({waitUntil:'networkidle'});assert.equal(await page.locator(`[data-adopt-text="${suggestionId}"]`).inputValue(),suggestionDraft);}
  }
  if(questions.length||args.phase==='adopt'){
   for(const name of ['存为备忘','意图草稿','规划提案']){
    const turn=page.locator('.discussion-turn').last(),before=Number((await turn.innerText()).match(/已保存 (\d+) 条建议/)?.[1]||0);
    await turn.getByRole('button',{name,exact:true}).click();
    await page.waitForFunction(expected=>{const turns=document.querySelectorAll('.discussion-turn');return Number(turns[turns.length-1]?.textContent.match(/已保存 (\d+) 条建议/)?.[1]||0)===expected},before+1);
    await page.waitForLoadState('networkidle');
    steps.push({action:'保存为'+name+'，等待本次记录增加后再进行下一项',time:new Date().toISOString(),passed:true});
   }
  }
  if(args.phase==='stale'){
   const fixture=path.join(server.workspace,'reader-fixture','novel'),binding=path.join(fixture,'20_outline','book_spine.json');
   const origin=JSON.parse(fs.readFileSync(path.join(fixture,'00_governance','execution_origin.json'),'utf8'));assert.equal(origin.fixture,true);assert.equal(origin.run_id,server.run_id);
   const fields=page.locator('[data-adopt-text]');assert.ok(await fields.count()>0,'Require previously verified real answers');
   const field=fields.last(),turn=await field.getAttribute('data-adopt-text'),draft='来源失效后仍要保留的个人修改文字。';await field.fill(draft);
   const original=fs.readFileSync(binding);
   try{
    fs.writeFileSync(binding,Buffer.concat([original,Buffer.from('\n')])); // Declared version-change fault; restore exact bytes below.
    await page.reload({waitUntil:'networkidle'});await page.locator('.discussion-turn').filter({hasText:'依据已失效'}).first().waitFor();
    assert.equal(await page.locator('[data-adopt]').count(),0,'Stale answers cannot be adopted');
    await page.getByText('查看本机保留的修改文字（依据已失效）',{exact:true}).last().click();
    const kept=page.locator(`[data-adopt-text="${turn}"]`);assert.equal(await kept.inputValue(),draft);assert.equal(await kept.getAttribute('readonly'),'');
    await page.screenshot({path:path.join(evidence,'stale-suggestions.png'),fullPage:true});steps.push({action:'真实回答绑定来源变化后禁止采用，同时保留个人修改文字',passed:true,time:new Date().toISOString()});
   }finally{fs.writeFileSync(binding,original)}
   await page.reload({waitUntil:'networkidle'});await page.locator('[data-adopt]').first().waitFor();assert.equal(await page.locator(`[data-adopt-text="${turn}"]`).inputValue(),draft);
   steps.push({action:'恢复故障文件原始字节后，原回答版本重新一致；未写入正文',passed:true,time:new Date().toISOString()});
  }
  assert.deepEqual(errors,[]);
 }catch(error){steps.push({action:'failure',passed:false,error:error.stack});process.exitCode=1;await page.screenshot({path:path.join(evidence,'failure.png'),fullPage:true}).catch(()=>{});}
 finally{
  await context.setOffline(false);
  fs.writeFileSync(sessionFile,JSON.stringify({pid:server.pid,state:await context.storageState()}));
  await context.tracing.stop({path:path.join(evidence,'model.trace.zip')});
  if(errors.length)process.exitCode=1;
  fs.writeFileSync(path.join(evidence,'model.json'),JSON.stringify({passed:!process.exitCode,run_id:server.run_id,engine_version:server.engine_version,build_at_start:buildAtStart,build_at_finish:captureBuildMetadata(),fixture:true,real_model:!['stale','adopt'].includes(args.phase),steps,errors,expectedNetworkErrors},null,2));
  console.log(JSON.stringify({steps,errors}));await browser.close();
 }
})().catch(error=>{console.error(error);process.exitCode=1});
