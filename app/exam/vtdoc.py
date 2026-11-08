r"""vi-tools 中繼檔（.vtdoc.html）：版面調整的結果 → 一個 HTML 檔（本身就是 NVDA 電子檔）。

規格見 E:\Project\vtdoc-規格草稿.md（定案後在 vi-tools docs/notes/vtdoc.md）。內容與 試卷.txt 一樣來自
layout.render_nodes，所以兩邊一致。看得到的內容就是輸出；hidden 的元素（不輸出的附圖、改成條列的表格）只是資料。
"""
import base64
import hashlib
import html
import io
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image

from . import layout, vertical

VERSION = 1
FIG_MAX = 1400  # 附圖長邊最多幾像素（200 dpi 約 18 公分）
SRC_MAX = 15 * 1024 * 1024  # 原始檔超過這個大小就改放縮小的頁面圖
PAGE_MAX = 1600  # 改放頁面圖時長邊幾像素
MATH = re.compile(r'\\\((.+?)\\\)')
LATIN = re.compile(r'[A-Za-z]')
CJK = re.compile(r'[\u3400-\u9fff]')
OPT_LABEL = re.compile(r'^\s*(?:[（(]\s*([A-EＡ-Ｅ])\s*[)）]|([①-⑳]))')

STYLE = '''body{font-family:'Microsoft JhengHei',sans-serif;font-size:20px;line-height:2;max-width:860px;margin:0 auto;padding:24px;color:#111;background:#fff}
h1{font-size:1.4em}h2{font-size:1.2em;border-bottom:2px solid #036;color:#036;margin-top:1.6em}
h3{font-size:1em;margin:1.2em 0 .3em}.vt-opt{margin:.2em 0 .2em 1.5em}.vt-figtext,.vt-figdesc{color:#333}
figure{margin:.6em 0}figure img{max-width:100%;border:1px solid #ccc}table.vt-table{border-collapse:collapse}
table.vt-table td,table.vt-table th{border:1px solid #555;padding:2px 8px}hr.vt-page{border:0;border-top:1px dashed #bbb}
@media print{body{font-size:14pt}}'''


def esc(s):
    return html.escape(s, quote=False)


def attr(s):
    return html.escape(str(s), quote=True)


def png_data(im, max_side=None):
    if max_side and max(im.size) > max_side:
        s = max_side / max(im.size)
        im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, 'PNG')
    data, mime = buf.getvalue(), 'image/png'
    if im.width * im.height > 40000:  # 照片、插圖用 JPEG 小很多；線條圖 PNG 比較小也比較清楚，取小的
        jb = io.BytesIO()
        im.convert('L' if im.mode in ('L', '1') else 'RGB').save(jb, 'JPEG', quality=75, optimize=True)
        if len(jb.getvalue()) < 0.7 * len(data):
            data, mime = jb.getvalue(), 'image/jpeg'
    return f'data:{mime};base64,' + base64.b64encode(data).decode('ascii')


class Doc:
    """產生一份中繼檔。images=False：圖不內嵌（只留處理方式與圖中文字）。
    src：原始檔（PDF 或圖檔），None＝不帶原卷。元素的 data-box＝「頁 x0 y0 x1 y1」，座標是那一頁的比例（0～1）。"""

    def __init__(self, d, pages, blocks, images=True, src=None):
        self.d, self.pages, self.blocks, self.images, self.src = Path(d), pages, blocks, images, src
        self.imgs = {}
        self.math = {}  # (頁, 區塊) → [[正規化 tex, 項目, 用過了沒]]
        self.n_fig = 0

    # ---------- 原卷圖 ----------
    def page_img(self, pi):
        if pi not in self.imgs:
            p = self.pages[pi]
            self.imgs[pi] = Image.open(p.get('png_d') or p['png']).convert('L')  # 頁面圖本來就是灰階，存灰階 PNG 小很多
        return self.imgs[pi]

    def disp_box(self, pi, box):
        p = self.pages[pi]
        return vertical.box_v2d(box, p['h']) if p.get('rot') else tuple(box)

    def norm_box(self, pi, box):
        """原卷上的位置 → [x0, y0, x1, y1]（頁面寬高的比例，直書頁轉回原卷方向）。"""
        im = self.page_img(pi)
        x0, y0, x1, y1 = self.disp_box(pi, box)
        W, H = im.size
        return [round(max(0.0, min(1.0, c)), 4) for c in (min(x0, x1) / W, min(y0, y1) / H, max(x0, x1) / W, max(y0, y1) / H)]

    def omitted(self):
        """沒輸出的地方（給下游檢查框錯、漏框）：略過的區塊、沒被收進任何區塊的字。
        → [{page, box, kind: skip|uncovered, text}]"""
        out = []
        for pi, (p, bl) in enumerate(zip(self.pages, self.blocks)):
            its = p['items']
            inb = set()
            for b in bl:
                inb.update(b.get('items', []))
                if b['type'] == 'skip':
                    t = ' '.join((its[i].get('text') or its[i].get('tex') or '') for i in b.get('items', []) if i < len(its))
                    out.append(dict(page=pi + 1, box=self.norm_box(pi, b['box']), kind='skip', text=t.strip()[:200]))
            for k, it in enumerate(its):
                if k in inb or it.get('dead'):
                    continue
                t = it.get('text') if it['kind'] != 'math' else '\\(' + (it.get('tex') or '') + '\\)'
                out.append(dict(page=pi + 1, box=self.norm_box(pi, it['box']), kind='uncovered', text=(t or '').strip()[:200]))
        return out

    def box_attr(self, pi, box):
        """原卷上的位置 → data-box（頁碼從 1 起，座標是頁面寬高的比例，直書頁轉回原卷方向）。"""
        if pi is None or not box:
            return ''
        im = self.page_img(pi)
        x0, y0, x1, y1 = self.disp_box(pi, box)
        W, H = im.size
        v = [max(0.0, min(1.0, c)) for c in (min(x0, x1) / W, min(y0, y1) / H, max(x0, x1) / W, max(y0, y1) / H)]
        return f' data-box="{pi + 1} ' + ' '.join(f'{c:.4f}' for c in v) + '"'

    def source_html(self, meta):
        """原卷放進中繼檔：PDF 或圖檔原樣放（不超過 15 MB）；太大或不是 PDF／圖檔 → 每頁縮小的灰階圖。"""
        if self.src is None:
            meta['source']['embed'] = 'none'
            return ''
        meta['source']['page_map'] = [p.get('src_page') or (k + 1) for k, p in enumerate(self.pages)]
        if self.src is True:
            mime, size = None, 0
        else:
            ext = self.src.suffix.lower()
            mime = {'.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}.get(ext)
            size = self.src.stat().st_size
        # 圖檔來源：辨識用的頁面圖可能轉正過，座標跟原圖不一定一樣，一律放頁面圖
        if mime == 'application/pdf' and size <= SRC_MAX:
            meta['source']['embed'] = 'pdf'
            b64 = base64.b64encode(self.src.read_bytes()).decode('ascii')
            return f'<script type="application/pdf" id="vtdoc-source">{b64}</script>\n'
        meta['source']['embed'] = 'pages'
        out = []
        for pi in range(len(self.pages)):
            im = self.page_img(pi)
            if max(im.size) > PAGE_MAX:
                s = PAGE_MAX / max(im.size)
                im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, 'JPEG', quality=70, optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode('ascii')
            out.append(f'<script type="image/jpeg" class="vtdoc-page" data-page="{pi + 1}">{b64}</script>\n')
        return ''.join(out)

    def crop(self, pi, box, pad=6, max_side=None):
        im = self.page_img(pi)
        x0, y0, x1, y1 = self.disp_box(pi, box)
        x0, y0 = max(0, int(x0) - pad), max(0, int(y0) - pad)
        x1, y1 = min(im.width, int(x1) + pad), min(im.height, int(y1) + pad)
        return im.crop((x0, y0, x1, y1)), (x0, y0)

    # ---------- 算式 ----------
    def math_items(self, pi, blk):
        key = (pi, blk)
        if key not in self.math:
            b = self.block(pi, blk)
            its = [self.pages[pi]['items'][i] for i in (b['items'] if b else [])]
            self.math[key] = [[re.sub(r'\s', '', it.get('tex', '')), it, False] for it in its
                              if it['kind'] == 'math' and not it.get('dead')]
        return self.math[key]

    def block(self, pi, blk):
        if pi is None or blk is None:
            return None
        return next((b for b in self.blocks[pi] if b['id'] == blk), None)

    def inline(self, text, pi=None, blk=None):
        """一行文字 → HTML：算式包成 vt-math（附原卷裁圖、信心），其他照字跳脫。"""
        out, k = [], 0
        for m in MATH.finditer(text):
            out.append(esc(text[k:m.start()]))
            tex = m.group(1).strip()
            a = f' data-tex="{attr(tex)}"'
            if pi is not None:
                key = re.sub(r'\s', '', tex)
                for c in self.math_items(pi, blk):
                    if not c[2] and c[0] == key:
                        c[2] = True
                        it = c[1]
                        a += f' data-conf="{it.get("score", 1):.2f}"' + self.box_attr(pi, it.get('box'))
                        if self.images:
                            im, _ = self.crop(pi, it['box'], pad=4, max_side=600)
                            a += f' data-crop="{png_data(im)}"'
                        break
            out.append(f'<span class="vt-math"{a}>{esc(m.group(0))}</span>')
            k = m.end()
        out.append(esc(text[k:]))
        return ''.join(out)

    @staticmethod
    def lang(text):
        """整行幾乎都是英文 → lang="en"（算式不算）。"""
        t = MATH.sub('', text)
        letters = len(LATIN.findall(t))
        return ' lang="en"' if letters >= 8 and not CJK.search(t) else ''

    # ---------- 表格 ----------
    def table(self, lines, b, how, hidden):
        rows, cap = [], ''
        for l in lines:
            k = l.find('<table')
            if k >= 0:
                cap = cap or l[:k].strip()  # 「表(一)<table …>」：表名放進 <caption>
                rows += TableParser.parse(l[k:])
        a = f' class="vt-table" data-how="{how}"'
        if b is not None:
            for k in ('hrow', 'hcol'):
                if b.get(k) is not None:
                    a += f' data-{k}="{1 if b[k] else 0}"'
        a += self.blk_attr(b)
        if hidden:
            a += ' hidden'
        cells = []
        for r in rows:
            tds = []
            for c in r:
                span = ''.join(f' {k}="{v}"' for k, v in (('colspan', c['colspan']), ('rowspan', c['rowspan'])) if v > 1)
                tds.append(f'<{c["tag"]}{span}>{self.inline(c["text"], b and b.get("_pi"), b and b.get("id"))}</{c["tag"]}>')
            cells.append('<tr>' + ''.join(tds) + '</tr>')
        capt = f'<caption>{self.inline(cap)}</caption>' if cap else ''
        return f'<table{a}>' + capt + ''.join(cells) + '</table>'

    def blk_attr(self, b):
        if b is None or '_pi' not in b:
            return ''
        return f' data-blk="p{b["_pi"] + 1}-b{b["id"]}"' + self.box_attr(b['_pi'], b.get('box'))

    # ---------- 附圖 ----------
    def figure(self, pi, b, how, role=None):
        self.n_fig += 1
        page = self.pages[pi]
        score = b['type'] == 'score'
        a = f' class="{"vt-score" if score else "vt-fig"}" id="fig-{self.n_fig}" data-how="{how}"'
        if not score and how in ('mark', 'figtext'):
            a += ' data-tactile="1"'
        if role:
            a += f' data-role="{role}"'
        a += self.blk_attr(b)
        if how in ('none', 'text'):
            a += ' hidden'
        labels = layout._fig_labels(page, b) if not score else []
        alt = ('圖中文字：' + '、'.join(t for t, _, _ in labels)) if labels else ('譜例' if score else '附圖')
        parts = []
        if self.images:
            im, (ox, oy) = self.crop(pi, b['box'])
            sc = min(1.0, FIG_MAX / max(im.size))  # 太大的圖縮小（標籤座標跟著縮）
            if sc < 1:
                im = im.resize((max(1, round(im.width * sc)), max(1, round(im.height * sc))), Image.LANCZOS)
            parts.append(f'<img src="{png_data(im)}" alt="{attr(alt)}">')
        else:
            ox = oy = 0
            sc = 1.0
        cap = (b.get('caption') or '').strip()
        if cap:
            parts.append(f'<figcaption>{esc(cap)}</figcaption>')
        if labels:
            lis = []
            for t, cx, cy in labels:
                if page.get('rot'):
                    cx, cy = vertical.pt_v2d(cx, cy, page['h'])
                lis.append(f'<li data-x="{round((cx - ox) * sc, 1)}" data-y="{round((cy - oy) * sc, 1)}">{esc(t)}</li>')
            parts.append('<ul class="vt-labels" hidden>' + ''.join(lis) + '</ul>')
        return f'<figure{a}>' + ''.join(parts) + '</figure>'

    # ---------- 整份 ----------
    def build(self, meta):
        for pi, bl in enumerate(self.blocks):
            for b in bl:
                b['_pi'] = pi
        try:
            nodes = layout.render_nodes(self.pages, self.blocks, drops=meta.setdefault('_drops', []))
            body = self.body(nodes)
        finally:
            for bl in self.blocks:
                for b in bl:
                    b.pop('_pi', None)
        drops = meta.pop('_drops')
        if drops:
            meta['drops'] = [dict(section=t, points=p, question=n, of=c) for t, p, n, c in drops]
        title = meta.get('title') or ''
        srch = self.source_html(meta)
        if srch:
            meta['omitted'] = self.omitted()
        mj = json.dumps(meta, ensure_ascii=False).replace('</', '<' + chr(92) + '/')  # 不讓 </script> 提早結束
        return ('<!DOCTYPE html>\n<html lang="zh-Hant">\n<head>\n<meta charset="utf-8">\n'
                f'<meta name="generator" content="vtdoc {VERSION}">\n<title>{esc(title)}</title>\n'
                f'<script type="application/json" id="vtdoc-meta">{mj}</script>\n'
                f'<style>{STYLE}</style>\n</head>\n<body>\n<main>\n{body}</main>\n{srch}</body>\n</html>\n')

    def body(self, nodes):
        out = []
        in_q = False
        page = 0
        k = 0
        main_kinds = ('title', 'section', 'q', 'qline', 'opt', 'optfig', 'text', 'drop')

        def close_q():
            nonlocal in_q
            if in_q:
                out.append('</section>')
                in_q = False

        while k < len(nodes):
            t, tag, info = nodes[k]
            kind, pi = info['kind'], info.get('pi')
            b = self.block(pi, info.get('blk'))
            if kind in main_kinds and pi is not None and pi > page:
                if kind in ('title', 'section', 'q', 'drop'):
                    close_q()
                for p in range(page + 1, pi + 1):
                    out.append(f'<hr class="vt-page" data-page="{p + 1}" aria-hidden="true">')
                page = pi
            if kind == 'blank':
                k += 1
                continue
            ba = self.blk_attr(b)
            if kind == 'title':
                close_q()
                out.append(f'<h1{ba}>{self.inline(t, pi, info.get("blk"))}</h1>')
            elif kind == 'section':
                close_q()
                pts = info.get('points')
                pa = f' data-points="{pts:g}"' if pts is not None else ''
                out.append(f'<h2 class="vt-section"{pa}{ba}>{self.inline(t, pi, info.get("blk"))}</h2>')
            elif kind in ('q', 'drop'):
                close_q()
                num = info.get('num') or ''
                na = f' data-num="{attr(num)}"' if num else ''
                if kind == 'drop':
                    orig = ''.join(f'<p>{self.inline(l)}</p>' for l in info.get('lines') or [])
                    out.append(f'<section class="vt-q" data-drop{na}{ba}><h3>{esc(t)}</h3>'
                               f'<template class="vt-dropped">{orig}</template>')
                else:
                    out.append(f'<section class="vt-q"{na}{ba}><h3{self.lang(t)}>{self.inline(t, pi, info.get("blk"))}</h3>')
                in_q = True
            elif kind == 'opt':
                m = OPT_LABEL.match(t)
                la = ''
                if m:
                    lab = m.group(1) or m.group(2)
                    lab = chr(ord(lab) - 0xFEE0) if 'Ａ' <= lab <= 'Ｅ' else lab
                    la = f' data-label="{attr(lab)}"'
                out.append(f'<p class="vt-opt"{la}{self.lang(t)}{ba}>{self.inline(t, pi, info.get("blk"))}</p>')
            elif kind == 'optfig':
                out.append(f'<p class="vt-optfig"{ba}>{esc(t)}</p>')
            elif kind == 'fig':
                out.append(self.figure(pi, b, info.get('how', 'mark'), info.get('role')))
            elif kind == 'table':
                how = info.get('how', 'table')
                # 後面接著的表格主體（tbltext）：保留表格時是 <table border="1"> 那一行，改成條列時是條列文字
                body = []
                j = k + 1
                while j < len(nodes) and nodes[j][2]['kind'] == 'tbltext' and nodes[j][2].get('blk') == info.get('blk'):
                    body.append(nodes[j][0])
                    j += 1
                html_lines = [l for l in body if '<table' in l]
                src = html_lines or [layout.to_paren(layout.rows_html(layout.table_rows(self.pages[pi], b)[1]))]
                out.append(self.table(src, b, how, hidden=how != 'table' or not html_lines))
                for l in body:
                    if '<table' not in l:
                        out.append(f'<p class="vt-tbltext"{ba}>{self.inline(l, pi, info.get("blk"))}</p>')
                k = j
                continue
            elif kind == 'tbltext':
                out.append(f'<p class="vt-tbltext"{ba}>{self.inline(t, pi, info.get("blk"))}</p>')
            elif kind == 'figtext':
                out.append(f'<p class="vt-figtext"{ba}>{self.inline(t, pi, info.get("blk"))}</p>')
            elif kind == 'figdesc':
                out.append(f'<p class="vt-figdesc"{ba}>{self.inline(t, pi, info.get("blk"))}</p>')
            else:  # text、qline、mark
                cls = ' class="vt-item"' if layout.SUBITEM.match(t) else ''
                out.append(f'<p{cls}{self.lang(t)}{ba}>{self.inline(t, pi, info.get("blk"))}</p>')
            k += 1
        close_q()
        return '\n'.join(out) + '\n'


class TableParser(HTMLParser):
    """<table border="1">…</table>（辨識套件產生或手打的）→ [[{tag, text, colspan, rowspan}]]。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.cell = [], None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'tr':
            self.rows.append([])
        elif tag in ('td', 'th'):
            if not self.rows:
                self.rows.append([])

            def num(k):
                try:
                    return max(1, int(a.get(k) or 1))
                except ValueError:
                    return 1
            self.cell = dict(tag=tag, text='', colspan=num('colspan'), rowspan=num('rowspan'))
            self.rows[-1].append(self.cell)
        elif tag == 'br' and self.cell is not None:
            self.cell['text'] += ' '

    def handle_endtag(self, tag):
        if tag in ('td', 'th'):
            self.cell = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell['text'] += data

    @classmethod
    def parse(cls, s):
        p = cls()
        p.feed(s)
        for r in p.rows:
            for c in r:
                c['text'] = c['text'].strip()
        return p.rows


def build(d, pages, blocks, images=True, source=True):
    """試卷資料夾 d 的版面 → 中繼檔 HTML（字串）。source：原卷放進去（nc 等工具對照原卷用）。"""
    d = Path(d)
    out = d.parent
    src = next((f for f in out.glob('來源.*')), None)
    name = re.sub(r'^\d{8}-\d{6}_', '', out.name)
    first = next((json.loads(f.read_text(encoding='utf-8')) for f in sorted(d.glob('p01.json'))), {})
    meta = dict(format='vtdoc', version=VERSION, title='', subject='',
                source=dict(name=name, kind=(src.suffix[1:].lower() if src else ''), pages=len(pages)),
                created=datetime.now().astimezone().isoformat(timespec='seconds'),
                history=[dict(tool='vi-tools 辨識套件', time=datetime.now().astimezone().isoformat(timespec='seconds'),
                              note='版面調整畫面產生')],
                project=dict(tool='辨識套件', folder=out.name))
    if src is not None:
        meta['source']['sha256'] = hashlib.sha256(src.read_bytes()).hexdigest()
    if first.get('src_page'):
        meta['source']['first_page'] = first['src_page']
    nodes_title = next((b for bl in blocks for b in bl if b['type'] == 'title'), None)
    if nodes_title is not None:
        pi = next(k for k, bl in enumerate(blocks) if nodes_title in bl)
        lines = layout.block_lines(pages[pi], nodes_title)
        meta['title'] = layout.to_paren(lines[0]) if lines else name
    meta['title'] = meta['title'] or name
    # 找不到原檔（11-07 以前辨識的資料夾沒留 來源.*）→ src=True：放每頁的辨識圖
    return Doc(d, pages, blocks, images, (src if src is not None else True) if source else None).build(meta)
