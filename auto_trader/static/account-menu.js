// PAPER-SIMPLIFY-02: the one shared account menu (top-right account icon) for every signed-in page.
// It replaces the stand-alone settings screen: account info, password / PIN change, broker connection and logout.
// It only talks to the existing, unchanged account APIs: GET /auth/me, GET /settings/account,
// PUT /settings/account/{password|pin}, GET|PUT /settings/connection, POST /auth/logout (session, CSRF, lockout and
// connection verification all stay on the server). The script is self-contained (own CSS, own dialogs) so it also works
// on the strict-CSP PAPER page, where only same-origin scripts and inline styles are allowed.
(() => {
  'use strict';
  if (window.__folioAccountMenu) return;
  window.__folioAccountMenu = true;
  const host = document.querySelector('.topbar .header-actions') || document.querySelector('.topbar .right') || document.querySelector('.topbar');
  if (!host) return;

  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const when = v => v ? new Date(v).toLocaleString('ko-KR') : '기록 없음';
  let csrf = '';
  async function api(path, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    if (method !== 'GET' && !csrf) csrf = (await api('/auth/me')).csrf_token;
    const headers = { 'Content-Type': 'application/json', ...(method !== 'GET' && csrf ? { 'X-CSRF-Token': csrf } : {}) };
    const res = await fetch(path, { ...options, credentials: 'same-origin', headers });
    if (res.status === 401) { location.replace('/login'); throw new Error('로그인이 필요합니다.'); }
    if (res.status === 204) return null;
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '입력값을 확인하세요.');
    if (body.csrf_token) csrf = body.csrf_token;
    return body;
  }

  const style = document.createElement('style');
  style.textContent = `
.folio-account{position:relative;display:inline-flex;align-items:center;margin-left:8px;font-size:13px;line-height:1.5}
.folio-account-button{width:36px;height:36px;padding:0;border-radius:50%;border:1px solid var(--panel-border,var(--line,#2a2b35));background:var(--panel,#191a21);color:var(--accent-bright,var(--bright,#c6b3f2));font-weight:650;cursor:pointer;position:relative}
.folio-account-button:hover,.folio-account-button[aria-expanded="true"]{background:var(--soft,#202129)}
.folio-account-button:focus-visible,.folio-account-panel button:focus-visible{outline:2px solid var(--accent-bright,var(--bright,#c6b3f2));outline-offset:2px}
.folio-account-button .folio-account-alert{position:absolute;right:-1px;top:-1px;width:10px;height:10px;border-radius:50%;background:var(--red,#f88188);border:2px solid var(--bg,#111216)}
.folio-account-panel{position:absolute;right:0;top:44px;z-index:300;width:min(340px,calc(100vw - 24px));max-height:calc(100vh - 80px);overflow:auto;padding:14px;border:1px solid var(--panel-border,var(--line,#2a2b35));border-radius:12px;background:var(--panel,#191a21);color:var(--text,#ebeaf0);box-shadow:0 12px 32px #0007;text-align:left}
.folio-account-panel[hidden]{display:none}
.folio-account-panel h3{margin:14px 0 6px;font-size:12px;color:var(--muted,#a2a4b2);font-weight:600}
.folio-account-who{display:flex;gap:10px;align-items:center}.folio-account-who strong{display:block;font-size:15px}.folio-account-who small{color:var(--muted,#a2a4b2)}
.folio-account-avatar{width:34px;height:34px;border-radius:50%;display:grid;place-items:center;background:var(--soft,#202129);color:var(--accent-bright,var(--bright,#c6b3f2));font-weight:650}
.folio-account-panel dl{display:grid;grid-template-columns:auto 1fr;gap:3px 12px;margin:0;font-size:12px}.folio-account-panel dt{color:var(--muted,#a2a4b2)}.folio-account-panel dd{margin:0;text-align:right}
.folio-account-panel .folio-btn{display:block;width:100%;margin-top:6px;padding:8px 10px;border-radius:8px;border:1px solid var(--panel-border,var(--line,#2a2b35));background:transparent;color:inherit;font:inherit;text-align:left;cursor:pointer}
.folio-account-panel .folio-btn:hover{background:var(--soft,#202129)}.folio-account-panel .folio-btn:disabled{opacity:.5;cursor:default}
.folio-account-panel .folio-btn.danger{color:var(--red,#f88188)}
.folio-account-state{float:right;font-size:11px;color:var(--muted,#a2a4b2)}
.folio-account-msg{margin:4px 0;font-size:12px;color:var(--muted,#a2a4b2)}.folio-account-msg.is-error{color:var(--red,#f88188)}
.folio-account-row{display:flex;justify-content:space-between;font-size:12px;padding:3px 0}
dialog.folio-account-dialog{width:min(400px,calc(100vw - 24px));padding:20px;border:1px solid var(--panel-border,var(--line,#2a2b35));border-radius:12px;background:var(--panel,#191a21);color:var(--text,#ebeaf0)}
dialog.folio-account-dialog::backdrop{background:#000a}
.folio-account-dialog h2{margin:0 0 6px;font-size:17px}.folio-account-dialog p{margin:0 0 10px;font-size:12px;color:var(--muted,#a2a4b2)}
.folio-account-dialog label{display:block;margin:10px 0;font-size:12px}
.folio-account-dialog input{display:block;width:100%;margin-top:4px;padding:9px 10px;border-radius:8px;border:1px solid var(--panel-border,var(--line,#2a2b35));background:var(--bg,#111216);color:inherit;font:inherit}
.folio-account-dialog .folio-dialog-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:12px}
.folio-account-dialog .folio-dialog-actions button{padding:8px 14px;border-radius:8px;border:1px solid var(--panel-border,var(--line,#2a2b35));background:transparent;color:inherit;font:inherit;cursor:pointer}
.folio-account-dialog .folio-dialog-actions button[type=submit]{background:var(--accent,#aa90e6);color:#111216;border-color:transparent}
.folio-account-dialog button:disabled{opacity:.5;cursor:default}
`;
  document.head.append(style);

  const wrap = document.createElement('div');
  wrap.className = 'folio-account';
  wrap.innerHTML = `
<button type="button" class="folio-account-button" id="folio-account-button" aria-haspopup="dialog" aria-expanded="false" aria-controls="folio-account-panel" aria-label="계정 메뉴">F<span class="folio-account-alert" hidden></span></button>
<div class="folio-account-panel" id="folio-account-panel" role="dialog" aria-label="계정 메뉴" hidden>
  <div class="folio-account-who"><span class="folio-account-avatar" aria-hidden="true">F</span><div><small>계정</small><strong id="current-user">-</strong></div></div>
  <h3>계정 정보</h3>
  <dl id="folio-account-profile"><dt>상태</dt><dd>메뉴를 열면 불러옵니다</dd></dl>
  <p class="folio-account-msg" id="folio-account-note" role="status" aria-live="polite"></p>
  <h3>보안</h3>
  <button type="button" class="folio-btn" data-open="password">비밀번호 변경</button>
  <button type="button" class="folio-btn" data-open="pin">PIN 등록·변경 <span class="folio-account-state" id="folio-account-pin-state"></span></button>
  <h3>증권사·계좌 연결</h3>
  <p class="folio-account-msg" id="folio-account-conn" role="status" aria-live="polite">연결 확인 전</p>
  <div id="folio-account-brokers"></div>
  <button type="button" class="folio-btn" id="folio-account-check">연결 확인</button>
  <button type="button" class="folio-btn" data-open="connection">연결 등록·변경</button>
  <h3>세션</h3>
  <button type="button" class="folio-btn danger" id="logout-button">로그아웃</button>
</div>`;
  host.append(wrap);

  const dlg = (id, title, note, fields, submit) => {
    const d = document.createElement('dialog');
    d.className = 'folio-account-dialog'; d.id = id; d.setAttribute('aria-labelledby', id + '-t');
    d.innerHTML = `<h2 id="${id}-t">${title}</h2><p>${note}</p><form method="dialog" novalidate>${fields}<p class="folio-account-msg" role="status" aria-live="polite"></p><div class="folio-dialog-actions"><button type="button" data-close>취소</button><button type="submit">${submit}</button></div></form>`;
    document.body.append(d);
    return d;
  };
  const pw = '<label>현재 비밀번호<input name="current_password" type="password" autocomplete="current-password" required maxlength="128"></label>';
  const dialogs = {
    password: dlg('folio-account-password', '비밀번호 변경', '8~20자로 설정하세요. 변경하면 모든 기기에서 로그아웃됩니다.', pw +
      '<label>새 비밀번호<input name="new_value" type="password" autocomplete="new-password" required minlength="8" maxlength="20"></label><label>새 비밀번호 확인<input name="confirmation" type="password" autocomplete="new-password" required minlength="8" maxlength="20"></label>', '비밀번호 변경'),
    pin: dlg('folio-account-pin', 'PIN 등록·변경', '화면 잠금과 주문 인증에 사용하는 숫자 6자리입니다. 저장 후 주문 인증을 다시 진행하세요.', pw +
      '<label>새 PIN<input name="new_value" type="password" inputmode="numeric" pattern="[0-9]{6}" autocomplete="off" required minlength="6" maxlength="6"></label><label>새 PIN 확인<input name="confirmation" type="password" inputmode="numeric" pattern="[0-9]{6}" autocomplete="off" required minlength="6" maxlength="6"></label>', 'PIN 저장'),
    connection: dlg('folio-account-connection', '증권 계좌 연결', '토스증권 Open API에서 발급한 키를 입력하세요. 연결을 확인한 뒤 이 컴퓨터에 암호화해 저장합니다. 저장 후에는 화면에 다시 표시하지 않습니다.',
      '<label>API 키 (TOSS_CLIENT_ID)<input name="client_id" type="password" autocomplete="off" required maxlength="2048" spellcheck="false"></label><label>Secret (TOSS_CLIENT_SECRET)<input name="client_secret" type="password" autocomplete="off" required maxlength="2048" spellcheck="false"></label>', '연결 저장'),
  };

  const $ = s => wrap.querySelector(s);
  const button = $('#folio-account-button'), panel = $('#folio-account-panel');
  const note = $('#folio-account-note'), conn = $('#folio-account-conn');
  let profileLoaded = false, connBusy = false;

  async function loadProfile() {
    try {
      const a = await api('/settings/account');
      $('#current-user').textContent = a.username;
      $('#folio-account-profile').innerHTML = `<dt>아이디</dt><dd>${esc(a.username)}</dd><dt>최근 로그인</dt><dd>${esc(when(a.last_login_at))}</dd><dt>계정 생성</dt><dd>${esc(when(a.created_at))}</dd>`;
      $('#folio-account-pin-state').textContent = a.pin_configured ? '등록됨' : '미등록';
      profileLoaded = true;
    } catch (e) { $('#folio-account-profile').innerHTML = `<dt>상태</dt><dd>${esc(e.message)}</dd>`; }
  }
  async function loadConnection() {
    if (connBusy) return;
    connBusy = true; $('#folio-account-check').disabled = true;
    conn.textContent = '연결 확인 중…'; conn.classList.remove('is-error'); $('#folio-account-brokers').replaceChildren();
    try {
      const c = await api('/settings/connection');
      conn.textContent = c.message; conn.classList.toggle('is-error', !c.connected);
      $('#folio-account-brokers').innerHTML = c.accounts.map(x => `<div class="folio-account-row"><strong>${esc(x.label)}</strong><span>${x.selected ? '사용 중' : '연결 계좌'}</span></div>`).join('');
      wrap.querySelector('.folio-account-alert').hidden = c.connected;
    } catch (e) { conn.textContent = e.message; conn.classList.add('is-error'); }
    finally { $('#folio-account-check').disabled = false; connBusy = false; }
  }
  function setOpen(open) {
    panel.hidden = !open; button.setAttribute('aria-expanded', String(open));
    if (open) { if (!profileLoaded) loadProfile(); loadConnection(); }
  }
  button.addEventListener('click', () => setOpen(panel.hidden));
  document.addEventListener('pointerdown', e => { if (!panel.hidden && !wrap.contains(e.target) && !e.target.closest?.('dialog')) setOpen(false); });
  wrap.addEventListener('keydown', e => { if (e.key === 'Escape' && !panel.hidden) { setOpen(false); button.focus(); } });
  $('#folio-account-check').addEventListener('click', loadConnection);

  // Logout: registered first (this script loads first), so a page's own #logout-button handler never fires twice.
  $('#logout-button').addEventListener('click', async e => {
    e.stopImmediatePropagation();
    try { await api('/auth/logout', { method: 'POST' }); } finally { location.replace('/login'); }
  });

  for (const [kind, d] of Object.entries(dialogs)) {
    const form = d.querySelector('form'), msg = d.querySelector('.folio-account-msg'), submit = form.querySelector('[type=submit]'), cancel = d.querySelector('[data-close]');
    const setMsg = (t, err) => { msg.textContent = t; msg.classList.toggle('is-error', !!err); };
    wrap.querySelector(`[data-open="${kind}"]`).addEventListener('click', () => {
      form.reset(); setMsg(''); note.textContent = ''; d.showModal(); form.elements[0].focus();
    });
    cancel.addEventListener('click', () => { if (!submit.disabled) d.close(); });
    d.addEventListener('cancel', e => { if (submit.disabled) e.preventDefault(); });
    d.addEventListener('close', () => { form.reset(); setMsg(''); });
    form.addEventListener('submit', async e => {
      e.preventDefault(); if (submit.disabled) return;
      const v = new FormData(form);
      if (kind !== 'connection' && v.get('new_value') !== v.get('confirmation')) { setMsg('새 입력과 확인 입력이 일치하지 않습니다.', true); return; }
      submit.disabled = cancel.disabled = true;
      setMsg(kind === 'connection' ? '계좌 연결 확인 중…' : '저장 중…');
      try {
        if (kind === 'connection') {
          const r = await api('/settings/connection', { method: 'PUT', body: JSON.stringify({ client_id: v.get('client_id'), client_secret: v.get('client_secret') }) });
          d.close(); note.textContent = r.message; await loadConnection();
        } else {
          const r = await api('/settings/account/' + kind, { method: 'PUT', body: JSON.stringify({ current_password: v.get('current_password'), new_value: v.get('new_value') }) });
          if (kind === 'password') { location.replace('/login'); return; }
          d.close(); note.textContent = r.message; await loadProfile();
        }
      } catch (err) { setMsg(err.message, true); }
      finally { submit.disabled = cancel.disabled = false; }
    });
  }

  // Name on the button's panel without opening it (also lets pages that only read #current-user keep working).
  api('/auth/me').then(s => { $('#current-user').textContent = s.username; }).catch(() => {});
  // First sign-in without a broker connection is sent here as /login -> /live#account: open the menu on the connection block.
  if (location.hash === '#account') {
    setOpen(true);
    try { history.replaceState(null, '', location.pathname + location.search); } catch (_) { /* keep hash */ }
  }
})();
