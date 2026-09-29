(() => {
  const dataText = document.querySelector('[data-header-data-status]');
  const dataDot = document.querySelector('[data-header-data-dot]');
  const liveLink = document.querySelector('[data-header-live-link]');

  fetch('/health', { credentials: 'same-origin' })
    .then(response => response.ok ? response.json() : Promise.reject())
    .then(health => {
      if (dataText) dataText.textContent = health.status === 'ok' ? '연결됨' : '확인 필요';
      if (dataDot) dataDot.classList.toggle('online', health.status === 'ok');
    })
    .catch(() => { if (dataText) dataText.textContent = '연결 확인 필요'; });

  if (!liveLink) return;

  let csrfToken = '';
  const setLiveStatus = status => {
    liveLink.textContent = status.authorized ? 'LIVE 인증 완료' : 'LIVE 주문 잠금 해제';
    liveLink.classList.toggle('live-ready', Boolean(status.authorized));
    liveLink.classList.toggle('danger', !status.authorized);
    if (status.authorized && status.authorized_until) {
      const remaining = new Date(status.authorized_until).getTime() - Date.now();
      if (remaining > 0) window.setTimeout(() => setLiveStatus({ authorized: false }), remaining);
    }
  };
  const request = async (path, options = {}) => {
    const response = await fetch(path, {
      ...options,
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}), ...(options.headers || {}) }
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: '요청에 실패했습니다.' }));
      throw new Error(error.detail || '요청에 실패했습니다.');
    }
    return response.json();
  };
  const modal = document.createElement('div');
  modal.className = 'modal-backdrop hidden';
  modal.innerHTML = `<section class="modal-card" role="dialog" aria-modal="true" aria-labelledby="global-live-auth-title"><button class="modal-close" type="button" aria-label="닫기">×</button><p class="section-kicker">LIVE SECURITY</p><h2 id="global-live-auth-title">LIVE 기능 잠금 해제</h2><p class="subtitle">실제 주문 기능을 사용하기 전에 6자리 PIN으로 본인임을 다시 확인합니다.</p><div class="preflight-box" data-global-toss-status>토스 API 연결을 확인하는 중...</div><form data-global-live-pin-form><label>6자리 PIN<input data-global-live-pin type="password" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="off" required></label><button class="button danger full" type="submit">인증하고 잠금 해제</button></form><p class="form-message" data-global-live-message>인증은 현재 로그인 세션에서 5분간 유효합니다.</p></section>`;
  document.body.appendChild(modal);
  const closeModal = () => { modal.classList.add('hidden'); modal.querySelector('[data-global-live-pin]').value = ''; };
  modal.querySelector('.modal-close').addEventListener('click', closeModal);
  modal.addEventListener('click', event => { if (event.target === modal) closeModal(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') closeModal(); });
  liveLink.addEventListener('click', async () => {
    modal.classList.remove('hidden');
    modal.querySelector('[data-global-live-pin]').focus();
    const tossStatus = modal.querySelector('[data-global-toss-status]');
    tossStatus.textContent = '토스 API 연결을 확인하는 중...';
    try {
      if (!csrfToken) csrfToken = (await request('/auth/me')).csrf_token;
      const result = await request('/toss/test-connection', { method: 'POST' });
      tossStatus.textContent = `API 정상 · 연결 계좌 ${result.account_count}개`;
    } catch (error) { tossStatus.textContent = error.message; }
  });
  modal.querySelector('[data-global-live-pin-form]').addEventListener('submit', async event => {
    event.preventDefault();
    const input = modal.querySelector('[data-global-live-pin]');
    const message = modal.querySelector('[data-global-live-message]');
    try {
      if (!csrfToken) csrfToken = (await request('/auth/me')).csrf_token;
      const status = await request('/auth/live-pin/verify', { method: 'POST', body: JSON.stringify({ pin: input.value }) });
      message.textContent = `인증 완료 · ${new Date(status.authorized_until).toLocaleTimeString('ko-KR')}까지 유효`;
      setLiveStatus(status);
      window.setTimeout(closeModal, 700);
    } catch (error) { message.textContent = error.message; }
    finally { input.value = ''; }
  });
  request('/auth/live-pin').then(setLiveStatus).catch(() => { liveLink.textContent = 'LIVE 상태 확인'; });
})();
