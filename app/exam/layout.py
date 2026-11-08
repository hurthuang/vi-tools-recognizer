r"""試卷版面：把 rawcache 存的算式與文字行分成區塊（題目、選項、附圖……），排出閱讀順序，輸出成手打的格式。

區塊類型：title 標題、section 大題、question 題目、option 選項、text 其他文字、figure 附圖、
          table 表格、score 譜例、skip 略過。
"""
import json
import re
from pathlib import Path

import cv2
import numpy as np

from . import mathocr

TYPES = ['title', 'section', 'question', 'option', 'text', 'figure', 'table', 'score', 'figtext', 'skip']

QNUM = re.compile(r'^[（(\s）)]*(\d{1,2})\s*[.．、]\s*(?![\d])')
SECTION = re.compile(r'^\s*([一二三四五六七八九十]+\s*[、．.]|第\s*[一二三四五六七八九十]+\s*部分|[壹貳參肆伍陸]\s*[、．.])')
OPTION = re.compile(r'[（(]\s*([A-EＡ-Ｅ])\s*[)）]')
SUBITEM = re.compile(r'^\s*[（(]\s*([1-9]|[一二三四五六])\s*[)）]')
NAME = re.compile(r'(\s年\s|年\s*班|座\s*號|姓\s*名|班級)')
FOOTER = re.compile(r'^[-－—\s]*\d{1,2}[-－—\s]*$|第\s*\d+\s*頁|共\s*\d+\s*頁|背面|請翻頁|試題結束|^\s*\d+\s*/\s*\d+\s*$')
TITLE = re.compile(r'學年度|段考|期[中末]|考試|測驗|模擬|評量')


# ---------- 附圖 ----------

def detect_figures(gray, items):
    """頁面上有墨跡、卻不是文字或算式的地方 → 附圖候選框。"""
    H, W = gray.shape
    ink = (gray < 160).astype(np.uint8)
    for it in items:
        x0, y0, x1, y1 = (int(v) for v in it['box'])
        ink[max(0, y0 - 4):y1 + 5, max(0, x0 - 4):x1 + 5] = 0
    k = max(9, W // 90)
    grown = cv2.dilate(ink, np.ones((k, k), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(grown, 8)
    # 被標示框切斷的線（數線上的點 D 剛好壓在線上）：左右相鄰、高度重疊的片段合併
    rects = []
    for i in range(1, n):
        x, y, w, h = (int(v) for v in st[i][:4])
        if min(w, h) - k + 1 < 6:  # 分欄直線、橫線：一條細線，不是圖
            continue
        rects.append([x, y, w, h])
    changed = True
    while changed:
        changed = False
        for a in range(len(rects)):
            for b in range(a + 1, len(rects)):
                ra, rb = rects[a], rects[b]
                gap = max(rb[0] - (ra[0] + ra[2]), ra[0] - (rb[0] + rb[2]))
                vo = min(ra[1] + ra[3], rb[1] + rb[3]) - max(ra[1], rb[1])
                flat = ra[3] <= 3 * k and rb[3] <= 3 * k  # 只接矮的橫向片段（數線）
                if flat and gap <= 2 * k and vo >= 0.5 * min(ra[3], rb[3]):
                    x0, y0 = min(ra[0], rb[0]), min(ra[1], rb[1])
                    x1, y1 = max(ra[0] + ra[2], rb[0] + rb[2]), max(ra[1] + ra[3], rb[1] + rb[3])
                    rects[a] = [x0, y0, x1 - x0, y1 - y0]
                    del rects[b]
                    changed = True
                    break
            if changed:
                break
    figs, frames = [], []
    for x, y, w, h in rects:
        rw, rh = w - k + 1, h - k + 1  # 膨脹前的大小
        big = min(rw, rh) >= W * 0.03 and rw * rh >= (W * 0.06) ** 2
        longish = max(rw, rh) >= W * 0.2 and min(rw, rh) >= 8  # 數線、座標軸（有刻度，不是一條細線）
        if not (big or longish):
            continue
        cnt = int(ink[y:y + h, x:x + w].sum())
        if cnt < 300:
            continue
        # 框線（題組說明、作答區外面的框）：墨跡幾乎都在邊上，裡面是空的
        m = k + 10
        if rw > 3 * m and rh > 3 * m:
            inner = int(ink[y + m:y + h - m, x + m:x + w - m].sum())
            if inner < 0.12 * cnt:
                frames.append([float(x + k // 2), float(y + k // 2), float(x + w - k // 2), float(y + h - k // 2)])
                continue
        figs.append([float(x + k // 2), float(y + k // 2), float(x + w - k // 2), float(y + h - k // 2)])
    return figs, frames


def inside(box, area, pad=0):
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return area[0] - pad <= cx <= area[2] + pad and area[1] - pad <= cy <= area[3] + pad


CAPTION = re.compile(r'^[（(]?\s*(圖|表)\s*[（(]?\s*[0-9一二三四五六七八九十]*\s*[)）]?$')


def overlap(box, area):
    """box 有多少比例落在 area 裡。"""
    w = max(0.0, min(box[2], area[2]) - max(box[0], area[0]))
    h = max(0.0, min(box[3], area[3]) - max(box[1], area[1]))
    a = max(1.0, (box[2] - box[0]) * (box[3] - box[1]))
    return w * h / a


def label_line(it):
    """圖旁邊一整行的標示（A B C 20 d e E、座標軸刻度）：中文字很少。"""
    if it['kind'] == 'math':
        return len(bare(it['tex'])) <= 8
    t = re.sub(r'圖|表', '', it['text'])
    return len(re.findall(r'[一-鿿]', t)) <= 6 and not QNUM.match(t) and not OPTION.match(t.strip())


def in_sentence(it, items):
    """這個項目緊貼著一段中文句子（同一行、左右相鄰）→ 是句子的一部分，不是圖上的標示。"""
    if it['kind'] == 'text' and len(re.findall(r'[一-鿿]', it['text'])) >= 4:
        return True
    y0, y1 = it['box'][1], it['box'][3]
    cy = (y0 + y1) / 2
    gap = 1.5 * (y1 - y0)
    for o in items:
        if o is it or o['kind'] != 'text':
            continue
        if len(re.findall(r'[一-鿿]', o['text'])) < 4 and not OPTION.search(o['text']):
            continue
        if o['box'][1] <= cy <= o['box'][3] and \
                (0 <= it['box'][0] - o['box'][2] < gap or 0 <= o['box'][0] - it['box'][2] < gap):
            return True
    return False


def is_label(it):
    """圖上的標示（A、B、60°、x……）：很短的字或算式。"""
    if it['kind'] == 'math':
        return len(bare(it['tex'])) <= 5
    return len(it['text'].strip()) <= 3


def bare(tex):
    """算式拿掉指令與括號後剩下的字（\\frac{D}{\\bullet} → D）。"""
    return re.sub(r'\\[a-zA-Z]+|[{}^_\s]', '', tex)


# ---------- 行 ----------

def group_lines(items):
    """分行：跟一行的「基準帶」垂直重疊一半以上才算同一行（高的算式不會把上下兩行黏在一起）。"""
    items = sorted(items, key=lambda it: (it['box'][1] + it['box'][3]) / 2)
    texts = [it['box'][3] - it['box'][1] for it in items if it['kind'] == 'text']
    th = float(np.median(texts)) if texts else 40.0
    lines = []
    for it in items:
        y0, y1 = it['box'][1], it['box'][3]
        best = None
        for ln in lines:
            if it['iso'] or ln['iso']:
                continue
            ov = min(y1, ln['b1']) - max(y0, ln['b0'])
            need = 0.5 * min(y1 - y0, ln['b1'] - ln['b0'], th * 1.2)
            if ov >= need and (best is None or ov > best[0]):
                best = (ov, ln)
        if best:
            ln = best[1]
            ln['items'].append(it)
            ln['y0'] = min(ln['y0'], y0)
            ln['y1'] = max(ln['y1'], y1)
            if it['kind'] == 'text' and ln['b1'] - ln['b0'] > th * 1.5:
                ln['b0'], ln['b1'] = y0, y1  # 基準帶改用文字的高度
        else:
            lines.append(dict(y0=y0, y1=y1, b0=y0, b1=y1, items=[it], iso=it['iso']))
    out = []
    for ln in sorted(lines, key=lambda l: l['y0']):
        hs = [it['box'][3] - it['box'][1] for it in ln['items'] if it['kind'] == 'text']
        h = float(np.median(hs)) if hs else ln['y1'] - ln['y0']
        text = mathocr.render_line(mathocr.tokens_of([dict(it) for it in ln['items']]), h)
        box = [min(it['box'][0] for it in ln['items']), ln['y0'], max(it['box'][2] for it in ln['items']), ln['y1']]
        out.append(dict(box=box, items=ln['items'], text=text, iso=ln['iso']))
    return out


DECOR = re.compile(r'(?:[(（]?\s*[●○◎⊗♣♠♥♦■□◆◇★☆▲△▼▽◢◣◤◥※]\s*[)）]?\s*){3,}')


def classify(text, page_no, y, H):
    t = DECOR.sub('', text).strip()
    if not t or not re.search(r'[\w一-鿿]', t):
        return 'skip'
    if FOOTER.search(t) and len(t) < 30:
        return 'skip'
    if CAPTION.match(t):
        return 'skip'  # 單獨的圖說「圖(一)」：附圖另外標
    if NAME.search(t) and len(t) < 40 and not QNUM.match(t):
        return 'skip'
    if page_no == 1 and y < H * 0.18 and TITLE.search(t) and not QNUM.match(t) and not SECTION.match(t):
        return 'title'
    if SECTION.match(t):
        return 'section'
    if QNUM.match(t):
        return 'question'
    if OPTION.match(t.lstrip()):
        return 'option'
    return 'text'


# ---------- 區塊 ----------

def split_column(items, W, H):
    """兩欄：中間要有一條夠寬（頁寬 2.5%）的空白直帶，兩邊都要有內容而且上下分布夠長。找不到回傳 None。"""
    if len(items) < 8:
        return None
    band = W * 0.006
    best = None
    for x in range(int(W * 0.3), int(W * 0.7), max(1, W // 400)):
        cross = [it for it in items if it['box'][0] < x + band and it['box'][2] > x - band]
        left = [it for it in items if it['box'][2] <= x - band]
        right = [it for it in items if it['box'][0] >= x + band]
        if len(left) < 6 or len(right) < 6:
            continue
        span = lambda its: max(i['box'][3] for i in its) - min(i['box'][1] for i in its)
        if span(left) < H * 0.3 or span(right) < H * 0.3:
            continue
        if best is None or len(cross) < best[0]:
            best = (len(cross), x)
    if best and best[0] <= max(3, len(items) * 0.04):
        return best[1]
    return None


def union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def auto_blocks(page, gray, page_no):
    """自動分區塊。回傳 [{id, type, box, items:[項目索引], order}]，order 是閱讀順序。"""
    items = page['items']
    W, H = page['w'], page['h']
    for i, it in enumerate(items):
        it['i'] = i
    # PDF 內嵌的圖片直接當附圖（夠大的才算；小的多半是圖示、算式圖）
    imgfigs = [r for r in page.get('images', []) if min(r[2] - r[0], r[3] - r[1]) >= W * 0.03 and
               (r[2] - r[0]) * (r[3] - r[1]) >= (W * 0.06) ** 2]
    if imgfigs:
        gray = gray.copy()
        for r in imgfigs:
            gray[max(0, int(r[1])):int(r[3]) + 1, max(0, int(r[0])):int(r[2]) + 1] = 255
    figs, frames = detect_figures(gray, items)
    if page.get('rot'):  # 直書（國語卷）：國字外面畫的方格（寫國字、注音格）不是附圖
        def wordy(f):
            n = sum(len(re.findall(r'[一-鿿ㄅ-ㄩ]', it['text'])) for it in items
                    if it['kind'] == 'text' and it.get('src') == 'pdf' and inside(it['box'], f, 4))
            return n >= 3
        figs = [f for f in figs if not wordy(f)]
    used = set()
    blocks = []
    for fb, isimg in [(r, True) for r in imgfigs] + [(f, False) for f in figs]:
        near = [fb[0] - 80, fb[1] - 90, fb[2] + 80, fb[3] + 90]
        mine = [it['i'] for it in items if it['i'] not in used and (
                (isimg and it.get('src') == 'ocr' and inside(it['box'], fb, 4)) or  # 圖片裡辨識出的字
                (isimg and it['kind'] == 'text' and inside(it['box'], fb, 60) and label_line(it) and
                 len(it['text'].strip()) <= 10) or  # 圖片旁邊的座標軸、圖說（文字層）
                (isimg and it['kind'] == 'text' and inside(it['box'], fb, 100) and len(it['text'].strip()) <= 3
                 and not OPTION.match(it['text'].strip()) and not QNUM.match(it['text'])) or  # 地圖上的(丁)
                (not in_sentence(it, items) and
                 ((inside(it['box'], fb, 12) and is_label(it)) or
                  ((overlap(it['box'], fb) > 0.6 or overlap(it['box'], near) > 0.9) and label_line(it)))))]
        if isimg and mine:
            # 第二輪：圖片加上已收的標籤之後，外圍的直排座標軸名稱（次、數、(、人、)）也收進來；圖說從整個範圍算
            fb = union([fb] + [items[i]['box'] for i in mine])
            mine += [it['i'] for it in items if it['i'] not in used and it['i'] not in mine and it['kind'] == 'text'
                     and inside(it['box'], fb, 50) and label_line(it) and len(it['text'].strip()) <= 10
                     and not CAPTION.match(it['text'].strip())]
            fb = union([fb] + [items[i]['box'] for i in mine])
        # 圖說「圖（1）」：在圖的下方或旁邊
        caption = ''
        for it in items:
            if it['i'] in used or it['i'] in mine or it['kind'] != 'text':
                continue
            cy, fy = (it['box'][1] + it['box'][3]) / 2, (fb[1] + fb[3]) / 2
            if CAPTION.match(it['text'].strip()) and abs(cy - fy) < (fb[3] - fb[1]) / 2 + 80 and \
                    it['box'][0] - fb[2] < 160 and fb[0] - it['box'][2] < 160:
                mine.append(it['i'])
                caption = it['text'].strip()
        for i in mine:
            if not caption and items[i]['kind'] == 'text' and CAPTION.match(items[i]['text'].strip()):
                caption = items[i]['text'].strip()
        typ = 'figure'
        if page.get('png') and is_table(page['png'], fb):  # 有格線的表格：框裡的字全部收進來
            cand = mine + [it['i'] for it in items if it['i'] not in used and it['i'] not in mine and
                           inside(it['box'], fb, 2)]
            if table_like(page, dict(type='table', box=fb, items=cand)):
                typ, mine = 'table', cand
        used.update(mine)
        box = union([fb] + [items[i]['box'] for i in mine])
        blocks.append(dict(type=typ, box=box, items=mine, caption=caption))
    rest = [it for it in items if it['i'] not in used]
    # 欄（直書頁有找到分隔線、空白帶的，用最能把兩邊分開的那一條）
    x = None
    best = 0
    for w in page.get('walls') or []:
        w = w[0] if isinstance(w, (list, tuple)) else w
        n = min(sum(1 for it in rest if it['box'][2] <= w), sum(1 for it in rest if it['box'][0] >= w))
        if n >= 3 and n > best:
            x, best = w, n
    if x is None:
        x = split_column(rest, W, H)
    zone = {}
    if x is None:
        for it in rest:
            zone[it['i']] = 1
    else:
        cross = [it for it in rest if it['box'][0] < x < it['box'][2]]
        top = [it for it in cross if it['box'][3] < H * 0.25]
        cut = max((it['box'][3] for it in top), default=-1)
        for it in rest:
            if (it['box'][1] + it['box'][3]) / 2 <= cut + 5:
                zone[it['i']] = 0
            else:
                zone[it['i']] = 1 if (it['box'][0] + it['box'][2]) / 2 < x else 2
    for z in (0, 1, 2):
        cur = None
        zr = max((it['box'][2] for it in rest if zone[it['i']] == z), default=0)  # 這一欄的右緣
        for ln in group_lines([it for it in rest if zone[it['i']] == z]):
            typ = classify(ln['text'], page_no, ln['box'][1], H)
            if typ == 'question' and any(inside(ln['box'], f) for f in frames):
                typ = 'text'  # 框裡的 1. 2. 3.（注意事項、活動規則）不是題號
            idx = [it['i'] for it in ln['items']]
            if typ in ('text', 'option') and cur is not None and cur['type'] in ('question', 'option', 'text'):
                if typ == 'option' and cur['type'] == 'question':
                    pass  # 選項另成一塊
                elif typ == 'text' and cur['type'] == 'option':
                    cur['items'] += idx  # 選項換行的續行
                    cur['box'] = union([cur['box'], ln['box']])
                    continue
                elif cur['type'] == typ or (typ == 'text' and cur['type'] == 'question'):
                    cur['items'] += idx
                    cur['box'] = union([cur['box'], ln['box']])
                    continue
            lh = ln['box'][3] - ln['box'][1]
            if typ == 'text' and cur is not None and cur['type'] == 'section' and cur.get('last') \
                    and cur['last'][1] >= zr - 1.5 * lh and not END.search(cur['last'][0]) \
                    and ln['box'][1] - cur['box'][3] < 1.5 * lh and not cur.get('wrapped') \
                    and not (QNUM.match(ln['text']) or BULLET.match(ln['text']) or OPTION.match(ln['text'])):
                cur['items'] += idx  # 大題標題寫滿一行折下來（只接一行）
                cur['box'] = union([cur['box'], ln['box']])
                cur['wrapped'] = True
                continue
            if typ == 'skip' and cur is not None and cur['type'] == 'skip' and ln['box'][1] - cur['box'][3] < 40:
                cur['items'] += idx
                cur['box'] = union([cur['box'], ln['box']])
                continue
            cur = dict(type=typ, box=ln['box'], items=idx, zone=z)
            if typ == 'section':
                cur['last'] = (ln['text'], ln['box'][2])
            blocks.append(cur)
    # 附圖的欄
    for b in blocks:
        if b['type'] in ('figure', 'table'):
            cx = (b['box'][0] + b['box'][2]) / 2
            b['zone'] = 1 if x is None else (1 if cx < x else 2)
            if x is not None and b['box'][3] <= max((items[i]['box'][3] for i in zone if zone[i] == 0), default=-1):
                b['zone'] = 0
            elif x is not None and b['box'][0] < x - W * 0.05 and b['box'][2] > x + W * 0.05 and b['box'][1] < H * 0.25:
                b['zone'] = 0  # 跨兩欄的頁首（班級姓名列、橫線）
                if b['box'][3] - b['box'][1] < H * 0.04:
                    b['type'] = 'skip'
    for b in blocks:  # 頁首範圍裡的文字（例如「二年 班 號 姓名」）也排在最上面
        if x is not None and b.get('zone') == 2 and b['type'] == 'skip' and any(
                o.get('zone') == 0 and o['box'][2] - o['box'][0] > W * 0.5 and o['box'][1] - 20 <= b['box'][1] <= o['box'][3]
                for o in blocks):
            b['zone'] = 0
    blocks.sort(key=lambda b: (b['zone'], b['box'][1]))
    for k, b in enumerate(blocks):
        b['id'] = k
        b['order'] = k
        b.pop('zone', None)
        b.pop('last', None)
        b.pop('wrapped', None)
    return blocks


# ---------- 輸出 ----------

def to_paren(s):
    r"""把 $...$ 換成 \( \)。"""
    return re.sub(r'\$(.+?)\$', lambda m: '\\(' + m.group(1).strip() + '\\)', s)


def from_paren(s):
    r"""\( \) 換回 $...$（手動改的文字存成 \( \) 寫法）。"""
    return re.sub(r'\\\((.+?)\\\)', lambda m: '$' + m.group(1).strip() + '$', s)


def join_lines(texts):
    out = ''
    for t in texts:
        t = t.strip()
        if not t:
            continue
        if out and (re.match(r'[A-Za-z0-9$\\(]', t[:1]) and re.search(r'[A-Za-z0-9$)]$', out)):
            out += ' '
        out += t
    return out


BULLET = re.compile(r'^\s*(?:[●•‧■□◆◇★☆※▲△]|[①-⑳]|[（(]\s*(?:[甲乙丙丁戊己庚辛]|[1-9]|[一二三四五六])\s*[)）]'
                    r'|[甲乙丙丁戊己庚辛]\s*(?:[：:]|、(?!\s*[甲乙丙丁戊己庚辛]))|\d{1,2}\s*[.．、](?!\d))')
# ↑「甲、冬季…」是項目，「甲、乙兩人」不是
END = re.compile(r'[。？?！!：:；;]\s*$')


def paragraphs(lns, strict):
    """group_lines 的行 → 段落。上一行寫到右邊（換行折下來的）才接起來，否則是原卷本來就斷行。
    strict（題目）：旁邊有附圖時每行都短，所以短行還要句尾是標點、或上一段是項目（甲）①…才斷。"""
    if not lns:
        return []
    R = max(l['box'][2] for l in lns)
    hs = sorted(l['box'][3] - l['box'][1] for l in lns if not l['iso']) or [lns[0]['box'][3] - lns[0]['box'][1]]
    h = hs[len(hs) // 2]
    out = [[lns[0]['text']]]
    for prev, ln in zip(lns, lns[1:]):
        full = prev['box'][2] >= R - 1.5 * h
        brk = bool(BULLET.match(ln['text'])) and not re.search(r'[圖表第如附見]\s*$', prev['text'])  # 「圖」換行「(四)」
        if not brk and not full:
            brk = not strict or bool(END.search(prev['text'])) or bool(BULLET.match(out[-1][0]))
        cur = ''.join(out[-1])
        if brk and len(re.findall(r'[（(]', cur)) > len(re.findall(r'[)）]', cur)):
            brk = False  # 括號還沒關（直書的「（①開心」折到下一欄「②傷心③紅心）」）
        if brk:
            out.append([ln['text']])
        else:
            out[-1].append(ln['text'])
    return [join_lines(p) for p in out]


# ---------- 表格 ----------

_GRID = {}


def _runs(mask, off):
    """一維 True 的連續段 → 各段中心（加上 off）。"""
    out, start = [], None
    for i, v in enumerate(list(mask) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append(off + (start + i - 1) / 2)
            start = None
    return out


def table_grid(png, box):
    """表格框裡的橫線、直線位置（頁面座標）。線要有表格寬（高）的一半長，字的筆畫不會被當成線。"""
    key = (str(png), tuple(round(v) for v in box))
    if key in _GRID:
        return _GRID[key]
    g = cv2.imdecode(np.fromfile(str(png), np.uint8), cv2.IMREAD_GRAYSCALE)
    pad = 8
    x0, y0 = max(0, int(box[0]) - pad), max(0, int(box[1]) - pad)
    x1, y1 = min(g.shape[1], int(box[2]) + pad), min(g.shape[0], int(box[3]) + pad)
    ink = (g[y0:y1, x0:x1] < 160).astype(np.uint8)
    H, W = ink.shape
    hor = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((1, max(50, W // 2)), np.uint8))
    ys = _runs(hor.sum(1) > 0, y0)
    # 直線至少要 1.5 列高（字的筆畫沒那麼長）；框可能包到表格外的字，不能用框高算
    gaps = sorted(b - a for a, b in zip(ys, ys[1:]) if b - a > 15)
    vk = max(40, int(1.5 * gaps[len(gaps) // 2])) if len(gaps) >= 2 else max(50, H // 2)
    ver = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((min(vk, H), 1), np.uint8))
    xs = _runs(ver.sum(0) > 0, x0)
    raw = (len(ys), len(xs))

    def edges(v, lo, hi):  # 框邊也當成線（框沒有包到外框線、或框畫太大時，框邊外的字才不會掉）
        a, b = bool(v) and v[0] > lo + 10, bool(v) and v[-1] < hi - 10
        return ([lo] if a else []) + v + ([hi] if b else []), a, b
    ys, top, bottom = edges(ys, box[1] - pad, box[3] + pad)
    xs, _, _ = edges(xs, box[0] - pad, box[2] + pad)

    def crosses(x, a, b):
        """直線 x 有沒有穿過 a～b 這一列（合併儲存格那一列沒有）。"""
        c = int(round(x - x0))
        r0, r1 = int(a - y0) + 4, int(b - y0) - 4
        if r1 <= r0 or not 0 <= c < W:
            return True
        col = ver[max(0, r0):min(H, r1), max(0, c - 3):c + 4].any(axis=1)
        return col.size == 0 or col.mean() > 0.6
    _GRID[key] = (ys, xs, crosses, top, bottom, raw)
    return _GRID[key]


def _pieces(items):
    """文字行拆成逐字的小片（一行可能跨好幾格），算式整塊。→ [(cx, cy, 項目)]"""
    out = []
    for it in items:
        cy = (it['box'][1] + it['box'][3]) / 2
        if it['kind'] == 'math':
            out.append(((it['box'][0] + it['box'][2]) / 2, cy, it))
            continue
        for c in it['chars']:
            sub = dict(it, chars=[c], text=c[0], box=(c[1], it['box'][1], c[2], it['box'][3]))
            out.append(((c[1] + c[2]) / 2, cy, sub))
    return out


def _cell_text(pieces):
    """同一格的小片 → 文字（同一行相鄰的字先接回一個項目，再照一般的排法）。"""
    items = []
    for _, _, it in sorted(pieces, key=lambda t: (round(t[1]), t[0])):
        last = items[-1] if items else None
        if it['kind'] == 'text' and last is not None and last['kind'] == 'text' and \
                abs(last['box'][1] - it['box'][1]) < 1 and it['box'][0] >= last['box'][0]:
            last['chars'] = last['chars'] + it['chars']
            last['text'] += it['text']
            last['box'] = (last['box'][0], last['box'][1], it['box'][2], last['box'][3])
            continue
        items.append(dict(it))
    if not items:
        return ''
    t = cjk(join_lines([ln['text'] for ln in group_lines(items)]))
    return re.sub(r'\$\s*([-+]?)\s*\{?\s*(\d+(?:\.\d+)?)\s*\}?\s*\$', r'\1\2', t)


def _split_gaps(pieces):
    """沒有直線的表格：一列裡的字，間隔大於字高 1.5 倍就換格。"""
    pieces = sorted(pieces, key=lambda t: t[0])
    hs = [t[2]['box'][3] - t[2]['box'][1] for t in pieces if t[2]['kind'] == 'text'] or [30]
    h = sorted(hs)[len(hs) // 2]
    cells, cur, right = [], [], None
    for t in pieces:
        if cur and t[2]['box'][0] - right > 1.5 * h:
            cells.append(cur)
            cur, right = [], None
        cur.append(t)
        right = t[2]['box'][2] if right is None else max(right, t[2]['box'][2])
    if cur:
        cells.append(cur)
    return cells


def table_rows(page, block):
    """表格區塊 → (表格上面的字, [[(格子文字, 跨幾欄)]], 表格下面的字)（$...$ 寫法）。
    有格線照格線分，沒有直線就照字的間隔分。框畫得比表格大時，外框線外面、沒有直線穿過的那條是表格外的字。"""
    items = [page['items'][i] for i in block['items'] if not page['items'][i].get('dead')]
    if not items:
        return [], [], []
    ys, xs, crosses, top, bottom = table_grid(page['png'], block['box'])[:5] if page.get('png') else \
        ([], [], None, False, False)
    pieces = _pieces(items)
    if len(ys) < 2:  # 沒有橫線：照文字分行
        ys = []
        for ln in group_lines([dict(it) for it in items]):
            ys.append(ln['box'][1] - 1)
        ys = ys + [max(it['box'][3] for it in items) + 1]
    rows, before, after = [], [], []
    n = len(ys) - 1
    for k, (a, b) in enumerate(zip(ys, ys[1:])):
        inrow = [t for t in pieces if a <= t[1] < b]
        if not inrow:
            continue
        edge = (k == 0 and top) or (k == n - 1 and bottom)
        if edge and not any(crosses(x, a, b) for x in xs[1:-1]):
            (before if k == 0 else after).append(_cell_text(inrow))
            continue
        if len(xs) >= 2:
            bounds = [xs[0]] + [x for x in xs[1:-1] if crosses(x, a, b)] + [xs[-1]]
            cells = []
            for l, r in zip(bounds, bounds[1:]):
                span = sum(1 for x in xs[1:] if l < x <= r)  # 合併儲存格跨幾欄
                cells.append(([t for t in inrow if l <= t[0] < r], span))
        else:
            cells = [(c, 1) for c in _split_gaps(inrow)]
        rows.append([(_cell_text(c), n) for c, n in cells])
    # 框邊當外框時多出來的空欄（左右兩邊整欄都空）
    while rows and all(len(r) > 1 and not r[0][0] and r[0][1] == 1 for r in rows):
        rows = [r[1:] for r in rows]
    while rows and all(len(r) > 1 and not r[-1][0] and r[-1][1] == 1 for r in rows):
        rows = [r[:-1] for r in rows]
    return before, rows, after


def _esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def is_table(png, box):
    """框裡有表格線：至少 3 條橫線、3 條直線（兩列兩欄），而且格子不是一大格（圖表的座標格線不算）。"""
    ys, xs, _, _, _, (ny, nx) = table_grid(png, box)
    return ny >= 3 and nx >= 3


def table_like(page, block):
    """自動判斷用：真的表格大多數格子有字，而且不是只有 A、B、x 這種頂點標記（幾何圖的格線不算表格）。"""
    _, rows, _ = table_rows(page, block)
    cells = [c for r in rows for c, _ in r]
    if len(rows) < 2 or len(cells) < 4:
        return False
    full = [c for c in cells if c.strip()]
    if len(full) < 0.6 * len(cells):
        return False
    def tiny(c):  # 頂點、標籤（A、甲、∠、P N）：沒有數字、去掉算式指令後兩個字以內
        v = re.sub(r'\\[a-zA-Z]+|[\s${}()^_\\]', '', c)
        return not re.search(r'\d', v) and len(v) <= 2
    tiny = [c for c in full if tiny(c)]
    return len(tiny) < 0.5 * len(full)


def table_html(page, block):
    """→ (表格上面的字, 表格 HTML, 表格下面的字)"""
    before, rows, after = table_rows(page, block)
    if not rows:
        return before, '', after
    return before, rows_html(rows), after


def rows_html(rows):
    def td(c, n):
        return (f'<td colspan="{n}">' if n > 1 else '<td>') + _esc(c) + '</td>'
    return '<table border="1">' + ''.join(
        '<tr>' + ''.join(td(c, n) for c, n in r) + '</tr>' for r in rows) + '</table>'


# 表格的輸出方式：table 保留表格（HTML，預設）、rows 文字以列為軸、cols 文字以欄為軸、mark 只標「(請參閱附表)」、none 不輸出
TABLE_HOW = ('table', 'rows', 'cols', 'mark', 'none')


def table_how(b):
    h = b.get('how')
    return h if h in TABLE_HOW else 'table'


def _grid(rows):
    """[(文字, 跨幾欄)] → 方格（合併儲存格的字填進它跨的每一格）。"""
    w = max(sum(n for _, n in r) for r in rows)
    return [sum(([c] * n for c, n in r), []) + [''] * (w - sum(n for _, n in r)) for r in rows]


def _num(c):
    """數字（可以帶短單位：30元、185mm、25%）。"""
    return bool(re.fullmatch(r'[\s$]*[-−－+]?\s*[\d.,，．]+\s*(?:[%％℃°]|[a-zA-Z]{1,3}|[一-鿿]{1,2})?[\s$]*', c))


def _collapse_heads(g):
    """兩層的標題列（「價格」底下分「中杯」「大杯」，左上角空白）合成一列：「價格 中杯」。"""
    n = 1
    while n < len(g) - 1 and not g[n][0].strip() and \
            sum(_num(c) for c in g[n][1:] if c.strip()) < 0.5 * max(1, len([c for c in g[n][1:] if c.strip()])):
        n += 1
    if n == 1:
        return g
    head = []
    for j in range(len(g[0])):
        parts = []
        for r in g[:n]:
            t = r[j].strip()
            if t and t not in parts:
                parts.append(t)
        head.append(' '.join(parts))
    return [head] + g[n:]


def table_heads(rows):
    """自動判斷 (第一列是標題, 第一欄是標題)：第一列幾乎都不是數字；第一欄不是數字、其他欄大多是數字。"""
    g = _grid(rows)
    if len(g) < 2 or len(g[0]) < 2:
        return bool(g), False
    body = [c for r in g[1:] for c in r[1:] if c.strip()]
    first_col = [r[0] for r in g[1:] if r[0].strip()]
    hrow = sum(not _num(c) for c in g[0][1:] if c.strip()) >= 0.5 * max(1, len([c for c in g[0][1:] if c.strip()])) \
        or sum(_num(c) for c in body) >= 0.5 * max(1, len(body))
    hcol = bool(first_col) and all(not _num(c) for c in first_col) and \
        sum(_num(c) for c in body) >= 0.5 * max(1, len(body))
    return hrow, hcol


def _join_bands(g):
    """拆成好幾段的表（1～6 月一段、7～12 月一段，「月分」那列重複）接成一張寬表。"""
    head = g[0]
    starts = [i for i, r in enumerate(g) if r and r[0].strip() and r[0] == head[0]]
    if len(starts) < 2:
        return g
    bands = [g[a:b] for a, b in zip(starts, starts[1:] + [len(g)])]
    if len({len(b) for b in bands}) != 1 or any([r[0] for r in b] != [r[0] for r in bands[0]] for b in bands):
        return g
    return [sum((b[i][1:] for b in bands[1:]), list(bands[0][i])) for i in range(len(bands[0]))]


UNIT = re.compile(r'^(.*?\S)\s*[（(]\s*([^()（）\d]{1,6}?)\s*[)）]\s*$')  # 「月均溫（℃）」；「大杯(1000毫升)」不是單位


def table_text(rows, axis, hrow=None, hcol=None):
    """表格 → 文字敘述的幾行。axis：rows 一列一行（「月均溫：1月 -3.7℃，2月 …」）、cols 一欄一行（「1月：月均溫 -3.7℃，降水量 3mm」）。
    標題裡的單位「月均溫（℃）」移到數字後面；合併儲存格的標題套到它跨的每一格；重複的標題列接成一張表。"""
    if not rows:
        return []
    ah, ac = table_heads(rows)
    hrow = ah if hrow is None else hrow
    hcol = ac if hcol is None else hcol
    g = _grid(rows)
    if hrow:
        g = _collapse_heads(_join_bands(g))
    if axis == 'cols':  # 轉置：一欄變成一列，標題列、標題欄互換
        g = [list(c) for c in zip(*g)]
        hrow, hcol = hcol, hrow

    def split_unit(h):
        m = UNIT.match(h.strip())
        return (m.group(1), m.group(2)) if m else (h.strip(), '')

    heads = g[0] if hrow else [''] * len(g[0])
    out = []
    for r in (g[1:] if hrow else g):
        label = r[0].strip() if hcol else ''
        name, unit = split_unit(label)
        parts = []
        for j, c in enumerate(r):
            if (hcol and j == 0) or not c.strip():
                continue
            hn, hu = split_unit(heads[j]) if heads[j].strip() else ('', '')
            u = unit or hu
            v = c.strip() + (u if u and _num(c) else '')
            parts.append(f'{hn} {v}' if hn else v)
        if not parts:
            continue
        line = '，'.join(parts)
        out.append(f'{name}：{line}' if name else line)
    return out


def table_lines(page, block):
    """表格區塊照輸出方式 → (表格上面的字, 主體的幾行, 表格下面的字)。"""
    before, rows, after = table_rows(page, block)
    if not rows:
        return before, [], after
    cap = block.get('caption', '').strip()
    how = table_how(block)
    if how in ('rows', 'cols'):
        body = ([cap] if cap else []) + table_text(rows, how, block.get('hrow'), block.get('hcol'))
    else:
        body = [cap + rows_html(rows)]
    return before, body, after


def block_lines(page, block, text=None):
    """一個區塊 → 輸出的幾行文字。附圖另外處理。text：直接給選項文字（題幹裡拆出來的）。"""
    typ = block['type']
    if text is None and block.get('text') is not None:  # 手動改過的文字（\( \) 寫法），照用
        return [from_paren(l.strip()) for l in block['text'] if l.strip()]
    if typ == 'table' and text is None:
        before, body, after = table_lines(page, block)
        return [l for l in before + body + after if l]
    if text is None:
        items =[page['items'][i] for i in block['items'] if not page['items'][i].get('dead')]
        if not items:
            return []
        lns = group_lines([dict(it) for it in items])
        texts = [ln['text'] for ln in lns]
    else:
        lns = None
        texts = [text]
    if typ == 'option':
        s = math_options(join_lines(texts))
        parts = [p.strip() for p in re.split(r'(?=[（(]\s*[A-EＡ-Ｅ]\s*[)）])', s) if p.strip()]
        out = []
        for p in parts:
            m = OPTION.match(p)
            if m:
                letter = chr(ord(m.group(1)) - 0xFEE0) if m.group(1) >= 'Ａ' else m.group(1)
                p = f'({letter}) ' + p[m.end():].strip()
            p = re.sub(r'(?<![如下右左上])\s*圖\s*[(（]\s*[一二三四五六七八九十\d]+\s*[)）]\s*$', '', p)  # 旁邊附圖的圖說
            out.append(cjk(p))
        return out
    if typ == 'question':
        out = paragraphs(lns, True) if lns else [join_lines(texts)]
        m = QNUM.match(out[0])
        if m:
            rest = re.sub(r'^[（(]\s*[)）]\s*', '', out[0][m.end():].strip())  # 答案欄（ ）
            out[0] = f'{m.group(1)}. ' + rest
        # 小項 (1)(2) 在同一行時拆開
        res = []
        for o in out:
            res += [p.strip() for p in re.split(r'(?<=\S)\s+(?=[（(]\s*[1-9]\s*[)）]\s*\S)', o) if p.strip()]
        # 選項跟題幹擠在同一段：(A) 之後有 (B) 才拆（不一定是最後一段，後面可能還有圖上的標籤）
        for j in range(len(res) - 1, -1, -1):
            seg = math_options(res[j])
            m = re.search(r'[（(]\s*[AＡ]\s*[)）]', seg)
            if m and re.search(r'[（(]\s*[B-EＢ-Ｅ]\s*[)）]', seg[m.end():]):
                opts = block_lines(page, dict(block, type='option'), text=seg[m.start():])
                res = res[:j] + [seg[:m.start()].strip()] + res[j + 1:] + opts
                break
        return [cjk(r) for r in res if r]
    return [cjk(t) for t in (paragraphs(lns, typ != 'text') if lns else [join_lines(texts)])]


MATH_OPT = re.compile(r'\(\s*([A-E])\s*\)')


def math_options(s):
    """選項記號被包進算式裡（$… ( B ) …$）時，把算式在記號處切開。"""
    def fix(m):
        seg = m.group(1)
        if not MATH_OPT.search(seg):
            return m.group(0)
        parts = MATH_OPT.split(seg)
        out = f'${parts[0].strip()}$' if parts[0].strip() else ''
        for k in range(1, len(parts), 2):
            out += f' ({parts[k]}) '
            if parts[k + 1].strip():
                out += f'${parts[k + 1].strip()}$'
        return out.strip()
    s = re.sub(r'[（(]\s*([A-E])\s*\$\s*\)', r'(\1) $', s)  # (A $) b=8 …$
    s = re.sub(r'\$([^$]*)\$', fix, s)
    return re.sub(r'\$\s*\$', ' ', s)


CJK = r'[　-〿㐀-鿿＀-￯]'


def cjk(s):
    """中文字之間多出來的空格拿掉（算式裡的不動）；符號裝飾（(●)(●)(●)…）拿掉。"""
    s = DECOR.sub('', s)
    parts = re.split(r'(\$[^$]*\$)', s)
    for k in range(0, len(parts), 2):
        for _ in range(2):
            parts[k] = re.sub(f'(?<={CJK}) +(?={CJK})', '', parts[k])
    return ''.join(parts)


DROP_TEXT = '「此題型盲生不適合作答」'
FIG_HOW = ('mark', 'figtext', 'text', 'none')


def fig_how(b):
    """附圖的處理方式：mark 只標「(請參閱附圖)」（之後做觸圖）、figtext 標記＋圖中文字、
    text 改成文字（圖裡的字或老師改寫的，不標附圖）、none 不輸出。"""
    h = b.get('how')
    return h if h in FIG_HOW else ('figtext' if b.get('figtext') else 'mark')


POINTS = re.compile(r'每\s*(?:題|小題)\s*(\d+(?:\.\d+)?)\s*分')


def drop_report(drops):
    """[(大題標題, 每題分數, 題號, 這大題的題數)] → 刪題說明的文字（照使用者手打的寫法）。"""
    if not drops:
        return ''
    out, order = [], []
    by = {}
    for sec, pts, n, total in drops:
        if sec not in by:
            by[sec] = (pts, [], total)
            order.append(sec)
        by[sec][1].append(n)
    for sec in order:
        pts, nums, total = by[sec]
        what = '刪除全部' if total and len(nums) >= total else '刪除第' + '、'.join(nums) + '題'
        score = f'，{pts * len(nums):g}分' if pts else ''
        name = re.split(r'[：:（(]', sec)[0].strip() if sec else '刪題'
        out.append(f'{name}：{what}，共 {len(nums)} 題{score}')
    return '\n'.join(out) + '\n'


def tex_plain(tex):
    """標籤用的算式 → 一般文字（30^{\\circ} → 30°、\\frac{1}{2} → 1/2）。"""
    s = re.sub(r'\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}', r'\1/\2', tex)
    for a, b in ((r'^{\circ}', '°'), (r'^\circ', '°'), (r'\circ', '°'), (r'\prime', "'"), (r'\angle', '∠'),
                 (r'\triangle', '△'), (r'\times', '×'), (r'\div', '÷'), (r'\pm', '±'), (r'\pi', 'π'),
                 (r'\overline', ''), (r'\mathrm', ''), (r'\left', ''), (r'\right', '')):
        s = s.replace(a, b)
    s = re.sub(r'\\[a-zA-Z]+', '', s)
    return re.sub(r'[{}^_\s~]', '', s)


EMPTY_OPT = re.compile(r'^\s*[（(]\s*[A-EＡ-Ｅ]\s*[)）]\s*$')
OPT_FIG_TEXT = '(選項請參閱附圖)'


def figures_own_labels(pages, blocks_by_page):
    """選項、文字區塊裡整個落在附圖框裡的字（圖裡的「原料」「市場」）還給附圖；選項字母、題號不動。
    版面調整時拉了一個包住好幾張圖的大框，圖裡的字會被收走，選項就變成「(B) 原料原料市場…」。"""
    def within(a, b, pad):
        return b[0] - pad <= a[0] and a[2] <= b[2] + pad and b[1] - pad <= a[1] and a[3] <= b[3] + pad
    for page, blocks in zip(pages, blocks_by_page):
        for b in blocks:
            if b['type'] != 'option' or b.get('text') is not None:
                continue
            # 只處理選項框整個包住兩張以上附圖的（選項是圖、排成 2×2）；自動框的附圖有時太大，會包到真的選項內容
            figs = [f for f in blocks if f['type'] == 'figure' and within(f['box'], b['box'], 20)]
            if len(figs) < 2:
                continue
            keep = []
            for i in b['items']:
                it = page['items'][i]
                x0, y0, x1, y1 = it['box']
                t = it.get('text', '') if it['kind'] == 'text' else ''
                short = len(re.sub(r'\s', '', t)) <= 6 if it['kind'] == 'text' else len(bare(it.get('tex', ''))) <= 3
                own = None if not short or OPTION.fullmatch(t.strip()) or QNUM.match(t) else next(
                    (f for f in figs if f['box'][0] - 2 <= x0 and x1 <= f['box'][2] + 2 and
                     f['box'][1] - 2 <= y0 and y1 <= f['box'][3] + 2), None)
                if own is None:
                    keep.append(i)
                elif i not in own['items']:
                    own['items'].append(i)
            b['items'] = keep
    return blocks_by_page


def empty_options(lines):
    """選項區塊只有 (A)(B)(C)(D) 沒有內容＝選項是圖（使用者手打版寫「(選項請參閱附圖)」）。"""
    return len(lines) >= 2 and all(EMPTY_OPT.match(l) for l in lines)


def image_options(pages, blocks_by_page):
    """→ {(頁, 題目區塊 id): [選項區塊]}：選項是圖的題目。"""
    out, q = {}, None
    for pi, (page, blocks) in enumerate(zip(pages, blocks_by_page)):
        for b in sorted(blocks, key=lambda b: b['order']):
            if b['type'] == 'question' and not b.get('cont'):
                q = (pi, b['id'])
            elif b['type'] in ('title', 'section'):
                q = None
            elif b['type'] == 'option' and q is not None and b.get('text') is None and \
                    empty_options(block_lines(page, b)):
                out.setdefault(q, []).append((pi, b))
    return out


def is_option_figure(pi, fig, opts):
    """附圖是選項的圖：同一頁、跟選項字母的範圍重疊或離不到 150 px，而且不是整個在選項上面（題幹的圖在選項上面）。"""
    def near(a, b, pad):
        return a[0] < b[2] + pad and b[0] < a[2] + pad and a[1] < b[3] + pad and b[1] < a[3] + pad
    return any(p == pi and fig['box'][3] > ob['box'][1] and near(fig['box'], ob['box'], 150) for p, ob in opts or [])


def _fig_labels(page, b):
    """附圖框裡的字 → [(文字, cx, cy)]（算式轉成一般文字，16 字以上、圖說不放）。"""
    labels = []
    for i in b['items']:
        it = page['items'][i]
        if it.get('dead'):
            continue
        t = tex_plain(it.get('tex', '')) if it['kind'] == 'math' else it['text'].strip()
        t = re.sub(r'^[（(](.*)[）)]$', r'\1', t)
        if not t or len(t) > 16 or CAPTION.match(t):
            continue
        x0, y0, x1, y1 = it['box']
        labels.append((t, (x0 + x1) / 2, (y0 + y1) / 2))
    return labels


def tactile_figures(pages, blocks_by_page):
    """要做觸圖的附圖（輸出方式是「標記」或「標記＋圖中文字」，刪掉的題目不算）
    → [dict(page, box, title, labels=[(文字, cx, cy)])]，座標是頁面圖檔的像素。
    選項是圖的，同一題的選項圖合成一頁「第N題 選項」，選項的 (A)(B)(C)(D) 也當標籤。"""
    out = []
    imgopt = image_options(pages, blocks_by_page)
    optpage = {}  # 題目 key → 合成的那一頁
    qnum, qkey, dropping, n_in_q = '', None, False, 0
    for pi, (page, blocks) in enumerate(zip(pages, blocks_by_page)):
        for b in sorted(blocks, key=lambda b: b['order']):
            typ = b['type']
            if typ == 'question' and not b.get('cont'):
                ls = block_lines(page, b)
                m = QNUM.match(ls[0]) if ls else None
                qnum, qkey, dropping, n_in_q = (m.group(1) if m else ''), (pi, b['id']), bool(b.get('drop')), 0
                continue
            if typ in ('title', 'section'):
                qnum, qkey, dropping, n_in_q = '', None, False, 0
                continue
            if typ != 'figure' or dropping or fig_how(b) not in ('mark', 'figtext'):
                continue
            if is_option_figure(pi, b, imgopt.get(qkey)):
                f = optpage.get(qkey)
                if f is None or f['page'] != pi:
                    f = optpage[qkey] = dict(page=pi, box=[int(v) for v in b['box']],
                                             title=(f'第{qnum}題' if qnum else '附圖') + ' 選項', labels=[])
                    out.append(f)
                f['box'] = [int(v) for v in union([f['box'], b['box']])]
                f['labels'] += _fig_labels(page, b)
                continue
            n_in_q += 1
            cap = (b.get('caption') or '').strip()
            title = (f'第{qnum}題' if qnum else '附圖') + (f' {cap}' if cap else (f' 圖{n_in_q}' if n_in_q > 1 else ''))
            out.append(dict(page=pi, box=[int(v) for v in b['box']], title=title, labels=_fig_labels(page, b)))
    # 選項字母：在合成的範圍裡（外加 80 px）的 (A)(B)(C)(D)
    for qkey, f in optpage.items():
        page = pages[f['page']]
        for p, ob in imgopt[qkey]:
            if p != f['page']:
                continue
            for i in ob['items']:
                it = page['items'][i]
                m = OPTION.match(it.get('text', '').strip()) if it['kind'] == 'text' and not it.get('dead') else None
                if m and inside(it['box'], f['box'], 80):
                    x0, y0, x1, y1 = it['box']
                    f['labels'].append((m.group(1).translate(str.maketrans('ＡＢＣＤＥ', 'ABCDE')), (x0 + x1) / 2, (y0 + y1) / 2))
                    f['box'] = [int(v) for v in union([f['box'], it['box']])]
    return out


def render_nodes(pages, blocks_by_page, drops=None):
    """整份 → 節點 [文字, 標籤, 資訊]。純文字（render）與中繼檔（vtdoc.build）都從這裡來，內容才會一致。
    文字是 None 的是附圖、表格的位置（純文字不輸出）。資訊 dict：
      kind  title／section／q／qline／opt／optfig／text／drop／figtext／figdesc／table／tbltext／mark
      pi、blk（來源頁與區塊 id）、num（題號）、lines（刪掉的題目原文）、how（附圖、表格的輸出方式）
    drops：給一個 list，會填入刪掉的題目（刪題說明用）。"""
    out = []  # [文字, 標籤, 資訊]
    dropped = set()  # 刪掉的題目 key
    dropping = False  # 刪掉的題目後面的選項、文字也不要
    sec_title, sec_pts, sec_q = '', None, []  # 目前的大題、每題分數、題號
    sec_drops = []
    qpos = {}  # (頁, 區塊 id) → 題幹在 out 的位置
    qtext = []  # [(頁, 區塊 id, 正規化題幹)]
    figs = []  # [(頁, 區塊, 前一題的 key)]
    last_q = None
    imgopt = image_options(pages, blocks_by_page)  # 選項是圖的題目

    def node(t, tag='', kind='text', pi=None, b=None, **kw):
        info = dict(kind=kind, pi=pi, blk=None if b is None else b['id'], **kw)
        return [t, tag, info]

    def blank():
        if out and out[-1][0] != '':
            out.append(['', '', dict(kind='blank')])

    for pi, (page, blocks) in enumerate(zip(pages, blocks_by_page)):
        for b in sorted(blocks, key=lambda b: b['order']):
            typ = b['type']
            if typ == 'skip':
                continue
            if typ in ('figure', 'table', 'score', 'figtext'):
                figs.append((pi, b, last_q))
                continue
            if dropping and typ not in ('title', 'section', 'question'):
                continue
            lines = [to_paren(l) for l in block_lines(page, b)]
            if not lines:
                continue
            if b.get('cont') and out and out[-1][0]:  # 跨頁接續：第一行接在上一行後面
                out[-1][0] = join_lines([out[-1][0], lines[0]])
                kind = out[-1][2]['kind']
                out += [node(l, '', 'qline' if kind in ('q', 'qline') else kind, pi, b) for l in lines[1:]]
                continue
            dropping = False
            if typ in ('title', 'section'):
                blank()
                hi = len(out)
                out.append(node(lines[0], 'h1' if typ == 'title' else 'h2', typ, pi, b))
                for l in lines[1:]:  # 標題框裡還有別的行（手動合併、說明文字）：照一般文字輸出，不要丟掉
                    out.append(node(l, '', 'text', pi, b))
                blank()
                if typ == 'section':
                    sec_drops.append((sec_title, sec_pts, sec_q))
                    m = POINTS.search(' '.join(lines))
                    sec_title, sec_pts, sec_q = lines[0], float(m.group(1)) if m else None, []
                    out[hi][2]['points'] = sec_pts
            elif typ == 'question':
                blank()
                last_q = (pi, b['id'])
                qpos[last_q] = len(out)
                m = QNUM.match(lines[0])
                num = m.group(1) if m else ''
                sec_q.append([num, False])
                if b.get('drop'):  # 刪題
                    dropped.add(last_q)
                    dropping = True
                    sec_q[-1][1] = True
                    out.append(node(f'{num}. {DROP_TEXT}' if num else DROP_TEXT, 'h3', 'drop', pi, b, num=num,
                                    lines=lines))
                    continue
                qtext.append((last_q, norm_ref(' '.join(lines))))
                out.append(node(lines[0], 'h3', 'q', pi, b, num=num))
                out += [node(l, '', 'qline', pi, b) for l in lines[1:]]
            elif typ == 'option' and b.get('text') is None and empty_options(lines):
                if not (out and out[-1][0] == OPT_FIG_TEXT):  # 選項是圖：一行「(選項請參閱附圖)」
                    out.append(node(OPT_FIG_TEXT, '', 'optfig', pi, b))
            elif typ == 'option':
                out += [node(l, '', 'opt', pi, b) for l in lines]
            else:
                out += [node(l, '', 'text', pi, b) for l in lines]
    sec_drops.append((sec_title, sec_pts, sec_q))
    if drops is not None:
        for title, pts, qs in sec_drops:
            for num, d in qs:
                if d:
                    drops.append((title, pts, num, len(qs)))
    # 附圖：圖說「圖（2）」對得到題幹裡的「圖（2）」就放那一題，否則放前一題
    inserts = []  # (位置, 節點)

    def put_top(nodes):
        nonlocal qpos
        out[0:0] = nodes
        qpos = {k: v + len(nodes) for k, v in qpos.items()}

    for pi, b, prev in figs:
        if prev in dropped:  # 刪掉的題目的附圖
            continue
        fig = node(None, '', 'fig' if b['type'] != 'table' else 'table', pi, b)  # 附圖、表格本身（純文字不輸出）
        if b['type'] == 'figtext':  # 圖中文字（對話框）：放在前一題題幹後面
            t = ' '.join(to_paren(l) for l in block_lines(pages[pi], dict(b, type='text')))
            if not t:
                continue
            t = f'{b["speaker"]}說：{t}' if b.get('speaker') else f'圖中文字：{t}'
            if prev is None:
                put_top([node(t, '', 'figtext', pi, b)])
            else:
                inserts.append((qpos[prev] + 1, node(t, '', 'figtext', pi, b)))
            continue
        mark = {'figure': '(請參閱附圖)', 'table': '(請參閱附表)', 'score': '［譜例］'}[b['type']]
        target = prev
        cap = norm_ref(b.get('caption', ''))
        if re.search(r'\d', cap):
            for key, t in qtext:
                if cap in t:
                    target = key
                    break
        how = fig_how(b) if b['type'] in ('figure', 'score') else table_how(b) if b['type'] == 'table' else 'mark'
        fig[2]['how'] = how
        if target in dropped:
            continue
        if how == 'none':
            if target is None:
                put_top([fig])
            else:
                inserts.append((qpos[target] + 1, fig))
            continue
        if how == 'text':  # 改成文字：放在題幹後面，不標附圖
            tl = [node(to_paren(l), '', 'figdesc', pi, b) for l in block_lines(pages[pi], dict(b, type='text'))]
            if target is None:
                put_top([fig] + tl)
            else:
                inserts += [(qpos[target] + 1, n) for n in reversed([fig] + tl)]
            continue
        if b['type'] == 'figure' and how == 'mark' and is_option_figure(pi, b, imgopt.get(prev)):
            fig[2]['role'] = 'options'  # 選項的圖：已經有「(選項請參閱附圖)」，題幹不再標
            if prev is not None:
                inserts.append((qpos[prev] + 1, fig))
            continue
        figtext = [to_paren(l) for l in block_lines(pages[pi], dict(b, type='text'))] if how == 'figtext' else []
        if b['type'] == 'table':
            if b.get('text') is not None:  # 手動改過
                before, tl, after = [], [to_paren(l) for l in block_lines(pages[pi], b)], []
                fig[2]['manual'] = True
            else:
                before, tl, after = table_lines(pages[pi], b)
                tl = [to_paren(l) for l in tl]
                before, after = [to_paren(l) for l in before if l], [to_paren(l) for l in after if l]
            if target is not None and before:  # 框到表格上面的字＝題幹的最後一行，接回題幹
                k = qpos[target]
                out[k][0] = join_lines([out[k][0]] + before)
                before = []
            bef = [node(l, '', 'text', pi, b) for l in before]
            aft = [node(l, '', 'text', pi, b) for l in after]
            if how == 'mark':  # 只標「(請參閱附表)」：表格本身不輸出，表格下面的字照放
                if target is not None and mark not in out[qpos[target]][0]:
                    out[qpos[target]][0] += mark
                nodes = ([node(mark, '', 'mark', pi, b)] if target is None else []) + bef + [fig] + aft
            else:
                nodes = bef + [fig] + [node(l, '', 'tbltext', pi, b) for l in tl] + aft
            if any(n[0] is not None for n in nodes):
                if target is None:
                    put_top(nodes)
                else:
                    inserts += [(qpos[target] + 1, n) for n in reversed(nodes)]
                continue
            # 表格沒有內容：跟一般附圖一樣標在題幹（下面），表格的位置照記
        if target is None:
            put_top([node(mark, '', 'mark', pi, b), fig])
            continue
        k = qpos[target]
        if mark not in out[k][0]:
            out[k][0] += mark
        if figtext:
            inserts.append((k + 1, node('圖中文字：' + ' '.join(figtext), '', 'figtext', pi, b)))
        inserts.append((k + 1, fig))
    for k, n in sorted(inserts, key=lambda x: x[0], reverse=True):  # 同一個位置照加入的倒序插（多行的表格才不會亂）
        out.insert(k, n)
    return out


def render(pages, blocks_by_page, tags=False, drops=None):
    """整份 → 手打格式的文字。drops：給一個 list，會填入刪掉的題目（刪題說明用）。"""
    nodes = render_nodes(pages, blocks_by_page, drops)
    res = [f'<{tag}>{t}</{tag}>' if tags and tag else t for t, tag, _info in nodes if t is not None]
    return '\n'.join(res).strip() + '\n'


def norm_ref(s):
    """「如下圖（1）」「圖(1)」→ 比對用的「圖1」。"""
    return re.sub(r'[\s()（）]', '', s)


def load(d, use_text_layer=True):
    """d：試卷資料夾（pNN.json／png，有文字層的還有 tNN.json）。"""
    pages, grays = [], []
    for js in sorted(d.glob('p*.json')):
        p = json.loads(js.read_text(encoding='utf-8'))
        for it in p['items']:
            if it['kind'] == 'math' and it.get('raw') is not None:
                it['tex'] = mathocr.tidy(it['raw'])  # tidy 改了不用重跑模型
        tl = js.with_name('t' + js.name[1:])  # PDF 文字層（textlayer.py）：取代 OCR 的文字行
        if tl.exists() and use_text_layer:
            t = json.loads(tl.read_text(encoding='utf-8'))
            drop = set(t['drop_math'])  # 算式框裡其實是中文的，改用文字層
            keep = set(t.get('keep_ocr', []))  # 附圖（內嵌圖片）裡的字：文字層沒有，用 OCR 的
            ocr_in_img = [dict(it, src='ocr') for k, it in enumerate(p['items']) if k in keep]
            p['items'] = [it for k, it in enumerate(p['items']) if it['kind'] == 'math' and k not in drop] +                 t['items'] + ocr_in_img
            p['images'] = t.get('images', [])
            if t.get('walls'):
                p['walls'] = t['walls']  # 直書頁：分隔線＋空白帶
        p['png'] = str(js.with_suffix('.png'))  # 表格找格線用
        if p.get('rot'):  # 直書頁：工作座標的圖（橫躺）；直立的留給觸圖
            p['png_d'] = p['png']
            p['png'] = str(js.with_name(js.stem + 'r.png'))
        pages.append(p)
        grays.append(Path(p['png']))
    return pages, grays


def auto_all(d):
    pages, pngs = load(d)
    blocks = []
    for n, (p, png) in enumerate(zip(pages, pngs), 1):
        gray = cv2.imdecode(np.fromfile(str(png), np.uint8), cv2.IMREAD_GRAYSCALE)
        blocks.append(auto_blocks(p, gray, n))
    repeated_to_skip(pages, blocks)
    page_continuations(pages, blocks)
    figures_own_labels(pages, blocks)
    return pages, blocks


def page_continuations(pages, blocks):
    """跨頁接續：一頁的第一塊是文字（不是題號、大題、選項開頭），上一頁最後一塊是題目或選項
    → 改成上一塊的類型、標 cont，輸出時接在上一行後面。"""
    for k in range(1, len(pages)):
        prev = [b for b in sorted(blocks[k - 1], key=lambda b: b['order']) if b['type'] != 'skip']
        cur = [b for b in sorted(blocks[k], key=lambda b: b['order']) if b['type'] != 'skip']
        if not prev or not cur or prev[-1]['type'] not in ('question', 'option') or cur[0]['type'] != 'text':
            continue
        t = ' '.join(block_lines(pages[k], cur[0])).strip()
        if t and not (QNUM.match(t) or SECTION.match(t) or OPTION.match(t) or BULLET.match(t)):
            cur[0]['type'] = prev[-1]['type']
            cur[0]['cont'] = True


def repeated_to_skip(pages, blocks):
    """每頁同樣位置都出現的短文字（頁首、頁尾、浮水印）→ 略過。頁面最下緣的短文字也略過。"""
    if len(pages) < 3:
        return
    keyed = []
    for pi, (p, bl) in enumerate(zip(pages, blocks)):
        for b in bl:
            if b['type'] not in ('text', 'title', 'section'):
                continue
            t = ' '.join(block_lines(p, b))
            if len(t) > 50 or QNUM.match(t):
                continue
            k = re.sub(r'\d+', '#', re.sub(r'\s', '', t))
            keyed.append((pi, b, k, (b['box'][1] + b['box'][3]) / 2 / p['h']))
    for pi, b, k, y in keyed:
        n = len({pj for pj, _, k2, y2 in keyed if k2 == k and abs(y2 - y) < 0.05})
        if n >= max(3, len(pages) // 2):
            b['type'] = 'skip'
    for p, bl in zip(pages, blocks):
        for b in bl:
            if b['type'] == 'text' and b['box'][1] > p['h'] * 0.95:
                b['type'] = 'skip'
