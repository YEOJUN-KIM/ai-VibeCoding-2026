const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('auto_trader/static/stocks.js','utf8');
let resolveRequest,rejectRequest,calls=0;
const button={dataset:{favoriteSymbol:'005930',favoriteActive:'false'},disabled:false,textContent:'♡',classList:{toggle(){}},getAttribute(){return '삼성전자 관심 종목에 추가'},setAttribute(){}};
const message={textContent:''};
const scope={api:()=>{calls++;return new Promise((resolve,reject)=>{resolveRequest=resolve;rejectRequest=reject})},$:()=>message};vm.createContext(scope);
vm.runInContext(source.slice(source.indexOf('async function toggleFavorite('),source.indexOf("$(\"#stock-filter-form\").addEventListener")),scope);
(async()=>{const first=scope.toggleFavorite(button);assert.equal(button.textContent,'♥');assert.equal(button.disabled,true);await scope.toggleFavorite(button);assert.equal(calls,1);resolveRequest({});await first;assert.equal(button.disabled,false);const second=scope.toggleFavorite(button);assert.equal(button.textContent,'♡');rejectRequest(new Error('save failed'));await second;assert.equal(button.textContent,'♥');assert.equal(button.dataset.favoriteActive,'true');assert.equal(message.textContent,'save failed');assert.equal(button.disabled,false);console.log('Favorite optimistic update, duplicate click guard and rollback passed');})().catch(e=>{console.error(e);process.exitCode=1});
