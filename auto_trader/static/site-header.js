(() => {
  const header = document.querySelector('.topbar');
  if (!header) return;
  const orderAuth=header.querySelector('#unlock-live-button,[data-header-live-link]');
  const sidebarBottom=document.querySelector('.workspace-nav-bottom');
  if(orderAuth&&sidebarBottom){
    orderAuth.classList.add('sidebar-order-auth');
    const account=sidebarBottom.querySelector('.sidebar-account');
    sidebarBottom.insertBefore(orderAuth,account||null);
  }
  const title = header.querySelector(':scope > div:not(.header-actions)');
  if (title) {
    title.classList.add('site-page-heading');
    header.after(title);
    if (document.body.classList.contains('live-page')) {
      const actions = document.createElement('div'); actions.className = 'site-page-actions';
      const privacy = document.querySelector('.asset-privacy-all');
      if (privacy) actions.append(privacy);
      const refresh = document.createElement('button'); refresh.type = 'button'; refresh.id = 'live-refresh';
      refresh.className = 'button secondary'; refresh.textContent = '↻ 새로고침';
      refresh.addEventListener('click', () => { if (typeof refreshActiveLiveSection === 'function') refreshActiveLiveSection(); });
      actions.append(refresh); title.append(actions);
    }
  }
  const search = document.createElement('div'); search.className = 'site-search';
  search.innerHTML = '<div class="site-search-open"><span aria-hidden="true" class="search-glass"></span><input type="search" placeholder="종목 찾기" aria-label="종목 검색" aria-controls="site-search-results" aria-expanded="false" maxlength="50" autocomplete="off"><kbd aria-hidden="true">/</kbd></div><div id="site-search-results" class="site-search-results" aria-live="polite" hidden></div>';
  header.prepend(search);
  const input = search.querySelector('input'), results = search.querySelector('.site-search-results');
  let serial = 0, controller, timer;
  function invalidate(){serial++;controller?.abort();clearTimeout(timer);}
  function close(){invalidate();results.hidden=true;input.setAttribute('aria-expanded','false');}
  function show(){results.hidden=false;input.setAttribute('aria-expanded','true');}
  function query(event){
    invalidate();const q=input.value.trim();
    if(!q||event?.isComposing){close();return;}
    const version=serial;show();results.textContent='검색 중…';
    timer=setTimeout(async()=>{
      controller=new AbortController();
      try {
        const response=await fetch(`/live/stocks/search?${new URLSearchParams({q,page_size:8})}`,{signal:controller.signal,credentials:'same-origin'});
        if(version!==serial||results.hidden)return;
        if(response.status===401){location.assign('/login');return;}if(!response.ok)throw new Error('검색하지 못했습니다. 다시 입력하세요.');
        const data=await response.json();if(version!==serial||results.hidden)return;
        results.replaceChildren();
        for(const item of data.results){
          const link=document.createElement('a');link.href=`/stocks/${encodeURIComponent(item.symbol)}`;
          link.className='site-search-result';
          if(window.CompanyIcons)link.append(CompanyIcons.icon(item.symbol,item.name));
          const text=document.createElement('span'),name=document.createElement('strong'),code=document.createElement('small');
          name.textContent=item.name;code.textContent=`${item.symbol} · ${item.market}`;text.append(name,code);link.append(text);results.append(link);
        }
        if(!data.results.length)results.textContent='검색 결과가 없습니다.';
      }catch(error){if(version===serial&&!results.hidden&&error.name!=='AbortError')results.textContent=error.message;}
    },100);
  }
  input.addEventListener('input',query);
  input.addEventListener('compositionend',query);
  input.addEventListener('focus',()=>{if(results.hidden&&input.value.trim())query();});
  search.querySelector('.site-search-open').addEventListener('click',()=>input.focus());
  search.addEventListener('keydown',event=>{
    if(event.isComposing)return;
    if(event.key==='Escape'){event.preventDefault();input.focus();close();}
    if(event.key==='ArrowDown'||event.key==='ArrowUp'){
      const links=[...results.querySelectorAll('a')];if(results.hidden||!links.length)return;
      event.preventDefault();const index=links.indexOf(document.activeElement);
      links[(index+(event.key==='ArrowDown'?1:-1)+links.length)%links.length].focus();
    }
    if(event.key==='Enter'&&event.target===input&&!results.hidden){const first=results.querySelector('a');if(first){event.preventDefault();first.click();}}
  });
  document.addEventListener('pointerdown',event=>{if(!search.contains(event.target))close();});
  search.addEventListener('focusout',event=>{if(!search.contains(event.relatedTarget))close();});
  document.addEventListener('keydown',event=>{
    if(event.key==='/'&&!event.ctrlKey&&!event.metaKey&&!event.altKey&&!event.target.isContentEditable&&!['INPUT','SELECT','TEXTAREA'].includes(event.target.tagName)&&!document.querySelector('dialog[open],.modal-backdrop:not(.hidden)')){event.preventDefault();input.focus();}
  });
})();
