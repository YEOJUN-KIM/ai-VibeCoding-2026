(() => {
  const tables = [...document.querySelectorAll('.table-wrap > table')];
  if (!tables.length) return;
  const overlay = document.createElement('div');
  overlay.className = 'floating-table-heading';
  overlay.setAttribute('aria-hidden', 'true');
  overlay.inert = true;
  overlay.hidden = true;
  document.body.append(overlay);
  let pending = false, activeTable = null, copiedHead = '';
  function update() {
    pending = false;
    const bars = [...document.querySelectorAll('.workspace-section-sidebar:not(.workspace-section-sidebar-empty), #stock-filter-form')];
    const top = Math.max(0, ...bars.map(bar => {
      const rect = bar.getBoundingClientRect();
      return rect.top <= 1 && rect.bottom > 0 ? rect.bottom : 0;
    }));
    const table = tables.find(item => {
      if (!item.tHead || !item.getClientRects().length) return false;
      return item.tHead.getBoundingClientRect().top < top && item.getBoundingClientRect().bottom > top;
    });
    if (!table) { overlay.hidden = true; return; }
    const wrapper = table.parentElement, bounds = wrapper.getBoundingClientRect();
    const heading = table.tHead, height = heading.getBoundingClientRect().height;
    if (table !== activeTable || copiedHead !== heading.innerHTML) {
      const copy = document.createElement('table');
      copy.className = table.className;
      copy.append(heading.cloneNode(true));
      copy.querySelectorAll('[id]').forEach(node => node.removeAttribute('id'));
      overlay.replaceChildren(copy);
      activeTable = table; copiedHead = heading.innerHTML;
    }
    const copy = overlay.firstElementChild;
    copy.style.width = `${table.getBoundingClientRect().width}px`;
    copy.style.transform = `translateX(${-wrapper.scrollLeft}px)`;
    [...heading.rows[0].cells].forEach((cell, index) => {
      copy.tHead.rows[0].cells[index].style.width = `${cell.getBoundingClientRect().width}px`;
    });
    Object.assign(overlay.style, {top:`${top}px`, left:`${bounds.left}px`, width:`${wrapper.clientWidth}px`,
      height:`${Math.max(0, Math.min(height, table.getBoundingClientRect().bottom - top))}px`});
    overlay.hidden = false;
  }
  function schedule() { if (!pending) { pending = true; requestAnimationFrame(update); } }
  document.addEventListener('scroll', schedule, {passive:true, capture:true});
  document.addEventListener('click', event => {
    const button = event.target.closest('.workspace-section-sidebar button');
    const bar = button?.closest('.workspace-section-sidebar');
    if (bar && bar.getBoundingClientRect().top <= 1 && window.scrollY > 0) {
      requestAnimationFrame(() => bar.scrollIntoView({block:'start'}));
    }
  }, {capture:true});
  window.addEventListener('resize', schedule);
  const observer = new ResizeObserver(schedule);
  tables.forEach(table => observer.observe(table));
  new MutationObserver(schedule).observe(document.querySelector('main') || document.body, {childList:true, subtree:true, attributes:true, attributeFilter:['hidden']});
  schedule();
})();
