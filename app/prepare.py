"""把 PDF 頁面或圖檔準備成辨識用的圖：抽出 PDF 內嵌的樂譜圖（沒有就整頁算圖），估計五線的線距，放大到辨識工具適合的大小。

試題 PDF 裡的掃描譜例常只有 8～14 px 的線距，Audiveris 會拒絕（太小）、homr 也認不好；放大到約 32 px 兩個都準很多。
"""
from collections import Counter

import numpy as np
from PIL import Image

TARGET_INTERLINE = 32
IMG_EXT = ('.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.gif', '.webp')


def parse_pages(spec, count):
    """'8,10' '8-10' '' → 0 起算的頁碼清單（空白表示全部）。"""
    spec = (spec or '').replace('，', ',').replace('～', '-').replace('~', '-').replace(' ', '')
    if not spec:
        return list(range(count))
    out = []
    for part in spec.split(','):
        if not part:
            continue
        if '-' in part:
            a, b = part.split('-', 1)
            a = int(a) if a else 1
            b = int(b) if b else count
            out.extend(range(a - 1, b))
        else:
            out.append(int(part) - 1)
    return [p for p in out if 0 <= p < count]


def staves(img):
    """找五線譜：每列黑點比例高的列當成譜線，連續五條等距的算一組。回傳 [(第一條線 y, 線距)]。
    分段（左右兩半各找）以容忍掃描歪斜；文字行不會連成五條等距的長線。"""
    a = np.asarray(img.convert('L')) < 128
    h, w = a.shape
    if h < 40 or w < 40:
        return []
    found = []
    for x0, x1 in ((0, w), (0, w // 2), (w // 2, w)):
        frac = a[:, x0:x1].mean(axis=1)
        rows = np.flatnonzero(frac > 0.45)
        lines = []
        for y in rows.tolist():
            if lines and y - lines[-1][-1] <= 1:
                lines[-1].append(y)
            else:
                lines.append([y])
        cs = [sum(l) / len(l) for l in lines if len(l) <= max(3, h // 100)]
        i = 0
        while i + 4 < len(cs):
            g = np.diff(cs[i:i + 5])
            if g.min() >= 4 and g.max() <= g.min() * 1.35:
                found.append((cs[i], float(g.mean())))
                i += 5
            else:
                i += 1
        if len(found) >= 1 and x1 == w and x0 == 0:
            break
    return found


def interline(img):
    """主要譜表的線距（px）；找不到五線譜回傳 None。總譜裡的小譜表（例如三重奏的弦樂）比鋼琴小，取最大的線距。"""
    st = staves(img)
    if not st:
        return None
    return max(d for _, d in st)


def normalize(img):
    """回傳 (放大後的灰階圖, 原線距, 放大倍數)。"""
    g = img.convert('L')
    il = interline(g)
    if il is None:
        return g, None, 1.0
    scale = max(1.0, min(4.0, TARGET_INTERLINE / il))
    if scale > 1.05:
        g = g.resize((round(g.width * scale), round(g.height * scale)), Image.LANCZOS)
    return g, il, scale


def from_pdf(path, pages_spec, log):
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    pdf = pdfium.PdfDocument(path)
    out = []
    for pi in parse_pages(pages_spec, len(pdf)):
        page = pdf[pi]
        pw, ph = page.get_size()
        found = []
        for o in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE]):
            try:
                x0, y0, x1, y1 = o.get_bounds()
                if (x1 - x0) * (y1 - y0) < 0.08 * pw * ph:
                    continue
                im = o.get_bitmap(render=False).to_pil()
            except Exception:
                continue
            if im.width < 300 or interline(im) is None:
                continue
            found.append((y0, im))
        if found:
            # PDF 座標由下往上：y 大的在上面
            found.sort(key=lambda t: -t[0])
            for k, (_, im) in enumerate(found):
                label = '第%d頁' % (pi + 1) + ('_圖%d' % (k + 1) if len(found) > 1 else '')
                out.append((label, im))
            log('第 %d 頁：抽出 %d 張內嵌的樂譜圖' % (pi + 1, len(found)))
        else:
            im = page.render(scale=300 / 72, grayscale=True).to_pil()
            if interline(im) is None:
                log('第 %d 頁：沒有找到五線譜，略過' % (pi + 1))
                continue
            out.append(('第%d頁' % (pi + 1), im))
            log('第 %d 頁：整頁以 300 dpi 算圖' % (pi + 1))
    return out


def load_inputs(path, pages_spec, log):
    low = str(path).lower()
    if low.endswith('.pdf'):
        return from_pdf(path, pages_spec, log)
    if low.endswith(IMG_EXT):
        im = Image.open(path)
        im.load()
        return [('圖', im)]
    raise ValueError('只能辨識 PDF 或圖檔（png、jpg、tif…）')
