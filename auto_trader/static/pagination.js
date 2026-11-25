// Shared presentation for existing paged lists. Callers own data, page size and requests.
window.FolioPagination = (() => {
  function numbers(current, total) {
    if (!Number.isInteger(total) || total <= 1) return [];
    const start = Math.max(1, Math.min(current - 2, total - 4));
    return Array.from({ length: Math.min(5, total) }, (_, i) => start + i);
  }
  function numberHtml(current, total, attributes = '') {
    return numbers(current, total).map(page => '<button type="button" class="folio-page-number' + (page === current ? ' active' : '') +
      '" data-page-to="' + page + '" aria-label="' + page + '페이지"' + (page === current ? ' aria-current="page"' : '') + attributes + '>' + page + '</button>').join('');
  }
  function paper(key, state, label) {
    const { page, pages, total } = state;
    if (pages <= 1) return '';
    const attributes = ' data-page-key="' + key + '"';
    const button = (to, text, disabled) => '<button type="button" data-page-to="' + to + '"' + attributes + (disabled ? ' disabled' : '') + '>' + text + '</button>';
    return '<nav class="pager folio-pagination" aria-label="' + label + '"><div class="folio-page-controls">' +
      button(1, '처음', page === 1) + button(page - 1, '이전', page === 1) +
      '<span class="folio-page-numbers">' + numberHtml(page, pages, attributes) + '</span>' +
      button(page + 1, '다음', page === pages) + button(pages, '마지막', page === pages) +
      '</div><small class="folio-page-summary">' + page + ' / ' + pages + ' · 총 ' + total + '개</small></nav>';
  }
  function directory(prefix, current, total, onChange) {
    const summary = document.getElementById(prefix + '-page');
    const root = summary.closest('.folio-pagination');
    root.hidden = total <= 1;
    summary.textContent = current + ' / ' + total;
    const region = document.getElementById(prefix + '-numbers');
    region.innerHTML = numberHtml(current, total);
    region.querySelectorAll('button').forEach(button => button.onclick = () => onChange(Number(button.dataset.pageTo)));
  }
  function busy(prefix) {
    document.getElementById(prefix + '-page').closest('.folio-pagination').querySelectorAll('button').forEach(button => button.disabled = true);
  }
  return { numbers, paper, directory, busy };
})();
