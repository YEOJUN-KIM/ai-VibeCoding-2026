// Independent fake-data HTML only. No production imports, server, or network.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {pathToFileURL}=require('node:url');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/User/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=__dirname,file=path.join(root,'folio-ux02-pc.html'),out=path.join(root,'evidence','integration-20261112');
const series=JSON.parse(fs.readFileSync(path.join(root,'fixtures/asset-series.fictional.json'),'utf8')).samples;
const html=fs.readFileSync(file,'utf8'),original=fs.readFileSync(path.join(root,'reference/folio-ux02-original.html'),'utf8');
fs.mkdirSync(out,{recursive:true});
const sha=s=>crypto.createHash('sha256').update(s).digest('hex');
const scripts=[...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
scripts.forEach(s=>new vm.Script(s));
assert.equal(sha(Buffer.from(original)),'5e011062ea527b40f3e985a3b485a1694d6640d271a73054873d32a3169b3127');
const results={date:new Date().toISOString(),htmlSha256:sha(fs.readFileSync(file)),sourceSha256:sha(fs.readFileSync(path.join(root,'reference/folio-ux02-original.html'))),mode:'headless Chrome, file URL, all network requests aborted',syntax:'PASS',historicalReferenceIntegrity:'PASS',checks:[],layouts:[],consoleErrors:[],externalRequests:[]};
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
 const expectedCount={day:12,week:6,month:24,year:24};
 for(const k of ['day','week','month','year']){
  await page.locator(`[data-period="${k}"]`).click();
  const data=await page.evaluate(()=>({key:currentPeriod,p:periods[currentPeriod],pts:chartPoints,title:document.querySelector('#period-title').textContent,value:document.querySelector('#period-value').textContent,wealth:document.querySelector('.wealth-value').textContent}));
  assert.equal(data.key,k);const S=data.p.samples,first=S[0],last=S.at(-1),gain=last.amount-first.amount;
  assert.equal(S.length,expectedCount[k]);assert.equal(data.pts.length,S.length);
  assert.equal(last.amount,10258000);assert(data.wealth.includes('10,258,000'));
  assert(data.value.includes(gain.toLocaleString('ko-KR')));assert(data.value.includes((gain/first.amount*100).toFixed(2)+'%'));
  // Every plotted sample exists verbatim in the fictional fixture; nothing before 2026-10-11.
  assert(S.every(x=>series.some(r=>r[0]===x.date&&r[1]===x.time&&r[2]===x.amount)));
  assert(S.every(x=>x.t>=Date.parse('2026-10-11T00:00:00+09:00')));assert(S.every((x,i,a)=>!i||x.t>a[i-1].t));
  assert(S.every(x=>/^\d\d:\d\d$/.test(x.time)));
  assert.equal(await page.locator('#chart-count').innerText(),'표본 '+S.length+'개');
  assert.equal(await page.locator('#asset-chart').getAttribute('aria-valuemax'),String(S.length-1));
  const r=await page.locator('#asset-chart').boundingBox();
  const tipFor=async index=>{
   assert.equal(await page.locator('#asset-chart').getAttribute('aria-valuenow'),String(index));
   const tip=await page.locator('#chart-tip').innerText(),sample=S[index],delta=sample.amount-first.amount;
   assert(tip.includes(sample.date.replaceAll('-','.')));assert(tip.includes(sample.time));assert(tip.includes(sample.amount.toLocaleString('ko-KR')));
   assert(tip.includes(Math.abs(delta).toLocaleString('ko-KR')+'원'));assert(tip.includes(Math.abs(delta/first.amount*100).toFixed(2)+'%'));
   const box=await page.locator('#chart-tip').boundingBox();assert(box.x>=r.x-.5&&box.x+box.width<=r.x+r.width+1,'tooltip clipped horizontally');assert(box.y>=r.y-.5&&box.y+box.height<=r.y+r.height+.5,'tooltip clipped vertically '+JSON.stringify({index,box,r}));
   const dot=await page.locator('#chart-dot').boundingBox(),guide=await page.locator('#chart-guide').boundingBox();
   assert(dot&&guide&&guide.height>150,'guide line and point visible');assert(dot.x>=r.x-5&&dot.x<=r.x+r.width+5);
   const overlap=Math.min(box.x+box.width,dot.x+dot.width)-Math.max(box.x,dot.x)>0&&Math.min(box.y+box.height,dot.y+dot.height)-Math.max(box.y,dot.y)>0;assert(!overlap,'tooltip must not cover selected point');
  };
  // Real pointer events exactly on every sample (x from the rendered point), plus both edges.
  for(let i=0;i<S.length;i++){await page.mouse.move(r.x+data.pts[i][0]/1000*r.width,r.y+r.height*.9);await tipFor(i)}
  await page.mouse.move(r.x+1,r.y+r.height*.5);await tipFor(0);await page.mouse.move(r.x+r.width-1,r.y+r.height*.5);await tipFor(S.length-1);
  assert.equal(await page.locator('#chart-from').innerText(),await page.evaluate(()=>stamp(periods[currentPeriod].samples[0])));
  assert.equal(await page.locator('#chart-to').innerText(),await page.evaluate(()=>stamp(periods[currentPeriod].samples.at(-1))));
  await page.mouse.move(r.x-40,r.y-40);await page.locator('#asset-chart').focus();
  await page.keyboard.press('Home');await tipFor(0);await page.keyboard.press('ArrowRight');await tipFor(1);await page.keyboard.press('ArrowLeft');await tipFor(0);
  await page.keyboard.press('ArrowLeft');await tipFor(0);await page.keyboard.press('End');await tipFor(S.length-1);await page.keyboard.press('ArrowRight');await tipFor(S.length-1);
  for(let i=S.length-2;i>=0;i--){await page.keyboard.press('ArrowLeft');await tipFor(i)}
  await page.keyboard.press('Escape');assert(await page.locator('#chart-tip').isHidden());
  const pointsY=data.pts.map(p=>p[1]);assert(new Set(pointsY.map(y=>y.toFixed(1))).size>2,'curve must not be a straight two-point line');
  if(k==='day')assert(Math.max(...pointsY)-Math.min(...pointsY)<120);
  check('period '+k,{samples:S.length,first:first.date+' '+first.time+' '+first.amount,last:last.date+' '+last.time+' '+last.amount,gain,percent:gain/first.amount*100,pointer:'every sample + both edges',keyboard:'Home/End/Left/Right/Escape over all samples',axisAndTooltip:'same samples'});
  await page.locator('#asset-chart').focus();await page.keyboard.press('ArrowLeft');await page.screenshot({path:path.join(out,`chart-${k}-1920.png`)});
 }
 // Representative existing controls, all in the independent in-memory demo.
 await page.locator('[data-tab="settings"]').click();await page.locator('#budget').fill('1000000');await page.locator('#settings-form button[type="submit"]').click();
 assert.equal(await page.evaluate(()=>saved.budget),1000000);assert.equal(await page.evaluate(()=>JSON.stringify(holdings)),baseline.holdings);
 await page.locator('[data-tab="history"]').click();await page.locator('#all-holdings [data-detail="005930"]').click();assert.equal(await page.locator('#modal .lot').count(),2);await page.locator('#modal-close').click();
 await page.locator('[data-filter="관망"]').click();assert.equal(await page.locator('#records details').count(),2);
 await page.locator('[data-tab="home"]').click();await page.locator('#start').click();assert((await page.locator('#run-label').innerText()).includes('신규 매수 제한'));await page.locator('#pause').click();assert((await page.locator('#run-label').innerText()).includes('일시정지'));await page.locator('#emergency').click();assert((await page.locator('#run-label').innerText()).includes('긴급정지'));
 for(const p of ['assets','stocks','research','news','paper']){await page.locator(`.nav [data-page="${p}"]`).click();assert(await page.locator('#page-'+p).isVisible())}
 check('Existing interactions','5 menus, 3 tabs, settings save, holdings unchanged, 2 lot snapshots, wait filter, start/pause/emergency');
 await page.locator('[data-tab="history"]').click();await page.locator('[data-filter="전체"]').click();
 for(const [key,count,gain] of [['all',7,258000],['day',2,35000],['week',6,128000],['month',7,258000],['year',7,258000]]){
  await page.locator(`[data-history-period="${key}"]`).click();assert.equal(await page.locator('#records details').count(),count);
  assert((await page.locator('#history-period-value').innerText()).includes(gain.toLocaleString('ko-KR')));
  assert.equal(await page.locator(`[data-history-period="${key}"]`).getAttribute('aria-pressed'),'true');
 }
 await page.locator('[data-history-period="day"]').click();await page.locator('[data-filter="매도"]').click();assert.equal(await page.locator('#records details').count(),0);
 await page.locator('[data-filter="전체"]').click();await page.locator('[data-history-period="all"]').click();
 await page.locator('#records details').first().locator('summary').click();assert(await page.locator('#records details').first().getAttribute('open')!==null);
 check('History periods and details','5 periods, arithmetic, period + type intersection, expandable record');
 await page.locator('[data-tab="settings"]').click();
 for(const profile of ['안정형','균형형','적극형']){let card=page.locator(`[data-profile="${profile}"]`);await card.hover();assert(await card.locator('.tooltip').isVisible());await card.click();assert.equal(await page.evaluate(()=>draft.profile),profile)}
 await page.locator('[data-sector="IT"]').click();assert.deepEqual(await page.evaluate(()=>draft.sectors),['IT']);
 await page.locator('#custom-profile').click();await page.locator('#m-stop').fill('0');await page.locator('#m-apply').click();assert(await page.locator('#modal-error').innerText());
 await page.locator('#m-stop').fill('4');await page.locator('#m-apply').click();assert(await page.locator('#modal').isHidden());assert.equal(await page.evaluate(()=>draft.stop),4);
 await page.locator('#revert').click();assert.equal(await page.evaluate(()=>draft.stop===saved.stop),true);
 await page.locator('#custom-profile').click();await page.locator('#m-stop').fill('4');await page.locator('#m-cancel').click();assert.equal(await page.evaluate(()=>draft.stop===saved.stop),true);
 const before=await page.evaluate(()=>JSON.stringify(saved));await page.locator('#budget').fill('-1');await page.locator('#settings-form button[type="submit"]').click();assert.equal(await page.evaluate(()=>JSON.stringify(saved)),before);assert(await page.locator('#settings-error').innerText());
 await page.locator('#revert').click();check('Settings basic operations','3 profile tooltips, sectors, custom validation/apply/cancel, revert, invalid budget rejected');

 await page.reload();assert.equal(await page.evaluate(()=>saved.budget),5129000);assert.equal(await page.evaluate(()=>run),'idle');check('Reload resets demo memory','PASS');
 const overlapAny=sel=>page.evaluate(sel=>{const els=[...document.querySelectorAll(sel)].filter(e=>e.getClientRects().length);const out=[];for(let i=0;i<els.length;i++)for(let j=i+1;j<els.length;j++){const x=els[i].getBoundingClientRect(),y=els[j].getBoundingClientRect();if(Math.min(x.right,y.right)-Math.max(x.left,y.left)>1&&Math.min(x.bottom,y.bottom)-Math.max(x.top,y.top)>1)out.push([els[i].className,els[j].className])}return out},sel);
 for(const [width,height] of [[1366,768],[1920,1080],[2560,1440]]){
  await page.setViewportSize({width,height});
  for(const tab of ['home','settings','history']){
   await page.locator(`[data-tab="${tab}"]`).click();await page.mouse.move(220,10);await page.evaluate(()=>scrollTo(0,document.documentElement.scrollHeight));
   const layout=await page.evaluate(()=>{
    const box=e=>{let r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}};
    const visible=e=>!!e.getClientRects().length;
    let controls=[...document.querySelectorAll('button,input,select')].filter(visible).filter(e=>!e.closest('dialog'));
    let overflow=controls.filter(e=>{let r=e.getBoundingClientRect();return r.right>innerWidth+1||r.left<0}).map(e=>e.id||e.textContent);
    let collisions=[];for(let i=0;i<controls.length;i++)for(let j=i+1;j<controls.length;j++){let a=controls[i],b=controls[j];if(a.contains(b)||b.contains(a))continue;let x=a.getBoundingClientRect(),y=b.getBoundingClientRect();if(Math.min(x.right,y.right)-Math.max(x.left,y.left)>2&&Math.min(x.bottom,y.bottom)-Math.max(x.top,y.top)>2)collisions.push([a.id||a.textContent,b.id||b.textContent])}
    const q=s=>document.querySelector(s),content=q('#main').getBoundingClientRect();
    return {viewport:[innerWidth,innerHeight],scrollWidth:document.documentElement.scrollWidth,mainWidth:content.width,shell:box(q('.shell')),chart:box(q('#asset-chart')),overflow,collisions};
   });
   assert(layout.scrollWidth<=width,'horizontal overflow');assert.equal(layout.overflow.length,0);assert.equal(layout.collisions.length,0);
   // Main content width targets from the follow-up brief.
   if(width===1366)assert(layout.mainWidth>=1000&&layout.mainWidth<=1366-218);
   if(width===1920)assert(layout.mainWidth>=1450&&layout.mainWidth<=1600,'1920 main '+layout.mainWidth);
   if(width===2560)assert(layout.mainWidth>=1700&&layout.mainWidth<=1900,'2560 main '+layout.mainWidth);
   const blocks={home:'#paper-home>*',settings:'#settings-form .settings-section,#settings-form .savebar',history:'#paper-history>*'}[tab];
   assert.deepEqual(await overlapAny(blocks),[],'overlapping blocks in '+tab);
   const geo=await page.evaluate(()=>{const r=s=>{const e=document.querySelector(s);if(!e||!e.getClientRects().length)return null;const b=e.getBoundingClientRect();return {l:b.left,r:b.right,t:b.top,b:b.bottom,w:b.width,h:b.height}};return {chartPanel:r('.chart-panel'),holdings:r('#paper-home>.holdings'),summary:r('#paper-home>.summary-grid'),pnl:r('#paper-home>.pnl-grid'),sections:[...document.querySelectorAll('#settings-form .settings-section')].map(e=>{const b=e.getBoundingClientRect();return {l:b.left,t:b.top,w:b.width}}),histPanels:[...document.querySelectorAll('#paper-history>.panel:not(.history-period)')].map(e=>{const b=e.getBoundingClientRect();return {l:b.left,w:b.width}})}});
   if(tab==='home'){
    if(width>=1600){assert(geo.chartPanel.r<=geo.holdings.l+1,'chart and holdings side by side');assert(geo.pnl.r<=geo.summary.l+1);assert(Math.abs(geo.chartPanel.t-geo.holdings.t)<2,'row aligned');assert(Math.abs(geo.chartPanel.h-geo.holdings.h)<2,'equal row heights');assert(layout.chart.width>=780&&layout.chart.width<=1100,'chart width '+layout.chart.width)}
    else{assert(geo.holdings.t>=geo.chartPanel.b-1,'single column below 1600');assert.equal(layout.chart.height,240)}
   }
   if(tab==='settings'&&width>=1600){assert(geo.sections[0].t===geo.sections[1].t&&geo.sections[2].t===geo.sections[3].t,'2x2 settings grid');assert(geo.sections.every(s=>s.w>=500))}
   if(tab==='history'&&width>=1600){assert(geo.histPanels.length===2&&geo.histPanels[1].l>geo.histPanels[0].l+geo.histPanels[0].w-2,'history two columns')}
   results.layouts.push({tab,...layout,geo});await page.screenshot({path:path.join(out,`${tab}-${width}x${height}.png`),fullPage:true});
  }
  // Sticky save bar: never covers a focused field; nothing overlapped at page end; actions still work.
  await page.locator('[data-tab="settings"]').click();await page.evaluate(()=>scrollTo(0,0));
  const fields=await page.locator('#settings-form').locator('input:visible,select:visible,button:visible,summary:visible').evaluateAll(es=>es.filter(e=>!e.closest('.savebar')).map((e,i)=>{e.dataset.vf=i;return i}));
  let stuckSeen=false;
  for(const i of fields){
   await page.locator(`[data-vf="${i}"]`).focus();
   const hit=await page.evaluate(i=>{const e=document.querySelector(`[data-vf="${i}"]`),r=e.getBoundingClientRect(),b=document.querySelector('.savebar').getBoundingClientRect();const inter=Math.min(r.right,b.right)-Math.max(r.left,b.left)>0&&Math.min(r.bottom,b.bottom)-Math.max(r.top,b.top)>0;return {inter,barTop:b.top,innerHeight,fieldBottom:r.bottom,stuck:b.bottom>=innerHeight-1}},i);
   if(hit.stuck)stuckSeen=true;assert(!hit.inter,'save bar covers focused field '+i+' '+JSON.stringify(hit));
  }
  await page.evaluate(()=>scrollTo(0,document.documentElement.scrollHeight));
  const endGeo=await page.evaluate(()=>{const b=document.querySelector('.savebar').getBoundingClientRect();return [...document.querySelectorAll('#settings-form .settings-section,#settings-error')].filter(e=>{const r=e.getBoundingClientRect();return r.height>0&&Math.min(r.bottom,b.bottom)-Math.max(r.top,b.top)>1}).length});
  assert.equal(endGeo,0,'at page end the bar overlaps nothing');
  await page.evaluate(()=>scrollTo(0,Math.floor(document.documentElement.scrollHeight/3)));await page.screenshot({path:path.join(out,`settings-sticky-${width}x${height}.png`)});
  await page.locator('#budget').fill('3000000');assert.equal(await page.evaluate(()=>draft.budget),3000000);
  await page.locator('#revert').click();assert.equal(await page.locator('#budget').inputValue(),String(await page.evaluate(()=>saved.budget)));
  await page.locator('#budget').fill('2000000');await page.locator('#defaults').click();assert.equal(await page.locator('#budget').inputValue(),'5129000');assert.equal(await page.evaluate(()=>draft.profile),'균형형');
  await page.locator('#budget').fill('2500000');await page.locator('#settings-form button[type="submit"]').click();assert.equal(await page.evaluate(()=>saved.budget),2500000);
  await page.locator('#defaults').click();await page.locator('#settings-form button[type="submit"]').click();assert.equal(await page.evaluate(()=>saved.budget),5129000);
  check('Sticky save bar '+width+'x'+height,{focusedFieldsChecked:fields.length,stickyExercisedWhileFocusing:stuckSeen,actions:'save, undo, reset'});
 }
 assert.equal(results.consoleErrors.length,0);assert.equal(results.externalRequests.length,0);
 check('Network and browser errors','No external requests or page/console errors observed');
 results.status='PASS';fs.writeFileSync(path.join(out,'validation.json'),JSON.stringify(results,null,2)+'\n');console.log(JSON.stringify({status:results.status,browser:results.browser,checks:results.checks.length,layouts:results.layouts.length,htmlSha256:results.htmlSha256}));
})().catch(e=>{results.status='FAIL';results.failure=e.stack;fs.writeFileSync(path.join(out,'validation.json'),JSON.stringify(results,null,2)+'\n');console.error(e);process.exitCode=1}).finally(async()=>{if(browser)await browser.close()});
