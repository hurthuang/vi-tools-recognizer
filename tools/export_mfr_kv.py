"""從 decoder_model.onnx 的權重重建 TrOCR decoder（記住前面算過的 key/value），匯出兩個 ONNX：
   decoder_cross.onnx：encoder_hidden_states → 每層 cross-attention 的 K、V（每張圖算一次）
   decoder_step.onnx ：這一步的 token、位置、前面的 self K/V、cross K/V → logits、新的 self K/V
要 torch、onnx（只在打包的電腦上跑，辨識套件本身不需要）。
用法：python tools/export_mfr_kv.py [mfr 模型資料夾，預設 build/models/mfr]；兩個檔寫在同一個資料夾"""
import sys, math
from pathlib import Path
import numpy as np
import onnx
from onnx import numpy_helper
import torch
from torch import nn
import torch.nn.functional as F

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / 'build' / 'models' / 'mfr'
SRC = OUT / 'decoder_model.onnx'

m = onnx.load(str(SRC))
init = {t.name: numpy_helper.to_array(t) for t in m.graph.initializer}
W = {}
for k, v in init.items():
    if k.startswith('decoder.model.decoder.'):
        W[k[len('decoder.model.decoder.'):]] = v
for n in m.graph.node:
    if n.op_type == 'MatMul':
        w = [i for i in n.input if i in init]
        if w:
            name = n.name.replace('/decoder/decoder/', '').replace('/decoder/', '').replace('/MatMul', '').replace('/', '.')
            W[name + '.weight'] = init[w[0]].T  # MatMul 是 [in,out]，Linear 存 [out,in]
print('權重', len(W), [k for k in W if 'layers' not in k])

L, D, H, V = 6, 256, 8, 1868
DH = D // H


class Step(nn.Module):
    def __init__(s):
        super().__init__()
        t = lambda k: nn.Parameter(torch.from_numpy(np.ascontiguousarray(W[k])).float(), requires_grad=False)
        s.emb, s.pos = t('embed_tokens.weight'), t('embed_positions.weight')
        s.lne = (t('layernorm_embedding.weight'), t('layernorm_embedding.bias'))
        s.p = {}
        for k in W:
            if k.startswith('layers.'):
                s.register_parameter(k.replace('.', '_'), t(k))
        out_key = [k for k in W if 'output_projection' in k][0]
        s.outw = t(out_key)

    def g(s, k):
        return getattr(s, k.replace('.', '_'))

    def lin(s, x, k, bias=True):
        return F.linear(x, s.g(k + '.weight'), s.g(k + '.bias') if bias else None)

    def ln(s, x, k):
        return F.layer_norm(x, (D,), s.g(k + '.weight'), s.g(k + '.bias'), 1e-5)

    def heads(s, x):  # [b,t,D] → [b,H,t,DH]
        b, t_, _ = x.shape
        return x.view(b, t_, H, DH).transpose(1, 2)

    def cross(s, enc):  # enc [b,577,384] → [L,2,b,H,577,DH]
        kv = []
        for i in range(L):
            k = s.heads(s.lin(enc, f'layers.{i}.encoder_attn.k_proj'))
            v = s.heads(s.lin(enc, f'layers.{i}.encoder_attn.v_proj'))
            kv.append(torch.stack([k, v]))
        return torch.stack(kv)

    def attn(s, q, k, v):
        a = torch.matmul(q, k.transpose(-1, -2)) / math.sqrt(DH)
        a = torch.softmax(a, dim=-1)
        return torch.matmul(a, v)

    def step(s, ids, past, cross):  # ids [b,1]；past [L,2,b,H,t,DH]；位置 = t
        b = ids.shape[0]
        t_ = past.shape[4]
        x = F.embedding(ids, s.emb) * math.sqrt(D)
        pos = s.pos[t_ + 2].view(1, 1, D)   # learned position，offset 2
        x = x + pos
        x = F.layer_norm(x, (D,), s.lne[0], s.lne[1], 1e-5)
        news = []
        for i in range(L):
            p = f'layers.{i}.'
            r = x
            q = s.heads(s.lin(x, p + 'self_attn.q_proj'))
            k = torch.cat([past[i, 0], s.heads(s.lin(x, p + 'self_attn.k_proj'))], dim=2)
            v = torch.cat([past[i, 1], s.heads(s.lin(x, p + 'self_attn.v_proj'))], dim=2)
            news.append(torch.stack([k, v]))
            h = s.attn(q, k, v).transpose(1, 2).reshape(b, 1, D)
            x = s.ln(r + s.lin(h, p + 'self_attn.out_proj'), p + 'self_attn_layer_norm')
            r = x
            q = s.heads(s.lin(x, p + 'encoder_attn.q_proj'))
            h = s.attn(q, cross[i, 0], cross[i, 1]).transpose(1, 2).reshape(b, 1, D)
            x = s.ln(r + s.lin(h, p + 'encoder_attn.out_proj'), p + 'encoder_attn_layer_norm')
            r = x
            x = s.lin(F.relu(s.lin(x, p + 'fc1')), p + 'fc2')
            x = s.ln(r + x, p + 'final_layer_norm')
        logits = F.linear(x[:, 0], s.outw)
        return logits, torch.stack(news)


net = Step().eval()


class Cross(nn.Module):
    def forward(s, enc):
        return net.cross(enc)


class StepM(nn.Module):
    def forward(s, ids, past, cross):
        return net.step(ids, past, cross)


b = 2
enc = torch.randn(b, 577, 384)
cr = net.cross(enc)
past = torch.zeros(L, 2, b, H, 3, DH)
ids = torch.tensor([[5], [7]])
kw = dict(opset_version=17, dynamo=False)
torch.onnx.export(Cross(), (enc,), str(OUT / 'decoder_cross.onnx'), input_names=['encoder_hidden_states'], output_names=['cross_kv'],
                  dynamic_axes={'encoder_hidden_states': {0: 'b', 1: 'e'}, 'cross_kv': {2: 'b', 4: 'e'}}, **kw)
torch.onnx.export(StepM(), (ids, past, cr), str(OUT / 'decoder_step.onnx'), input_names=['input_ids', 'past_kv', 'cross_kv'],
                  output_names=['logits', 'present_kv'],
                  dynamic_axes={'input_ids': {0: 'b'}, 'past_kv': {2: 'b', 4: 't'}, 'cross_kv': {2: 'b', 4: 'e'},
                                'logits': {0: 'b'}, 'present_kv': {2: 'b', 4: 't1'}}, **kw)
for f in ('decoder_cross.onnx', 'decoder_step.onnx'):
    print(f, round((OUT / f).stat().st_size / 1e6, 1), 'MB')
