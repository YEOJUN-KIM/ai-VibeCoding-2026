const AssetPrivacy = (() => {
  const storageKey = 'folio.asset-privacy.v1';
  const groups = ['account', 'assets', 'purchase', 'market', 'profit', 'daily', 'krw', 'usd', 'holdings'];
  let concealed = new Set();
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || '[]');
    if (Array.isArray(saved)) concealed = new Set(saved.filter(group => groups.includes(group)));
  } catch (_) { /* Storage can be unavailable; toggles still work for this visit. */ }
  const values = new Map();
  const listeners = [];
  const hidden = group => concealed.has(group);
  const eye = isHidden => `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>${isHidden?'<path d="m3 3 18 18"/>':''}</svg>`;
  function paint(id, value) {
    const node = document.getElementById(id);
    if (!node) return;
    const masked = hidden(value.group);
    node.textContent = masked ? '••••••' : value.text;
    node.dataset.privacyHidden = String(masked);
    if (masked) node.setAttribute('aria-label', '가려진 자산 정보');
    else node.removeAttribute('aria-label');
    if (value.tone !== undefined) node.className = masked ? 'neutral' : value.tone;
  }
  function write(id, group, text, tone) {
    const value = {group, text, tone};
    values.set(id, value);
    paint(id, value);
  }
  function render() {
    for (const [id, value] of values) paint(id, value);
    document.querySelectorAll('[data-privacy-toggle]').forEach(button => {
      const group = button.dataset.privacyToggle;
      const masked = group === 'all' ? groups.every(hidden) : hidden(group);
      const label = group === 'all' ? (masked ? '전체 자산 보기' : '전체 자산 가리기') : `${button.dataset.privacyLabel} ${masked ? '보기' : '가리기'}`;
      button.setAttribute('aria-label', label);
      button.setAttribute('aria-pressed', String(masked));
      button.title = label;
      button.innerHTML = eye(masked) + (group === 'all' ? `<span>${label}</span>` : '');
    });
    listeners.forEach(listener => listener());
  }
  function toggle(group) {
    if (group === 'all') concealed = groups.every(hidden) ? new Set() : new Set(groups);
    else if (groups.includes(group)) hidden(group) ? concealed.delete(group) : concealed.add(group);
    try { localStorage.setItem(storageKey, JSON.stringify([...concealed])); } catch (_) {}
    render();
  }
  document.querySelectorAll('[data-private-value]').forEach(node => write(node.id, node.dataset.privateValue, node.textContent));
  document.querySelectorAll('[data-privacy-toggle]').forEach(button => button.addEventListener('click', () => toggle(button.dataset.privacyToggle)));
  render();
  return {hidden, write, toggle, onChange: listener => listeners.push(listener)};
})();
