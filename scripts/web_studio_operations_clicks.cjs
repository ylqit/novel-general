/* Source import and operational pages on a live, explicitly labelled fixture. */
const {captureBuildMetadata}=require('./web_test_metadata.cjs');const buildAtStart=captureBuildMetadata();
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const args=Object.fromEntries(process.argv.slice(2).reduce((rows,v,i,a)=>i%2?rows:[...rows,[v.replace(/^--/,''),a[i+1]]],[]));
const {chromium}=require(args.playwright),root=path.resolve(args.evidence),server=JSON.parse(fs.readFileSync(path.join(root,'server.json'),'utf8'));
if(!server.fixture)throw Error('These operational tests require labelled fixtures');
const run=path.join(root,'operations-'+Date.now());fs.mkdirSync(run);
(async()=>{
 const saved=JSON.parse(fs.readFileSync(path.join(root,'browser-session.json'),'utf8'));
 const browser=await chromium.launch({executablePath:args.browser,headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'zh-CN',...(saved.pid===server.pid?{storageState:{...saved.state,origins:[]}}:{})});
 await context.tracing.start({screenshots:true,snapshots:true,sources:true});const page=await context.newPage(),steps=[],errors=[],expected=[];
 page.setDefaultTimeout(45000);page.on('pageerror',e=>errors.push({message:e.message,url:''}));page.on('console',m=>{if(m.type()==='error')errors.push({message:m.text(),url:m.location().url})});
 page.on('response',async response=>{if([403,409].includes(response.status())){const body=await response.text().catch(()=>'');if((response.url().endsWith('/recovery/action')&&body.includes('恢复对象已变化'))||(response.url().endsWith('/literary/create')&&body.includes('automated_rehearsal_ineligible'))||(response.url().endsWith('/publication/action')&&/blocked|rights|policy|预检|未通过|不能|阻断/.test(body)))expected.push({url:response.url(),status:response.status(),body})}});
 page.on('dialog',d=>d.accept());
 async function step(name,work){await work();steps.push({name,passed:true,time:new Date().toISOString()});fs.writeFileSync(path.join(run,'steps.json'),JSON.stringify(steps,null,2));console.log(name)}
 try{
  await page.goto(saved.pid===server.pid?server.base_url:server.bootstrap_url,{waitUntil:'networkidle'});
  await page.locator('.project-card').filter({has:page.getByText('已有正式章节可阅读',{exact:true})}).getByRole('link',{name:'继续创作',exact:true}).click();
  if(args.phase==='recovery'){
   const projectRoot=path.resolve(server.workspace,'reader-fixture','novel');
   const snapshot=()=>{const result={};for(const name of ['00_governance','10_bible','20_outline','30_state','40_manuscript/final']){const directory=path.join(projectRoot,name);if(fs.existsSync(directory))for(const relative of fs.readdirSync(directory,{recursive:true})){const file=path.join(directory,relative);if(fs.statSync(file).isFile())result[path.relative(projectRoot,file)]=require('node:crypto').createHash('sha256').update(fs.readFileSync(file)).digest('hex')}}return result};
   const canonicalBefore=snapshot(),recoveryRequests=[];page.on('request',request=>{if(request.method()==='POST'&&request.url().endsWith('/recovery/action'))recoveryRequests.push(request.url())});
   await page.getByRole('link',{name:'恢复与审计',exact:true}).click();
   for(const [action,label] of [['discard','丢弃未应用准备'],['rollback','回滚未完成写入'],['cleanup','清理已提交快照']])await step('实际故障恢复：'+label,async()=>{
    // Preparation only: interrupt a real transaction on a fixed workbench file
    // in the labelled synthetic project. The recovery itself is a browser click.
    const prepare=String.raw`
from pathlib import Path
import json, sys
from longform_engine.storage import apply_transaction
from longform_engine.storage import project as storage
root=Path(sys.argv[1]).resolve();action=sys.argv[2]
origin=json.loads((root/'00_governance/execution_origin.json').read_text(encoding='utf-8'))
assert origin['fixture'] is True and origin['run_id']==sys.argv[3]
assert action in {'discard','rollback','cleanup'}
target=root/'50_workbench/studio/recovery_fixture.txt';target.parent.mkdir(parents=True,exist_ok=True)
target.write_text('before',encoding='utf-8')
transaction=apply_transaction(root,command='Web '+action+' recovery fixture',touched_paths=[target])
if action=='discard':
 def interrupt(*args,**kwargs):raise RuntimeError('fixture snapshot interruption')
 original=storage.snapshot_transaction_path;storage.snapshot_transaction_path=interrupt
 try:
  try:transaction.begin()
  except RuntimeError as error:assert str(error)=='fixture snapshot interruption'
  else:raise AssertionError('fault did not trigger')
 finally:storage.snapshot_transaction_path=original
elif action=='cleanup':
 original=storage.cleanup_transaction_snapshot;storage.cleanup_transaction_snapshot=lambda path:['fixture cleanup interruption']
 try:
  with transaction:target.write_text('after',encoding='utf-8')
 finally:storage.cleanup_transaction_snapshot=original
else:
 transaction.begin();target.write_text('interrupted',encoding='utf-8')
print(json.dumps({'report':str(transaction.report_file),'target':str(target)}))
`;
    const record=JSON.parse(require('node:child_process').execFileSync(args.python,['-X','utf8','-c',prepare,projectRoot,action,server.run_id],{encoding:'utf8',windowsHide:true}));
    await page.locator('#refresh-recovery').click();const button=page.getByRole('button',{name:label,exact:true});await button.waitFor();assert.equal(await button.isDisabled(),true,'Exact acknowledgement is required');
    await page.getByLabel('我已核对并确认'+label,{exact:true}).check();assert.equal(await button.isEnabled(),true);
    if(action==='discard'){
     const original=fs.readFileSync(record.report);
     try{fs.writeFileSync(record.report,Buffer.concat([original,Buffer.from('\n')]));await button.click();await page.locator('#operation-result').getByText(/恢复对象已变化/).waitFor();assert.equal(fs.readFileSync(record.target,'utf8'),'before');await page.screenshot({path:path.join(run,'recovery-stale.png'),fullPage:true});}
     finally{fs.writeFileSync(record.report,original)}
     await page.locator('#refresh-recovery').click();await button.waitFor();await page.getByLabel('我已核对并确认'+label,{exact:true}).check();
    }
    const requestCount=recoveryRequests.length;await button.dblclick();await page.getByText('没有待恢复的阻断，已有正文可正常阅读。',{exact:true}).waitFor();assert.equal(recoveryRequests.length,requestCount+1,'Double click must execute one recovery request');
    const report=JSON.parse(fs.readFileSync(record.report,'utf8'));assert.equal(report.cleanup_complete,true);assert.equal(report.status,{discard:'aborted_before_apply',rollback:'rolled_back',cleanup:'applied'}[action]);assert.equal(report.recovered_by||report.cleanup_approved_by,'human');assert.equal(fs.existsSync(path.resolve(projectRoot,report.snapshot_dir)),false,'Successful recovery removes its snapshot');
    assert.equal(fs.readFileSync(record.target,'utf8'),action==='cleanup'?'after':'before');assert.deepEqual(snapshot(),canonicalBefore,'Recovery fixture must not alter canonical content');
    await page.screenshot({path:path.join(run,'recovery-'+action+'.png'),fullPage:true});
   });
   await page.waitForLoadState('networkidle');return;
  }
  await page.getByRole('link',{name:'原著资料',exact:true}).click();await page.getByRole('link',{name:'导入与处理原著资料',exact:true}).click();
  const workName='界面资料夹具'+Date.now(),itemName='验材记录 '+Date.now();
  await step('通过表单登记资料所属作品',async()=>{
   await page.locator('#source-work-name').fill(workName);await page.locator('#source-work-creator').fill('隔离测试夹具');await page.locator('#source-work-version').fill('界面测试版本');
   await page.getByRole('button',{name:'保存作品资料',exact:true}).click();await page.locator('#source-register-result').getByText(/作品资料已保存/).waitFor();
   await page.locator('#nav').getByRole('button',{name:'批量导入',exact:true}).click();await page.locator('#uploadWork').selectOption({label:workName});
  });
  await step('真实上传、自然分组确认和导入',async()=>{
   await page.locator('#sourceType').fill('自写界面测试笔记');await page.locator('#sourceVersion').fill('夹具第一版');await page.locator('#unitRange').fill('独立测试摘录');
   await page.locator('#files').setInputFiles({name:'验材记录.txt',mimeType:'text/plain',buffer:Buffer.from('这是明确标识的界面测试资料，不是小说或原著事实。\n验材记录：接触材料才可以判断残留火息。\n污染会影响测量，需要重复核对。','utf8')});
   await page.locator('#upload').click();await page.locator('#source-group-name-0').waitFor();await page.locator('#source-group-name-0').fill(itemName);await page.locator('#source-group-approve-0').check();
   await page.locator('#confirmGroups').click();await page.waitForFunction(()=>{try{return JSON.parse(document.querySelector('#groupResult').textContent).approved_group_count===1}catch{return false}});
   await page.locator('#applyIngest').click();await page.waitForFunction(()=>{try{return JSON.parse(document.querySelector('#groupResult').textContent).status==='applied'}catch{return false}});
   await page.locator('#nav').getByRole('button',{name:'原著资料库',exact:true}).click();await page.locator('#source-library-cards article').filter({hasText:itemName}).getByRole('button',{name:'打开资料',exact:true}).click();
  });
  await step('处理真实上传文本并查看证据摘录',async()=>{
   await page.locator('#processPlan').click();await page.waitForFunction(()=>{try{return Boolean(JSON.parse(document.querySelector('#processResult').textContent).job_id)}catch{return false}});
   await page.locator('#processRun').click();await page.waitForFunction(()=>{try{const r=JSON.parse(document.querySelector('#processResult').textContent);return r.schema==='source_normalization_manifest_v1'}catch{return false}});
   await page.locator('#source-preview').click();await page.locator('#source-preview-text').getByText(/接触材料才可以判断/).waitFor();
   await page.screenshot({path:path.join(run,'source-evidence.png'),fullPage:true});
  });
  for(const width of [375,768,1280,1440,1920])await step(`资料页 ${width}px`,async()=>{await page.setViewportSize({width,height:1000});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:path.join(run,`sources-${width}.png`)})});
  await page.setViewportSize({width:1440,height:1000});await page.getByRole('link',{name:'返回作品资料',exact:true}).click();
  await step('平台预检与带模拟来源声明的本机材料导出',async()=>{
   await page.getByRole('link',{name:'发布中心',exact:true}).click();await page.locator('#publication-preflight').click();await page.getByText('预检已生成，请查看实际待处理项。',{exact:true}).waitFor();
   const previous=await page.locator('#operation-result').textContent();await page.locator('#publication-export').click();await page.waitForFunction(previous=>{const text=document.querySelector('#operation-result')?.textContent||'';return text.trim()!==previous.trim()},previous);
   assert.match(await page.locator('#operation-result').innerText(),/自动演练阅读材料已生成/);
   const result=JSON.parse(await page.locator('#operation-result pre').textContent());
   const projectRoot=path.resolve(server.workspace,'reader-fixture','novel'),bundle=path.resolve(projectRoot,result.bundle_file);
   assert.ok(bundle.startsWith(path.join(projectRoot,'80_exports')+path.sep));
   const text=fs.readFileSync(bundle,'utf8');assert.match(text,/自动演练材料：人工步骤由测试流程模拟，未通过真实人工文学或平台发布验收/);
   assert.match(text,/# 第 1 章 阅读夹具/);assert.match(text,/# 第 400 章 阅读夹具/);
  });
  await step('恢复状态刷新和已有内容正常访问',async()=>{await page.getByRole('link',{name:'恢复与审计',exact:true}).click();await page.getByText('没有待恢复的阻断，已有正文可正常阅读。',{exact:true}).waitFor();await page.locator('#refresh-recovery').click();await page.getByText('没有待恢复的阻断，已有正文可正常阅读。',{exact:true}).waitFor()});
  await step('自动演练不冒充正式文学样本',async()=>{
   await page.getByRole('link',{name:'质量评测',exact:true}).click();await page.locator('#create').waitFor();
   assert.match(await page.locator('#app').innerText(),/自动演练/);assert.match(await page.locator('#app').innerText(),/未验证/);
   await page.locator('[name="trial_id"]').fill('fixture-'+Date.now());await page.locator('[name="stage"]').selectOption('opening');
   await page.locator('#samples .source').selectOption(new URL(page.url()).pathname.split('/')[2]);
   await page.getByRole('button',{name:'核对证据并创建匿名包',exact:true}).click();await page.locator('#message').getByText(/自动演练|simulated|rehearsal/).waitFor();
   await page.screenshot({path:path.join(run,'literary-ineligible.png'),fullPage:true});
  });
  assert.deepEqual(errors.filter(error=>!expected.some(item=>item.url===error.url&&error.message.includes(String(item.status)))),[]);
 }catch(error){steps.push({name:'failure',passed:false,error:error.stack});process.exitCode=1;await page.screenshot({path:path.join(run,'failure.png'),fullPage:true}).catch(()=>{});}
 finally{fs.writeFileSync(path.join(root,'browser-session.json'),JSON.stringify({pid:server.pid,state:await context.storageState()}));await context.tracing.stop({path:path.join(run,'operations.trace.zip')});if(errors.filter(error=>!expected.some(item=>error.url===item.url&&error.message.includes(String(item.status)))).length)process.exitCode=1;
  fs.writeFileSync(path.join(run,'operations.json'),JSON.stringify({passed:!process.exitCode,run_id:server.run_id,engine_version:server.engine_version,build_at_start:buildAtStart,build_at_finish:captureBuildMetadata(),fixture:true,real_model:false,steps,errors,expected_rejections:expected},null,2));console.log(JSON.stringify({steps,errors,expected}));await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
