"""文字辨識（RapidOCR，PaddleOCR 模型，離線）：PDF 或圖檔 → 依閱讀順序排好的純文字。

- PDF 頁面有文字層時（電子檔）可以直接抽文字，比 OCR 準；掃描檔才 OCR。
- 辨識結果依位置排成行（上下重疊的框算同一行，同一行由左到右），行距大的地方空一行當段落。
- 信心低的短字（多半是浮水印、譜例上的雜點）不放進正文，另列在「請對照原稿」清單。
"""
import re

import numpy as np
from PIL import Image

import prepare

DPI = 250
LOW = 0.8  # 信心低於這個值的字列入「請對照原稿」
DROP_SHORT = 0.75  # 兩個字以內又低於這個值的，當成雜訊不放進正文

_engine = None


def engine():
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR
        _engine = RapidOCR(params={'Global.log_level': 'error'}) if _accepts_params() else RapidOCR()
    return _engine


def _accepts_params():
    try:
        import inspect
        from rapidocr import RapidOCR
        return 'params' in inspect.signature(RapidOCR.__init__).parameters
    except Exception:  # noqa: BLE001
        return False


WHITEN = 200  # 比這個淺的灰（浮水印）當成白色
RETRY = 0.9  # 信心低於這個值的行，單獨切出來再認一次


def clean(img):
    """去掉淺灰色的浮水印：灰階 >= WHITEN 的點改成白色。"""
    g = np.array(img.convert('L'))
    g[g >= WHITEN] = 255
    return g


def score_bands(gray):
    """譜例所在的上下範圍：找五線譜，同一組（行距小於 6 倍線距）合成一段，上下各留 4 倍線距（放符尾、加線、表情記號）。"""
    st = prepare.staves(Image.fromarray(gray))
    if not st:
        return []
    st = sorted(set((round(y), d) for y, d in st))
    bands = []
    for y, d in st:
        top, bot = y - 4 * d, y + 4 * d + 7 * d  # 下方留多一點：踏板記號、指法
        if bands and top <= bands[-1][1] + 8 * d:
            bands[-1][1] = max(bands[-1][1], bot)
            bands[-1][0] = min(bands[-1][0], top)
        else:
            bands.append([top, bot])
    return bands


def merge_bands(bands):
    out = []
    for a, b in sorted(bands):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def ocr_image(img, skip_scores=True, extra_bands=()):
    """回傳 [(x0, y0, x1, y1, 文字, 信心)]；譜例範圍內的字不要，換成一個「［譜例］」。
    extra_bands：已知的譜例範圍（PDF 內嵌的樂譜圖），跟偵測到的五線合併。"""
    gray = clean(img)
    bands = merge_bands(list(score_bands(gray)) + [list(b) for b in extra_bands]) if skip_scores else []
    arr = np.stack([gray] * 3, axis=-1)
    r = engine()(arr, use_det=True, use_cls=True, use_rec=True)  # 參數會留在引擎上，每次都要明講
    out = []
    if r.boxes is None or r.txts is None:
        return out
    H, W = gray.shape
    for box, txt, sc in zip(r.boxes, r.txts, r.scores):
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        t = (txt or '').strip()
        sc = float(sc)
        cy = (y0 + y1) / 2
        if any(a <= cy <= b for a, b in bands):
            continue
        if sc < RETRY:
            # 整頁辨識偶爾把長行認成亂碼（例如「「：·0」）；把這一行單獨切出來再認一次
            c = gray[max(0, int(y0) - 4):min(H, int(y1) + 4), max(0, int(x0) - 4):min(W, int(x1) + 4)]
            if c.size:
                r2 = engine()(np.stack([c] * 3, axis=-1), use_det=False, use_cls=False)
                if r2.txts and r2.scores and float(r2.scores[0]) > sc:
                    t, sc = r2.txts[0].strip(), float(r2.scores[0])
        if t:
            out.append((x0, y0, x1, y1, t, sc))
    for a, b in bands:
        out.append((0, a, 1, a + 10, '［譜例］', 1.0))
    return out


def layout(items):
    """把辨識框排成文字行與段落。回傳 (正文, 請對照原稿的清單)。"""
    keep, doubt = [], []
    for it in items:
        x0, y0, x1, y1, t, sc = it
        if sc < DROP_SHORT and len(t) <= 2:
            doubt.append((t, sc, '（可能是雜訊，未放進正文）'))
            continue
        keep.append(it)
        if sc < LOW:
            doubt.append((t, sc, ''))
    if not keep:
        return '', doubt
    keep.sort(key=lambda b: ((b[1] + b[3]) / 2, b[0]))
    lines = []
    for b in keep:
        cy = (b[1] + b[3]) / 2
        h = b[3] - b[1]
        for ln in lines:
            if abs(ln['cy'] - cy) < 0.5 * min(h, ln['h']):
                ln['items'].append(b)
                n = len(ln['items'])
                ln['cy'] = (ln['cy'] * (n - 1) + cy) / n
                ln['h'] = max(ln['h'], h)
                ln['y0'] = min(ln['y0'], b[1])
                ln['y1'] = max(ln['y1'], b[3])
                break
        else:
            lines.append({'cy': cy, 'h': h, 'y0': b[1], 'y1': b[3], 'items': [b]})
    lines.sort(key=lambda l: l['cy'])
    heights = sorted(l['h'] for l in lines)
    med = heights[len(heights) // 2]
    out = []
    prev = None
    for ln in lines:
        its = sorted(ln['items'], key=lambda b: b[0])
        parts = []
        for k, b in enumerate(its):
            if k and b[0] - its[k - 1][2] > 1.5 * med:
                parts.append('　')  # 同一行裡相隔很遠（例如選項並排）
            elif k:
                a, c = parts[-1][-1:], b[4][:1]
                if a.isascii() and c.isascii() and a.isalnum() and c.isalnum():
                    parts.append(' ')
            parts.append(b[4])
        if prev is not None and ln['y0'] - prev['y1'] > 1.2 * med:
            out.append('')
        out.append(''.join(parts))
        prev = ln
    text = '\n'.join(fix_choices(out))
    return text, doubt + choice_doubts(text)


CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩'
BLACK = '❶❷❸❹❺❻❼❽❾❿'
JUNK = re.compile(r'^[0OoＯ○◯•·.。]\s*')
QNUM = re.compile(r'^\s*\d+\s*[.．、]')


def _mark(line):
    s = line.lstrip()
    if s[:1] and s[0] in CIRCLED:
        return CIRCLED.index(s[0]) + 1
    if s[:1] and s[0] in BLACK:
        return BLACK.index(s[0]) + 1
    return None


def fix_choices(lines):
    """選項的圈號（尤其黑底 ❶❷）常被認成 0、O 或漏掉：同一組選項裡認得出一部分，就依順序補齊。"""
    n = len(lines)
    marks = [_mark(l) for l in lines]
    # 整組圈號都沒認出來（例如 ❶ 全變成 O）：題目（以問號結尾）下一行以 0、O 開頭，當成第 1 個選項
    for i in range(1, n):
        if marks[i] is None and lines[i - 1].rstrip().endswith(('？', '?')) and JUNK.match(lines[i]) and not QNUM.match(lines[i]):
            marks[i] = 1
            lines[i] = CIRCLED[0] + JUNK.sub('', lines[i].lstrip(), count=1)
    done = [False] * n
    i = 0
    while i < n:
        if marks[i] is None or done[i]:
            i += 1
            continue
        # 由這個有圈號的行往前、往後找同一組（連續、沒有空行、不是題號行）
        start = i - (marks[i] - 1)
        ok = start >= 0 and all(lines[k].strip() and not QNUM.match(lines[k]) and (marks[k] in (None, k - start + 1))
                                for k in range(start, i))
        if not ok:
            start = i
        first = marks[start] if marks[start] else 1
        end = i
        k = i + 1
        while k < n and lines[k].strip() and not QNUM.match(lines[k]) and k - start + first <= 10:
            want = k - start + first
            if marks[k] is not None and marks[k] != want:
                break
            if marks[k] is None and want > 4 and not any(marks[j] is not None and j > k for j in range(k, min(n, k + 3))):
                break
            end = k
            k += 1
        # 往後只補到最後一個有圈號的行，或補滿 4 個
        last_marked = max(j for j in range(start, end + 1) if marks[j] is not None)
        stop = max(last_marked, min(end, start + 4 - first))
        known = sum(1 for j in range(start, stop + 1) if marks[j] is not None)
        if known >= 2 or (known >= 1 and stop > start):
            for j in range(start, stop + 1):
                v = j - start + first
                if marks[j] is None:
                    lines[j] = CIRCLED[v - 1] + JUNK.sub('', lines[j].lstrip(), count=1)
                elif lines[j].lstrip()[0] in BLACK:
                    s = lines[j].lstrip()
                    lines[j] = CIRCLED[v - 1] + s[1:]
                done[j] = True
        i = stop + 1
    return [_fix_inline(re.sub('^([%s])\\s+' % CIRCLED, r'\1', l)) for l in lines]


def _fix_inline(line):
    """同一行橫排的選項：開頭的 1、0、O 後面接著 ②③… 時，把開頭換成 ①。"""
    found = [CIRCLED.index(c) + 1 for c in line if c in CIRCLED]
    if found and found == list(range(2, 2 + len(found))):
        m = re.match(r'^([1lI|0OoＯ○])\s*(?=\S)', line)
        if m:
            return CIRCLED[0] + line[m.end():]
    return line


def choice_doubts(text):
    """圈號不連續（例如只認出 ②）的行，列入「請對照原稿」。"""
    out = []
    for line in text.split('\n'):
        found = [CIRCLED.index(c) + 1 for c in line if c in CIRCLED]
        if not found:
            continue
        s = line.strip()
        horizontal = len(found) >= 2 or '  ' in s or '　' in s or s[:1] not in CIRCLED
        if not horizontal:
            continue  # 直排選項的一行（開頭一個圈號）
        # 橫排：要從 ① 開始連續，而且至少 4 個
        ok = found == list(range(1, 1 + len(found))) and len(found) >= 4 and s.startswith(CIRCLED[0])
        if not ok:
            out.append((line.strip(), 0.0, '（選項圈號可能不完整，黑底圈號常認不出來）'))
    return out


def score_images(page):
    """PDF 頁面上內嵌的樂譜圖（找得到五線的）在算圖後的上下範圍（px）。"""
    import pypdfium2.raw as raw
    pw, ph = page.get_size()
    s = DPI / 72
    out = []
    for o in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE]):
        try:
            x0, y0, x1, y1 = o.get_bounds()
            if (x1 - x0) * (y1 - y0) < 0.03 * pw * ph:
                continue
            im = o.get_bitmap(render=False).to_pil()
        except Exception:  # noqa: BLE001
            continue
        if prepare.interline(im) is not None:
            out.append(((ph - y1) * s, (ph - y0) * s))
    return out


def text_layer(page):
    try:
        t = page.get_textpage().get_text_range()
    except Exception:  # noqa: BLE001
        return ''
    t = t.replace('\r\n', '\n').replace('\r', '\n')
    return '\n'.join(l.rstrip() for l in t.split('\n')).strip()


def run(path, pages_spec='', use_text_layer=False, log=print):
    """回傳 [(頁面名稱, 正文, 請對照原稿清單, 方式)]。"""
    low = str(path).lower()
    results = []
    if low.endswith('.pdf'):
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(path)
        for pi in prepare.parse_pages(pages_spec, len(pdf)):
            page = pdf[pi]
            label = '第 %d 頁' % (pi + 1)
            tl = text_layer(page) if use_text_layer else ''
            if len(tl.replace('\n', '').strip()) >= 20:
                results.append((label, tl, [], '文字層'))
                log('%s：使用 PDF 的文字層' % label)
                continue
            img = page.render(scale=DPI / 72).to_pil()
            text, doubt = layout(ocr_image(img, extra_bands=score_images(page)))
            results.append((label, text, doubt, '文字辨識'))
            log('%s：文字辨識完成（%d 字）' % (label, len(text.replace('\n', ''))))
    elif low.endswith(prepare.IMG_EXT):
        img = Image.open(path)
        img.load()
        text, doubt = layout(ocr_image(img))
        results.append(('圖', text, doubt, '文字辨識'))
        log('文字辨識完成（%d 字）' % len(text.replace('\n', '')))
    else:
        raise ValueError('只能辨識 PDF 或圖檔（png、jpg、tif…）')
    return results


def format_results(results, source):
    body = []
    doubts = []
    for label, text, doubt, how in results:
        if len(results) > 1:
            body.append('=== %s ===' % label)
        body.append(text if text else '（這一頁沒有辨識出文字）')
        body.append('')
        for t, sc, note in doubt:
            doubts.append('%s：「%s」%s%s' % (label, t, ('信心 %.2f' % sc) if sc else '', note))
    text = '\n'.join(body).rstrip() + '\n'
    hows = sorted(set(r[3] for r in results))
    report = '來源：%s\n方式：%s\n' % (source, '、'.join(hows))
    if doubts:
        report += '\n請對照原稿（信心較低的字）：%d 處\n' % len(doubts) + '\n'.join('  ' + d for d in doubts) + '\n'
    else:
        report += '\n沒有信心較低的字。\n'
    return text, report
