// 辨識進度對話框（首頁與版面調整畫面共用）：送出工作、每秒問一次進度，顯示頁數、經過與預估剩下的時間，可以停止。
// 用法：const j = await haodianRun(payload, { onPoll(j) {} });  回傳做完的工作；失敗會丟出錯誤，停止（一頁都沒做完）回傳 null。
(function () {
  const css = `
  dialog.hd-prog { border: 1px solid #94a3b8; border-radius: 8px; padding: 18px 22px; width: min(30em, 90vw);
    box-shadow: 0 10px 30px rgba(0,0,0,.25); font-size: 15px; }
  dialog.hd-prog::backdrop { background: rgba(15, 23, 42, .35); }
  dialog.hd-prog h2 { margin: 0 0 8px; font-size: 18px; }
  dialog.hd-prog .file { color: #334155; word-break: break-all; margin-bottom: 8px; }
  dialog.hd-prog progress { width: 100%; height: 14px; margin: 6px 0; }
  dialog.hd-prog .stage { min-height: 1.4em; }
  dialog.hd-prog .time, dialog.hd-prog .note { color: #555; font-size: 13px; }
  dialog.hd-prog .btns { text-align: right; margin-top: 12px; }
  dialog.hd-prog .btns button { font-size: 15px; padding: 4px 14px; }
  `;
  let dlg = null;

  function build() {
    if (dlg) return dlg;
    const st = document.createElement('style');
    st.textContent = css;
    document.head.appendChild(st);
    dlg = document.createElement('dialog');
    dlg.className = 'hd-prog';
    dlg.setAttribute('aria-labelledby', 'hd-prog-h');
    dlg.innerHTML = `<h2 id="hd-prog-h">辨識中</h2>
      <div class="file"></div>
      <div class="stage" role="status" aria-live="polite"></div>
      <progress max="1"></progress>
      <div class="time"></div>
      <div class="note">只在這台電腦處理，不會傳到網路。第一次要載入模型，前面會停比較久，不是當機。</div>
      <div class="btns"><button type="button" class="stop">停止</button></div>`;
    // Esc 不關掉對話框（要停請按「停止」）
    dlg.addEventListener('cancel', e => e.preventDefault());
    document.body.appendChild(dlg);
    return dlg;
  }

  const fmt = s => s >= 60 ? `${Math.floor(s / 60)} 分 ${s % 60} 秒` : `${s} 秒`;
  const about = s => s < 60 ? '不到 1 分鐘' : `約 ${Math.round(s / 60)} 分鐘`;

  window.haodianRun = async function (payload, opt = {}) {
    const d = build();
    const $ = q => d.querySelector(q);
    const unit = payload.mode === 'score' ? '張' : '頁';
    $('.file').textContent = payload.name || '';
    $('.stage').textContent = '讀取檔案中…';
    $('.time').textContent = '';
    $('progress').removeAttribute('value');   // 不知道進度時是來回跑的條
    const stop = $('.stop');
    stop.disabled = false;
    stop.textContent = '停止';
    d.showModal();
    stop.focus();
    let id = null, stopping = false;
    stop.onclick = async () => {
      if (!id || stopping) return;
      stopping = true;
      stop.disabled = true;
      stop.textContent = '停止中…';
      $('.stage').textContent = `做完這一${unit}就停，做完的會保留。`;
      try { await fetch(`/api/jobs/${id}/cancel`, { method: 'POST' }); } catch (e) { /* 下一輪會再問狀態 */ }
    };
    try {
      const res = await fetch('/api/jobs', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      const j0 = await res.json();
      if (!res.ok) throw new Error(j0.error || res.statusText);
      id = j0.id;
      let lastStage = '';
      for (;;) {
        await new Promise(r => setTimeout(r, 1000));
        let j;
        try { j = await (await fetch('/api/jobs/' + id)).json(); }
        catch (e) { throw new Error('連不到本機服務，黑色視窗是否被關掉了？'); }
        if (opt.onPoll) opt.onPoll(j);
        if (j.state === 'error') throw new Error(j.log[j.log.length - 1] || '辨識失敗');
        if (j.state === 'stopped') return null;
        if (j.state !== 'running') { j.id = id; return j; }
        const p = $('progress');
        if (j.total) { p.max = j.total; p.value = j.done; } else p.removeAttribute('value');
        // 狀態列只在換頁（或換步驟）時更新，螢幕報讀不會一直念
        let stage = j.total ? `第 ${Math.min(j.done + 1, j.total)}／${j.total} ${unit}` : '準備中';
        const last = (j.log[j.log.length - 1] || '').replace(/\[\d+\/\d+\]$/, '');
        if (/載入模型|等前一份/.test(last)) stage = last;
        if (stopping || j.stopping) stage = `停止中：做完這一${unit}就停（已做完 ${j.done}／${j.total || '?'} ${unit}）`;
        if (stage !== lastStage) { $('.stage').textContent = stage; lastStage = stage; }
        $('.time').textContent = `已經過 ${fmt(j.elapsed)}` + (j.eta != null ? `，預估還要${about(j.eta)}` : '');
      }
    } finally {
      d.close();
    }
  };
})();
