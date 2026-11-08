r"""MFD（Pix2Text mfd-1.5，YOLO11m）：找頁面上的算式，只用 onnxruntime＋OpenCV（不需要 torch／ultralytics）。

detect(img, resized_shape=1600) → [{'box': (x0, y0, x1, y1), 'score', 'type': 'embedding'|'isolated'}]
跟 Pix2Text 的 mfd 同樣的前處理（letterbox）、NMS、排序、去重（19 份試卷 2,976 個框 100% 相同）。
"""
from functools import cmp_to_key

import cv2
import numpy as np
import onnxruntime as ort

from . import model_path

NAMES = ['embedding', 'isolated']
STRIDE = 32
_s = None


def _load():
    global _s
    if _s is None:
        so = ort.SessionOptions()
        so.log_severity_level = 3
        _s = ort.InferenceSession(str(model_path('mfd') / 'pix2text-mfd-1.5.onnx'), so, providers=['CPUExecutionProvider'])
    return _s


def letterbox(im, size, auto=True):
    """ultralytics LetterBox：等比縮放、置中補 114 灰；auto＝只補到 32 的倍數。"""
    h, w = im.shape[:2]
    r = min(size / h, size / w)
    nw, nh = max(round(w * r), 1), max(round(h * r), 1)
    dw, dh = size - nw, size - nh
    if auto:
        dw, dh = dw % STRIDE, dh % STRIDE
    dw, dh = dw / 2, dh / 2
    if (w, h) != (nw, nh):
        im = cv2.resize(im, (nw, nh), interpolation=cv2.INTER_LINEAR)
    top, bottom = round(dh - 0.1), round(dh + 0.1)
    left, right = round(dw - 0.1), round(dw + 0.1)
    return cv2.copyMakeBorder(im, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114))


def nms(boxes, scores, iou):
    order = scores.argsort()[::-1]
    keep = []
    while order.size:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        a = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        b = (boxes[order[1:], 2] - boxes[order[1:], 0]) * (boxes[order[1:], 3] - boxes[order[1:], 1])
        order = order[1:][inter / (a + b - inter + 1e-9) <= iou]
    return keep


def _cmp(b1, b2):
    """cnstd sort_boxes：y 方向重疊過半就由左到右，否則由上到下。"""
    a, b = b1['box'], b2['box']
    if not (a[3] <= b[1] or b[3] <= a[1]):
        ov = (min(a[3], b[3]) - max(a[1], b[1])) / max(1, min(a[3] - a[1], b[3] - b[1]))
        if ov > 0.5:
            return a[0] - b[0]
    return a[1] - b[1]


def _partial(b1, b2):
    """交集／b2 面積（cnstd box_partial_overlap）。"""
    iw = min(b1[2], b2[2]) - max(b1[0], b2[0])
    ih = min(b1[3], b2[3]) - max(b1[1], b2[1])
    inter = max(0.0, iw) * max(0.0, ih)
    return inter / ((b2[2] - b2[0]) * (b2[3] - b2[1]) + 1e-6)


def dedup(out, thr=0.1):
    keep = [True] * len(out)
    for i, a in enumerate(out):
        if not keep[i]:
            continue
        for j in range(i + 1, len(out)):
            if not keep[j]:
                continue
            v1, v2 = _partial(a['box'], out[j]['box']), _partial(out[j]['box'], a['box'])
            if v1 >= v2:
                if v1 >= thr:
                    keep[j] = False
            elif v2 >= thr:
                keep[i] = False
                break
    return [o for k, o in zip(keep, out) if k]


def detect(img, resized_shape=1600, conf=0.25, iou=0.7, max_det=300, auto=True):
    """img：PIL（RGB）或 RGB 的 numpy 陣列。"""
    rgb = np.asarray(img.convert('RGB')) if hasattr(img, 'convert') else np.asarray(img)
    h0, w0 = rgb.shape[:2]
    size = int(np.ceil(resized_shape / STRIDE) * STRIDE)
    lb = letterbox(rgb, size, auto)
    x = (lb.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
    s = _load()
    pred = s.run(None, {s.get_inputs()[0].name: x})[0][0].T  # (N, 4+類別數)
    cls_scores = pred[:, 4:]
    cls = cls_scores.argmax(1)
    sc = cls_scores.max(1)
    m = sc > conf
    xywh, cls, sc = pred[m, :4], cls[m], sc[m]
    boxes = np.stack([xywh[:, 0] - xywh[:, 2] / 2, xywh[:, 1] - xywh[:, 3] / 2,
                      xywh[:, 0] + xywh[:, 2] / 2, xywh[:, 1] + xywh[:, 3] / 2], 1)
    off = boxes + cls[:, None] * 7680.0  # 不同類別分開做 NMS（ultralytics max_wh）
    keep = nms(off, sc, iou)[:max_det]
    boxes, sc, cls = boxes[keep], sc[keep], cls[keep]
    # 換回原圖座標（ultralytics scale_boxes）
    hh, ww = lb.shape[:2]
    gain = min(hh / h0, ww / w0)
    px, py = round((ww - w0 * gain) / 2 - 0.1), round((hh - h0 * gain) / 2 - 0.1)
    boxes[:, [0, 2]] = ((boxes[:, [0, 2]] - px) / gain).clip(0, w0)
    boxes[:, [1, 3]] = ((boxes[:, [1, 3]] - py) / gain).clip(0, h0)
    out = [dict(box=tuple(float(v) for v in b), score=float(c), type=NAMES[int(k)])
           for b, c, k in zip(boxes, sc, cls)]
    out = sorted(out, key=cmp_to_key(_cmp))
    return dedup(out)
