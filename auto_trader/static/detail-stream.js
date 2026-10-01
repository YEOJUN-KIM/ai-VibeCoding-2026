(() => {
  let stream=null, syncTimer=null, latest=null, syncing=false;
  const status=(text)=>{const node=document.querySelector('#detail-stream-status');if(node)node.textContent=text;};
  function bucket(time, interval) {
    if(interval==='1m')return Math.floor(time/60000)*60000;
    if(interval==='1h')return Math.floor(time/3600000)*3600000;
    const local=new Date(time+9*3600000);
    let year=local.getUTCFullYear(), month=local.getUTCMonth(), day=local.getUTCDate();
    if(interval==='1w')day-=(local.getUTCDay()+6)%7;
    if(interval==='1mo')day=1;
    if(interval==='1y'){month=0;day=1;}
    return Date.UTC(year,month,day)-9*3600000;
  }
  function apply(frame, force=false){
    if(!lastDetailData || frame.topic!==`trade:kr:${activeSymbol}`)return;
    const tick=frame.data, price=Number(tick?.price), time=Date.parse(tick?.timestamp);
    if(!Number.isFinite(price)||price<=0||!Number.isFinite(time))return;
    if(!force&&latest&&time<Date.parse(latest.data.timestamp))return;
    latest=frame;
    const data=lastDetailData;
    data.price=price;
    const base=Number(data.previous_close);
    const rate=base>0?(price/base-1)*100:null;
    data.change_rate_percent=rate;
    document.querySelector('#detail-price').textContent=won.format(price);
    const difference=base>0?price-base:null;
    document.querySelector('#detail-change').textContent=rate==null?'-':`${difference>0?'+':difference<0?'−':''}${won.format(Math.abs(difference))} (${rate>0?'+':''}${rate.toFixed(2)}%)`;
    document.querySelector('#detail-previous-close').textContent=`전일 종가 ${base>0?won.format(base):'-'}`;
    document.querySelector('#detail-change').className=rate==null||rate===0?'neutral':rate>0?'positive':'negative';
    document.querySelector('#detail-price-date').textContent=`${new Date(time).toLocaleTimeString('ko-KR')} 체결`;
    status('실시간 수신 중 · 체결가 반영');
    // Stream ticks are lossy. Only provisional OHLC is updated here; candle
    // volume remains the REST value and is resynchronized once per minute.
    const candles=data.candles;
    const last=candles.at(-1);
    const selected=typeof activeCandleInterval==='undefined'?(activePeriod==='1D'?'1m':'1d'):activeCandleInterval;
    const interval=data.candle_interval || selected;
    if(last && interval===selected){
      const lastTime=Date.parse(last.timestamp);
      const same=bucket(lastTime,interval)===bucket(time,interval);
      if(same){last.close_price=price;last.high_price=Math.max(Number(last.high_price),price);last.low_price=Math.min(Number(last.low_price),price);}
      else if(time>lastTime){candles.push({timestamp:new Date(bucket(time,interval)).toISOString(),open_price:price,high_price:price,low_price:price,close_price:price,volume:0});}
      if(time>=lastTime){
        document.querySelector('#detail-chart').innerHTML=chartSvg(candles);
        window.restoreChartHover?.();
        document.querySelector('#chart-end').textContent=formatTimestamp(candles.at(-1).timestamp);
        const highs=candles.map(c=>Number(c.high_price)), lows=candles.map(c=>Number(c.low_price));
        document.querySelector('#detail-high').textContent=won.format(Math.max(...highs));
        document.querySelector('#detail-low').textContent=won.format(Math.min(...lows));
        document.querySelector('#chart-range').textContent=`${periodLabels[activePeriod][0]} · ${won.format(Math.min(...lows))}–${won.format(Math.max(...highs))}`;
      }
    }
    if(interval===selected)detailCache.set(typeof chartCacheKey==='undefined'?activePeriod:chartCacheKey(activePeriod),data);
    document.querySelector('#detail-message').textContent='가격은 실시간 체결 기준 · 장중 봉은 잠정값 · 봉·거래량은 1분마다 다시 맞춥니다.';
  }
  async function sync(){
    if(syncing||document.hidden||!lastDetailData)return;
    syncing=true;const period=activePeriod, sequence=detailSequence;
    const interval=typeof activeCandleInterval==='undefined'?'1m':activeCandleInterval;
    try{const data=await api(`/live/stocks/${encodeURIComponent(activeSymbol)}/detail?period=${period}&candle_interval=${interval}&refresh=true`);
      if(period===activePeriod&&sequence===detailSequence){detailCache.clear();detailCache.set(typeof chartCacheKey==='undefined'?period:chartCacheKey(period,interval),data);renderDetail(data);if(latest)apply(latest,true);}
    }catch{status('봉 동기화 지연 · 마지막 수신값 표시');}finally{syncing=false;}
  }
  function open(){
    if(document.hidden||stream||!activeSymbol||!lastDetailData)return;
    status('실시간 연결 중');
    stream=new EventSource(`/live/stocks/${encodeURIComponent(activeSymbol)}/stream`);
    stream.onmessage=event=>{
      let frame;try{frame=JSON.parse(event.data);}catch{return;}
      if(frame.type==='message')apply(frame);
      else if(frame.type==='status'){
        const labels={connecting:'실시간 연결 중',connected:'실시간 연결됨 · 다음 체결 대기',reconnecting:'시세 연결 끊김 · 재연결 중',rejected:'실시간 구독 거부',unauthorized:'로그인 또는 화면 잠금 확인 필요'};
        status(labels[frame.state]||'시세 연결 확인 중');
        if(frame.state==='connected')sync();
        if(frame.state==='unauthorized'||frame.state==='rejected'){stream.close();stream=null;}
      }
    };
    stream.onerror=()=>status('시세 연결 끊김 · 재연결 중 · 마지막 수신값 표시');
  }
  window.reapplyDetailStream=()=>{if(latest)apply(latest,true);};
  window.startDetailStream=()=>{open();if(!syncTimer)syncTimer=setInterval(sync,60000);};
  document.addEventListener('visibilitychange',()=>{if(document.hidden){stream?.close();stream=null;status('다른 화면 보는 중 · 연결 대기');}else{sync();open();}});
  window.addEventListener('pagehide',()=>{stream?.close();clearInterval(syncTimer);});
})();
