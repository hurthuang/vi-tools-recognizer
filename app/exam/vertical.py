r"""直書頁：內部轉成「橫躺」的座標（工作座標），現有的分題、兩欄、表格邏輯照樣用；畫面上照原樣直立顯示。

直書由右到左一欄一欄讀、欄內由上到下。把頁面逆時針轉 90 度，欄就變成由上到下的行、欄內變成由左到右，
跟橫書一樣。上下分段的直書（國小國語卷常見）轉過來就是左右兩欄。

座標（rot＝1）：
  畫面 (dx, dy)、畫面寬 Wd  →  工作 (vx, vy) = (dy, Wd − dx)；工作頁寬＝畫面高、工作頁高＝畫面寬
  工作 (vx, vy)、工作頁高 Hv →  畫面 (dx, dy) = (Hv − vy, vx)
試卷資料夾：pNN.png 是直立的頁面（畫面顯示、匯出觸圖），pNNr.png 是轉過的（找附圖、表格格線用）；
pNN.json 的 w、h、items 都是工作座標，rot＝1。版面調整畫面拿到的都是畫面座標（to_client／from_client 轉換）。
"""
import copy
import re
import unicodedata

VFORM = re.compile('[︐-︙︰-﹏]')  # 直式標點（︽︾﹁﹂︵︶︒…）


def norm_text(s):
    """直式標點換回一般標點（NFKC：︽→《、﹁→「、︒→。）。"""
    return VFORM.sub(lambda m: unicodedata.normalize('NFKC', m.group(0)), s)


def box_d2v(b, Wd):
    return (b[1], Wd - b[2], b[3], Wd - b[0])


def box_v2d(b, Hv):
    return (Hv - b[3], b[0], Hv - b[1], b[2])


def pt_v2d(x, y, Hv):
    return Hv - y, x


def walls(img, min_frac=0.6):
    """工作座標的頁面圖裡，由上貫穿到下的直線（直書上下分段的分隔線，轉過來變直的）→ [[x, 上, 下], …]。
    只找頁面中間 15%～85% 的範圍（外框不算）。"""
    import numpy as np
    a = np.asarray(img.convert('L')) < 128
    H, W = a.shape
    out = []
    for x in range(int(W * 0.15), int(W * 0.85)):
        col = a[:, x].astype(np.int8)
        d = np.diff(np.concatenate(([0], col, [0])))
        st, en = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
        # 線可能有小斷點：容許 1% 高度的空隙，把相鄰的段接起來
        best = cur = 0
        last = top = None
        span = None
        for s, e in zip(st, en):
            if last is not None and s - last <= H * 0.01:
                cur += e - s + (s - last)
            else:
                cur, top = e - s, s
            last = e
            if cur > best:
                best, span = cur, (top, e)
        if best >= H * min_frac:
            if out and x - out[-1][-1][0] <= 2:
                out[-1].append((x,) + span)
            else:
                out.append([(x,) + span])
    return [[float(sum(g[0] for g in grp) / len(grp)), float(min(g[1] for g in grp)), float(max(g[2] for g in grp))]
            for grp in out]


CJK = re.compile('[㐀-鿿]')


def horizontal_runs(chars):
    """直書頁裡橫寫的字串（頁首「第三課︱人間好時節」、圖說）：照 pdfium 字序往右接、同一條基線的字
    → [[字的編號, …], …]。只有兩個數字的不算（直書裡橫寫的「16」交給 geometric_order 處理）。"""
    runs, cur = [], []
    for k in range(len(chars)):
        if cur:
            a, b = chars[cur[-1]], chars[k]
            h = max(a[4] - a[2], 1)
            ov = min(a[4], b[4]) - max(a[2], b[2])
            if ov > 0.6 * min(h, b[4] - b[2]) and 0 < (b[1] + b[3]) / 2 - (a[1] + a[3]) / 2 < 2.5 * h \
                    and b[1] - a[3] < 1.5 * h:
                cur.append(k)
                continue
            runs.append(cur)
        cur = [k]
    if cur:
        runs.append(cur)
    out = []
    for r in runs:
        t = ''.join(chars[k][0] for k in r)
        if len(r) >= 3 or (len(r) >= 2 and CJK.search(t)):
            out.append(r)
    return out


def drop_tiny(chars):
    """很小的中文字（不到一般字的一半）：Word 注音字型標「正確字」注音時留下的小字（改錯字題錯字「士」旁邊的小「土」）。"""
    hs = sorted(max(c[3] - c[1], c[4] - c[2]) for c in chars if CJK.match(c[0]))
    if len(hs) < 20:
        return chars
    h = hs[len(hs) // 2]
    return [c for c in chars if not (CJK.match(c[0]) and max(c[3] - c[1], c[4] - c[2]) < 0.5 * h)]


def drop_line_numbers(chars):
    """課本頁邊的行號（5、10，在欄頭上方）：工作座標裡是一行最前面的小數字，跟後面的字隔開一段。"""
    hs = sorted(c[4] - c[2] for c in chars if CJK.match(c[0]))
    if not hs:
        return chars
    h = hs[len(hs) // 2]
    digits = [k for k, c in enumerate(chars) if c[0].isdigit() and c[0].isascii()]
    drop = set()
    for k in digits:
        c = chars[k]
        cy = (c[2] + c[4]) / 2
        row = [o for o in chars if o is not c and abs((o[2] + o[4]) / 2 - cy) < 0.6 * h]
        left = [o for o in row if o[3] <= c[1] + 1 and c[1] - o[3] < 1.5 * h and not (o[0].isdigit() and o[0].isascii())]
        right = [o for o in row if o[1] >= c[3] - 1 and not (o[0].isdigit() and o[0].isascii())]
        if left or not right:
            continue
        gap = min(o[1] for o in right) - c[3]
        # 兩位數的行號（10）轉過來上下疊：跟另一個數字同一個 x
        if gap > 0.5 * h and max(c[3] - c[1], c[4] - c[2]) < 0.8 * h:
            drop.add(k)
    for k in list(drop):  # 疊在一起的另一個數字也拿掉
        c = chars[k]
        for j in digits:
            o = chars[j]
            if j not in drop and min(o[3], c[3]) - max(o[1], c[1]) > 0.5 * (c[3] - c[1]) and abs(o[2] - c[2]) < h:
                drop.add(j)
    return [c for k, c in enumerate(chars) if k not in drop]


def gutters(chars, W, min_gap=1.5):
    """工作座標的字裡，所有行都沒有字的直的空白帶（直書上下分段、本文與下方注釋之間）→ [[x, 上, 下], …]。
    空白帶要比一個字寬 min_gap 倍以上；頁面最左右兩邊的空白不算。"""
    if len(chars) < 20:
        return []
    hs = sorted(c[4] - c[2] for c in chars if CJK.match(c[0]))
    if not hs:
        return []
    h = hs[len(hs) // 2]
    cov = [0] * (int(W) + 2)
    for c in chars:
        for x in range(max(0, int(c[1])), min(len(cov), int(c[3]) + 1)):
            cov[x] = 1
    xs = [x for x, v in enumerate(cov) if v]
    lo, hi = min(xs), max(xs)
    out, run = [], None
    for x in range(lo, hi + 1):
        if not cov[x]:
            run = x if run is None else run
        elif run is not None:
            if x - run >= min_gap * h:
                out.append([(run + x) / 2, min(c[2] for c in chars), max(c[4] for c in chars)])
            run = None
    return out


def chars_vertical(chars):
    """文字層的字（畫面座標 (字, x0, y0, x1, y1)，照 pdfium 字序）是不是直書：
    看前後兩個字是往右接（橫書）還是往下接（直書）。"""
    nh = nv = 0
    for a, b in zip(chars, chars[1:]):
        h = max(a[4] - a[2], a[3] - a[1], 1)
        ax, ay = (a[1] + a[3]) / 2, (a[2] + a[4]) / 2
        bx, by = (b[1] + b[3]) / 2, (b[2] + b[4]) / 2
        if abs(by - ay) < 0.3 * h and 0 < bx - ax < 2 * h:
            nh += 1
        elif abs(bx - ax) < 0.3 * h and 0 < by - ay < 2 * h:
            nv += 1
    return nv > max(20, 1.5 * nh)


def items_vertical(items):
    """OCR 的文字行（畫面座標）大多是瘦長的（直的欄）→ 直書。"""
    tall = wide = 0
    for it in items:
        if it['kind'] != 'text' or len(it.get('text', '')) < 3:
            continue
        x0, y0, x1, y1 = it['box']
        if y1 - y0 > 2 * (x1 - x0):
            tall += 1
        elif x1 - x0 > 2 * (y1 - y0):
            wide += 1
    return tall > max(5, 1.5 * wide)


def item_d2v(it, Wd):
    """畫面座標的辨識項目 → 工作座標。直的行（OCR 的欄）逐字框是沿著 y 的，轉過來剛好是沿著 x。"""
    it = dict(it)
    x0, y0, x1, y1 = it['box']
    it['box'] = box_d2v(it['box'], Wd)
    if it.get('kind') == 'text':
        it['text'] = norm_text(it['text'])
        cs = it.get('chars') or []
        if it.get('vert'):
            it['chars'] = [(norm_text(c), a, b, s) for c, a, b, s in cs]
        else:  # 橫的行（例如附圖裡的字）轉過來變直的：逐字框平均分在新的寬度上
            n = max(1, len(cs))
            vx0, _, vx1, _ = it['box']
            it['chars'] = [(c, vx0 + (vx1 - vx0) * k / n, vx0 + (vx1 - vx0) * (k + 1) / n, s)
                           for k, (c, _a, _b, s) in enumerate(cs)]
    return it


def to_client(pages, blocks):
    """工作座標 → 畫面座標（版面調整畫面用）。不改原本的資料。"""
    if not any(p.get('rot') for p in pages):
        return pages, blocks
    pages2, blocks2 = [], copy.deepcopy(blocks)
    for p, bl in zip(pages, blocks2):
        if not p.get('rot'):
            pages2.append(p)
            continue
        Hv = p['h']
        items = []
        for it in p['items']:
            it = dict(it, box=box_v2d(it['box'], Hv))
            it.pop('chars', None)  # 畫面用不到逐字框
            items.append(it)
        pages2.append(dict(p, w=p['h'], h=p['w'], items=items))
        for b in bl:
            b['box'] = list(box_v2d(b['box'], Hv))
    return pages2, blocks2


def from_client(pages, blocks, items_req=None):
    """版面調整畫面送回來的區塊（與復原時的辨識項目）→ 工作座標。pages 是工作座標的頁面。"""
    for p, bl in zip(pages, blocks):
        if p.get('rot'):
            Wd = p['h']
            for b in bl:
                b['box'] = list(box_d2v(b['box'], Wd))
    if items_req:
        out = []
        for p, items in zip(pages, items_req):
            if not p.get('rot'):
                out.append(items)
                continue
            # 逐字框畫面沒有，從伺服器這邊同一個項目拿回來（復原時項目的順序、數目跟存檔時一樣）
            old = p['items']
            new = []
            for k, it in enumerate(items):
                it = dict(it, box=box_d2v(it['box'], p['h']))
                if k < len(old) and 'chars' in old[k]:
                    it['chars'] = old[k]['chars']
                new.append(it)
            out.append(new)
        return out
    return None
