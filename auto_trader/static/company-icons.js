(() => {
  const logos = new Set(['005930', '000660', '035420', '005380']);
  function icon(symbol, name) {
    const badge = document.createElement('span');
    badge.className = 'company-icon';
    badge.setAttribute('aria-hidden', 'true');
    const fallback = document.createElement('span');
    fallback.textContent = String(name || symbol || '?').replace(/\s/g, '').slice(0, 2);
    badge.append(fallback);
    if (logos.has(symbol)) {
      const image = document.createElement('img');
      image.alt = ''; image.loading = 'lazy'; image.decoding = 'async';
      image.addEventListener('load', () => badge.classList.add('has-logo'));
      image.addEventListener('error', () => { image.remove(); badge.classList.remove('has-logo'); });
      image.src = `/static/company-logos/${symbol}.svg`;
      badge.append(image);
    }
    return badge;
  }
  window.CompanyIcons = {icon};
  function decorate() {
    document.querySelectorAll('[data-company-symbol]').forEach(node => {
      node.replaceWith(icon(node.dataset.companySymbol,node.dataset.companyName));
    });
    document.querySelectorAll('.stock-name').forEach(name => {
      const host = name.closest('a') || name.parentElement;
      if (!host || host.classList.contains('company-identity')) return;
      const path = host.getAttribute('href') || '';
      const symbol = path.match(/\/stocks\/([^?/#]+)/)?.[1] || host.querySelector('.stock-code')?.textContent.trim().split(/[ ·]/)[0];
      if (!symbol) return;
      host.classList.add('company-identity');
      host.insertBefore(icon(decodeURIComponent(symbol), name.textContent), host.firstChild);
    });
  }
  let queued = false;
  new MutationObserver(records => {
    if (queued || !records.some(r => [...r.addedNodes].some(n => n.nodeType === 1 && !n.classList?.contains('company-icon')))) return;
    queued = true; requestAnimationFrame(() => {queued = false; decorate();});
  }).observe(document.body, {childList:true, subtree:true});
  decorate();
})();
