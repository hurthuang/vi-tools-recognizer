'use strict';
(() => {
  const $ = (id) => document.getElementById(id);
  let jobId = null;
  let shown = 0;
  const mode = () => document.querySelector('input[name=mode]:checked').value;

  const readB64 = (file) =>
    new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result).split(',')[1] || '');
      r.onerror = () => reject(r.error);
      r.readAsDataURL(file);
    });

  const say = (msg) => {
    $('status').textContent = msg;
  };

  const syncMode = () => {
    const text = mode() === 'text';
    $('score-opts').hidden = text;
    $('text-opts').hidden = !text;
  };
  document.querySelectorAll('input[name=mode]').forEach((r) => r.addEventListener('change', syncMode));
  syncMode();

  $('form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = $('file').files[0];
    if (!f) return say('請先選擇 PDF 或圖檔。');
    $('go').disabled = true;
    $('result').hidden = true;
    $('log').innerHTML = '';
    shown = 0;
    say('上傳中…');
    try {
      const m = mode();
      const refs = [];
      if (m === 'score') for (const r of $('refs').files) refs.push({ name: r.name, data: await readB64(r) });
      const res = await fetch('/api/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: m, name: f.name, data: await readB64(f), pages: $('pages').value, refs, textLayer: $('text-layer').checked }),
      });
      const j = await res.json();
      if (!res.ok) throw new Error(j.error || res.statusText);
      jobId = j.id;
      say(m === 'text' ? '文字辨識中，請稍候。每頁大約 3 到 10 秒，完成時會通知。' : '辨識中，請稍候。每張圖大約 30 到 60 秒，完成時會通知。');
      poll();
    } catch (err) {
      say('無法開始：' + err.message);
      $('go').disabled = false;
    }
  });

  async function poll() {
    let j;
    try {
      const res = await fetch('/api/jobs/' + jobId);
      j = await res.json();
    } catch (err) {
      say('連不到本機服務，黑色視窗是否被關掉了？');
      $('go').disabled = false;
      return;
    }
    for (; shown < j.log.length; shown++) {
      const li = document.createElement('li');
      li.textContent = j.log[shown];
      $('log').appendChild(li);
    }
    if (j.state === 'running') return setTimeout(poll, 1000);
    $('go').disabled = false;
    if (j.state === 'error') return say('辨識失敗：' + (j.log[j.log.length - 1] || ''));
    const ul = $('files');
    ul.innerHTML = '';
    for (const f of j.files) {
      const li = document.createElement('li');
      const a = document.createElement('a');
      a.href = '/api/jobs/' + jobId + '/file?name=' + encodeURIComponent(f.name);
      a.textContent = '下載 ' + f.label;
      a.download = f.name;
      li.appendChild(a);
      ul.appendChild(li);
    }
    $('text-box').hidden = !j.text;
    $('text-out').value = j.text || '';
    $('report').textContent = j.report;
    $('result').hidden = false;
    say('辨識完成，結果在下方。');
    $('h-result').focus();
  }

  $('copy').addEventListener('click', async () => {
    const t = $('text-out').value;
    try {
      await navigator.clipboard.writeText(t);
    } catch (err) {
      $('text-out').select();
      document.execCommand('copy');
    }
    say('已複製 ' + t.replace(/\s/g, '').length + ' 個字。');
  });

  $('open').addEventListener('click', () => {
    if (jobId) fetch('/api/jobs/' + jobId + '/open', { method: 'POST' });
  });
})();
