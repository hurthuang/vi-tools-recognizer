r"""PDF 有文字層時，中文、題號、選項直接讀文字層（不用 OCR），算式仍用 MFD／MFR。

text_items(pdf_page, dpi, math_boxes) → (文字行, 要改回文字的算式編號)，文字行跟 mathocr.raw_items 同樣格式。
"""
import ctypes
import re

import pypdfium2.raw as raw

from . import vertical

LIGHT = 200  # 填色三色都比這亮＝浮水印、淺色字，丟掉（OCR 那邊也是把淺色漂白）
# Symbol 字型的私用區字元（U+F020～F0FF）→ 一般字元
PUA = {'': 'π', '': '≅', '': '°', '': '×', '': '÷', '': '≤', '': '≥',
       '': '≠', '': '∠', '': '⊥', '': '≈', '': '±', '': '√', '': '≡',
       '': '−', '': '+', '': '=', '': '<', '': '>', '': 'α', '': 'β',
       '': 'θ', '': '△', '': '∼', '': '→', '': '∞', '': '…'}


ZHUYIN_ONLY = re.compile(r'ZhuYin', re.I)  # 華康「ZhuYinNR」這類只有注音的字型；字＋注音的是 StdKaiZuinn，不算


def font_name(tp, i):
    buf = ctypes.create_string_buffer(256)
    n = raw.FPDFText_GetFontInfo(tp.raw, i, buf, 256, None)
    return buf.raw[:max(0, n - 1)].decode('utf-8', 'replace') if n else ''


def has_text(doc):
    """前兩頁有一定數量的字，才算有文字層（掃描檔沒有）。"""
    n = 0
    for i in range(min(2, len(doc))):
        n += len(doc[i].get_textpage().get_text_range().strip())
    return n > 100


def frame(page):
    """頁面的可見範圍與旋轉 → (左, 上, 寬, 高, 旋轉, 字要在裡面的範圍 (左, 下, 右, 上))，都是沒轉的 PDF 座標（點）。
    算圖是從可見範圍（裁切框與媒體框的交集）的左上角開始，字和圖片的座標要扣掉這個原點。"""
    r = raw.FS_RECTF()
    if raw.FPDF_GetPageBoundingBox(page.raw, r):
        x0, y0, x1, y1 = r.left, r.bottom, r.right, r.top
    else:
        w, h = page.get_size()
        if page.get_rotation() in (90, 270):
            w, h = h, w
        x0, y0, x1, y1 = 0, 0, w, h
    trim = (x0, y0, x1, y1)  # 字要在看得到的範圍裡，有裁切框（出版品）的話也要在框裡
    t = [raw.c_float() for _ in range(4)]
    if raw.FPDFPage_GetTrimBox(page.raw, *t):
        tb = [v.value for v in t]  # left, bottom, right, top
        trim = (max(x0, min(tb[0], tb[2])), max(y0, min(tb[1], tb[3])), min(x1, max(tb[0], tb[2])), min(y1, max(tb[1], tb[3])))
    return x0, y1, x1 - x0, y1 - y0, page.get_rotation(), trim


def to_display(box, W0, H0, rot):
    """沒轉、左上原點的框 (x0, y0, x1, y1) → 畫面座標（頁面設了旋轉 90／270 度的轉過去）。"""
    x0, y0, x1, y1 = box
    if rot == 90:    # 順時針轉 90 度顯示（Word 的直書頁）：(x, y) → (H0 − y, x)
        return H0 - y1, x0, H0 - y0, x1
    if rot == 270:
        return y0, W0 - x1, y1, W0 - x0
    return x0, y0, x1, y1


def chars_of(page, dpi):
    """→ [(字, x0, y0, x1, y1)]（畫面的像素座標，左上原點；頁面有設旋轉 90 度的也換成畫面座標），
    去掉自動產生的空白換行、淺色字。"""
    tp = page.get_textpage()
    s = dpi / 72
    ox, top, W0, H0, rot, trim = frame(page)
    out, zys = [], []
    r, g, b, a = raw.c_uint(), raw.c_uint(), raw.c_uint(), raw.c_uint()
    for i in range(tp.count_chars()):
        if raw.FPDFText_IsGenerated(tp.raw, i):
            continue
        ch = tp.get_text_range(i, 1)
        if not ch or ch.isspace() or ch in '￾￿':
            continue
        ch = enclosed(PUA.get(ch, ch))  # ⒈→1.、⑴→(1)
        if raw.FPDFText_GetFillColor(tp.raw, i, r, g, b, a) and min(r.value, g.value, b.value) > LIGHT:
            continue
        zy = bool(ZHUYIN_ONLY.search(font_name(tp, i)))  # 只印注音的字型：印出來是注音、文字層是國字
        l, bt, rt, t = tp.get_charbox(i, loose=True)
        tl, tb, tr, tt = tp.get_charbox(i)
        if rt - l <= 0 or t - bt <= 0:
            continue
        x0, x1 = (tl, tr) if tr > tl else (l, rt)
        if not (trim[0] <= (x0 + x1) / 2 <= trim[2] and trim[1] <= (tb + tt) / 2 <= trim[3]):
            continue  # 看不到的地方、裁切框外（出血、印刷資訊「…indd 下午 03:49」）
        bx = to_display((x0 - ox, top - t, x1 - ox, top - bt), W0, H0, rot)
        out.append((ch,) + tuple(v * s for v in bx))
        zys.append(zy)
    # 只印注音的字疊在另一個國字旁邊＝錯字旁標的正確字注音（改錯字題），不要；
    # 單獨的（寫國字題「這杯「ㄉㄨㄥ」瓜茶」）留著：輸出國字，點字照讀音轉出來就是那個注音
    def near(a, b):
        w = max(b[3] - b[1], b[4] - b[2])
        return abs((a[1] + a[3]) / 2 - (b[1] + b[3]) / 2) < 0.9 * w and abs((a[2] + a[4]) / 2 - (b[2] + b[4]) / 2) < 0.9 * w
    keep = []
    for k, c in enumerate(out):
        if zys[k] and any(not zys[j] and CJK_RE.match(o[0]) and near(c, o) for j, o in enumerate(out) if j != k):
            continue
        keep.append(c)
    return keep


CJK_RE = re.compile(r'[一-鿿]')


def text_items(page, dpi, math_boxes, pad=2, allc=None, geometric=False, walls=()):
    """→ (文字行, 要丟掉的算式編號)。算式框裡其實是中文（「圖（二）」被當成算式）時，改用文字層。
    allc：已經換成工作座標的字（直書頁）；geometric：不照 pdfium 字序，照位置排成行（直書頁的字序常常是亂的）；
    walls：分隔線 [x, 上, 下]，線經過的地方行不跨過去（直書上下分段）。"""
    if allc is None:
        allc = chars_of(page, dpi)

    def inside(c, b):
        cx, cy = (c[1] + c[3]) / 2, (c[2] + c[4]) / 2
        return b[0] - pad <= cx <= b[2] + pad and b[1] - pad <= cy <= b[3] + pad

    drop = []
    for k, b in enumerate(math_boxes):
        cs = [c for c in allc if inside(c, b)]
        if not cs or not any(CJK_RE.match(c[0]) for c in cs):
            continue
        hs = sorted(c[4] - c[2] for c in cs)
        h = hs[len(hs) // 2]
        cys = [(c[2] + c[4]) / 2 for c in cs]
        if max(cys) - min(cys) < 0.5 * h:  # 一整行（分數、上下標會有高低）
            drop.append(k)
    keep = [b for k, b in enumerate(math_boxes) if k not in drop]
    chars = [c for c in allc if not any(inside(c, b) for b in keep)]  # 算式範圍裡的文字層是亂的，用 MFR 的
    if geometric:
        chars = geometric_order(chars, walls)
    # 照閱讀順序（pdfium 的字序）串成行：同一條基線、x 往右接續；跳回左邊或換基線就斷
    lines = []
    for c in chars:
        h = c[4] - c[2]
        ln = lines[-1] if lines else None
        if ln:
            ly0, ly1 = ln['y0'], ln['y1']
            ov = min(ly1, c[4]) - max(ly0, c[2])
            last = ln['chars'][-1]
            gap = c[1] - last[3]
            forward = (c[1] + c[3]) / 2 > (last[1] + last[3]) / 2  # 字框可能重疊，看中心有沒有往右
            if geometric and not forward:  # 直書裡橫寫的數字轉過來上下疊（geometric_order 已經排好前後）
                forward = min(c[3], last[3]) - max(c[1], last[1]) > 0.5 * min(c[3] - c[1], last[3] - last[1])
            cy = (c[2] + c[4]) / 2
            cross = any(last[3] - 2 <= w[0] <= c[1] + 2 or (last[1] + last[3]) / 2 < w[0] < (c[1] + c[3]) / 2
                        for w in walls if w[1] - h <= cy <= w[2] + h)
            if ov > 0.5 * min(h, ly1 - ly0) and forward and gap < 2.5 * (max(h, ly1 - ly0) if geometric else h) and not cross:
                ln['chars'].append(c)
                ln['y0'], ln['y1'] = min(ly0, c[2]), max(ly1, c[4])
                continue
        lines.append(dict(chars=[c], y0=c[2], y1=c[4]))
    items = []
    for ln in lines:
        cs = ln['chars']
        box = (min(c[1] for c in cs), ln['y0'], max(c[3] for c in cs), ln['y1'])
        items.append(dict(box=box, text=enclosed(''.join(c[0] for c in cs)),
                          chars=[(c[0], c[1], c[3], 1.0) for c in cs], kind='text', score=1.0, iso=False,
                          src='pdf'))
    return items, drop


ENCLOSED = re.compile('[⑴-⒛]')


def enclosed(s):
    """⒈⒉（數字句點）→ 1. 2.，⑴⑵（括號數字）→ (1)(2)：國小教材常用，換掉題號、小題才認得出來。"""
    def f(m):
        o = ord(m.group(0))
        return f'({o - 0x2473})' if o <= 0x2487 else f'{o - 0x2487}.'
    return ENCLOSED.sub(f, s)


def geometric_order(chars, walls=()):
    """照位置排字序：先依分隔線分成幾欄，每欄裡同一行（中心高度相近、跟行的高度重疊一半以上）由左到右，
    行由上到下。字放進最吻合的那一行（標點在格子的一角，不一定跟第一個碰到的行最吻合）。"""
    if walls:  # 分隔線經過的高度範圍裡，線兩邊的字分開排；線沒經過的（例如標題那一行）不分
        groups = [[] for _ in range(len(walls) + 1)]
        for c in chars:
            cx, cy, h = (c[1] + c[3]) / 2, (c[2] + c[4]) / 2, c[4] - c[2]
            groups[sum(1 for w in walls if cx > w[0] and w[1] - h <= cy <= w[2] + h)].append(c)
        return [c for g in groups for c in geometric_order(g)]
    rows = []
    for c in sorted(chars, key=lambda c: (c[2] + c[4]) / 2):
        cy, h = (c[2] + c[4]) / 2, c[4] - c[2]
        best, bs = None, 0
        for r in rows[-8:]:
            ov = min(r['y1'], c[4]) - max(r['y0'], c[2])
            rh = r['y1'] - r['y0']
            if ov > 0.5 * min(h, rh) and abs(cy - r['cy']) < 0.6 * max(h, rh):
                sc = ov / max(h, 1) - abs(cy - r['cy']) / max(rh, 1)
                if best is None or sc > bs:
                    best, bs = r, sc
        if best is not None:
            best['cs'].append(c)
            best['y0'], best['y1'] = min(best['y0'], c[2]), max(best['y1'], c[4])
        else:
            rows.append(dict(cs=[c], y0=c[2], y1=c[4], cy=cy))
    out = []
    for r in sorted(rows, key=lambda r: r['y0']):
        cs = sorted(r['cs'], key=lambda c: (c[1] + c[3]) / 2)
        # 直書裡橫寫的數字（「16」兩個字並排）轉過來變上下疊：同一個 x、下面的才是前面的字
        k = 0
        while k < len(cs) - 1:
            a, b = cs[k], cs[k + 1]
            ov = min(a[3], b[3]) - max(a[1], b[1])
            if ov > 0.5 * min(a[3] - a[1], b[3] - b[1]) and (a[2] + a[4]) < (b[2] + b[4]):
                cs[k], cs[k + 1] = b, a
                k += 2
                continue
            k += 1
        out += cs
    return out


def image_regions(page, dpi, min_frac=0.003, max_frac=0.6):
    """頁面上內嵌圖片的範圍（像素座標，左上原點）。太小的（點、線）、幾乎整頁的（掃描底圖）不算。"""
    s = dpi / 72
    pw, ph = page.get_size()
    ox, top, W0, H0, rot, _trim = frame(page)
    out = []
    for o in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE], max_depth=4):
        try:
            x0, y0, x1, y1 = o.get_bounds()
        except Exception:  # noqa: BLE001
            continue
        a = (x1 - x0) * (y1 - y0) / (pw * ph)
        if min_frac <= a <= max_frac:
            out.append([x0, y0, x1, y1])
    return [[v * s for v in to_display((x0 - ox, top - y1, x1 - ox, top - y0), W0, H0, rot)] for x0, y0, x1, y1 in out]


def page_layer(page, dpi, items, min_chars=20, Wd=None, walls=()):
    """一頁的文字層資料（存成 tNN.json）；這頁沒有文字層（掃描頁）回傳 None，整頁用 OCR。

    items：這頁 OCR／MFD 的結果（mathocr.raw_items）。
    → dict(items=文字層的行, drop_math=改回文字的算式, images=內嵌圖片範圍,
           keep_ocr=圖片裡、文字層沒有字的 OCR 行（附圖裡的字）)"""
    chars = chars_of(page, dpi)
    if len(chars) < min_chars:
        return None
    mi = [k for k, it in enumerate(items) if it['kind'] == 'math']
    imgs = image_regions(page, dpi)
    hz_items = []
    if Wd is not None:  # 直書頁：字、圖片範圍換成工作座標（items 已經是工作座標）
        runs = vertical.horizontal_runs(chars)
        used = {k for r in runs for k in r}
        for r in runs:  # 橫寫的字串：自己一行，不跟直書的行混在一起
            cs = [chars[k] for k in r]
            box = vertical.box_d2v((min(c[1] for c in cs), min(c[2] for c in cs), max(c[3] for c in cs),
                                    max(c[4] for c in cs)), Wd)
            n = len(cs)
            hz_items.append(dict(box=box, text=vertical.norm_text(''.join(c[0] for c in cs)),
                                 chars=[(vertical.norm_text(c[0]), box[0] + (box[2] - box[0]) * k / n,
                                         box[0] + (box[2] - box[0]) * (k + 1) / n, 1.0) for k, c in enumerate(cs)],
                                 kind='text', score=1.0, iso=False, src='pdf'))
        chars = [(vertical.norm_text(c[0]),) + vertical.box_d2v(c[1:], Wd)
                 for k, c in enumerate(chars) if k not in used]
        imgs = [list(vertical.box_d2v(r, Wd)) for r in imgs]
        chars = vertical.drop_line_numbers(vertical.drop_tiny(chars))
        walls = sorted([list(w) for w in walls] +
                       [g for g in vertical.gutters(chars, max((c[3] for c in chars), default=0) + 1)
                        if all(abs(g[0] - w[0]) > 20 for w in walls)])
    titems, drop = text_items(page, dpi, [items[k]['box'] for k in mi], allc=chars, geometric=Wd is not None,
                              walls=walls)
    titems += hz_items

    def center_in(b, r, pad=4):
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        return r[0] - pad <= cx <= r[2] + pad and r[1] - pad <= cy <= r[3] + pad

    keep = []
    for k, it in enumerate(items):
        if it['kind'] != 'text' or not any(center_in(it['box'], r) for r in imgs):
            continue
        b = it['box']
        if any(b[0] <= (c[1] + c[3]) / 2 <= b[2] and b[1] <= (c[2] + c[4]) / 2 <= b[3] for c in chars):
            continue  # 圖上蓋了文字層的字（文字方塊）：用文字層的
        keep.append(k)
    out = dict(items=titems, drop_math=[mi[k] for k in drop], images=imgs, keep_ocr=keep)
    if Wd is not None and walls:
        out['walls'] = list(walls)
    return out
