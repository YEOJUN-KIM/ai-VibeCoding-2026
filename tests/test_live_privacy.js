const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const privacy=fs.readFileSync('auto_trader/static/live-privacy.js','utf8');
const live=fs.readFileSync('auto_trader/static/live.js','utf8');
let stored=null;
function create(){
 const nodes=new Map();
 const node=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'-',innerHTML:'',className:'',dataset:{},attributes:{},setAttribute(k,v){this.attributes[k]=v},removeAttribute(k){delete this.attributes[k]}});return nodes.get(id)};
 const scope={Intl,Map,Set,JSON,Array,localStorage:{getItem:()=>stored,setItem:(key,value)=>stored=value},document:{getElementById:node,querySelector:selector=>node(selector.slice(1)),querySelectorAll:()=>[]}};
 vm.createContext(scope);vm.runInContext(privacy,scope);
 vm.runInContext(live.slice(0,live.indexOf('let csrfToken'))+live.slice(live.indexOf('const escapeHtml'),live.indexOf('async function api'))+live.slice(live.indexOf('function stockCell'),live.indexOf('function renderCandidates'))+'AssetPrivacy.onChange(renderPrivateHoldings);',scope);
 return {scope,node,run:code=>vm.runInContext(code,scope)};
}
const {scope,node,run}=create();
scope.portfolio={account_label:'private-account',total_purchase_krw:123456,market_value:135790,profit_loss:12334,profit_rate:9.99,daily_profit_loss:-555,daily_profit_rate:-0.4,market_open_today:true,daily_profit_reference_date:'2026-10-02',holdings:[{name:'private-stock',symbol:'005930',market_country:'KR',quantity:7,average_purchase_price:98765,last_price:123456,market_value:864192,profit_loss:172837,profit_rate:25}]};
run('renderPortfolio(portfolio)');assert.match(node('live-purchase').textContent,/123,456/);
run("AssetPrivacy.toggle('purchase')");assert.equal(node('live-purchase').textContent,'••••••');assert.equal(node('live-purchase').attributes['aria-label'],'가려진 자산 정보');assert.match(node('live-market-value').textContent,/135,790/);
scope.portfolio.total_purchase_krw=777777;run('renderPortfolio(portfolio)');assert.equal(node('live-purchase').textContent,'••••••','polling never exposes a masked number');
run("AssetPrivacy.toggle('purchase')");assert.match(node('live-purchase').textContent,/777,777/,'unmask shows freshest data');assert.equal(node('live-purchase').attributes['aria-label'],undefined);
run("AssetPrivacy.toggle('all')");assert.equal(node('live-account-label').textContent,'••••••');assert.equal(node('live-profit-rate').textContent,'••••••');assert.equal(node('live-profit').className,'neutral');assert.doesNotMatch(node('live-holdings-body').innerHTML,/private-stock|005930|123,456/);
run('renderPortfolio(portfolio)');assert.doesNotMatch(node('live-holdings-body').innerHTML,/private-stock|005930/);
const restored=create();assert.equal(restored.run("AssetPrivacy.hidden('holdings')"),true,'reload restores only privacy preference');assert.doesNotMatch(stored,/private-account|private-stock|777777/,'stored preference contains no portfolio data');
run("AssetPrivacy.toggle('profit')");assert.notEqual(node('live-profit').textContent,'••••••');assert.equal(node('live-account-label').textContent,'••••••','individual reveal leaves other groups hidden');
run("AssetPrivacy.toggle('all');AssetPrivacy.toggle('all')");assert.match(node('live-holdings-body').innerHTML,/private-stock/);assert.equal(node('live-profit').className,'positive');
stored='invalid JSON';assert.equal(create().run("AssetPrivacy.hidden('assets')"),false);
console.log('Asset privacy passed: per-card masking, all-account/holdings masking, polling, latest-value restore, persistence and malformed storage.');
