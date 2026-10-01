const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const nodes=new Map(),node=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',value:'111000',dataset:{edited:'true'}});return nodes.get(id)};
let socket;class ES{constructor(){socket=this}close(){this.closed=true}}
const data={previous_close:100,candles:[{timestamp:'2026-10-01T05:10:00Z',open_price:100,high_price:101,low_price:99,close_price:100,volume:10}]};
const scope={document:{hidden:false,querySelector:node,addEventListener(){}},window:{addEventListener(){}},EventSource:ES,activeSymbol:'005930',activePeriod:'1D',lastDetailData:data,detailCache:new Map(),won:{format:n=>String(n)},periodLabels:{'1D':['1일']},formatTimestamp:t=>t,chartSvg:c=>'chart '+c.at(-1).close_price,setInterval(){},clearInterval(){},api(){return Promise.resolve(data)},renderDetail(){},detailSequence:1,Date,Number,Math};
vm.createContext(scope);vm.runInContext(fs.readFileSync('auto_trader/static/detail-stream.js','utf8'),scope);scope.window.startDetailStream();
const send=(price,timestamp)=>socket.onmessage({data:JSON.stringify({type:'message',topic:'trade:kr:005930',data:{price,timestamp}})});
send(105,'2026-10-01T05:10:20Z');assert.equal(node('#detail-price').textContent,'105');assert.equal(node('#detail-change').textContent,'+5 (+5.00%)');assert.equal(data.candles[0].close_price,105);assert.equal(data.candles[0].volume,10);assert.equal(node('#order-price').value,'111000');
send(90,'2026-10-01T05:09:20Z');assert.equal(node('#detail-price').textContent,'105');
send(106,'2026-10-01T05:11:00Z');assert.equal(data.candles.length,2);assert.equal(data.candles[1].volume,0);assert.equal(node('#detail-chart').innerHTML,'chart 106');console.log('Stream price/rate, minute rollover, stale ticks, volume and order input preservation passed');

scope.activeCandleInterval='1w';scope.activePeriod='1Y';scope.periodLabels['1Y']=['1년'];data.candle_interval='1w';data.candles=[{timestamp:'2026-09-27T15:00:00Z',open_price:100,high_price:106,low_price:99,close_price:106,volume:100}];
send(107,'2026-10-02T05:11:00Z');assert.equal(data.candles.length,1);assert.equal(data.candles[0].close_price,107);assert.equal(data.candles[0].volume,100);
send(108,'2026-10-05T05:11:00Z');assert.equal(data.candles.length,2);assert.equal(data.candles[1].timestamp,'2026-10-04T15:00:00.000Z');
console.log('Weekly stream bucket boundaries passed');
