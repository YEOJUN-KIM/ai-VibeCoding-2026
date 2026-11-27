// Served only by scripts/pagination_mock.py. Run await runPaginationChecks() in the mock tab.
window.runPaginationChecks = async function () {
  if (location.hostname !== '127.0.0.1' || !location.port) throw Error('mock loopback required');
  const report = { route:location.pathname, viewport:innerWidth, checks:0, geometry:[], errors:[] };
  const check = (condition, label) => { report.checks++; if (!condition) report.errors.push(label); };
  const settle = async predicate => {
    const end = performance.now() + 5000;
    while (!predicate()) { if (performance.now() > end) throw Error('UI timeout'); await new Promise(resolve=>setTimeout(resolve,20)); }
  };
  const holdingsDetail = () => {
    document.querySelector('#all-holdings [data-detail]').click();
    check(document.querySelector('#modal').open,'holding detail opens');
    check(document.querySelector('#modal-body').textContent.includes('매수 기준단가'),'holding lot detail preserved');
    document.querySelector('#modal-close').click();
  };
  const mock = async values => { const r = await fetch('/__mock?' + new URLSearchParams(values)); if (!r.ok) throw Error('mock state failed'); };
  const rootFor = key => document.querySelector(key.startsWith('#') ? key : '#' + key + '-page')?.closest('.folio-pagination');
  const rect = el => { const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height}; };
  const measure = (root, label) => {
    const controls = root.querySelector('.folio-page-controls');
    const buttons = [...controls.children];
    const row = {label, root:rect(root), controls:rect(controls), slots:buttons.map(rect), numbers:[...root.querySelectorAll('.folio-page-number')].map(el=>({text:el.textContent,...rect(el)}))};
    report.geometry.push(row);
    check(controls.scrollWidth <= root.clientWidth, label + ': controls fit root');
    check(row.numbers.every(r=>r.width===row.numbers[0].width),label+': equal number widths');
    check(row.numbers.every(r=>r.width>=24),label+': 108 fits reserved width');
    check([...root.querySelectorAll('.folio-page-number')].every(el=>getComputedStyle(el).fontVariantNumeric==='tabular-nums' && el.scrollWidth<=el.clientWidth),label+': tabular digits fit');
    return row;
  };
  const stable = rows => {
    const initial = rows[0];
    rows.slice(1).forEach(row=>check(row.slots.every((r,i)=>r.x===initial.slots[i].x && r.width===initial.slots[i].width),row.label+': stable navigation x/width'));
  };
  const numbers = root => [...root.querySelectorAll('.folio-page-number')].map(el=>Number(el.textContent));
  const expectNumbers = (root, expected, label) => check(JSON.stringify(numbers(root))===JSON.stringify(expected),label);
  const edges = root => [...root.querySelector('.folio-page-controls').children].filter(el=>el.tagName==='BUTTON');
  if (location.pathname === '/paper-mvp') {
    await mock({records:1074,holdings:1074}); await refresh();
    for (const [key,renderList] of [['home',renderHome],['all',renderHistory],['records',renderHistory]]) {
      tab = key==='home'?'home':'history';
      ['home','settings','history'].forEach(t=>document.querySelector('#paper-'+t).hidden=t!==tab);
      const selector = {home:'#home-holdings',all:'#all-holdings',records:'#records'}[key];
      const getRoot = () => document.querySelector(selector+' .folio-pagination');
      const rows=[];
      for (const p of [1,7,108]) {
        pageOf[key]=p;renderList();
        rows.push(measure(getRoot(),key+':'+p));
        check(pageOf[key]===p,key+': selected page '+p);
      }
      stable(rows);
      pageOf[key]=1;renderList();
      check(edges(getRoot()).slice(0,2).every(b=>b.disabled),key+': first disabled');
      edges(getRoot())[2].click();check(pageOf[key]===2,key+': next');
      edges(getRoot())[1].click();check(pageOf[key]===1,key+': prev');
      edges(getRoot())[3].click();const last=pageOf[key];
      check(edges(getRoot()).slice(2).every(b=>b.disabled),key+': last disabled');
      edges(getRoot())[0].click();check(pageOf[key]===1,key+': first');
      getRoot().querySelector('[data-page-to="3"].folio-page-number').click();check(pageOf[key]===3,key+': number click');
      await refresh();check(pageOf[key]===3,key+': refresh retention');
      check(document.querySelectorAll(selector+(key==='records'?' .record':' tbody tr')).length===(key==='home'?5:10),key+': page size');
      check(last>3,key+': last page available');
    }
    tab='history';filter='전체';historyPeriod='all';
    for (const total of [0,1,2,3,4,5,6]) {
      await mock({records:total*10,holdings:total*10});await refresh();pageOf.records=1;renderHistory();
      const root=document.querySelector('#records .folio-pagination');
      if(total<=1)check(!root,'records hidden '+total);
      else {expectNumbers(root,Array.from({length:Math.min(total,5)},(_,i)=>i+1),'records exact numbers '+total);measure(root,'records total:'+total);}
    }
    await mock({records:1074,holdings:1074});await refresh();
    for(const key of ['home','all']) {
      const size=key==='home'?5:10;
      const selector=key==='home'?'#home-holdings':'#all-holdings';
      tab=key==='home'?'home':'history';
      ['home','settings','history'].forEach(t=>document.querySelector('#paper-'+t).hidden=t!==tab);
      for(const total of [1,2,5,6]) {
        await mock({holdings:total*size});await refresh();pageOf[key]=1;(key==='home'?renderHome:renderHistory)();
        const root=document.querySelector(selector+' .folio-pagination');
        if(total===1)check(!root,key+' hidden one');
        else {expectNumbers(root,Array.from({length:Math.min(5,total)},(_,i)=>i+1),key+' exact total '+total);measure(root,key+' total:'+total);}
      }
    }
    tab='history';['home','settings','history'].forEach(t=>document.querySelector('#paper-'+t).hidden=t!==tab);
    holdingsDetail();
    await mock({records:1074,holdings:1074});await refresh();
    for(const type of ['매수','매도','관망','제어','전체']) {
      pageOf.records=7;
      document.querySelector('[data-filter="'+type+'"]').click();
      check(pageOf.records===1,'filter reset '+type);
      const expected=records().filter(x=>type==='전체'||x.type===type);
      check(document.querySelector('#record-count').textContent.includes(expected.length+'건'),'filter exact count '+type);
      check([...document.querySelectorAll('#records .record .badge')].every(el=>type==='전체'||el.textContent===type),'filter category '+type);
      if(type==='제어')check(expected.length===135,'control count excludes other records');
    }
    const detail=document.querySelector('#records details');detail.open=true;
    await new Promise(resolve=>setTimeout(resolve,30));await refresh();
    check(document.querySelector('#records details').open,'expanded detail survives refresh');
    document.querySelector('[data-history-period="day"]').click();await refresh();check(pageOf.records===1,'period filter reset');
    document.querySelector('[data-history-period="all"]').click();await refresh();
    pageOf.records=108;pageOf.all=108;pageOf.home=108;
    await mock({records:25,holdings:25});await refresh();
    check(pageOf.records===3 && pageOf.all===3 && pageOf.home===5,'shrink clamp all lists');
    await mock({records:0,holdings:0});await refresh();
    check(pageOf.records===1 && pageOf.all===1 && pageOf.home===1,'zero clamp all lists');
    check(!document.querySelector('.folio-pagination'),'zero lists hidden');
    await mock({records:1074,holdings:1074});await refresh();
  } else if (location.pathname === '/stocks') {
    document.querySelector('#stock-page-size').value='20';
    await mock({directory:2148});
    const rows=[];
    for(const page of [1,7,108]) {await loadStocks(page);check(activePage===page,'stock selected '+page);rows.push(measure(rootFor('stock'),'stock:'+page));}
    stable(rows);
    const getRoot=()=>rootFor('stock');
    check(edges(getRoot()).slice(2).every(b=>b.disabled),'stock last disabled');
    document.querySelector('#stock-first').click();await settle(()=>activePage===1&&!loading);
    check(edges(getRoot()).slice(0,2).every(b=>b.disabled),'stock first disabled');
    document.querySelector('#stock-next').click();await settle(()=>activePage===2&&!loading);
    document.querySelector('#stock-prev').click();await settle(()=>activePage===1&&!loading);
    getRoot().querySelector('[data-page-to="5"]').click();await settle(()=>activePage===5&&!loading);
    check(activePage===5,'stock navigation buttons');
    for(const total of [0,1,2,5,6]) {await mock({directory:total*20});await loadStocks(1);if(total<=1)check(getRoot().hidden,'stock hidden '+total);else expectNumbers(getRoot(),Array.from({length:Math.min(5,total)},(_,i)=>i+1),'stock exact '+total);}
    await mock({directory:1074});await loadStocks(1);
  } else if (location.pathname === '/long-term') {
    await mock({directory:1074});await loadMyCandidates();selectLongTermPageTab('mine');
    const rows=[];
    for(const page of [1,7,108]){myPage=page;renderMyCandidates(myCandidates);rows.push(measure(rootFor('my-watch'),'watch:'+page));}
    stable(rows);
    check(document.querySelector('#my-watch-last').disabled,'watch last disabled');
    document.querySelector('#my-watch-first').click();document.querySelector('#my-watch-next').click();check(myPage===2,'watch next');
    document.querySelector('#my-watch-prev').click();check(myPage===1,'watch prev');
    rootFor('my-watch').querySelector('[data-page-to="5"]').click();check(myPage===5,'watch number');
    await loadMyCandidates();check(myPage===5,'watch refresh retention');
    for(const total of [0,1,2,5,6]){await mock({directory:total*10});await loadMyCandidates();myPage=1;renderMyCandidates(myCandidates);if(total<=1)check(rootFor('my-watch').hidden,'watch hidden '+total);else expectNumbers(rootFor('my-watch'),Array.from({length:Math.min(5,total)},(_,i)=>i+1),'watch exact '+total);}
    await mock({directory:1074});await loadMyCandidates();
    selectLongTermPageTab('etf');await loadEtfs(true);await settle(()=>!document.querySelector('#etf-next').disabled);
    const etfRows=[];
    document.querySelector('#etf-first').click();await settle(()=>document.querySelector('#etf-page').textContent==='1 / 108'&&!document.querySelector('#etf-next').disabled);
    etfRows.push(measure(rootFor('etf'),'etf:1'));
    for(let i=1;i<7;i++){document.querySelector('#etf-next').click();await settle(()=>document.querySelector('#etf-page').textContent===(i+1)+' / 108'&&!document.querySelector('#etf-next').disabled);}
    etfRows.push(measure(rootFor('etf'),'etf:7'));
    document.querySelector('#etf-last').click();await settle(()=>document.querySelector('#etf-page').textContent==='108 / 108'&&!document.querySelector('#etf-first').disabled);
    etfRows.push(measure(rootFor('etf'),'etf:108'));stable(etfRows);
    check(document.querySelector('#etf-next').disabled&&document.querySelector('#etf-last').disabled,'etf last disabled');
    document.querySelector('#etf-prev').click();await settle(()=>document.querySelector('#etf-page').textContent==='107 / 108'&&!document.querySelector('#etf-next').disabled);
    check(true,'etf prev');
    for(const total of [0,1,2,5,6]){await mock({directory:total*10});document.querySelector('#etf-search-form').dispatchEvent(new Event('submit',{cancelable:true}));await settle(()=>document.querySelector('#etf-page').textContent==='1 / '+Math.max(1,total));if(total<=1)check(rootFor('etf').hidden,'etf hidden '+total);else expectNumbers(rootFor('etf'),Array.from({length:Math.min(5,total)},(_,i)=>i+1),'etf exact '+total);}
    await mock({directory:1074});await loadEtfs(true);
  }
  check(document.documentElement.scrollWidth<=innerWidth,'document horizontal overflow');
  report.documentWidth=document.documentElement.scrollWidth;
  report.passed=report.checks-report.errors.length;
  window.paginationReport=report;
  return report;
};
