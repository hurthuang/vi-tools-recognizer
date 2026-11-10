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
    const m = mode();
    $('score-opts').hidden = m !== 'score';
    $('text-opts').hidden = m !== 'text';
    $('exam-opts').hidden = m !== 'exam';
  };
  document.querySelectorAll('input[name=mode]').forEach((r) => r.addEventListener('change', syncMode));
  syncMode();
  // 試卷版（沒有 Audiveris、homr）：樂譜不能選，預設改成試卷
  fetch('/api/info').then((r) => r.json()).then((info) => {
    if (info.music !== false) return;
    const sc = document.querySelector('input[name=mode][value=score]');
    sc.disabled = true;
    sc.parentElement.insertAdjacentText('beforeend', '（試卷版沒有樂譜辨識；要辨識樂譜請下載完整版）');
    if (sc.checked) { document.querySelector('input[name=mode][value=exam]').checked = true; syncMode(); }
  }).catch(() => {});

  $('form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = $('file').files[0];
    if (!f) return say('請先選擇 PDF 或圖檔。');
    $('go').disabled = true;
    $('result').hidden = true;
    $('log').innerHTML = '';
    shown = 0;
    say('讀取檔案中…（只在這台電腦處理，不會傳到網路）');
    try {
      const m = mode();
      const refs = [];
      if (m === 'score') for (const r of $('refs').files) refs.push({ name: r.name, data: await readB64(r) });
      say({ text: '文字辨識中，請稍候。每頁大約 3 到 10 秒。', exam: '試卷辨識中，請稍候。每頁大約 10 到 30 秒。' }[m] ||
        '辨識中，請稍候。每張圖大約 30 到 60 秒。');
      const showLog = (j) => {   // 進度對話框後面的記錄照樣列出來
        for (; shown < j.log.length; shown++) {
          const li = document.createElement('li');
          li.textContent = j.log[shown];
          $('log').appendChild(li);
        }
      };
      const j = await haodianRun({ mode: m, name: f.name, data: await readB64(f), pages: $('pages').value, refs,
        textLayer: m === 'exam' ? $('exam-text-layer').checked : $('text-layer').checked }, { onPoll: showLog });
      if (!j) { say('已停止，沒有做完的頁。'); return; }
      done(j);
    } catch (err) {
      say('辨識失敗：' + err.message);
    } finally {
      $('go').disabled = false;
    }
  });

  function done(j) {
    jobId = j.id;
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
    $('exam-link').hidden = !j.exam;
    if (j.exam) $('exam-a').href = '/exam/?name=' + encodeURIComponent(j.exam);
    $('text-box').hidden = !j.text;
    $('text-out').value = j.text || '';
    $('report').textContent = j.report;
    $('result').hidden = false;
    say(j.log.some(x => x.startsWith('已停止')) ? '已停止，做完的部分在下方。' : '辨識完成，結果在下方。');
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
