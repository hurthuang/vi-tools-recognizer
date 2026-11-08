r"""試卷：辨識（process）、版面狀態（exam_state／save_state）、右邊窗格的預覽、重新辨識一塊。

試卷資料夾（輸出\<時間>_<名稱>\試卷資料\）：
  pNN.png    漂白後的頁面圖（版面調整畫面顯示用）
  pNN.json   算式與文字行（含位置、逐字框、信心分數）
  tNN.json   PDF 文字層的文字行（有文字層才有）
  edits.json 版面調整改過的區塊（沒有就自動判斷）
"""
import json
import threading
import time
from pathlib import Path

from PIL import Image

from . import layout, mathocr, textlayer, vertical

LOCK = threading.Lock()  # 模型一次只跑一個
IMG_EXT = ('.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.gif', '.webp')
MAX_SIDE = 2400  # 照片太大先縮（A4 200 dpi 約 1654×2339）
LOW = 0.6  # 算式信心低於這個值，畫面上標成要看


def _pages(src, pages_spec):
    """→ (頁碼清單（1 起算）, 取第 k 頁圖的函式, pdf 頁面或 None 的函式, 有沒有文字層)。"""
    import prepare
    low = str(src).lower()
    if low.endswith('.pdf'):
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(src))
        idx = prepare.parse_pages(pages_spec, len(doc))
        has = textlayer.has_text(doc)
        return ([i + 1 for i in idx], lambda n: doc[n - 1].render(scale=mathocr.DPI / 72).to_pil().convert('RGB'),
                lambda n: doc[n - 1], has)
    if low.endswith(IMG_EXT):
        def img(_):
            im = Image.open(src)
            im.load()
            im = im.convert('RGB')
            s = MAX_SIDE / max(im.size)
            return im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS) if s < 1 else im
        return [1], img, lambda n: None, False
    raise ValueError('只能辨識 PDF 或圖檔（png、jpg、tif…）')


class Stopped(Exception):
    """使用者按了「停止」，而且一頁都還沒做完。"""


def process(src, pages_spec, d, use_text_layer=True, log=print, progress=None, cancel=None):
    """辨識每一頁，存到試卷資料夾 d。回傳 dict(pages, math, low, text_layer)。
    progress(做完幾頁, 共幾頁)；cancel 是 threading.Event，設了就做完這頁停下來，只留做完的頁。"""
    progress = progress or (lambda *a: None)
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    nums, page_img, pdf_page, has_text = _pages(src, pages_spec)
    if not nums:
        raise ValueError('沒有要辨識的頁（請檢查頁碼）。')
    use_tl = use_text_layer and has_text
    log('PDF 有文字層：中文、題號、選項直接讀文字層，算式與附圖裡的字用辨識（掃描頁整頁辨識）。' if use_tl else
        '全部用辨識（掃描檔、圖檔，或沒有勾選使用文字層）。')
    n_tl = n_img = 0
    t0 = time.time()
    if LOCK.locked():
        log('等前一份辨識做完…')
    with LOCK:
        log('載入模型…')
        mathocr.models()
        nm = nl = 0
        done = []
        for k, n in enumerate(nums, 1):
            if cancel is not None and cancel.is_set():
                log(f'已停止：做完 {k - 1}／{len(nums)} 頁')
                break
            progress(k - 1, len(nums))
            log(f'第 {n} 頁辨識中…')
            t = time.time()
            img = mathocr.clean(page_img(n))
            items = mathocr.raw_items(img)
            pg = pdf_page(n) if use_tl else None
            dch = textlayer.chars_of(pg, mathocr.DPI) if pg is not None else []
            vert = vertical.chars_vertical(dch) if len(dch) >= 20 else vertical.items_vertical(items)
            img.convert('L').save(d / f'p{k:02d}.png', optimize=True)  # 畫面顯示用（直立）
            Wd = None
            if vert:  # 直書：轉成工作座標（見 vertical.py）
                Wd = img.size[0]
                items = [vertical.item_d2v(it, Wd) for it in items]
                img = img.transpose(Image.ROTATE_90)
                img.convert('L').save(d / f'p{k:02d}r.png', optimize=True)
            meta = dict(w=img.size[0], h=img.size[1], dpi=mathocr.DPI, src_page=n, items=items)
            if vert:
                meta['rot'] = 1
            (d / f'p{k:02d}.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
            mi = [j for j, it in enumerate(items) if it['kind'] == 'math']
            how = '直書，辨識' if vert else '辨識'
            wl = vertical.walls(img) if vert else []
            if wl:
                meta['walls'] = wl
                (d / f'p{k:02d}.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
            tl = textlayer.page_layer(pg, mathocr.DPI, items, Wd=Wd, walls=wl) if use_tl else None
            if tl is not None:  # 這頁有文字層（沒有的是掃描頁，整頁用辨識）
                (d / f't{k:02d}.json').write_text(json.dumps(tl, ensure_ascii=False), encoding='utf-8')
                drop = set(tl['drop_math'])
                mi = [j for j in mi if j not in drop]
                n_tl += 1
                n_img += len(tl['keep_ocr'])
                how = ('直書，' if vert else '') + '文字層' + (f'＋附圖裡的字 {len(tl["keep_ocr"])} 行' if tl['keep_ocr'] else '')
            nm += len(mi)
            nl += sum(1 for j in mi if items[j]['score'] < LOW)
            log(f'第 {n} 頁：{how}，算式 {len(mi)} 個（{time.time() - t:.0f} 秒）[{k}/{len(nums)}]')
            done.append(n)
            progress(k, len(nums))
    if not done:
        raise Stopped('已停止，還沒有做完任何一頁。')
    log(f'辨識完成，共 {time.time() - t0:.0f} 秒。')
    return dict(pages=done, math=nm, low=nl, text_layer=use_tl, text_pages=n_tl, image_lines=n_img)


def exam_state(d):
    """→ (pages, blocks)：有 edits.json 就用它，否則自動分區塊。"""
    d = Path(d)
    ed = d / 'edits.json'
    if ed.exists():
        pages, _ = layout.load(d)
        e = json.loads(ed.read_text(encoding='utf-8'))
        for p, items in zip(pages, e['items']):
            p['items'] = items
            for it in items:
                if it['kind'] == 'math' and it.get('raw') is not None:
                    it['tex'] = mathocr.tidy(it['raw'])
        return pages, layout.figures_own_labels(pages, e['blocks'])  # 大框收走的圖裡的字還給附圖
    return layout.auto_all(d)


def save_state(d, pages, blocks):
    Path(d, 'edits.json').write_text(json.dumps(dict(items=[p['items'] for p in pages], blocks=blocks),
                                                ensure_ascii=False), encoding='utf-8')


def write_text(d, pages, blocks):
    """試卷.txt（\\( \\) 寫法，可以直接貼到 vi-tools）寫在試卷資料夾的上一層；有刪題就另寫 刪題說明.txt。"""
    drops = []
    text = layout.render(pages, blocks, drops=drops)
    out = Path(d).parent
    out.joinpath('試卷.txt').write_text(text, encoding='utf-8-sig')
    f = out / '刪題說明.txt'
    if drops:
        f.write_text(layout.drop_report(drops), encoding='utf-8-sig')
    elif f.exists():
        f.unlink()
    return text


def export_vtdoc(d, images=True, source=True):
    """版面 → vi-tools 中繼檔（試卷.vtdoc.html，寫在試卷資料夾的上一層）→ bytes。"""
    from . import vtdoc
    pages, blocks = exam_state(d)
    data = vtdoc.build(d, pages, blocks, images, source).encode('utf-8')
    Path(d).parent.joinpath('試卷.vtdoc.html').write_bytes(data)
    return data


def export_tactile(d):
    """要做觸圖的附圖 → vi-tools 觸圖製作的編輯檔（附圖.tactile，寫在試卷資料夾的上一層）。
    每張附圖一頁：原圖（PNG）、圖裡的字當標籤（原位置）；觸圖製作打開時才轉成點陣，可以描圖、重新產生圖形。
    → (檔案內容 bytes, 張數)"""
    import base64
    import io
    pages, blocks = exam_state(d)
    figs = layout.tactile_figures(pages, blocks)
    out = []
    imgs = {}
    for f in figs:
        p = pages[f['page']]
        if f['page'] not in imgs:
            imgs[f['page']] = Image.open(p.get('png_d') or p['png']).convert('RGB')
        im = imgs[f['page']]
        if p.get('rot'):
            f = dict(f, box=vertical.box_v2d(f['box'], p['h']),
                     labels=[(t,) + vertical.pt_v2d(cx, cy, p['h']) for t, cx, cy in f['labels']])
        x0, y0, x1, y1 = f['box']
        pad = 6
        x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
        x1, y1 = min(im.width, x1 + pad), min(im.height, y1 + pad)
        buf = io.BytesIO()
        im.crop((x0, y0, x1, y1)).save(buf, 'PNG', optimize=True)
        out.append(dict(
            title=f['title'],
            src=dict(image='data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode('ascii'),
                     labels=[dict(text=t, cx=round(cx - x0, 1), cy=round(cy - y0, 1)) for t, cx, cy in f['labels']]),
        ))
    data = json.dumps(dict(format='vi-tools-tactile', version=1, pages=out, source='vi-tools 辨識套件'),
                      ensure_ascii=False).encode('utf-8')
    if out:
        Path(d).parent.joinpath('附圖.tactile').write_bytes(data)
    return data, len(out)


def preview(pages, blocks, tags):
    """整份文字，加上每個區塊排出來的行（右邊窗格用）。"""
    drops = []
    text = layout.render(pages, blocks, tags=tags, drops=drops)
    seg = []
    dropping = False
    for pi, (p, bl) in enumerate(zip(pages, blocks)):
        for b in sorted(bl, key=lambda b: b['order']):
            hint = ''
            how = ''
            if b['type'] in ('title', 'section', 'question'):
                dropping = bool(b.get('drop')) and b['type'] == 'question'
            off = dropping and not b.get('drop')  # 刪掉的題目的選項、附圖
            if b['type'] in ('skip',):
                lines = []
            elif b['type'] == 'table':
                how = layout.table_how(b)
                if how == 'none':
                    lines = ['［附表］（不輸出）']
                elif how == 'mark':
                    lines = ['［附表］（只標「(請參閱附表)」）']
                else:
                    lines = [layout.to_paren(l) for l in layout.block_lines(p, b)] or ['［附表］（沒有找到表格裡的字）']
            elif b['type'] in ('figure', 'score'):
                how = layout.fig_how(b)
                body = [layout.to_paren(l) for l in layout.block_lines(p, dict(b, type='text'))]
                mark = {'figure': '［附圖］', 'score': '［譜例］'}[b['type']]
                if how == 'text':
                    lines = body or ['（改成文字：還沒有內容，按 E 輸入）']
                elif how == 'none':
                    lines = [mark + '（不輸出）']
                elif how == 'figtext':
                    lines = [mark, '圖中文字：' + ' '.join(body)]
                else:
                    lines = [mark]
                    if body:  # 圖裡辨識出的字先給老師看（不輸出）
                        hint = ' '.join(body)
            elif b['type'] == 'figtext':
                t = ' '.join(layout.to_paren(l) for l in layout.block_lines(p, dict(b, type='text')))
                lines = [f'{b["speaker"]}說：{t}' if b.get('speaker') else f'圖中文字：{t}']
            elif b['type'] == 'question' and b.get('drop'):
                ls = layout.block_lines(p, b)
                m = layout.QNUM.match(ls[0]) if ls else None
                lines = [(f'{m.group(1)}. ' if m else '') + layout.DROP_TEXT]
            else:
                lines = [layout.to_paren(l) for l in layout.block_lines(p, b)]
                if b['type'] == 'option' and b.get('text') is None and layout.empty_options(lines):
                    lines = [layout.OPT_FIG_TEXT]  # 選項是圖；要改成文字描述按 E
            low = [i for i in b['items'] if i < len(p['items']) and p['items'][i]['kind'] == 'math'
                   and p['items'][i]['score'] < LOW]
            edited = b.get('text') is not None
            # 手動修改時編輯框裡先放的文字
            src_b = b if b['type'] in ('title', 'section', 'question', 'option', 'text', 'table') else dict(b, type='text')
            src = [layout.to_paren(l) for l in layout.block_lines(p, src_b)] if b['type'] != 'skip' else []
            seg.append(dict(page=pi, id=b['id'], type=b['type'], lines=lines, low=0 if edited else len(low),
                            edited=edited, src=src, hint=hint, how=how, drop=bool(b.get('drop')), off=off))
    return dict(text=text, seg=seg, drops=layout.drop_report(drops))


def reocr(d, pages, blocks, pi, bid):
    """只重新辨識一個區塊：框裡原有的項目換成新辨識的。"""
    p = pages[pi]
    b = next(b for b in blocks[pi] if b['id'] == bid)
    img = Image.open(p['png']).convert('RGB')
    x0, y0, x1, y1 = (int(v) for v in b['box'])
    pad = 6
    crop = img.crop((max(0, x0 - pad), max(0, y0 - pad), x1 + pad, y1 + pad))
    ox, oy = max(0, x0 - pad), max(0, y0 - pad)
    if p.get('rot'):  # 工作座標的圖是橫躺的：轉回直立辨識，再換回工作座標
        up = crop.transpose(Image.ROTATE_270)
        with LOCK:
            new = [vertical.item_d2v(it, up.size[0]) for it in mathocr.raw_items(up)]
    else:
        with LOCK:
            new = mathocr.raw_items(crop)
    for it in new:
        bx = it['box']
        it['box'] = (bx[0] + ox, bx[1] + oy, bx[2] + ox, bx[3] + oy)
        if 'chars' in it:
            it['chars'] = [(c, a + ox, e + ox, s) for c, a, e, s in it['chars']]
    old = set(b['items'])
    for i in old:
        p['items'][i]['dead'] = True  # 留著位置，索引才不會亂
    start = len(p['items'])
    p['items'] += new
    for ob in blocks[pi]:
        ob['items'] = [i for i in ob['items'] if i not in old]
    b['items'] = list(range(start, start + len(new)))
    b.pop('text', None)  # 重新辨識了，手動改的文字作廢
    return len(new)


def api_state(pages, blocks, tags=False):
    prev = preview(pages, blocks, tags)
    pages, blocks = vertical.to_client(pages, blocks)  # 直書頁：畫面座標
    return dict(pages=[dict(w=p['w'], h=p['h'], items=p['items'], rot=p.get('rot', 0)) for p in pages], blocks=blocks,
                **prev)


def handle(d, act, req):
    """版面調整畫面的 POST：save／reocr／reset。"""
    if act == 'reset':
        ed = Path(d) / 'edits.json'
        if ed.exists():
            ed.unlink()
        pages, blocks = exam_state(d)
    else:
        pages, _ = exam_state(d)
        blocks = req['blocks']
        items = vertical.from_client(pages, blocks, req.get('items'))  # 直書頁：換回工作座標
        if req.get('items'):  # 復原：連辨識項目一起還原
            for p, it in zip(pages, items):
                p['items'] = it
        for bl in blocks:  # order 重新編成 0、1、2…
            for k, b in enumerate(sorted(bl, key=lambda b: b['order'])):
                b['order'] = k
        if act == 'reocr':
            reocr(d, pages, blocks, req['page'], req['id'])
        save_state(d, pages, blocks)
    write_text(d, pages, blocks)
    return api_state(pages, blocks, bool(req.get('tags')))
