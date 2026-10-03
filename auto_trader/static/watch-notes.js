(() => {
  const dialog = document.createElement('dialog');
  dialog.className = 'watch-note-dialog';
  dialog.setAttribute('aria-labelledby', 'watch-note-title');
  dialog.innerHTML = `<form><h2 id="watch-note-title">기업 메모</h2><p class="subtitle">관심을 가진 이유와 앞으로 확인할 점을 남겨두세요.</p><label for="watch-note-text">내 메모</label><textarea id="watch-note-text" maxlength="2000" rows="7" placeholder="예: 다음 분기 실적에서 매출 성장과 영업이익률 확인"></textarea><div class="watch-note-caption"><span role="status" data-note-status></span><small data-note-count>0 / 2,000</small></div><div class="watch-note-actions"><button type="button" class="button secondary" data-note-cancel>취소</button><button type="submit" class="button primary">메모 저장</button></div></form>`;
  document.body.append(dialog);
  const form = dialog.querySelector('form'), text = dialog.querySelector('textarea');
  const status = dialog.querySelector('[data-note-status]'), cancel = dialog.querySelector('[data-note-cancel]');
  const save = dialog.querySelector('[type="submit"]');
  let code = '', busy = false, opener = null;
  const count = () => { dialog.querySelector('[data-note-count]').textContent = `${text.value.length.toLocaleString('ko-KR')} / 2,000`; };
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-watch-note]');
    if (!button || busy) return;
    const item = myCandidates.find(item => item.symbol === button.dataset.watchNote);
    if (!item) return;
    code = item.symbol; opener = button;
    dialog.querySelector('h2').textContent = `${item.name} · 기업 메모`;
    text.value = item.note || ''; status.textContent = ''; count();
    dialog.showModal(); text.focus();
  });
  text.addEventListener('input', count);
  cancel.addEventListener('click', () => { if (!busy) dialog.close(); });
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  dialog.addEventListener('close', () => { if (opener?.isConnected) opener.focus({preventScroll:true}); });
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (busy) return;
    busy = true; save.disabled = cancel.disabled = true; text.disabled = true;
    status.textContent = '저장 중…';
    try {
      const note = text.value.trim();
      await api(`/research/long-term/watchlist/${encodeURIComponent(code)}/note`, {method:'PUT', body:JSON.stringify({note})});
      myCandidates = myCandidates.map(item => item.symbol === code ? {...item,note} : item);
      renderMyCandidates(myCandidates);
      // Rendering replaces the original button; restore focus to its replacement.
      opener = [...document.querySelectorAll('[data-watch-note]')].find(button => button.dataset.watchNote === code) || document.querySelector('#my-watch-query');
      dialog.close();
    } catch (error) { status.textContent = `저장하지 못했습니다. ${error.message}`; }
    finally { busy = false; save.disabled = cancel.disabled = false; text.disabled = false; }
  });
})();
