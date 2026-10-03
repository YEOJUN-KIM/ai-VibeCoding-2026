(() => {
  const heading=document.querySelector('.site-page-heading');if(!heading)return;
  const strip=document.createElement('div');strip.className='market-hours';strip.setAttribute('aria-label','정규장 운영 상태');
  for(const [market,label] of [['KR','국내 장'],['US','해외 장']]){
    const item=document.createElement('span');item.dataset.market=market;item.dataset.state='unknown';item.textContent=label+' 확인 중';item.title=market==='KR'?'국내 정규장 기준':'미국 정규장 기준 · 프리·애프터마켓 제외';strip.append(item);
  }
  heading.append(strip);
  const formatTime=value=>new Date(value).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',month:'numeric',day:'numeric',weekday:'short',hour:'2-digit',minute:'2-digit',hour12:false});
  function render(item,status){
    const market=item.dataset.market,state=['open','closed'].includes(status?.state)?status.state:'unknown';
    const label=(market==='KR'?'국내 장 ':'해외 장 ')+(state==='open'?'열림':state==='closed'?(status.holiday?'휴장':'닫힘'):'확인 불가');
    item.dataset.state=state;
    const copy=document.createElement('span');copy.className='market-hours-copy';
    const title=document.createElement('span');title.textContent=label;copy.append(title);
    const stamp=state==='open'?status.closes_at:state==='closed'?status.next_open_at:null;
    let detail='';
    if(stamp&&!Number.isNaN(new Date(stamp).getTime())){
      detail=(state==='open'?'종료 ':'다음 개장 ')+formatTime(stamp);
      const time=document.createElement('small');time.textContent=detail;copy.append(time);
    }
    item.replaceChildren(copy);
    item.title=(market==='KR'?'국내 정규장 기준':'미국 정규장 기준 · 프리·애프터마켓 제외 · 휴장 여부는 미국 현지 날짜 기준')+(detail?' · '+detail+' (한국시간)':'');
  }
  let busy=false;
  async function update(){
    if(busy||document.hidden)return;busy=true;
    try{
      const response=await fetch('/market-hours',{credentials:'same-origin'});if(!response.ok)throw new Error();
      const data=await response.json();
      strip.querySelectorAll('[data-market]').forEach(item=>{
        render(item,data[item.dataset.market]);
      });
    }catch(_){strip.querySelectorAll('[data-market]').forEach(item=>render(item,null));}
    finally{busy=false;}
  }
  update();setInterval(update,60000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)update();});
})();
