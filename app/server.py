r"""本機網頁服務：只聽 127.0.0.1，只接受同一來源的請求。

GET  /                    操作頁面
GET  /api/info            版本
GET  /api/ocr             給 vi-tools 偵測（版面整理等頁面找本機的辨識套件）
POST /api/ocr             JSON {mode: auto|math|text, image | pdf_sha256＋[pdf]＋page＋box} → {lines[]}、{tex, score} 或 {need_pdf}（exam/region.py）
POST /api/doc             JSON {name, data(base64), pages} → {id}：整份原卷辨識（試卷模式），給 vi-tools 版面整理開 PDF／圖
GET  /api/doc/<id>        {state, done, total, eta, log[]}；state＝running／done／stopped／error
GET  /api/doc/<id>/vtdoc  中繼檔（含原卷與位置；沒有 MathML，版面整理存檔時轉）
POST /api/doc/<id>/cancel 做完這一頁就停
POST /api/word            JSON {name, data(base64)} → {pdf, docx}（base64）：用這台電腦的 Word 把 .doc／.docx 另存 PDF（0.4.1）
POST /api/omr             JSON {image(base64／dataURL), engines?} → {results:[{engine, musicxml | error, sec}], spacing, scale, sec}：
                          辨識一塊譜例（Audiveris、homr 一起跑），給 vi-tools 文字辨識頁的樂譜模式（0.4.2，omr_region.py）；試卷版沒有 → 409
                          只有 /api/ocr、/api/doc、/api/word、/api/omr 接受 vi-tools 網頁（線上版、桌面版、本機副本）的跨來源請求（CORS）
POST /api/jobs            JSON {name, data(base64), pages, refs:[{name, data}]} → {id}
GET  /api/jobs/<id>       {state, log[], report, files[]}
GET  /api/jobs/<id>/file?name=…   下載輸出檔
POST /api/jobs/<id>/open  用檔案總管開輸出資料夾
/exam/                   試卷版面調整畫面（資料在 輸出\<資料夾>\試卷資料\）
  GET  /exam/api/exams、/exam/api/exam/<資料夾>、/exam/page/<資料夾>/<頁>.png
  GET  /exam/api/tactile/<資料夾>   附圖 → 觸圖製作的編輯檔（.tactile）
  POST /exam/api/(save|reocr|reset)/<資料夾>
"""
import base64
import re
import json
import os
import subprocess
import threading
import time
import traceback
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import job
from prepare import IMG_EXT
from main import VERSION

# 可以呼叫 /api/ocr 的 vi-tools 網頁：線上版、桌面版（vitools.local）、本機的副本（127.0.0.1、localhost）
VITOOLS_ORIGIN = re.compile(r'^(https://hurthuang\.github\.io|https://vitools\.local|http://(127\.0\.0\.1|localhost)(:\d+)?)$')
OCR_MAX = 30 * 1024 * 1024
DOC_MAX = 200 * 1024 * 1024    # 整份原卷（base64 JSON）

WEB = Path(__file__).resolve().parent / 'web'
EXAM_WEB = WEB / 'exam'
JOBS = {}
PORT_RANGE = range(18760, 18780)
TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
         '.musicxml': 'application/vnd.recordare.musicxml+xml', '.txt': 'text/plain; charset=utf-8', '.png': 'image/png'}


class Job:
    def __init__(self):
        self.id = uuid.uuid4().hex[:12]
        self.state = 'running'
        self.log = []
        self.report = ''
        self.text = ''
        self.workdir = None
        self.files = []
        self.exam = ''
        self.started = time.time()
        self.done = 0           # 做完幾頁（張）
        self.total = 0          # 共幾頁；0＝還不知道
        self.first = None       # 開始做第一頁的時間（估剩下時間用，不算載入模型）
        self.last = None
        self.cancel = threading.Event()

    def add(self, msg):
        self.log.append(str(msg))

    def fail(self, e):
        if self.cancel.is_set():   # 按了停止、一頁都還沒做完：不算錯誤
            self.add('已停止。')
            self.state = 'stopped'
            return
        self.add('發生錯誤：%s' % e)
        (self.workdir / '錯誤.txt').write_text(traceback.format_exc(), encoding='utf-8')
        self.state = 'error'

    def progress(self, done, total):
        now = time.time()
        if self.first is None:
            self.first = now
        if done != self.done:
            self.last = now     # 最近做完一頁的時間
        self.done, self.total = done, total

    def status(self):
        now = time.time()
        eta = None
        if self.first is not None and 0 < self.done < self.total:
            avg = (self.last - self.first) / self.done        # 每頁平均
            eta = max(round(avg * (self.total - self.done) - (now - self.last)), 5)
        return {'state': self.state, 'log': self.log, 'report': self.report, 'text': self.text, 'files': self.files,
                'workdir': str(self.workdir), 'exam': self.exam, 'done': self.done, 'total': self.total,
                'elapsed': round(now - self.started), 'eta': eta, 'stopping': self.cancel.is_set()}


def start_job(payload):
    if payload.get('mode', 'score') == 'score':
        import engines
        if not engines.music_available():
            raise ValueError('這個版本是試卷版，沒有樂譜辨識。要辨識樂譜請下載完整版（vi-tools-recognizer-…-portable.zip）。')
    j = Job()
    JOBS[j.id] = j
    name = os.path.basename(payload.get('name') or 'input.pdf')
    workdir = job.new_workdir(name)
    j.workdir = workdir
    src = workdir / ('來源' + Path(name).suffix.lower())
    src.write_bytes(base64.b64decode(payload['data']))
    refs = []
    for k, r in enumerate(payload.get('refs') or []):
        p = workdir / ('手打參考%d%s' % (k + 1, Path(r.get('name') or 'ref.musicxml').suffix.lower() or '.musicxml'))
        p.write_bytes(base64.b64decode(r['data']))
        refs.append(p)

    def work_text():
        try:
            r = job.run_text(src, payload.get('pages') or '', bool(payload.get('textLayer')), workdir=workdir, log=j.add,
                             display_name=name, progress=j.progress, cancel=j.cancel)
            j.text = r['text']
            j.report = r['report']
            j.files = [{'name': '文字.txt', 'label': '辨識出的文字（文字檔）'}, {'name': '報告.txt', 'label': '報告（文字檔）'}]
            j.state = 'done'
        except Exception as e:  # noqa: BLE001
            j.fail(e)

    def work():
        try:
            r = job.run(src, payload.get('pages') or '', refs, workdir=workdir, log=j.add, display_name=name,
                        progress=j.progress, cancel=j.cancel)
            j.report = r['report']
            files = []
            for it in r['items']:
                for eng, p in it['results'].items():
                    if p:
                        files.append({'name': p.name, 'label': '%s：%s 的 MusicXML' % (it['label'], eng)})
            files.append({'name': '報告.txt', 'label': '報告（文字檔）'})
            j.files = files
            j.state = 'done'
        except Exception as e:  # noqa: BLE001
            j.fail(e)

    def work_exam():
        try:
            r = job.run_exam(src, payload.get('pages') or '', payload.get('textLayer', True) is not False, workdir=workdir,
                             log=j.add, display_name=name, progress=j.progress, cancel=j.cancel)
            j.text = r['text']
            j.report = r['report']
            j.exam = workdir.name
            j.files = [{'name': '試卷.txt', 'label': '試卷文字（文字檔，算式用 \\( \\) 寫法）'},
                       {'name': '報告.txt', 'label': '報告（文字檔）'}]
            j.state = 'done'
        except Exception as e:  # noqa: BLE001
            j.fail(e)

    mode = payload.get('mode')
    threading.Thread(target={'text': work_text, 'exam': work_exam}.get(mode, work), daemon=True).start()
    return j


class Handler(BaseHTTPRequestHandler):
    server_version = 'HaodianOMR/' + VERSION

    def log_message(self, fmt, *args):  # 安靜
        pass

    def _origin_ok(self):
        o = self.headers.get('Origin')
        if not o:
            return True
        host = self.headers.get('Host', '')
        return o in ('http://' + host,)

    def _vitools_origin(self):
        """vi-tools 網頁（線上版、桌面版、本機副本）可以呼叫 /api/ocr；回傳要放進 CORS 的來源，不接受 → None。"""
        o = self.headers.get('Origin') or ''
        return o if VITOOLS_ORIGIN.match(o) else None

    def _cors(self, o):
        if not o:
            return None
        return {'Access-Control-Allow-Origin': o, 'Vary': 'Origin', 'Access-Control-Allow-Private-Network': 'true',
                'Access-Control-Allow-Methods': 'GET, POST', 'Access-Control-Allow-Headers': 'Content-Type',
                'Access-Control-Max-Age': '600'}

    def do_OPTIONS(self):
        u = urllib.parse.urlparse(self.path)
        o = self._vitools_origin()
        p = u.path.rstrip('/')
        if o and (p in ('/api/ocr', '/api/doc', '/api/word', '/api/omr') or p.startswith('/api/doc/')):
            self.send_response(204)
            for k, v in self._cors(o).items():
                self.send_header(k, v)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        self._send(403, {'error': '不接受其他網站的請求'})

    def _ocr(self, method, body=b''):
        """vi-tools 的辨識：GET /api/ocr（偵測）、POST /api/ocr {image, mode}。只聽 127.0.0.1，網頁只接受 vi-tools。"""
        o = self._vitools_origin()
        if self.headers.get('Origin') and not o:
            return self._send(403, {'error': '不接受其他網站的請求'})
        extra = self._cors(o)
        if method == 'GET':
            import engines
            import word
            return self._send(200, {'name': 'vi-tools 辨識套件', 'version': VERSION, 'ocr': ['auto', 'math', 'text'], 'doc': True,
                                    'word': word.available(), 'music': engines.music_available(), 'omr': engines.music_available()}, extra=extra)
        try:
            req = json.loads(body.decode('utf-8'))
            from exam import region
            return self._send(200, region.recognize(req), extra=extra)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._send(400 if isinstance(e, ValueError) else 500, {'error': '%s: %s' % (type(e).__name__, e)}, extra=extra)

    def _omr(self, body):
        """vi-tools 的樂譜辨識：POST /api/omr {image} → 兩個引擎的 MusicXML（omr_region.py）。"""
        o = self._vitools_origin()
        if self.headers.get('Origin') and not o:
            return self._send(403, {'error': '不接受其他網站的請求'})
        extra = self._cors(o)
        import engines
        if not engines.music_available():
            return self._send(409, {'error': '這是試卷版，沒有樂譜辨識（要用完整版的辨識套件）'}, extra=extra)
        try:
            import omr_region
            return self._send(200, omr_region.recognize(json.loads(body.decode('utf-8'))), extra=extra)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._send(400 if isinstance(e, ValueError) else 500, {'error': '%s: %s' % (type(e).__name__, e)}, extra=extra)

    def _doc(self, method, rest, body=b''):
        """vi-tools 版面整理開 PDF／圖：整份辨識成中繼檔。rest＝/api/doc 後面的路徑。"""
        o = self._vitools_origin()
        if self.headers.get('Origin') and not o:
            return self._send(403, {'error': '不接受其他網站的請求'})
        extra = self._cors(o)
        try:
            if method == 'POST' and not rest:
                req = json.loads(body.decode('utf-8'))
                name = os.path.basename(req.get('name') or 'input.pdf')
                if not name.lower().endswith(('.pdf',) + IMG_EXT):
                    return self._send(400, {'error': '只能辨識 PDF 或圖檔'}, extra=extra)
                if name.lower().endswith('.pdf'):    # 之後「重新辨識一塊」不用再送一次 PDF
                    from exam import region
                    region.remember_pdf(base64.b64decode(req['data']))
                j = start_job({'name': name, 'data': req['data'], 'pages': req.get('pages') or '', 'mode': 'exam',
                               'textLayer': req.get('textLayer', True)})
                j.from_vitools = True
                return self._send(200, {'id': j.id}, extra=extra)
            j = JOBS.get(rest[0]) if rest else None
            if not j or not getattr(j, 'from_vitools', False):
                return self._send(404, {'error': '找不到這個工作'}, extra=extra)
            if method == 'GET' and len(rest) == 1:
                s = j.status()
                return self._send(200, {k: s[k] for k in ('state', 'done', 'total', 'elapsed', 'eta', 'stopping')} |
                                  {'log': s['log'][-5:]}, extra=extra)
            if method == 'GET' and rest[1:] == ['vtdoc']:
                if j.state not in ('done', 'stopped') or not j.exam:
                    return self._send(409, {'error': '還沒辨識完'}, extra=extra)
                from exam import review
                d = job.exam_dir(j.exam)
                if not d:
                    return self._send(404, {'error': '一頁都沒有辨識完'}, extra=extra)
                return self._send(200, review.export_vtdoc(d, True, True), 'text/html; charset=utf-8', extra=extra)
            if method == 'POST' and rest[1:] == ['cancel']:
                j.cancel.set()
                j.add('停止中：做完這一頁就停，做完的頁會保留。')
                return self._send(200, {'ok': True}, extra=extra)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._send(400 if isinstance(e, (ValueError, KeyError)) else 500,
                              {'error': '%s: %s' % (type(e).__name__, e)}, extra=extra)
        return self._send(404, {'error': 'not found'}, extra=extra)

    def _word(self, body):
        '''vi-tools 版面整理開 Word 檔：用這台電腦的 Word 另存 PDF（和 .docx）當原卷。'''
        o = self._vitools_origin()
        if self.headers.get('Origin') and not o:
            return self._send(403, {'error': '不接受其他網站的請求'})
        extra = self._cors(o)
        try:
            req = json.loads(body.decode('utf-8'))
            import word
            r = word.convert(base64.b64decode(req['data']), os.path.basename(req.get('name') or 'input.docx'))
            return self._send(200, {k: base64.b64encode(v).decode('ascii') for k, v in r.items()}, extra=extra)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._send(400 if isinstance(e, (ValueError, KeyError, RuntimeError)) else 500, {'error': str(e)}, extra=extra)

    def _send(self, code, body, ctype='application/json; charset=utf-8', extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        parts = [p for p in u.path.split('/') if p]
        if not parts or parts == ['index.html']:
            return self._file(WEB / 'index.html')
        if parts[0] in ('app.js', 'style.css', 'progress.js') and len(parts) == 1:
            return self._file(WEB / parts[0])
        if parts and parts[0] == 'exam':
            return self._exam_get(u, [urllib.parse.unquote(p) for p in parts[1:]])
        if parts == ['api', 'ocr']:
            return self._ocr('GET')
        if parts[:2] == ['api', 'doc']:
            return self._doc('GET', parts[2:])
        if parts == ['api', 'info']:
            import engines
            return self._send(200, {'name': 'vi-tools 辨識套件', 'version': VERSION, 'engines': ['Audiveris', 'homr'], 'music': engines.music_available(),
                                    'modes': ['score', 'text', 'exam']})
        if len(parts) >= 3 and parts[:2] == ['api', 'jobs']:
            j = JOBS.get(parts[2])
            if not j:
                return self._send(404, {'error': '找不到這個工作'})
            if len(parts) == 3:
                return self._send(200, j.status())
            if parts[3] == 'file':
                name = urllib.parse.parse_qs(u.query).get('name', [''])[0]
                if name not in [f['name'] for f in j.files]:
                    return self._send(404, {'error': '沒有這個檔案'})
                p = j.workdir / name
                data = p.read_bytes()
                disp = "attachment; filename*=UTF-8''" + urllib.parse.quote(name)
                return self._send(200, data, TYPES.get(p.suffix.lower(), 'application/octet-stream'), {'Content-Disposition': disp})
        self._send(404, {'error': 'not found'})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        parts = [p for p in u.path.split('/') if p]
        n = int(self.headers.get('Content-Length') or 0)
        if parts == ['api', 'ocr']:
            if n > OCR_MAX:
                return self._send(413, {'error': '圖太大'}, extra=self._cors(self._vitools_origin()))
            return self._ocr('POST', self.rfile.read(n) if n else b'')
        if parts[:2] == ['api', 'doc']:
            if n > DOC_MAX:
                return self._send(413, {'error': '檔案太大'}, extra=self._cors(self._vitools_origin()))
            return self._doc('POST', parts[2:], self.rfile.read(n) if n else b'')
        if parts == ['api', 'omr']:
            if n > OCR_MAX:
                return self._send(413, {'error': '圖太大'}, extra=self._cors(self._vitools_origin()))
            return self._omr(self.rfile.read(n) if n else b'')
        if parts == ['api', 'word']:
            if n > DOC_MAX:
                return self._send(413, {'error': '檔案太大'}, extra=self._cors(self._vitools_origin()))
            return self._word(self.rfile.read(n) if n else b'')
        if not self._origin_ok():
            return self._send(403, {'error': '不接受其他網站的請求'})
        body = self.rfile.read(n) if n else b''
        if parts[:2] == ['exam', 'api']:
            return self._exam_post([urllib.parse.unquote(p) for p in parts[2:]], body)
        if parts == ['api', 'jobs']:
            try:
                payload = json.loads(body.decode('utf-8'))
                j = start_job(payload)
            except Exception as e:  # noqa: BLE001
                return self._send(400, {'error': str(e)})
            return self._send(200, {'id': j.id})
        if len(parts) == 4 and parts[:2] == ['api', 'jobs'] and parts[3] == 'cancel':
            j = JOBS.get(parts[2])
            if not j:
                return self._send(404, {'error': '找不到這個工作'})
            j.cancel.set()
            j.add('停止中：做完這一頁就停，做完的頁會保留。')
            return self._send(200, {'ok': True})
        if len(parts) == 4 and parts[:2] == ['api', 'jobs'] and parts[3] == 'open':
            j = JOBS.get(parts[2])
            if j and j.workdir and os.name == 'nt':
                subprocess.Popen(['explorer', str(j.workdir)])
                return self._send(200, {'ok': True})
            return self._send(404, {'error': '找不到'})
        self._send(404, {'error': 'not found'})

    # ---------- 試卷版面調整 ----------
    def _exam_get(self, u, rest):
        if not u.path.endswith('/') and not rest:
            return self._send(301, '', extra={'Location': '/exam/'})
        if not rest:
            return self._file(EXAM_WEB / 'index.html')
        if len(rest) == 1 and rest[0] in ('app.js', 'style.css', 'tex-svg.js'):
            return self._file(EXAM_WEB / rest[0])
        if rest == ['api', 'exams']:
            return self._send(200, job.exam_names())
        if len(rest) == 3 and rest[:2] == ['api', 'exam']:
            d = job.exam_dir(rest[2])
            if not d:
                return self._send(404, {'error': '找不到這份試卷'})
            from exam import review
            pages, blocks = review.exam_state(d)
            return self._send(200, review.api_state(pages, blocks))
        if len(rest) == 3 and rest[:2] == ['api', 'vtdoc']:  # 中繼檔（.vtdoc.html）
            d = job.exam_dir(rest[2])
            if not d:
                return self._send(404, {'error': '找不到這份試卷'})
            from exam import review
            q = urllib.parse.parse_qs(u.query)
            images = q.get('images', ['1'])[0] != '0'
            source = q.get('source', ['1'])[0] != '0'
            data = review.export_vtdoc(d, images, source)
            name = urllib.parse.quote(re.sub(r'^\d{8}-\d{6}_', '', rest[2]) + '.vtdoc.html')
            return self._send(200, data, 'text/html; charset=utf-8',
                              extra={'Content-Disposition': "attachment; filename=exam.vtdoc.html; filename*=UTF-8''" + name})
        if len(rest) == 3 and rest[:2] == ['api', 'tactile']:  # 附圖 → 觸圖製作的編輯檔
            d = job.exam_dir(rest[2])
            if not d:
                return self._send(404, {'error': '找不到這份試卷'})
            from exam import review
            data, n = review.export_tactile(d)
            if not n:
                return self._send(404, {'error': '沒有要做觸圖的附圖（輸出方式是「標記」的附圖才會匯出）'})
            name = urllib.parse.quote(re.sub(r'^\d{8}-\d{6}_', '', rest[2]) + '_附圖.tactile')
            return self._send(200, data, 'application/json',
                              extra={'Content-Disposition': "attachment; filename=figures.tactile; filename*=UTF-8''" + name,
                                     'X-Figures': str(n)})
        if len(rest) == 3 and rest[0] == 'page' and re.fullmatch(r'\d+\.png', rest[2]):
            d = job.exam_dir(rest[1])
            f = d and d / ('p%02d.png' % int(rest[2][:-4]))
            if f and f.exists():
                return self._send(200, f.read_bytes(), 'image/png')
        self._send(404, {'error': 'not found'})

    def _exam_post(self, rest, body):
        if len(rest) != 2 or rest[0] not in ('save', 'reocr', 'reset'):
            return self._send(404, {'error': 'not found'})
        d = job.exam_dir(rest[1])
        if not d:
            return self._send(404, {'error': '找不到這份試卷'})
        try:
            from exam import review
            req = json.loads(body.decode('utf-8')) if body else {}
            return self._send(200, review.handle(d, rest[0], req))
        except Exception as e:  # noqa: BLE001
            (d / '錯誤.txt').write_text(traceback.format_exc(), encoding='utf-8')
            return self._send(500, {'error': '%s: %s' % (type(e).__name__, e)})

    def _file(self, p):
        if not p.exists():
            return self._send(404, {'error': 'not found'})
        self._send(200, p.read_bytes(), TYPES.get(p.suffix.lower(), 'application/octet-stream'))


class Server(ThreadingHTTPServer):
    """Windows 上 SO_REUSEADDR 會讓第二個辨識套件綁到同一個連接埠（請求還是被舊的接走），改成獨佔。"""
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        import socket
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def serve(open_browser=True):
    httpd = None
    for port in PORT_RANGE:
        try:
            httpd = Server(('127.0.0.1', port), Handler)
            break
        except OSError:
            continue
    if not httpd:
        raise SystemExit('找不到可用的連接埠（18760～18779 都被佔用）')
    url = 'http://127.0.0.1:%d/' % httpd.server_address[1]
    print('vi-tools 辨識套件 %s 已啟動：%s' % (VERSION, url))
    print('用完關掉這個視窗即可結束。')
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
