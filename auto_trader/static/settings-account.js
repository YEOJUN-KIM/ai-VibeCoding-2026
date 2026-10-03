(() => {
  const profile = document.querySelector('#account-profile');
  if (!profile) return;
  const date = value => value ? new Date(value).toLocaleString('ko-KR') : '기록 없음';
  async function loadProfile() {
    const account = await api('/settings/account');
    profile.innerHTML = `<dt>아이디</dt><dd>${escapeHtml(account.username)}</dd><dt>최근 로그인</dt><dd>${escapeHtml(date(account.last_login_at))}</dd><dt>계정 생성</dt><dd>${escapeHtml(date(account.created_at))}</dd>`;
    document.querySelector('#account-pin-state').textContent = account.pin_configured ? '등록됨' : '미등록';
  }
  let connectionBusy = false;
  async function loadConnection() {
    if (connectionBusy) return;
    connectionBusy = true;
    const button = document.querySelector('#account-check-connection');
    const message = document.querySelector('#account-connection-message');
    const list = document.querySelector('#account-broker-accounts');
    button.disabled = true;
    message.textContent = '연결 확인 중…';
    list.replaceChildren();
    try {
      const connection = await api('/settings/connection');
      message.textContent = connection.message;
      message.classList.toggle('is-error', !connection.connected);
      list.innerHTML = connection.accounts.map(account => `<div class="account-broker-row"><strong>${escapeHtml(account.label)}</strong><span>${account.selected ? '사용 중' : '연결 계좌'}</span></div>`).join('');
    } catch (error) {
      message.textContent = error.message;
      message.classList.add('is-error');
    } finally { button.disabled = false; connectionBusy = false; }
  }
  for (const kind of ['password', 'pin']) {
    const form = document.querySelector(`#account-${kind}-form`);
    const dialog = document.querySelector(`#account-${kind}-dialog`);
    const saveButton = form.querySelector('[type="submit"]');
    const clearForm = () => {
      form.reset();
      const message = form.querySelector('.form-message');
      message.textContent = '';
      message.classList.remove('is-error');
    };
    document.querySelector(`[data-credential-open="${kind}"]`).addEventListener('click', () => {
      clearForm();
      document.querySelector('#account-security-message').textContent = '';
      dialog.showModal();
      form.elements.current_password.focus();
    });
    dialog.querySelectorAll('[data-credential-close]').forEach(button => button.addEventListener('click', () => {
      if (!saveButton.disabled) dialog.close();
    }));
    dialog.addEventListener('cancel', event => { if (saveButton.disabled) event.preventDefault(); });
    dialog.addEventListener('close', clearForm);
    dialog.addEventListener('click', event => {
      if (event.target !== dialog || saveButton.disabled) return;
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
    });
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const message = form.querySelector('.form-message');
      const button = saveButton;
      if (button.disabled) return;
      const values = new FormData(form);
      if (values.get('new_value') !== values.get('confirmation')) {
        message.textContent = '새 입력과 확인 입력이 일치하지 않습니다.';
        message.classList.add('is-error');
        form.elements.confirmation.focus();
        return;
      }
      button.disabled = true;
      dialog.querySelectorAll('[data-credential-close]').forEach(close => { close.disabled = true; });
      message.textContent = '저장 중…';
      message.classList.remove('is-error');
      try {
        const result = await api(`/settings/account/${kind}`, {method: 'PUT', body: JSON.stringify({current_password: values.get('current_password'), new_value: values.get('new_value')})});
        form.reset();
        message.textContent = result.message;
        if (kind === 'password') { location.replace('/login'); return; }
        document.querySelector('#account-security-message').textContent = result.message;
        dialog.close();
        await Promise.allSettled([loadProfile(), loadStatuses()]);
      } catch (error) { message.textContent = error.message; message.classList.add('is-error'); }
      finally {
        button.disabled = false;
        dialog.querySelectorAll('[data-credential-close]').forEach(close => { close.disabled = false; });
      }
    });
  }
  document.querySelector('#account-check-connection').addEventListener('click', loadConnection);
  const connectionDialog = document.querySelector('#account-connection-dialog');
  const connectionForm = document.querySelector('#account-connection-form');
  const connectionSubmit = connectionForm.querySelector('[type="submit"]');
  const connectionCancel = document.querySelector('#account-connect-cancel');
  document.querySelector('#account-connect-open').addEventListener('click', () => {
    connectionForm.reset();
    connectionForm.querySelector('.form-message').textContent = '';
    connectionDialog.showModal();
    connectionForm.elements.client_id.focus();
  });
  connectionCancel.addEventListener('click', () => { if (!connectionSubmit.disabled) connectionDialog.close(); });
  connectionDialog.addEventListener('cancel', event => { if (connectionSubmit.disabled) event.preventDefault(); });
  connectionDialog.addEventListener('close', () => connectionForm.reset());
  connectionForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (connectionSubmit.disabled) return;
    const message = connectionForm.querySelector('.form-message');
    connectionSubmit.disabled = connectionCancel.disabled = true;
    message.textContent = '계좌 연결 확인 중…';
    message.classList.remove('is-error');
    try {
      const values = new FormData(connectionForm);
      const result = await api('/settings/connection', {method: 'PUT', body: JSON.stringify({client_id: values.get('client_id'), client_secret: values.get('client_secret')})});
      connectionDialog.close();
      document.querySelector('#account-security-message').textContent = result.message;
      await Promise.allSettled([loadConnection(), loadStatuses()]);
    } catch (error) { message.textContent = error.message; message.classList.add('is-error'); }
    finally { connectionSubmit.disabled = connectionCancel.disabled = false; }
  });
  let initialized = false;
  function initialize() {
    if (initialized) return;
    initialized = true;
    loadProfile().catch(error => { profile.textContent = error.message; initialized = false; });
    loadConnection();
  }
  document.querySelector('[data-settings-tab="account"]').addEventListener('click', initialize);
  if (!['#strategy', '#risk'].includes(location.hash)) initialize();
})();
