(() => {
  const dataText = document.querySelector('[data-header-data-status]');
  const dataDot = document.querySelector('[data-header-data-dot]');
  const liveLink = document.querySelector('[data-header-live-link]');
  const headerActions = document.querySelector('.header-actions');
  const userBox = headerActions?.querySelector('.user-box');
  let screenCsrfToken = '';
  let pageLoadedLocked = false;

  const screenLockButton = document.createElement('button');
  screenLockButton.className = 'button secondary header-lock-button';
  screenLockButton.type = 'button';
  screenLockButton.textContent = '화면 잠금';
  if (headerActions) headerActions.insertBefore(screenLockButton, userBox || null);

  const screenLock = document.createElement('div');
  screenLock.className = 'modal-backdrop hidden';
  screenLock.innerHTML = `<section class="modal-card" role="dialog" aria-modal="true" aria-labelledby="screen-lock-title"><p class="section-kicker">SCREEN LOCK</p><h2 id="screen-lock-title">화면이 잠겼습니다</h2><p class="subtitle">자리를 비운 동안 계좌와 설정을 보호합니다. 6자리 PIN을 입력하면 원래 화면으로 돌아갑니다.</p><form data-screen-unlock-form autocomplete="off"><label>6자리 PIN<input class="pin-secret" data-screen-unlock-pin type="text" name="screen-unlock-code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="one-time-code" autocapitalize="off" spellcheck="false" data-lpignore="true" data-1p-ignore required></label><button class="button primary full" type="submit">화면 잠금 해제</button></form><p class="form-message" data-screen-lock-message>화면을 열어도 LIVE 주문 권한은 별도로 인증해야 합니다.</p></section>`;
  document.body.appendChild(screenLock);
  const showScreenLock = () => {
    screenLock.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    window.setTimeout(() => screenLock.querySelector('[data-screen-unlock-pin]').focus(), 0);
  };
  const hideScreenLock = () => {
    screenLock.classList.add('hidden');
    document.body.style.overflow = '';
  };
  const screenRequest = async (path, options = {}) => {
    const response = await fetch(path, {
      ...options,
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', ...(screenCsrfToken ? { 'X-CSRF-Token': screenCsrfToken } : {}), ...(options.headers || {}) }
    });
    if (response.status === 401) { window.location.replace('/login'); throw new Error('로그인이 필요합니다.'); }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || '요청에 실패했습니다.');
    return body;
  };
  const sessionReady = screenRequest('/auth/me').then(session => {
    screenCsrfToken = session.csrf_token;
    if (session.locked) {
      pageLoadedLocked = true;
      showScreenLock();
    }
    return session;
  });
  const maintenanceBanner = document.createElement('div');
  maintenanceBanner.className = 'global-maintenance-banner hidden';
  maintenanceBanner.setAttribute('role', 'status');
  maintenanceBanner.innerHTML = '<div class="maintenance-marquee"><span data-maintenance-message>장기 관찰 후보 데이터를 갱신 중입니다 · 잠시 기다려 주세요</span><span aria-hidden="true">장기 관찰 후보 데이터를 갱신 중입니다 · 잠시 기다려 주세요</span></div>';
  document.body.prepend(maintenanceBanner);
  const pollMaintenanceStatus = async () => {
    let delay = 60000;
    try {
      await sessionReady;
      const status = await screenRequest('/research/long-term/candidate-status');
      maintenanceBanner.classList.toggle('hidden', !status.running);
      const message = status.running
        ? `장기분석 점검 중 · ${status.message} · 다른 기능은 정상 이용 가능합니다 · 잠시 기다려 주세요`
        : '';
      maintenanceBanner.querySelectorAll('.maintenance-marquee span').forEach(item => { item.textContent = message; });
      delay = status.running ? 10000 : 60000;
    } catch (_) {
      maintenanceBanner.classList.add('hidden');
    }
    window.setTimeout(pollMaintenanceStatus, delay);
  };
  pollMaintenanceStatus();
  screenLockButton.addEventListener('click', async () => {
    try {
      await sessionReady;
      await screenRequest('/auth/lock', { method: 'POST' });
      localStorage.setItem('auto-trader-screen-locked', String(Date.now()));
      showScreenLock();
    } catch (error) {
      window.alert(error.message);
    }
  });
  screenLock.querySelector('[data-screen-unlock-form]').addEventListener('submit', async event => {
    event.preventDefault();
    const input = screenLock.querySelector('[data-screen-unlock-pin]');
    const message = screenLock.querySelector('[data-screen-lock-message]');
    try {
      await screenRequest('/auth/unlock', { method: 'POST', body: JSON.stringify({ pin: input.value }) });
      input.value = '';
      localStorage.removeItem('auto-trader-screen-locked');
      hideScreenLock();
      if (pageLoadedLocked) window.location.reload();
    } catch (error) {
      message.textContent = error.message;
      input.select();
    }
  });
  window.addEventListener('storage', event => {
    if (event.key === 'auto-trader-screen-locked') {
      if (event.newValue) showScreenLock();
      else window.location.reload();
    }
  });

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
  modal.innerHTML = `<section class="modal-card" role="dialog" aria-modal="true" aria-labelledby="global-live-auth-title"><button class="modal-close" type="button" aria-label="닫기">×</button><p class="section-kicker">LIVE SECURITY</p><h2 id="global-live-auth-title">LIVE 기능 잠금 해제</h2><p class="subtitle">실제 주문 기능을 사용하기 전에 6자리 PIN으로 본인임을 다시 확인합니다.</p><div class="preflight-box" data-global-toss-status>토스 API 연결을 확인하는 중...</div><form data-global-live-pin-form autocomplete="off"><label>6자리 PIN<input class="pin-secret" data-global-live-pin type="text" name="live-authorization-code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="one-time-code" autocapitalize="off" spellcheck="false" data-lpignore="true" data-1p-ignore required></label><button class="button danger full" type="submit">인증하고 잠금 해제</button></form><p class="form-message" data-global-live-message>인증은 현재 로그인 세션에서 5분간 유효합니다.</p></section>`;
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
