const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../auto_trader/static/stock-detail.js'), 'utf8');
const functions = source.slice(source.indexOf('async function loadDetail(period)'), source.indexOf('function prefetchNearbyPeriods()'));
const chart = {loading:false, busy:null, classList:{add(){chart.loading=true;},remove(){chart.loading=false;}},setAttribute(_,v){chart.busy=v;},removeAttribute(){chart.busy=null;}};
const pending = [];
let requests = 0, rendered;
const scope = {resetChartViewport(){},document:{querySelectorAll:()=>[]},$: selector=>selector==='#detail-chart'?chart:{},renderDetail:data=>{rendered=data;},
  api:()=>{requests++;return new Promise((resolve,reject)=>pending.push({resolve,reject}));}};
vm.createContext(scope);
vm.runInContext('let activePeriod, activeCandleInterval="1m", detailSequence=0; const chartCacheKey=(p,i=activeCandleInterval)=>i+":"+p; const detailCache=new Map(), detailRequests=new Map(); const activeSymbol="005930"; const periodLabels={"1D":["1일"],"1M":["1개월"]};'+functions,scope);
(async()=>{
  vm.runInContext('detailCache.set("1m:1D",{period:"1D"})',scope);
  const slow = vm.runInContext('loadDetail("1M")',scope);
  assert.equal(chart.loading,true);
  await vm.runInContext('loadDetail("1D")',scope);
  assert.equal(chart.loading,false,'cached period must clear old loading overlay');
  assert.equal(chart.busy,null);
  pending.shift().resolve({period:'1M'});await slow;
  assert.equal(rendered.period,'1D','late response must not replace latest selection');
  vm.runInContext('detailCache.delete("1m:1M")',scope);
  const first=vm.runInContext('fetchDetail("1M")',scope), duplicate=vm.runInContext('fetchDetail("1M")',scope);
  assert.equal(first,duplicate,'same-period requests must share one promise');
  pending.shift().resolve({period:'1M'});await first;
  const before=requests;await vm.runInContext('fetchDetail("1M")',scope);assert.equal(requests,before,'cached switch must not request data again');
  console.log('Chart regression checks passed: cached-switch loading, stale response, in-flight merge, cache reuse.');
})().catch(e=>{console.error(e);process.exitCode=1;});
