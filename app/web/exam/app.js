'use strict';
// 版面調整（試做）：原卷上的區塊框 ↔ 右邊排好的結果

const TYPES = [
  { t: 'question', k: '1', name: '題目', color: '#2563eb' },
  { t: 'option', k: '2', name: '選項', color: '#0891b2' },
  { t: 'text', k: '3', name: '文字', color: '#6b7280' },
  { t: 'figure', k: '4', name: '附圖', color: '#16a34a' },
  { t: 'table', k: '5', name: '表格', color: '#65a30d' },
  { t: 'score', k: '6', name: '譜例', color: '#c026d3' },
  { t: 'figtext', k: '7', name: '圖中文字', color: '#ea580c' },
  { t: 'section', k: '8', name: '大題', color: '#7c3aed' },
  { t: 'title', k: '9', name: '標題', color: '#be123c' },
  { t: 'skip', k: '0', name: '略過', color: '#9ca3af' },
];
const T = Object.fromEntries(TYPES.map(x => [x.t, x]));
const $ = s => document.querySelector(s);
const SVGNS = 'http://www.w3.org/2000/svg';

let exam = null, data = null, cur = 0, sel = new Set(), scale = 0.5;
let undo = [], orderSeq = null, lastType = 'text';

function status(msg) { $('#status').textContent = msg; }
function page() { return data.pages[cur]; }
function blocks() { return data.blocks[cur]; }
function blk(id) { return blocks().find(b => b.id === id); }

async function api(url, body) {
  const r = await fetch(url, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
  });
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

// ---------- 載入 ----------
async function init() {
  const ts = $('#p-type');   // 類型：下拉選單（數字鍵照樣可以改）
  for (const x of TYPES) ts.add(new Option(`${x.k} ${x.name}`, x.t));
  ts.onchange = () => { if (ts.value) setType(ts.value); };
  initMenus();
  const names = await api('api/exams');
  const s = $('#exam');
  for (const n of names) s.add(new Option(n, n));
  s.onchange = () => load(s.value);
  bindUI();
  $('#b-open').onclick = () => $('#openfile').click();
  $('#openfile').onchange = () => { const f = $('#openfile').files[0]; $('#openfile').value = ''; if (f) openFile(f); };
  const want = new URLSearchParams(location.search).get('name');
  if (want && names.includes(want)) s.value = want;
  if (names.length) load(s.value);
  else status('還沒有辨識過的試卷：按「開啟檔案」選 PDF 或圖檔。');
}

// ---------- 開新檔案：辨識完直接打開 ----------
async function openFile(f) {
  const btn = $('#b-open');
  btn.disabled = true;
  try {
    status(`讀取 ${f.name}…（只在這台電腦處理，不會傳到網路）`);
    const b64 = await new Promise((ok, bad) => {
      const r = new FileReader();
      r.onload = () => ok(String(r.result).split(',')[1] || '');
      r.onerror = () => bad(r.error);
      r.readAsDataURL(f);
    });
    status(`辨識 ${f.name} 中…`);
    const j = await haodianRun({ mode: 'exam', name: f.name, data: b64, pages: $('#openpages').value, textLayer: true });
    if (!j) { status('已停止，沒有做完的頁。'); return; }
    const s = $('#exam');
    s.insertBefore(new Option(j.exam, j.exam), s.firstChild);
    s.value = j.exam;
    await load(j.exam);
    const stopped = j.log.some(x => x.startsWith('已停止'));
    status(`${f.name} ${stopped ? '已停止，保留做完的 ' : '辨識完成，'}${data.pages.length} 頁。結果也存在輸出資料夾的 試卷.txt。`);
  } catch (e) {
    status('辨識失敗：' + e.message);
  } finally {
    btn.disabled = false;
  }
}

async function load(name) {
  exam = name; status(`載入 ${name}…`);
  data = await api(`api/exam/${encodeURIComponent(name)}`);
  undo = []; sel.clear(); orderSeq = null; cur = 0;
  thumbs(); showPage(0);
  status(`${name}：${data.pages.length} 頁`);
}

function thumbs() {
  const nav = $('#thumbs'); nav.innerHTML = '';
  data.pages.forEach((p, i) => {
    const low = data.seg.filter(s => s.page === i).reduce((a, s) => a + s.low, 0);
    const b = document.createElement('button');
    b.type = 'button';
    b.innerHTML = `<img src="page/${encodeURIComponent(exam)}/${i + 1}.png" alt="">` +
      `<span class="n">第 ${i + 1} 頁</span>${low ? ` <span class="warn" title="信心低的算式">⚠${low}</span>` : ''}`;
    b.onclick = () => showPage(i);
    if (i === cur) b.classList.add('cur');
    nav.appendChild(b);
  });
}

function showPage(i) {
  cur = i; sel.clear(); orderSeq = null; $('#b-order').classList.remove('on');
  [...$('#thumbs').children].forEach((b, k) => b.classList.toggle('cur', k === i));
  const img = $('#pageimg');
  img.onload = () => { if (!img.dataset.fitted) { fit(); img.dataset.fitted = 1; } draw(); };
  img.src = `page/${encodeURIComponent(exam)}/${i + 1}.png`;
  draw(); renderResult();
}

// ---------- 縮放 ----------
function applyZoom() {
  const p = page();
  const img = $('#pageimg');
  img.style.width = (p.w * scale) + 'px';
  const svg = $('#overlay');
  svg.setAttribute('width', p.w * scale);
  svg.setAttribute('height', p.h * scale);
  svg.setAttribute('viewBox', `0 0 ${p.w} ${p.h}`);
  $('#z-val').textContent = Math.round(scale * 100) + '%';
}
function fit() {
  scale = ($('#pagewrap').clientWidth - 20) / page().w;
  applyZoom();
}

// ---------- 畫框 ----------
function el(tag, attrs) {
  const e = document.createElementNS(SVGNS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}

function draw() {
  if (!data) return;
  applyZoom();
  const svg = $('#overlay'); svg.innerHTML = '';
  const p = page();
  const owned = new Set(blocks().flatMap(b => b.items));
  p.items.forEach((it, i) => {
    if (it.dead) return;
    const [x0, y0, x1, y1] = it.box;
    svg.appendChild(el('rect', { x: x0, y: y0, width: x1 - x0, height: y1 - y0,
      class: 'item' + (owned.has(i) ? '' : ' orphan') }));
    if (it.kind === 'math' && it.score < 0.6)
      svg.appendChild(el('rect', { x: x0, y: y0, width: x1 - x0, height: y1 - y0, class: 'low' }));
  });
  const ordered = [...blocks()].sort((a, b) => a.order - b.order);
  ordered.forEach((b, n) => {
    const [x0, y0, x1, y1] = b.box;
    const c = T[b.type].color;
    const r = el('rect', { x: x0, y: y0, width: x1 - x0, height: y1 - y0, stroke: c, fill: c,
      class: 'blk' + (sel.has(b.id) ? ' sel' : '') + (b.type === 'skip' ? ' skip' : ''), 'data-id': b.id });
    svg.appendChild(r);
    const inSeq = orderSeq && orderSeq.includes(b.id);
    const t = el('text', { x: x0 + 4, y: y0 + 28, fill: c, class: 'lab' });
    t.textContent = `${n + 1} ${T[b.type].name}${inSeq ? ' ✓' : ''}`;
    svg.appendChild(t);
  });
  if (sel.size === 1) {
    const b = blk([...sel][0]);
    if (b) {
      const [x0, y0, x1, y1] = b.box, s = 16;
      for (const [hx, hy, k] of [[x0, y0, 'nw'], [x1, y0, 'ne'], [x0, y1, 'sw'], [x1, y1, 'se']])
        svg.appendChild(el('rect', { x: hx - s / 2, y: hy - s / 2, width: s, height: s, class: 'handle', 'data-h': k }));
    }
  }
  props();
}

// ---------- 右邊結果 ----------
// 表格的 HTML 只取格子裡的文字重建（不直接塞 innerHTML）
function tableInto(el, caption, html) {
  if (caption.trim()) { const c = document.createElement('div'); c.textContent = caption; el.appendChild(c); }
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const t = document.createElement('table'); t.className = 'restable';
  for (const tr of doc.querySelectorAll('tr')) {
    const r = t.insertRow();
    for (const td of tr.querySelectorAll('td, th')) r.insertCell().textContent = td.textContent;
  }
  el.appendChild(t);
}

function renderResult() {
  if (!data) return;
  const box = $('#restext'); box.innerHTML = '';
  const src = document.querySelector('input[name=view]:checked').value === 'src';
  box.classList.toggle('src', src);
  const all = $('#allres').checked;
  let lastPage = -1;
  for (const s of data.seg) {
    if (!all && s.page !== cur) continue;
    if (s.page !== lastPage) {
      const h = document.createElement('div'); h.className = 'pg'; h.textContent = `第 ${s.page + 1} 頁`;
      box.appendChild(h); lastPage = s.page;
    }
    const d = document.createElement('div');
    d.className = 'seg t-' + s.type + (s.type === 'skip' ? ' skip' : '') + (s.off ? ' off' : '') + (s.drop ? ' drop' : '') + (s.page === cur && sel.has(s.id) ? ' sel' : '');
    d.style.borderLeftColor = T[s.type].color;
    d.dataset.page = s.page; d.dataset.id = s.id;
    d.title = T[s.type].name;
    const lines = s.type === 'skip' ? ['（略過）'] : s.lines;
    for (const l of lines) {
      const ld = document.createElement('div'); ld.className = 'line';
      const ti = l.indexOf('<table');
      if (!src && ti >= 0) tableInto(ld, l.slice(0, ti), l.slice(ti)); else ld.textContent = l;
      d.appendChild(ld);
    }
    if (s.hint) {
      const h = document.createElement('div'); h.className = 'hint';
      h.textContent = '圖中的字（沒有輸出；要放進題目，選這塊後在上方「輸出」改成「標記＋圖中文字」或「改成文字」）：' + s.hint;
      d.appendChild(h);
    }
    if (s.low) {
      const m = document.createElement('span'); m.className = 'lowmark'; m.textContent = `信心低 ${s.low}`;
      d.appendChild(m);
    }
    if (s.edited) {
      const m = document.createElement('span'); m.className = 'editmark'; m.textContent = '手動修改';
      d.appendChild(m);
    }
    d.onclick = () => {
      if (d.querySelector('textarea')) return;
      if (s.page !== cur) showPage(s.page);
      sel = new Set([s.id]); draw(); markSeg(); scrollToBlock(s.id);
    };
    d.ondblclick = ev => { ev.preventDefault(); editText(s.page, s.id); };
    box.appendChild(d);
  }
  if (data.drops) {
    const dd = document.createElement('div'); dd.className = 'drops';
    const t = document.createElement('b'); t.textContent = '刪題說明（存成 刪題說明.txt）';
    const pre = document.createElement('div'); pre.textContent = data.drops;
    dd.append(t, pre); box.appendChild(dd);
  }
  if (!src && window.MathJax && MathJax.typesetPromise) MathJax.typesetPromise([box]).catch(() => {});
}

// 手動修改文字：右邊那一段換成編輯框（\( \) 寫法），存在區塊的 text
function editText(pi, id) {
  if (pi === undefined) {
    if (sel.size !== 1) { status('選一個區塊再修改文字'); return; }
    pi = cur; id = [...sel][0];
  }
  const s = data.seg.find(x => x.page === pi && x.id === id);
  if (!s || s.type === 'skip') { status('略過的區塊沒有文字可以改'); return; }
  if (pi !== cur) showPage(pi);
  sel = new Set([id]); draw();
  if (!$('#allres').checked) renderResult();
  const d = document.querySelector(`#restext .seg[data-page="${pi}"][data-id="${id}"]`);
  if (!d) return;
  d.innerHTML = '';
  const ta = document.createElement('textarea');
  ta.value = s.src.join('\n');
  ta.rows = Math.max(2, s.src.length + 1);
  ta.setAttribute('aria-label', '修改這一塊的文字；算式用 \\( \\) 包起來；Ctrl+Enter 完成，Esc 取消');
  const pv = document.createElement('div'); pv.className = 'editpv';
  const bar = document.createElement('div'); bar.className = 'editbar';
  const mk = (txt, fn, title) => {
    const b = document.createElement('button'); b.type = 'button'; b.textContent = txt; if (title) b.title = title;
    b.onclick = e => { e.stopPropagation(); fn(); }; bar.appendChild(b); return b;
  };
  let timer = null;
  const preview = () => {
    pv.textContent = ta.value;
    if (window.MathJax && MathJax.typesetPromise) MathJax.typesetPromise([pv]).catch(() => {});
  };
  ta.oninput = () => { clearTimeout(timer); timer = setTimeout(preview, 300); };
  const done = () => mutate(() => {
    const b = blk(id);
    b.text = ta.value.split('\n').map(l => l.trimEnd());
    // 附圖改寫：原本只標記的改成「改成文字」（老師寫的敘述取代附圖），其他照原本的選擇
    if (['figure', 'score'].includes(b.type) && (b.how || (b.figtext ? 'figtext' : 'mark')) === 'mark') { b.how = 'text'; delete b.figtext; }
  }).then(() => status('已存手動修改的文字'));
  const cancel = () => { renderResult(); markSeg(); status('取消修改'); };
  mk('完成', done, 'Ctrl+Enter');
  mk('取消', cancel, 'Esc');
  if (s.edited) mk('改回辨識結果', () => mutate(() => { delete blk(id).text; }).then(() => status('已改回辨識結果')));
  const tip = document.createElement('span'); tip.className = 'edittip';
  tip.textContent = '算式用 \\( \\) 包起來；一行一個題幹／選項';
  bar.appendChild(tip);
  ta.onkeydown = e => {
    if (e.key === 'Enter' && e.ctrlKey) { e.preventDefault(); done(); }
    else if (e.key === 'Escape') { e.preventDefault(); cancel(); }
  };
  ta.onclick = e => e.stopPropagation();
  d.append(ta, bar, pv);
  preview();
  ta.focus();
  status('修改文字中：Ctrl+Enter 完成，Esc 取消');
}

function markSeg() {
  for (const d of document.querySelectorAll('#restext .seg'))
    d.classList.toggle('sel', +d.dataset.page === cur && sel.has(+d.dataset.id));
  const d = document.querySelector('#restext .seg.sel');
  if (d) d.scrollIntoView({ block: 'nearest' });
}

function scrollToBlock(id) {
  const b = blk(id); if (!b) return;
  const w = $('#pagewrap');
  w.scrollTo({ top: Math.max(0, b.box[1] * scale - 60), behavior: 'smooth' });
}

// ---------- 送到 vi-tools 文件整理 ----------
// 打開文件整理（網址帶 #from=haodian），等它回「vitools-ready」再用 postMessage 送文字。
// vi-tools 的位置預設是線上版；用本機的副本時在主控台設 localStorage['haodian.vitools'] = 'http://127.0.0.1:8000/'。
const VITOOLS = () => localStorage.getItem('haodian.vitools') || 'https://hurthuang.github.io/vi-tools/';

async function sendToVitools() {
  if (!data || !data.text.trim()) { status('還沒有文字可以送'); return; }
  const text = data.text, name = exam.replace(/^\d{8}-\d{6}_/, '');
  // 新分頁要在按下去的當下開，不然會被瀏覽器擋；中繼檔、剪貼簿在等文件整理準備好的時候做
  const url = new URL('pdf-to-accessible.html', VITOOLS()).href + '#from=haodian';
  // 剪貼簿要在這一頁還有焦點時寫（開了新分頁就失去焦點）；等文件整理回應的監聽也要在開分頁的當下就掛上
  const clipP = navigator.clipboard ? navigator.clipboard.writeText(text).then(() => true, () => false) : Promise.resolve(false);
  const w = window.open(url, '_blank');
  const vtP = buildVtdoc().then(r => r.html).catch(() => null);  // 中繼檔做不出來就只送文字
  const origin = new URL(url).origin;
  const okP = new Promise(res => {
    const t = setTimeout(() => { window.removeEventListener('message', on); res(false); }, 20000);
    async function on(e) {
      if (e.source !== w || !e.data || e.data.type !== 'vitools-ready') return;
      clearTimeout(t);
      window.removeEventListener('message', on);
      const vt = await vtP;
      w.postMessage({ type: 'haodian-text', name, text, formatted: true, vtdoc: vt }, origin);
      res(true);
    }
    window.addEventListener('message', on);
  });
  const copied = await Promise.race([clipP, new Promise(r => setTimeout(() => r(false), 3000))]);
  const fallback = copied ? '文字已複製到剪貼簿，可以在文件整理按 Ctrl＋V 貼上。' : '請按「輸出」→「複製全部文字」再貼到文件整理。';
  if (!w) { status('瀏覽器擋住了新分頁。' + fallback); return; }
  status('打開文件整理中…');
  const ok = await okP;
  status(ok ? `已送到文件整理（新分頁），標成「已排版」：可以在那邊改字、朗讀檢查，改完按「存回中繼檔」。`
    : `文件整理沒有回應（可能沒有網路，或文件整理還是舊版）。${fallback}`);
}

// ---------- 中繼檔（.vtdoc.html） ----------
// 伺服器產生 HTML（算式是 \( \) 純文字＋data-tex），這裡用 MathJax 把每個算式轉成 MathML 再下載，NVDA 才念得出來。
// 伺服器產生中繼檔，算式轉成 MathML → { html, name, n（轉好的算式）, bad }
async function buildVtdoc() {
  const r = await fetch(`api/vtdoc/${encodeURIComponent(exam)}?images=${$('#vt-images').checked ? 1 : 0}&source=${$('#vt-source').checked ? 1 : 0}`);
  if (!r.ok) throw new Error((await r.json()).error || r.statusText);
  let html = await r.text();
  let n = 0, bad = 0;
  if (window.MathJax && MathJax.tex2mml) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    for (const s of doc.querySelectorAll('.vt-math[data-tex]')) {
      try {
        s.innerHTML = MathJax.tex2mml(s.dataset.tex, { display: s.classList.contains('vt-display') });
        n++;
      } catch (e) { bad++; }  // 轉不過的留 \( \) 原碼
    }
    html = '<!DOCTYPE html>\n' + doc.documentElement.outerHTML;
  }
  const name = decodeURIComponent((r.headers.get('Content-Disposition') || '').split("UTF-8''")[1] || 'exam.vtdoc.html');
  return { html, name, n, bad };
}

async function saveVtdoc() {
  if (!exam) { status('先選一份試卷'); return; }
  status('產生中繼檔中…');
  try {
    const { html, name, n, bad } = await buildVtdoc();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([html], { type: 'text/html;charset=utf-8' }));
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    status(`已存成中繼檔 ${name}（${Math.round(html.length / 1024)} KB）` + (n ? `，算式 ${n} 個轉成 MathML` : '') +
      (bad ? `，${bad} 個轉不過留原碼` : '') + '。用瀏覽器打開就是 NVDA 電子檔；輸出資料夾也存了一份（沒有 MathML）。');
  } catch (e) { status('存中繼檔失敗：' + e.message); }
}

// ---------- 選單（開啟檔案、重來、輸出） ----------
function initMenus() {
  const menus = [...document.querySelectorAll('details.menu')];
  document.addEventListener('click', e => {
    for (const m of menus) if (m.open && !m.contains(e.target)) m.open = false;
  });
  for (const m of menus) {
    m.addEventListener('toggle', () => {
      if (!m.open) return;
      for (const o of menus) if (o !== m) o.open = false;
      const l = m.querySelector('.menu-list');   // 靠右的選單換行到左邊時，改成往右展開，不要超出畫面
      l.classList.remove('flip');
      if (l.getBoundingClientRect().left < 0) l.classList.add('flip');
    });
    m.querySelectorAll('.menu-list button').forEach(b => b.addEventListener('click', () => { m.open = false; }));
    m.addEventListener('keydown', e => { if (e.key === 'Escape' && m.open) { m.open = false; m.querySelector('summary').focus(); e.stopPropagation(); } });
  }
  const op = $('#m-open');
  op.addEventListener('toggle', () => { if (op.open) $('#openpages').focus(); });
  $('#openpages').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); $('#b-open').click(); } });
}

// ---------- 屬性 ----------
function props() {
  const pr = $('#props');
  const bs = [...sel].map(blk).filter(Boolean);
  if (!bs.length) { pr.hidden = true; return; }
  pr.hidden = false;
  const one = bs.length === 1, b = bs[0];
  const same = bs.every(x => x.type === b.type);
  const ts = $('#p-type');
  [...ts.options].filter(o => !o.value).forEach(o => o.remove());
  if (same) ts.value = b.type;
  else { ts.add(new Option('（不同類型）', ''), 0); ts.value = ''; }
  $('#p-head').textContent = one ? '' : `選了 ${bs.length} 塊`;
  $('#p-head').hidden = one;
  $('#b-merge').hidden = one;
  $('#b-edit').hidden = $('#b-reocr').hidden = $('#b-allpages').hidden = !one;
  if (!one) { $('#p-extra').innerHTML = ''; return; }
  pr.style.borderLeft = `6px solid ${T[b.type].color}`;
  let h = '';
  if (b.type === 'figure' || b.type === 'score') {
    const how = b.how || (b.figtext ? 'figtext' : 'mark');
    const opt = (v, t) => `<option value="${v}" ${how === v ? 'selected' : ''}>${t}</option>`;
    h += `<label>輸出 <select id="p-how">${opt('mark', '標「(請參閱附圖)」（之後做觸圖）')}` +
      `${opt('figtext', '標記＋圖中文字')}${opt('text', '改成文字（圖裡的字，或按 E 改寫；不標附圖）')}` +
      `${opt('none', '不輸出')}</select></label>　`;
  }
  if (b.type === 'table') {
    const how = b.how || 'table';
    const opt = (v, t, cur) => `<option value="${v}" ${cur === v ? 'selected' : ''}>${t}</option>`;
    h += `<label>輸出 <select id="p-thow">${opt('table', '保留表格', how)}${opt('rows', '文字：以列為軸（一列一行）', how)}` +
      `${opt('cols', '文字：以欄為軸（一欄一行）', how)}${opt('mark', '只標「(請參閱附表)」', how)}${opt('none', '不輸出', how)}</select></label>　`;
    if (how === 'rows' || how === 'cols') {
      const tri = (id, v, name) => `<label>${name} <select id="${id}">${opt('', '自動', v === undefined ? '' : String(v))}` +
        `${opt('true', '是', String(v))}${opt('false', '不是', String(v))}</select></label>　`;
      h += tri('p-hrow', b.hrow, '第一列是標題') + tri('p-hcol', b.hcol, '第一欄是標題');
    }
  }
  if (b.type === 'question')
    h += `<label><input type="checkbox" id="p-drop" ${b.drop ? 'checked' : ''}> 刪題（此題型盲生不適合作答）</label>　`;
  if (b.type === 'figtext')
    h += `<label>說話者 <input type="text" id="p-speaker" size="8" value="${(b.speaker || '').replace(/"/g, '&quot;')}"></label>`;
  h += `<span style="color:#666">（${b.items.length} 個辨識項目）</span>`;
  $('#p-extra').innerHTML = h;
  const hw = $('#p-how');
  if (hw) hw.onchange = () => mutate(() => { b.how = hw.value; delete b.figtext; });
  const th = $('#p-thow');
  if (th) th.onchange = () => mutate(() => { if (th.value === 'table') delete b.how; else b.how = th.value; }).then(props);
  for (const k of ['hrow', 'hcol']) {
    const s = $('#p-' + k);
    if (s) s.onchange = () => mutate(() => { if (s.value === '') delete b[k]; else b[k] = s.value === 'true'; });
  }
  const dr = $('#p-drop');
  if (dr) dr.onchange = () => mutate(() => { if (dr.checked) b.drop = true; else delete b.drop; });
  const sp = $('#p-speaker');
  if (sp) sp.onchange = () => mutate(() => { b.speaker = sp.value.trim(); });
}

// ---------- 修改 ----------
function snapshot() { return JSON.stringify({ blocks: data.blocks, items: data.pages.map(p => p.items) }); }

async function save(extra) {
  const body = Object.assign({ blocks: data.blocks, tags: $('#tags').checked }, extra || {});
  const r = await api(`api/save/${encodeURIComponent(exam)}`, body);
  data.text = r.text; data.seg = r.seg; data.drops = r.drops; data.blocks = r.blocks;
  r.pages.forEach((p, i) => { data.pages[i].items = p.items; });
  draw(); renderResult(); markSeg(); thumbsWarn();
}

function thumbsWarn() {
  [...$('#thumbs').children].forEach((b, i) => {
    const low = data.seg.filter(s => s.page === i).reduce((a, s) => a + s.low, 0);
    const w = b.querySelector('.warn');
    if (w) w.textContent = low ? `⚠${low}` : '';
  });
}

async function mutate(fn) {
  undo.push(snapshot());
  if (undo.length > 50) undo.shift();
  fn();
  try { await save(); } catch (e) { status('存檔失敗：' + e.message); }
}

async function doUndo() {
  const s = undo.pop(); if (!s) { status('沒有可以復原的動作'); return; }
  const o = JSON.parse(s);
  data.blocks = o.blocks;
  o.items.forEach((it, i) => { data.pages[i].items = it; });
  sel.clear();
  await save({ items: o.items });
  status('已復原');
}

function center(box) { return [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]; }
function inBox(pt, b) { return pt[0] >= b[0] && pt[0] <= b[2] && pt[1] >= b[1] && pt[1] <= b[3]; }

// 框改了：框裡的辨識項目歸這一塊，框外的放掉
function claim(b) {
  const p = page();
  const mine = [];
  // 選項、文字的大框包住整張附圖時，圖裡的字（「原料」「市場」）留給附圖；選項字母、題號照收
  const within = (a, o) => o[0] - 20 <= a[0] && a[2] <= o[2] + 20 && o[1] - 20 <= a[1] && a[3] <= o[3] + 20;
  const figs = ['option', 'text'].includes(b.type) ? blocks().filter(o => o !== b && o.type === 'figure' && within(o.box, b.box)) : [];
  const figOf = it => /^\s*[（(]\s*[A-EＡ-Ｅ]\s*[)）]\s*$/.test(it.text || '') || /^\s*\d{1,2}\s*[.．、]/.test(it.text || '')
    ? null : figs.find(o => inBox(center(it.box), o.box));
  const toFig = [];
  p.items.forEach((it, i) => {
    if (it.dead || !inBox(center(it.box), b.box)) return;
    const f = figOf(it);
    if (f) toFig.push([f, i]); else mine.push(i);
  });
  for (const o of blocks()) if (o !== b) o.items = o.items.filter(i => !mine.includes(i));
  for (const [f, i] of toFig) {
    for (const o of blocks()) if (o !== f) o.items = o.items.filter(x => x !== i);
    if (!f.items.includes(i)) f.items.push(i);
  }
  b.items = mine;
}

// 新框的閱讀順序：排在框住的字、框住的區塊原本最前面的位置；什麼都沒框到，就排在同一欄（左右有重疊）
// 上方最近那塊的後面。兩欄的卷子不能只看高度，否則右欄上半部的題目會被排到左欄中間
function newBlockOrder(box) {
  const p = page(), bs = blocks();
  const mine = new Set();
  p.items.forEach((it, i) => { if (!it.dead && inBox(center(it.box), box)) mine.add(i); });
  const hit = bs.filter(b => b.items.some(i => mine.has(i)) || inBox(center(b.box), box));
  if (hit.length) return Math.min(...hit.map(b => b.order));
  const xo = b => Math.min(b.box[2], box[2]) - Math.max(b.box[0], box[0]);
  const above = bs.filter(b => b.box[3] <= box[1] + 10 && xo(b) > 0.3 * Math.min(b.box[2] - b.box[0], box[2] - box[0]));
  if (above.length) {
    const near = above.reduce((a, b) => (b.box[3] > a.box[3] ? b : a));
    return near.order + 0.5;
  }
  return -0.5;
}

function nextId() { return Math.max(-1, ...data.blocks.flat().map(b => b.id)) + 1; }

function setType(t) {
  if (!sel.size) { status('先選一個區塊'); return; }
  lastType = t;
  mutate(() => {
    for (const id of sel) {
      const b = blk(id);
      if (!b) continue;
      if (b.type !== t && (b.type === 'table' || t === 'table')) { delete b.how; delete b.hrow; delete b.hcol; }  // 附圖和表格的輸出方式不通用
      b.type = t;
      if (t === 'table') claimBox(b);
    }
  });
  status(`改成「${T[t].name}」`);
}

// 表格：框裡的字（原本被分到其他區塊的）都收進來；被收空的文字區塊拿掉
function claimBox(b) {
  const its = [];
  page().items.forEach((it, i) => { if (!it.dead && inBox(center(it.box), b.box)) its.push(i); });
  for (const o of blocks()) if (o !== b) o.items = o.items.filter(i => !its.includes(i));
  b.items = its;
  data.blocks[cur] = blocks().filter(o => o === b || o.items.length || ['figure', 'table', 'score'].includes(o.type));
}

function removeBlocks() {
  if (!sel.size) { status('先選要刪除的區塊（Shift＋點可以選多塊）'); return; }
  const n = sel.size;
  mutate(() => { data.blocks[cur] = blocks().filter(b => !sel.has(b.id)); sel.clear(); });
  status(`已刪除 ${n} 個區塊（Ctrl＋Z 可以復原）`);
}

function merge() {
  if (sel.size < 2) { status('按住 Shift 點選兩塊以上再合併'); return; }
  mutate(() => {
    const bs = [...sel].map(blk).sort((a, b) => a.order - b.order);
    const a = bs[0];
    for (const b of bs.slice(1)) {
      a.box = [Math.min(a.box[0], b.box[0]), Math.min(a.box[1], b.box[1]), Math.max(a.box[2], b.box[2]), Math.max(a.box[3], b.box[3])];
      a.items = a.items.concat(b.items);
      data.blocks[cur] = blocks().filter(x => x !== b);
    }
    sel = new Set([a.id]);
  });
}

async function reocr() {
  if (sel.size !== 1) { status('選一個區塊再重新辨識'); return; }
  const b = blk([...sel][0]);
  undo.push(snapshot());
  status('重新辨識中…（第一次要載入模型，大約 20 秒）');
  try {
    const r = await api(`api/reocr/${encodeURIComponent(exam)}`, { blocks: data.blocks, page: cur, id: b.id, tags: $('#tags').checked });
    data.text = r.text; data.seg = r.seg; data.drops = r.drops; data.blocks = r.blocks;
    r.pages.forEach((p, i) => { data.pages[i].items = p.items; });
    draw(); renderResult(); markSeg();
    status('重新辨識完成');
  } catch (e) { status('重新辨識失敗：' + e.message); }
}

function applyAllPages() {
  if (sel.size !== 1) { status('選一個區塊（例如頁首）再套用到所有頁'); return; }
  const src = blk([...sel][0]);
  const area = b => Math.max(1, (b[2] - b[0]) * (b[3] - b[1]));
  const ov = (a, b) => Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0])) * Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
  mutate(() => {
    data.blocks.forEach((bl, pi) => {
      if (pi === cur) return;
      const hit = bl.filter(b => ov(b.box, src.box) > 0.5 * area(b.box));
      if (hit.length) { for (const b of hit) b.type = src.type; return; }
      const items = [];
      data.pages[pi].items.forEach((it, i) => { if (!it.dead && inBox(center(it.box), src.box)) items.push(i); });
      if (!items.length) return;
      for (const o of bl) o.items = o.items.filter(i => !items.includes(i));
      bl.push({ id: nextId(), type: src.type, box: [...src.box], items, order: -1 });
    });
  });
  status(`每頁同樣位置都改成「${T[src.type].name}」`);
}

function toggleOrder() {
  if (orderSeq) {
    orderSeq = null; $('#b-order').classList.remove('on'); status('排順序結束'); draw(); return;
  }
  orderSeq = []; $('#b-order').classList.add('on');
  status('依閱讀順序點各區塊；沒點到的排在後面。再按「排順序」或 Esc 結束。');
  draw();
}

function orderClick(id) {
  if (orderSeq.includes(id)) return;
  orderSeq.push(id);
  mutate(() => {
    const rest = blocks().filter(b => !orderSeq.includes(b.id)).sort((a, b) => a.order - b.order);
    orderSeq.forEach((bid, k) => { blk(bid).order = k; });
    rest.forEach((b, k) => { b.order = orderSeq.length + k; });
  });
}

// ---------- 滑鼠 ----------
function pt(ev) {
  const r = $('#overlay').getBoundingClientRect();
  return [(ev.clientX - r.left) / scale, (ev.clientY - r.top) / scale];
}

let drag = null;
function onDown(ev) {
  if (ev.button !== 0) return;
  const p = pt(ev);
  const tgt = ev.target;
  if (tgt.dataset.h) {
    const b = blk([...sel][0]);
    drag = { mode: 'resize', h: tgt.dataset.h, b, start: p, orig: [...b.box], snap: snapshot() };
  } else if (tgt.dataset.id !== undefined) {
    const id = +tgt.dataset.id;
    if (orderSeq) { orderClick(id); return; }
    if (ev.shiftKey) { sel.has(id) ? sel.delete(id) : sel.add(id); draw(); markSeg(); return; }
    if (!sel.has(id) || sel.size > 1) { sel = new Set([id]); draw(); markSeg(); }
    const b = blk(id);
    drag = { mode: 'move', b, start: p, orig: [...b.box], snap: snapshot() };
  } else {
    if (orderSeq) return;
    sel.clear(); draw(); markSeg();
    drag = { mode: 'draw', start: p, rect: null };
  }
  ev.preventDefault();
}

function onMove(ev) {
  if (!drag) return;
  const p = pt(ev), dx = p[0] - drag.start[0], dy = p[1] - drag.start[1];
  if (drag.mode === 'move') {
    const o = drag.orig;
    drag.b.box = [o[0] + dx, o[1] + dy, o[2] + dx, o[3] + dy];
    drag.moved = Math.abs(dx) + Math.abs(dy) > 4;
    if (drag.moved) draw();
  } else if (drag.mode === 'resize') {
    const o = drag.orig, b = [...o];
    if (drag.h.includes('w')) b[0] = o[0] + dx;
    if (drag.h.includes('e')) b[2] = o[2] + dx;
    if (drag.h.includes('n')) b[1] = o[1] + dy;
    if (drag.h.includes('s')) b[3] = o[3] + dy;
    drag.b.box = [Math.min(b[0], b[2]), Math.min(b[1], b[3]), Math.max(b[0], b[2]), Math.max(b[1], b[3])];
    drag.moved = true; draw();
  } else {
    const x0 = Math.min(p[0], drag.start[0]), y0 = Math.min(p[1], drag.start[1]);
    const x1 = Math.max(p[0], drag.start[0]), y1 = Math.max(p[1], drag.start[1]);
    drag.box = [x0, y0, x1, y1];
    if (!drag.rect) { drag.rect = el('rect', { class: 'draw' }); $('#overlay').appendChild(drag.rect); }
    Object.entries({ x: x0, y: y0, width: x1 - x0, height: y1 - y0 }).forEach(([k, v]) => drag.rect.setAttribute(k, v));
  }
}

async function onUp() {
  if (!drag) return;
  const d = drag; drag = null;
  if ((d.mode === 'move' || d.mode === 'resize') && d.moved) {
    undo.push(d.snap);
    claim(d.b);
    try { await save(); } catch (e) { status('存檔失敗：' + e.message); }
  } else if (d.mode === 'draw' && d.box && d.box[2] - d.box[0] > 15 && d.box[3] - d.box[1] > 15) {
    let nb;
    await mutate(() => {
      // 新的一塊排在框住的項目原本所在區塊的位置
      nb = { id: nextId(), type: lastType, box: d.box, items: [], order: 0 };
      const at = newBlockOrder(d.box);
      blocks().push(nb);
      claim(nb);
      const ord = blocks().filter(b => b !== nb).sort((a, b) => a.order - b.order);
      const k0 = ord.findIndex(b => b.order >= at);
      ord.splice(k0 < 0 ? ord.length : k0, 0, nb);
      ord.forEach((b, k) => { b.order = k; });
      sel = new Set([nb.id]);
    });
    status(nb.items.length ? `新增「${T[nb.type].name}」，框住 ${nb.items.length} 個項目` :
      '新增的框裡沒有辨識到的字；要辨識請按「重新辨識這塊」（R）');
  } else draw();
}

// ---------- 鍵盤 ----------
function onKey(ev) {
  if (ev.target.matches('input, select, textarea')) return;
  if (ev.ctrlKey && ev.key.toLowerCase() === 'z') { ev.preventDefault(); doUndo(); return; }
  if (ev.ctrlKey || ev.altKey || ev.metaKey) return;
  const ty = TYPES.find(x => x.k === ev.key);
  if (ty) { setType(ty.t); ev.preventDefault(); return; }
  switch (ev.key) {
    case 'Delete': case 'Backspace': removeBlocks(); ev.preventDefault(); break;
    case 'g': case 'G': merge(); break;
    case 'r': case 'R': reocr(); break;
    case 'o': case 'O': toggleOrder(); break;
    case 'e': case 'E': case 'Enter': ev.preventDefault(); editText(); break;
    case 'Escape': if (orderSeq) toggleOrder(); else { sel.clear(); draw(); markSeg(); } break;
    case 'PageDown': if (cur < data.pages.length - 1) showPage(cur + 1); ev.preventDefault(); break;
    case 'PageUp': if (cur > 0) showPage(cur - 1); ev.preventDefault(); break;
    case 'Tab': {
      ev.preventDefault();
      const ord = [...blocks()].sort((a, b) => a.order - b.order);
      if (!ord.length) break;
      const k = sel.size === 1 ? ord.findIndex(b => b.id === [...sel][0]) : -1;
      const n = ord[(k + (ev.shiftKey ? -1 : 1) + ord.length) % ord.length];
      sel = new Set([n.id]); draw(); markSeg(); scrollToBlock(n.id);
      break;
    }
  }
}

function bindUI() {
  const svg = $('#overlay');
  svg.addEventListener('mousedown', onDown);
  window.addEventListener('mousemove', onMove);
  window.addEventListener('mouseup', onUp);
  window.addEventListener('keydown', onKey);
  $('#z-in').onclick = () => { scale *= 1.2; draw(); };
  $('#z-out').onclick = () => { scale /= 1.2; draw(); };
  $('#z-fit').onclick = () => { fit(); draw(); };
  $('#b-order').onclick = toggleOrder;
  $('#b-merge').onclick = merge;
  $('#b-del').onclick = removeBlocks;
  $('#b-reocr').onclick = reocr;
  $('#b-edit').onclick = () => editText();
  $('#b-allpages').onclick = applyAllPages;
  $('#b-undo').onclick = doUndo;
  // 清除區塊：全部拿掉，自己重新框（框裡的字不屬於任何區塊就不輸出）
  const clearBlocks = all => {
    const n = all ? data.blocks.reduce((s, b) => s + b.length, 0) : blocks().length;
    if (!n) { status('沒有區塊可以清除'); return; }
    mutate(() => { if (all) data.blocks = data.blocks.map(() => []); else data.blocks[cur] = []; sel.clear(); })
      .then(() => status(`已清除${all ? '全部頁' : '這一頁'}的 ${n} 個區塊：拖曳空白處框出新的區塊（Ctrl＋Z 可以復原）`));
  };
  $('#b-clear-page').onclick = () => clearBlocks(false);
  $('#b-clear-all').onclick = () => clearBlocks(true);
  $('#b-reset').onclick = async () => {
    undo.push(snapshot());
    const r = await api(`api/reset/${encodeURIComponent(exam)}`, {});
    data.text = r.text; data.seg = r.seg; data.drops = r.drops; data.blocks = r.blocks;
    r.pages.forEach((p, i) => { data.pages[i].items = p.items; });
    sel.clear(); draw(); renderResult(); status('已重新自動判斷（可以按復原回到剛才）');
  };
  $('#tags').onchange = () => save();
  document.querySelectorAll('input[name=view]').forEach(r => r.onchange = renderResult);
  $('#allres').onchange = renderResult;
  $('#b-send').onclick = sendToVitools;
  $('#b-vtdoc').onclick = saveVtdoc;
  $('#b-copy').onclick = async () => { await navigator.clipboard.writeText(data.text); status('已複製全部文字'); };
  $('#b-save').onclick = () => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob(['﻿' + data.text], { type: 'text/plain' }));
    a.download = `${exam}.txt`; a.click();
  };
  $('#b-tactile').onclick = async () => {
    status('匯出附圖中…');
    try {
      const r = await fetch(`api/tactile/${encodeURIComponent(exam)}`);
      if (!r.ok) throw new Error((await r.json()).error || r.statusText);
      const a = document.createElement('a');
      a.href = URL.createObjectURL(await r.blob());
      a.download = `${exam.replace(/^\d{8}-\d{6}_/, '')}_附圖.tactile`; a.click();
      status(`已匯出 ${r.headers.get('X-Figures')} 張附圖（輸出資料夾也存了一份 附圖.tactile）：到 vi-tools 觸圖製作按「開啟檔案」打開`);
    } catch (e) { status('匯出失敗：' + e.message); }
  };
}

init().catch(e => status('錯誤：' + e.message));
