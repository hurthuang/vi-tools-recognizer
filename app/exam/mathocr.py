r"""試卷的算式與文字辨識：MFD 找算式、MFR 算式轉 LaTeX、RapidOCR 認中文，排成一行行的文字（算式用 $...$）。"""
import re

import numpy as np
from PIL import Image

DPI = 200
WHITEN = 200

_p2t = None
_ocr = None


def models():
    """(算式模型, RapidOCR)。算式模型有 mfd(img, resized_shape) 與 latex_ocr.recognize(crops)。"""
    global _p2t, _ocr
    if _p2t is None:
        from . import ort_mfd, ort_mfr

        class Ort:
            class latex_ocr:  # noqa: N801
                recognize = staticmethod(ort_mfr.recognize)

            mfd = staticmethod(ort_mfd.detect)

        import ocr  # 辨識套件文字辨識的 RapidOCR（同一個引擎）
        _p2t, _ocr = Ort, ocr.engine()
    return _p2t, _ocr


SPACES = re.compile(r'\\[!,;:> ]|~|\\q?quad')


WRAP = re.compile(r'\\(?:mathrm|mathbf|boldsymbol|textbf|bm|mathit|mathsf|underline|mathring|mathtt|mathcal|operatorname|texttt|textsc|textrm|textit|textsf)\s*\{')


def unwrap(t):
    """\\mathbf{...}、\\boldsymbol{...} 這類只改字型的包裝拿掉（括號可巢狀）。"""
    while True:
        m = WRAP.search(t)
        if not m:
            return t
        i, d = m.end(), 1
        while i < len(t) and d:
            d += {'{': 1, '}': -1}.get(t[i], 0)
            i += 1
        t = t[:m.start()] + t[m.end():i - 1] + t[i:]


def tidy(tex):
    """把 MFR 的 LaTeX 整理成人手打的樣子。"""
    tex = unwrap(tex)
    # 只有一列、沒有換行的 array／matrix 是框框誤判，拿掉外殼
    tex = re.sub(r'\\begin\{(array|matrix)\}\s*(?:\{[lcr ]*\}|[lcr]\b)?([^\\]*?)\\end\{\1\}', r'\2', tex)
    tex = re.sub(r'\\(?:mathsf|mathring)\s*(?=\w)', '', tex)
    tex = tex.replace('℃', '^{\\circ} C').replace('•C', '^{\\circ} C')
    t = tex.replace('\\\\', '\x00').replace('\\left\\{', '\\{').replace('\\right\\}', '\\}')
    t = SPACES.sub(' ', t)
    t = re.sub(r'\\(?:bf|mathbf|boldsymbol|bm)\s*(?=[\w])', '', t)
    t = re.sub(r'\{\\bf\s*([^{}]*)\}', r'\1', t)
    t = re.sub(r'(?<![\w}^_])\{(\w+)\}', r'\1', t)
    t = re.sub(r'\\left\s*([(\[|.])', r'\1', t)
    t = re.sub(r'\\right\s*([)\]|.])', r'\1', t)
    t = t.replace('\\left.', '').replace('\\right.', '')
    t = t.replace('\\cfrac', '\\frac').replace('\\dfrac', '\\frac')
    for _ in range(4):
        t = re.sub(r'\\(?:mathrm|mathbf|boldsymbol|textbf|bm|mathit|it)\s*\{([^{}]*)\}', r'\1', t)
        t = re.sub(r'\{\{([^{}]*)\}\}', r'{\1}', t)
    t = re.sub(r'\\(?:boldsymbol|textbf|bm|it|tt|rm|sc|sf)\b\s*', '', t)
    t = re.sub(r'\\cdot(?![a-zA-Z])', ',', t)  # 試卷裡多半是頓號、逗號（\cdots 不動）
    t = re.sub(r'/\s*/', r'\\parallel ', t).replace('\\bot', '\\perp')
    t = re.sub(r'\\(triangle|angle)(?=[A-Z])', r'\\\1 ', t)
    # 聯立方程式：\left\{\begin{matrix}…\end{matrix}\right. → cases
    t = re.sub(r'\\?\{\s*\\begin\{(?:matrix|array)\}(?:\s*\{[lcr ]*\})?(.*?)\\end\{(?:matrix|array)\}\s*\.?',
               lambda m: '\\begin{cases}' + m.group(1) + '\\end{cases}', t, flags=re.S)
    t = re.sub(r'\{([^{}\x00]*)\}(?=\s*(?:\x00|\\end\{cases\}))', r'\1', t)
    t = re.sub(r'(?<=\\begin\{cases\})\s*\{', '', t)
    t = t.replace('\x00', ' \\\\ ')
    t = re.sub(r'\s*\\\\\s*\\end\{', r' \\end{', t)
    t = re.sub(r'\s+', ' ', t).strip()
    t = re.sub(r'\s+', ' ', t).strip()
    for _ in range(4):
        t = re.sub(r'(\d) (\d)', r'\1\2', t)
        t = re.sub(r'(\d) \. (\d)', r'\1.\2', t)
    t = re.sub(r'\{\s+', '{', t)
    t = re.sub(r'\s+\}', '}', t)
    t = re.sub(r'\^\{(\w)\}', r'^\1', t)
    t = re.sub(r'_\{(\w)\}', r'_\1', t)
    return t


def clean(img):
    a = np.asarray(img.convert('L'))
    a = np.where(a >= WHITEN, 255, a).astype(np.uint8)
    return Image.fromarray(a).convert('RGB')


def box_of(pos):
    p = np.asarray(pos, dtype=float).reshape(-1, 2)
    return p[:, 0].min(), p[:, 1].min(), p[:, 0].max(), p[:, 1].max()


SUP = {'²': '^2', '³': '^3', '¹': '^1'}
MATHCH = set('()（）+-−－=＝0123456789.×÷²³<>＜＞')
GREEK_FIX = [(r'\( ?\\mathbb\{Z\} ?\)', '(乙)'), (r'\( ?\\mathbb\{T\} ?\)', '(丁)'), (r'\( ?\\mathbb\{E\} ?\)', '(甲)')]


def recognize(img):
    img = clean(img)
    return arrange(raw_items(img), img.size[0])


def raw_items(img):
    """已經 clean 過的頁面圖 → 算式與文字行（含位置、逐字框、信心分數），還沒排版。"""
    tf, ocr = models()
    mf = tf.mfd(img.copy(), resized_shape=1600)
    crops, mboxes = [], []
    for m in mf:
        x0, y0, x1, y1 = box_of(m['box'])
        crops.append(img.crop((int(x0), int(y0), int(x1), int(y1))))
        mboxes.append((x0, y0, x1, y1, m['type']))
    res = tf.latex_ocr.recognize(crops, batch_size=8) if crops else []
    items = []
    masked = np.array(img)
    for (x0, y0, x1, y1, typ), r in zip(mboxes, res):
        items.append(dict(box=(x0, y0, x1, y1), tex=tidy(r['text']), raw=r['text'], kind='math',
                          score=r['score'], iso=typ == 'isolated'))
        masked[max(0, int(y0) - 1):int(y1) + 2, max(0, int(x0) - 1):int(x1) + 2] = 255
    o = ocr(masked, use_det=True, use_cls=True, use_rec=True, return_word_box=True)
    if o.boxes is not None:
        for b, t, s, words in zip(o.boxes, o.txts, o.scores, o.word_results):
            if not t.strip():
                continue
            chars = []
            bx = box_of(b)
            vert = len(t.strip()) > 1 and bx[3] - bx[1] > 1.5 * (bx[2] - bx[0])  # 直的一欄：逐字框沿著 y
            for w, ws, wb in words:
                x0, y0, x1, y1 = box_of(wb)
                a0, a1 = (y0, y1) if vert else (x0, x1)
                n = max(1, len(w))
                for k, ch in enumerate(w):
                    chars.append((ch, a0 + (a1 - a0) * k / n, a0 + (a1 - a0) * (k + 1) / n, ws))
            it = dict(box=bx, text=t, chars=chars, kind='text', score=s, iso=False)
            if vert:
                it['vert'] = True
            items.append(it)
    for it in items:
        it['box'] = tuple(float(v) for v in it['box'])
        it['score'] = float(it['score'])
        if 'chars' in it:
            it['chars'] = [(c, float(a), float(b), float(s)) for c, a, b, s in it['chars']]
    return items


# 算式框包到的前後標點。冒號不動（比例式 a:b:c 的冒號是算式的一部分）
LEAD_PUNCT = {',': '，', '，': '，', '、': '、'}
TRAIL_PUNCT = {',': '，', '.': '。', ';': '；', '，': '，', '。': '。', '、': '、', '；': '；'}
PUNCT_OUT = set(TRAIL_PUNCT.values())
CJK_CH = re.compile(r'[一-鿿　-〿！-／：-＠]')


def split_punct(tex, before_cjk=True, after_cjk=True):
    """(前面的標點, 算式本體, 後面的標點)。前一個字是中文（或行首）才拿前面的，
    後一個字是中文（或行尾）、而且本體括號成對才拿後面的——後面接英數就是算式還沒完（a,b,c, w）。"""
    pre = post = ''
    t = tex.strip()
    if before_cjk and t[:1] in LEAD_PUNCT:
        pre = LEAD_PUNCT[t[0]]
        t = t[1:].lstrip()
    if after_cjk and t[-1:] in TRAIL_PUNCT and t[-2:-1] != '\\':
        core = t[:-1].rstrip()
        if core and balanced(core):
            post, t = TRAIL_PUNCT[t[-1]], core
    return pre, t, post


def edge_punct(toks):
    """render_line 用：算式頭尾的標點依前後文移到算式外。整串只有標點的（虛線被當成算式）拿掉。"""
    out = []
    for i, t in enumerate(toks):
        if t['kind'] != 'math':
            out.append(t)
            continue
        if re.fullmatch(r'[\s,.，。、]+', t['tex']):
            continue
        prv = out[-1] if out else None
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        b = prv is None or (prv['kind'] == 'ch' and CJK_CH.match(prv['ch']))
        a = nxt is None or (nxt['kind'] == 'ch' and CJK_CH.match(nxt['ch']))
        pre, core, post = split_punct(t['tex'], b, a)
        if pre:
            out.append(dict(kind='ch', ch=pre, x0=t['x0'] - 0.2, x1=t['x0'] - 0.1, score=t['score']))
        out.append(dict(t, tex=core))
        if post:
            out.append(dict(kind='ch', ch=post, x0=t['x1'] + 0.1, x1=t['x1'] + 0.2, score=t['score']))
    toks[:] = out


def tokens_of(parts):
    """一行裡的文字逐字、算式整塊，依 x 排好。"""
    toks = []
    for p in parts:
        if p['kind'] == 'math':
            toks.append(dict(kind='math', tex=p['tex'], x0=p['box'][0], x1=p['box'][2], score=p['score']))
        else:
            for ch, x0, x1, sc in p['chars']:
                toks.append(dict(kind='ch', ch=ch, x0=x0, x1=x1, score=sc))
    toks.sort(key=lambda t: (t['x0'] + t['x1']) / 2)
    return toks


def balanced(s):
    d = 0
    for c in s:
        if c in '(（':
            d += 1
        elif c in ')）':
            d -= 1
            if d < 0:
                return False
    return d == 0


def absorb(toks, h):
    """算式框沒包到的括號、數字收進算式（收了之後括號要成對）。"""
    i = 0
    while i < len(toks):
        t = toks[i]
        if t['kind'] != 'math':
            i += 1
            continue
        L = []
        j = i - 1
        while j >= 0 and toks[j]['kind'] == 'ch' and toks[j]['ch'] in MATHCH and len(L) < 6:
            nxt = toks[j + 1]
            if nxt['x0'] - toks[j]['x1'] > h * 0.6:
                break
            L.insert(0, j)
            j -= 1
        R = []
        j = i + 1
        while j < len(toks) and toks[j]['kind'] == 'ch' and toks[j]['ch'] in MATHCH and len(R) < 6:
            prv = toks[j - 1]
            if toks[j]['x0'] - prv['x1'] > h * 0.6:
                break
            R.append(j)
            j += 1
        core = t['tex']
        best = None
        if not balanced(core):
            for a in range(len(L) + 1):
                for b in range(len(R) + 1):
                    left = ''.join(toks[k]['ch'] for k in L[len(L) - a:])
                    right = ''.join(toks[k]['ch'] for k in R[:b])
                    if balanced(left + core + right) and (best is None or a + b < best[0]):
                        best = (a + b, a, b, left, right)
        else:
            left = ''.join(toks[k]['ch'] for k in L[-1:]) if L else ''
            right = ''.join(toks[k]['ch'] for k in R[:2]) if R else ''
            if left in '(（' and left and right[:1] in (')', '）'):
                b = 2 if right[1:2] in SUP else 1
                best = (1 + b, 1, b, left, right[:b])
        if best:
            _, a, b, left, right = best
            t['tex'] = norm_chars(left) + ' ' + core + ' ' + norm_chars(right)
            t['tex'] = re.sub(r'\s+', ' ', t['tex']).strip()
            drop = set(L[len(L) - a:]) | set(R[:b])
            toks[:] = [x for k, x in enumerate(toks) if k not in drop]
            i -= a
        i += 1


def norm_chars(s):
    s = s.replace('（', '(').replace('）', ')').replace('－', '-').replace('−', '-').replace('＝', '=')
    s = s.replace('×', '\\times ').replace('÷', '\\div ').replace('＜', '<').replace('＞', '>')
    for k, v in SUP.items():
        s = s.replace(k, v)
    return s


def render_line(toks, h):
    absorb(toks, h)
    edge_punct(toks)
    s = ''
    prev = None
    for t in toks:
        if t['kind'] == 'math':
            tex = t['tex']
            plain = re.fullmatch(r'[-0-9.]+|[A-Za-z]', tex)
            piece = tex if plain else f'${tex}$'
            for pat, rep in GREEK_FIX:
                piece = re.sub(pat, '$' + rep + '$', piece)
            piece = piece.replace('$$', '').replace('$ $', ' ')
            if s and not s.endswith(' ') and s[-1] not in PUNCT_OUT:
                s += ' '
            s += piece
            prev = t
            continue
        ch = t['ch']
        if ch in PUNCT_OUT:  # 標點緊貼前面
            s = s.rstrip(' ')
        elif prev is not None and (prev['kind'] == 'math' or t['x0'] - prev['x1'] > h * 0.5) and not s.endswith(' ') \
                and not (prev['kind'] == 'ch' and prev['ch'] in PUNCT_OUT):
            s += ' '
        s += ch
        prev = t
    s = re.sub(r'\$\s*\$', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def split_column(items, W):
    """找兩欄中間的空白直線；找不到回傳 None。"""
    if len(items) < 8:
        return None
    best = None
    for x in range(int(W * 0.35), int(W * 0.65), max(1, W // 400)):
        cross = sum(1 for it in items if it['box'][0] < x < it['box'][2])
        left = sum(1 for it in items if it['box'][2] <= x)
        right = sum(1 for it in items if it['box'][0] >= x)
        if left < 4 or right < 4:
            continue
        if best is None or cross < best[0]:
            best = (cross, x)
    if best and best[0] <= max(2, len(items) * 0.03):
        return best[1]
    return None


def lines_of(items):
    items = sorted(items, key=lambda it: (it['box'][1] + it['box'][3]) / 2)
    lines = []
    for it in items:
        y0, y1 = it['box'][1], it['box'][3]
        c = (y0 + y1) / 2
        for ln in lines:
            if ln['y0'] - 2 <= c <= ln['y1'] + 2 and not it['iso'] and not ln['iso']:
                ln['items'].append(it)
                ln['y0'] = min(ln['y0'], y0)
                ln['y1'] = max(ln['y1'], y1)
                break
        else:
            lines.append(dict(y0=y0, y1=y1, items=[it], iso=it['iso']))
    out = []
    for ln in sorted(lines, key=lambda l: l['y0']):
        hs = [it['box'][3] - it['box'][1] for it in ln['items'] if it['kind'] == 'text']
        h = float(np.median(hs)) if hs else ln['y1'] - ln['y0']
        out.append((ln['y0'], render_line(tokens_of(ln['items']), h)))
    return out


def arrange(items, W):
    x = split_column(items, W)
    if x is None:
        return [s for _, s in lines_of(items)]
    cross = [it for it in items if it['box'][0] < x < it['box'][2]]
    H = max(it['box'][3] for it in items)
    top_cross = [it for it in cross if it['box'][3] < H * 0.25]
    cut = max((it['box'][3] for it in top_cross), default=-1)
    head = [it for it in items if (it['box'][1] + it['box'][3]) / 2 <= cut + 5]
    rest = [it for it in items if it not in head]
    left = [it for it in rest if (it['box'][0] + it['box'][2]) / 2 < x]
    right = [it for it in rest if (it['box'][0] + it['box'][2]) / 2 >= x]
    return [s for _, s in lines_of(head)] + [s for _, s in lines_of(left)] + [s for _, s in lines_of(right)]
