r"""給 vi-tools 用的「辨識一塊」：網頁把原卷上框起來的那一塊送過來，這裡辨識完回傳文字。

兩種送法：
  圖：  {image: data:image/png;base64,…}               只用辨識（掃描檔、圖檔）
  PDF： {pdf_sha256, [pdf: base64], page, box:[x0,y0,x1,y1]}
        page＝原始 PDF 的頁碼（從 1 起），box＝頁面寬高的比例。中文、題號讀文字層，算式用 MFR，跟試卷辨識一樣。
        PDF 只送一次：辨識套件記住最近幾份（照 sha256），沒記住的回 {need_pdf: true}，網頁再連 PDF 一起送。
type：vi-tools 的類型（q 題目、opt 選項，其他照文字），照辨識套件的規則排成輸出的幾行
mode：
  auto  文字＋算式，一行一筆，算式寫成 \( \)
  math  整塊當一個算式（使用者自己框住算式，不用 MFD）→ LaTeX
"""
import base64
import hashlib
import io
import re
from collections import OrderedDict

from PIL import Image

from . import mathocr
from .review import LOCK

MAX_SIDE = 4000
PAD = 6
_pdfs = OrderedDict()   # sha256 → PDF 的 bytes（最近 3 份）


def decode(data_url):
    m = re.match(r'data:image/(png|jpeg|webp);base64,(.+)$', data_url or '', re.S)
    if not m:
        raise ValueError('image 要是 data:image/png;base64,… 的圖')
    img = Image.open(io.BytesIO(base64.b64decode(m.group(2)))).convert('RGB')
    if max(img.size) > MAX_SIDE:
        s = MAX_SIDE / max(img.size)
        img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    return img


def dollars(s):
    r"""mathocr 一行的寫法（算式用 $…$，純數字不包）→ 中繼檔的寫法（\( \)）"""
    return re.sub(r'\$([^$]+)\$', lambda m: '\\(' + m.group(1).strip() + '\\)', s)


# vi-tools 的類型 → 辨識套件版面的類型（排成輸出文字的規則：題號、答案欄（ ）、選項拆開、折行接回去）
KIND = {'q': 'question', 'opt': 'option'}


def _lines(items, typ='p'):
    from . import layout
    # 空的算式（答案欄的（ ）被當成算式）拿掉
    items = [it for it in items if it['kind'] != 'math' or re.search(r'[0-9A-Za-z]', layout.bare(it.get('tex') or ''))]
    if not items:
        return []
    page = dict(items=items)
    block = dict(type=KIND.get(typ, 'text'), items=list(range(len(items))))
    return [dollars(s) for s in layout.block_lines(page, block) if s.strip()]


def _math(img):
    from . import ort_mfr
    with LOCK:
        r = ort_mfr.recognize([img])[0]
    return dict(mode='math', tex=mathocr.tidy(r['text']), score=round(float(r['score']), 3))


def _auto_image(img, typ='p', math=True):
    with LOCK:
        items = mathocr.raw_items(img, math)
    ms = [it['score'] for it in items if it['kind'] == 'math']
    return dict(mode='auto' if math else 'text', lines=_lines(items, typ), math_score=round(min(ms), 3) if ms else None,
                text_layer=False)


def remember_pdf(data):
    sha = hashlib.sha256(data).hexdigest()
    _pdfs[sha] = data
    _pdfs.move_to_end(sha)
    while len(_pdfs) > 3:
        _pdfs.popitem(last=False)
    return sha


def recognize(req):
    mode = req.get('mode') or 'auto'
    if req.get('pdf_sha256') or req.get('pdf'):
        if req.get('pdf'):
            sha = remember_pdf(base64.b64decode(req['pdf']))
        else:
            sha = req['pdf_sha256']
            if sha not in _pdfs:
                return dict(need_pdf=True)
            _pdfs.move_to_end(sha)
        return _from_pdf(_pdfs[sha], int(req.get('page') or 1), req.get('box'), mode, req.get('type') or 'p')
    img = mathocr.clean(decode(req.get('image')))
    return _math(img) if mode == 'math' else _auto_image(img, req.get('type') or 'p', mode != 'text')


def _from_pdf(data, n, box, mode, typ):
    import pypdfium2 as pdfium

    from . import textlayer, vertical
    if not box or len(box) != 4:
        raise ValueError('box 要是 [x0, y0, x1, y1]')
    doc = pdfium.PdfDocument(data)
    if not 1 <= n <= len(doc):
        raise ValueError('沒有第 %d 頁' % n)
    pg = doc[n - 1]
    page = mathocr.clean(pg.render(scale=mathocr.DPI / 72).to_pil().convert('RGB'))
    W, H = page.size
    pad = 2 if mode == 'math' else PAD   # 算式：使用者框得剛好，多切會切到旁邊的字
    x0, y0 = max(0, int(box[0] * W) - pad), max(0, int(box[1] * H) - pad)
    x1, y1 = min(W, int(box[2] * W) + pad), min(H, int(box[3] * H) + pad)
    if x1 - x0 < 4 or y1 - y0 < 4:
        raise ValueError('框太小')
    crop = page.crop((x0, y0, x1, y1))
    if mode == 'math':
        return _math(crop)
    use_math = mode != 'text'   # text：只辨識文字，不找算式
    with LOCK:
        items = mathocr.raw_items(crop, use_math)
    for it in items:   # 換成整頁的座標，才能跟文字層對
        b = it['box']
        it['box'] = (b[0] + x0, b[1] + y0, b[2] + x0, b[3] + y0)
        if 'chars' in it:
            it['chars'] = [(c, a + x0, e + x0, s) for c, a, e, s in it['chars']]
    chars = textlayer.chars_of(pg, mathocr.DPI)
    if len(chars) < 20 or vertical.chars_vertical(chars):
        # 掃描頁（沒有文字層）、直書頁：只用辨識
        r = _auto_image(crop, typ, use_math)
        r['text_layer'] = False
        return r
    tl = textlayer.page_layer(pg, mathocr.DPI, items)
    if tl is None:
        return _auto_image(crop, typ, use_math)

    def inside(b):
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        return x0 <= cx <= x1 and y0 <= cy <= y1

    drop = set(tl['drop_math'])
    keep = set(tl['keep_ocr'])
    out = [it for it in tl['items'] if inside(it['box'])]
    out += [it for k, it in enumerate(items) if (it['kind'] == 'math' and k not in drop) or k in keep]
    for it in out:
        it.setdefault('iso', False)
    ms = [it['score'] for it in out if it['kind'] == 'math']
    return dict(mode='auto' if use_math else 'text', lines=_lines(out, typ), math_score=round(min(ms), 3) if ms else None,
                text_layer=True)
