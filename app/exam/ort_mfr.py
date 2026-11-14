r"""MFR（Pix2Text mfr-1.5，MIT）：算式圖 → LaTeX，只用 onnxruntime＋tokenizers（不需要 torch）。

recognize(imgs) → [{'text', 'score'}]，跟 Pix2Text 的 latex_ocr.recognize 一樣（含 post_process）。
"""
import os

import numpy as np
import onnxruntime as ort
from PIL import Image
from tokenizers import Tokenizer

from . import model_path

BOS, EOS, PAD = 1, 2, 0
MAX_NEW = 1024
_s = None


def _load():
    global _s
    if _s is None:
        so = ort.SessionOptions()
        so.log_severity_level = 3
        d = model_path('mfr')
        enc = ort.InferenceSession(str(d / 'encoder_model.onnx'), so, providers=['CPUExecutionProvider'])
        dec = ort.InferenceSession(str(d / 'decoder_model.onnx'), so, providers=['CPUExecutionProvider'])
        tok = Tokenizer.from_file(str(d / 'tokenizer.json'))
        # 記住前面算過的結果（tools/export_mfr_kv.py 從 decoder_model.onnx 的權重做的）：
        # 每一步只算新的那個符號，結果跟 decoder_model 一樣、快 2～3 倍；沒有這兩個檔就用原本的
        kv = None
        if (d / 'decoder_step.onnx').is_file() and (d / 'decoder_cross.onnx').is_file() and os.environ.get('MFR_KV') != '0':
            kv = (ort.InferenceSession(str(d / 'decoder_cross.onnx'), so, providers=['CPUExecutionProvider']),
                  ort.InferenceSession(str(d / 'decoder_step.onnx'), so, providers=['CPUExecutionProvider']))
        _s = (enc, dec, tok, kv)
    return _s


def _pixels(imgs):
    """DeiTImageProcessor：RGB、384×384 bicubic、/255、(x-0.5)/0.5。"""
    arr = []
    for im in imgs:
        im = im.convert('RGB').resize((384, 384), Image.BICUBIC)
        a = np.asarray(im, dtype=np.float32) / 255.0
        arr.append(((a - 0.5) / 0.5).transpose(2, 0, 1))
    return np.stack(arr).astype(np.float32)


def _softmax_max(logits):
    m = logits.max(axis=-1, keepdims=True)
    e = np.exp(logits - m)
    return (e.max(axis=-1) / e.sum(axis=-1))


def recognize(imgs, batch_size=8):
    enc, dec, tok, kv = _load()
    enc_in = enc.get_inputs()[0].name
    dnames = [i.name for i in dec.get_inputs()]
    out = []
    for s in range(0, len(imgs), batch_size):
        part = imgs[s:s + batch_size]
        hidden = enc.run(None, {enc_in: _pixels(part)})[0]
        n = len(part)
        ids = np.full((n, 1), BOS, dtype=np.int64)
        done = np.zeros(n, dtype=bool)
        probs = [[] for _ in range(n)]
        if kv:
            cross = kv[0].run(None, {'encoder_hidden_states': hidden})[0]
            past = np.zeros((cross.shape[0], 2, n, cross.shape[3], 0, cross.shape[5]), np.float32)
        for _ in range(MAX_NEW):
            if kv:
                logits, past = kv[1].run(None, {'input_ids': ids[:, -1:], 'past_kv': past, 'cross_kv': cross})
            else:
                feed = {}
                for name in dnames:
                    if 'input_ids' in name:
                        feed[name] = ids
                    elif 'encoder_hidden_states' in name:
                        feed[name] = hidden
                    elif 'attention_mask' in name:
                        feed[name] = np.ones_like(ids)
                logits = dec.run(None, feed)[0][:, -1, :]
            nxt = logits.argmax(axis=-1)
            pm = _softmax_max(logits)
            for b in range(n):
                if not done[b]:
                    probs[b].append(float(pm[b]))
            nxt = np.where(done, PAD, nxt)
            ids = np.concatenate([ids, nxt[:, None].astype(np.int64)], axis=1)
            done |= nxt == EOS
            if done.all():
                break
        for b in range(n):
            seq = [int(t) for t in ids[b] if t not in (BOS, EOS, PAD)]
            text = tok.decode(seq, skip_special_tokens=True)
            p = np.array(probs[b]) + 1e-8
            out.append(dict(text=post_process(text), score=float(np.exp(np.log(p).mean()))))
    return out


def post_process(text):
    from .pix2text_post import post_process as pp
    return pp(text)
