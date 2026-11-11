// Independent fake-data HTML only. No production imports, server, or network.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {pathToFileURL}=require('node:url');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/User/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=__dirname,file=path.join(root,'folio-ux02-pc.html'),out=path.join(root,'evidence');
const html=fs.readFileSync(file,'utf8'),original=fs.readFileSync(path.join(root,'reference/folio-ux02-original.html'),'utf8');
fs.mkdirSync(out,{recursive:true});
const sha=s=>crypto.createHash('sha256').update(s).digest('hex');
const scripts=[...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
scripts.forEach(s=>new vm.Script(s));
assert.equal(sha(Buffer.from(original)),'5e011062ea527b40f3e985a3b485a1694d6640d271a73054873d32a3169b3127');
// All original interaction code outside chart block remains byte-identical in text.
const originalJS=[...original.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)][0][1];
assert.equal(scripts[0].split('const sample=')[0].split('// Source has')[0],originalJS.split('const periods=')[0]);
assert.equal(scripts[0].split('function holdingTable()')[1],originalJS.split('function holdingTable()')[1]);
const results={date:new Date().toISOString(),htmlSha256:sha(fs.readFileSync(file)),sourceSha256:sha(fs.readFileSync(path.join(root,'reference/folio-ux02-original.html'))),mode:'headless Chrome, file URL, all network requests aborted',syntax:'PASS',preservedInteractionCode:'PASS',checks:[],layouts:[],consoleErrors:[],externalRequests:[]};
let browser;
(async()=>{
 browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--disable-background-networking','--disable-component-update','--no-first-run']});
 results.browser=browser.version();
 const context=await browser.newContext({viewport:{width:1920,height:1080},deviceScaleFactor:1,serviceWorkers:'block'});
 await context.route('**/*',r=>{if(/^https?:/.test(r.request().url())){results.externalRequests.push(r.request().url());return r.abort()}return r.continue()});
 const page=await context.newPage();page.on('pageerror',e=>results.consoleErrors.push(e.message));page.on('console',m=>{if(m.type()==='error')results.consoleErrors.push(m.text())});
 await page.goto(pathToFileURL(file).href);await page.waitForSelector('#plot path');
 const check=(name,detail)=>results.checks.push({name,result:'PASS',detail});
 const ids=await page.locator('[id]').evaluateAll(es=>es.map(e=>e.id));assert.equal(new Set(ids).size,ids.length);check('HTML browser parse and duplicate IDs',{count:ids.length,compatMode:await page.evaluate(()=>document.compatMode)});
 assert.equal(await page.locator('.nav [data-page]').count(),5);assert.equal(await page.locator('[data-tab]').count(),3);
 assert.equal(await page.locator('.chart-panel h2,.chart-panel .sub,#plot text').count(),0);check('Removed visible chart heading/subtitle/Y numbers','PASS');
 const baseline=await page.evaluate(()=>({holdings:JSON.stringify(holdings),saved:JSON.stringify(saved)}));
 for(const k of ['day','week','month','year']){
  await page.locator(`[data-period="${k}"]`).click();
  const data=await page.evaluate(()=>({key:currentPeriod,p:periods[currentPeriod],pts:chartPoints,title:document.querySelector('#period-title').textContent,value:document.querySelector('#period-value').textContent}));
  assert.equal(data.key,k);let first=data.p.samples[0],last=data.p.samples.at(-1),gain=last.amount-first.amount;
  assert.equal(last.amount,10258000);assert(data.value.includes(gain.toLocaleString('ko-KR')));assert(data.value.includes((gain/first.amount*100).toFixed(2)+'%'));
  assert(data.p.samples.every(s=>s.t>=Date.parse('2026-10-11T00:00:00+09:00')));assert(data.p.samples.every((s,i,a)=>!i||s.t>a[i-1].t));
  const r=await page.locator('#asset-chart').boundingBox();
  // Real pointer events at boundaries, any height, and either side of nearest-point midpoint.
  for(const [fraction,index] of [[0,0],[.2,0],[.49,0],[.51,1],[.8,1],[.999,1]]){
   await page.mouse.move(r.x+r.width*fraction+.1,r.y+r.height*.9);
   assert.equal(await page.locator('#asset-chart').getAttribute('aria-valuenow'),String(index));
   const tip=await page.locator('#chart-tip').innerText(),sample=data.p.samples[index];
   assert(tip.includes(sample.date.replaceAll('-','.')));assert(tip.includes(sample.time||'시각 미제공'));assert(tip.includes(sample.amount.toLocaleString('ko-KR')));
   const delta=sample.amount-first.amount;assert(tip.includes(Math.abs(delta).toLocaleString('ko-KR')+'원'));assert(tip.includes((delta/first.amount*100).toFixed(2)+'%'));
   const box=await page.locator('#chart-tip').boundingBox();assert(box.x>=r.x&&box.x+box.width<=r.x+r.width+1);assert(box.y>=r.y&&box.y+box.height<=r.y+r.height);
   const dot=await page.locator('#chart-dot').boundingBox();assert(box.y+box.height<dot.y||box.y>dot.y+dot.height,'tooltip must not cover sample dot');
  }
  assert.equal(await page.locator('#chart-from').innerText(),await page.evaluate(()=>stamp(periods[currentPeriod].samples[0])));
  assert.equal(await page.locator('#chart-to').innerText(),await page.evaluate(()=>stamp(periods[currentPeriod].samples.at(-1))));
  await page.locator('#asset-chart').focus();await page.keyboard.press('Home');assert.equal(await page.locator('#asset-chart').getAttribute('aria-valuenow'),'0');await page.keyboard.press('ArrowRight');assert.equal(await page.locator('#asset-chart').getAttribute('aria-valuenow'),'1');await page.keyboard.press('ArrowLeft');assert.equal(await page.locator('#asset-chart').getAttribute('aria-valuenow'),'0');await page.keyboard.press('End');assert.equal(await page.locator('#asset-chart').getAttribute('aria-valuenow'),'1');await page.keyboard.press('Escape');assert(await page.locator('#chart-tip').isHidden());
  if(k==='day')assert(Math.abs(data.pts[1][1]-data.pts[0][1])<35);
  check('period '+k,{gain,percent:gain/first.amount*100,samples:data.p.samples,points:data.pts,pointer:'6 positions',keyboard:'Home/End/Left/Right/Escape',axisAndTooltip:'same samples'});
  await page.locator('#asset-chart').focus();await page.keyboard.press('End');await page.screenshot({path:path.join(out,`chart-${k}-1920.png`)});
 }
 // Representative existing controls, all in the independent in-memory demo.
 await page.locator('[data-tab="settings"]').click();await page.locator('#budget').fill('1000000');await page.locator('#settings-form button[type="submit"]').click();
 assert.equal(await page.evaluate(()=>saved.budget),1000000);assert.equal(await page.evaluate(()=>JSON.stringify(holdings)),baseline.holdings);
 await page.locator('[data-tab="history"]').click();await page.locator('#all-holdings [data-detail="005930"]').click();assert.equal(await page.locator('#modal .lot').count(),2);await page.locator('#modal-close').click();
 await page.locator('[data-filter="관망"]').click();assert.equal(await page.locator('#records details').count(),2);
 await page.locator('[data-tab="home"]').click();await page.locator('#start').click();assert((await page.locator('#run-label').innerText()).includes('신규 매수 제한'));await page.locator('#pause').click();assert((await page.locator('#run-label').innerText()).includes('일시정지'));await page.locator('#emergency').click();assert((await page.locator('#run-label').innerText()).includes('긴급정지'));
 for(const p of ['assets','stocks','research','news','paper']){await page.locator(`.nav [data-page="${p}"]`).click();assert(await page.locator('#page-'+p).isVisible())}
 check('Existing interactions','5 menus, 3 tabs, settings save, holdings unchanged, 2 lot snapshots, wait filter, start/pause/emergency');
 await page.reload();assert.equal(await page.evaluate(()=>saved.budget),5129000);assert.equal(await page.evaluate(()=>run),'idle');check('Reload resets demo memory','PASS');
 for(const [width,height] of [[1366,768],[1920,1080],[2560,1440]]){
  await page.setViewportSize({width,height});
  for(const tab of ['home','settings','history']){
   await page.locator(`[data-tab="${tab}"]`).click();await page.mouse.move(220,10);
   const layout=await page.evaluate(()=>{
    const box=e=>{let r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right}};
    const visible=e=>!!e.getClientRects().length;
    let controls=[...document.querySelectorAll('button,input,select')].filter(visible).filter(e=>!e.closest('dialog'));
    let overflow=controls.filter(e=>{let r=e.getBoundingClientRect();return r.right>innerWidth+1||r.left<0}).map(e=>e.id||e.textContent);
    let collisions=[];for(let i=0;i<controls.length;i++)for(let j=i+1;j<controls.length;j++){let a=controls[i],b=controls[j];if(a.contains(b)||b.contains(a))continue;let x=a.getBoundingClientRect(),y=b.getBoundingClientRect();if(Math.min(x.right,y.right)-Math.max(x.left,y.left)>2&&Math.min(x.bottom,y.bottom)-Math.max(x.top,y.top)>2)collisions.push([a.id||a.textContent,b.id||b.textContent])}
    return {viewport:[innerWidth,innerHeight],scrollWidth:document.documentElement.scrollWidth,shell:box(document.querySelector('.shell')),chart:box(document.querySelector('#asset-chart')),overflow,collisions};
   });
   assert(layout.scrollWidth<=width);assert.equal(layout.overflow.length,0);assert.equal(layout.collisions.length,0);assert(layout.shell.width<=1224);if(tab==='home'){assert(layout.chart.width<=902&&layout.chart.width>=800);assert.equal(layout.chart.height,240)}
   results.layouts.push({tab,...layout});await page.screenshot({path:path.join(out,`${tab}-${width}x${height}.png`),fullPage:true});
  }
 }
 assert.equal(results.consoleErrors.length,0);assert.equal(results.externalRequests.length,0);
 check('Network and browser errors','No external requests or page/console errors observed');
 results.status='PASS';fs.writeFileSync(path.join(out,'validation.json'),JSON.stringify(results,null,2)+'\n');console.log(JSON.stringify({status:results.status,browser:results.browser,checks:results.checks.length,layouts:results.layouts.length,htmlSha256:results.htmlSha256}));
})().catch(e=>{results.status='FAIL';results.failure=e.stack;fs.writeFileSync(path.join(out,'validation.json'),JSON.stringify(results,null,2)+'\n');console.error(e);process.exitCode=1}).finally(async()=>{if(browser)await browser.close()});
