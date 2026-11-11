"""vi-tools 的「辨識一塊譜例」：POST /api/omr {image}（一張圖，base64 或 dataURL）→ MusicXML。

用給網頁的文字辨識頁（樂譜模式）、之後的點字樂譜／版面整理。結果只是草稿：試題上的方框、箭頭會被當成小節線或音符，
調號、拍號常常漏，要在音樂點字對照原圖改。

- 先估譜線間距：Audiveris、homr 都是照 300 DPI 左右的譜訓練的（線距約 20 像素），截圖的線距常只有 8～12 像素，
  照線距放大到約 20 像素（最多 4 倍）；找不到譜線就照圖的大小放大到短邊至少 600。
- Audiveris、homr 同時跑（各一個行程），兩個結果都回傳，網頁讓使用者選。
"""
import base64
import io
import re
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import engines

TARGET_SP = 20          # 放大後的譜線間距（像素）
MAX_SCALE = 4
MAX_PIXELS = 40_000_000    # 收進來的圖最多 4 千萬像素（一頁 A4 600 DPI 約 3,500 萬）
MAX_SIDE = 12_000          # 放大後長邊最多 12,000 像素（Audiveris、homr 用不到更大的）
_LOCK = threading.Lock()   # 一次辨識一塊（兩個引擎各吃一顆 CPU 以上，同時好幾塊會很慢）


def _decode(s):
    if not isinstance(s, str) or not s:
        raise ValueError('沒有圖')
    m = re.match(r'^data:image/[\w.+-]+;base64,', s)
    if m:
        s = s[m.end():]
    from PIL import Image
    try:
        raw = base64.b64decode(s, validate=False)
    except Exception:  # noqa: BLE001
        raise ValueError('圖的資料壞掉了（不是 base64）')
    try:
        im = Image.open(io.BytesIO(raw))
    except Exception:  # noqa: BLE001
        raise ValueError('讀不出這張圖')
    # 先看尺寸再解碼：檔案很小、尺寸很大的圖解碼後會吃光記憶體
    if im.width * im.height > MAX_PIXELS:
        raise ValueError('圖太大（%d×%d），請裁小一點再辨識' % (im.width, im.height))
    im.load()
    if im.mode in ('RGBA', 'LA', 'P'):    # 透明的截圖底色當白色
        im = im.convert('RGBA')
        bg = Image.new('RGBA', im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        im = bg
    return im.convert('L')


def staff_spacing(im):
    """灰階圖 → 譜線間距（像素，中位數）；找不到回傳 None。長度 ≥ 寬 30% 的細橫線，五條等距一組。"""
    import numpy as np
    a = np.asarray(im) < 128
    h, w = a.shape
    rows = []
    for y in range(h):
        r = a[y]
        if r.sum() < w * 0.3:
            continue
        d = np.diff(np.concatenate(([0], r.view(np.int8), [0])))
        st, en = np.where(d == 1)[0], np.where(d == -1)[0]
        if len(st) and (en - st).max() >= w * 0.3:
            rows.append(y)
    lines = []
    for y in rows:
        if lines and y - lines[-1][1] <= 1:
            lines[-1][1] = y
        else:
            lines.append([y, y])
    ys = [(a0 + a1) / 2 for a0, a1 in lines]
    gaps = []
    i = 0
    while i + 4 < len(ys):
        g = [ys[i + k] - ys[i + k - 1] for k in range(1, 5)]
        avg = sum(g) / 4
        if avg >= 3 and max(g) - min(g) <= max(2, avg * 0.25):
            gaps.append(avg)
            i += 5
        else:
            i += 1
    if not gaps:
        return None
    gaps.sort()
    return gaps[len(gaps) // 2]


def recognize(req):
    im = _decode(req.get('image'))
    sp = staff_spacing(im)
    if sp:
        k = max(1.0, min(MAX_SCALE, TARGET_SP / sp))
    else:
        k = max(1.0, min(MAX_SCALE, 600 / max(1, min(im.size))))
    k = min(k, MAX_SIDE / max(im.size))   # 放大後也不能太大
    if k > 1.05:
        from PIL import Image
        im = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
    want = [e for e in (req.get('engines') or ['homr', 'audiveris']) if e in ('homr', 'audiveris')]
    if not want:
        raise ValueError('engines 只能是 homr、audiveris')
    with _LOCK:
        work = Path(tempfile.mkdtemp(prefix='vitools-omr-'))
        try:
            img = work / 'score.png'
            im.save(img)
            t0 = time.time()

            def run(name):
                t = time.time()
                try:
                    out = getattr(engines, name)(img, work, timeout=300)
                    if not out:
                        return {'engine': name, 'error': '沒有辨識出樂譜', 'sec': round(time.time() - t, 1)}
                    return {'engine': name, 'musicxml': Path(out).read_text(encoding='utf-8', errors='replace'),
                            'sec': round(time.time() - t, 1)}
                except Exception as e:  # noqa: BLE001
                    return {'engine': name, 'error': '%s: %s' % (type(e).__name__, e), 'sec': round(time.time() - t, 1)}

            with ThreadPoolExecutor(max_workers=len(want)) as ex:
                results = list(ex.map(run, want))
            return {'results': results, 'spacing': round(sp, 1) if sp else None, 'scale': round(k, 2),
                    'sec': round(time.time() - t0, 1)}
        finally:
            shutil.rmtree(work, ignore_errors=True)
