"""本機網頁服務：只聽 127.0.0.1，只接受同一來源的請求。

GET  /                    操作頁面
GET  /api/info            版本（之後給 vi-tools 偵測用）
POST /api/jobs            JSON {name, data(base64), pages, refs:[{name, data}]} → {id}
GET  /api/jobs/<id>       {state, log[], report, files[]}
GET  /api/jobs/<id>/file?name=…   下載輸出檔
POST /api/jobs/<id>/open  用檔案總管開輸出資料夾
"""
import base64
import json
import os
import subprocess
import threading
import traceback
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import job
from main import VERSION

WEB = Path(__file__).resolve().parent / 'web'
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

    def add(self, msg):
        self.log.append(str(msg))


def start_job(payload):
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
                             display_name=name)
            j.text = r['text']
            j.report = r['report']
            j.files = [{'name': '文字.txt', 'label': '辨識出的文字（文字檔）'}, {'name': '報告.txt', 'label': '報告（文字檔）'}]
            j.state = 'done'
        except Exception as e:  # noqa: BLE001
            j.add('發生錯誤：%s' % e)
            (workdir / '錯誤.txt').write_text(traceback.format_exc(), encoding='utf-8')
            j.state = 'error'

    def work():
        try:
            r = job.run(src, payload.get('pages') or '', refs, workdir=workdir, log=j.add, display_name=name)
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
            j.add('發生錯誤：%s' % e)
            (workdir / '錯誤.txt').write_text(traceback.format_exc(), encoding='utf-8')
            j.state = 'error'

    threading.Thread(target=work_text if payload.get('mode') == 'text' else work, daemon=True).start()
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
        if parts[0] in ('app.js', 'style.css') and len(parts) == 1:
            return self._file(WEB / parts[0])
        if parts == ['api', 'info']:
            return self._send(200, {'name': '豪點樂譜辨識', 'version': VERSION, 'engines': ['Audiveris', 'homr']})
        if len(parts) >= 3 and parts[:2] == ['api', 'jobs']:
            j = JOBS.get(parts[2])
            if not j:
                return self._send(404, {'error': '找不到這個工作'})
            if len(parts) == 3:
                return self._send(200, {'state': j.state, 'log': j.log, 'report': j.report, 'text': j.text, 'files': j.files,
                                        'workdir': str(j.workdir)})
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
        if not self._origin_ok():
            return self._send(403, {'error': '不接受其他網站的請求'})
        u = urllib.parse.urlparse(self.path)
        parts = [p for p in u.path.split('/') if p]
        n = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(n) if n else b''
        if parts == ['api', 'jobs']:
            try:
                payload = json.loads(body.decode('utf-8'))
                j = start_job(payload)
            except Exception as e:  # noqa: BLE001
                return self._send(400, {'error': str(e)})
            return self._send(200, {'id': j.id})
        if len(parts) == 4 and parts[:2] == ['api', 'jobs'] and parts[3] == 'open':
            j = JOBS.get(parts[2])
            if j and j.workdir and os.name == 'nt':
                subprocess.Popen(['explorer', str(j.workdir)])
                return self._send(200, {'ok': True})
            return self._send(404, {'error': '找不到'})
        self._send(404, {'error': 'not found'})

    def _file(self, p):
        if not p.exists():
            return self._send(404, {'error': 'not found'})
        self._send(200, p.read_bytes(), TYPES.get(p.suffix.lower(), 'application/octet-stream'))


def serve(open_browser=True):
    httpd = None
    for port in PORT_RANGE:
        try:
            httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
            break
        except OSError:
            continue
    if not httpd:
        raise SystemExit('找不到可用的連接埠（18760～18779 都被佔用）')
    url = 'http://127.0.0.1:%d/' % httpd.server_address[1]
    print('豪點樂譜辨識 %s 已啟動：%s' % (VERSION, url))
    print('用完關掉這個視窗即可結束。')
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
