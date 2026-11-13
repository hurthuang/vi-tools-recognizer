r"""辨識套件自己更新程式（app\ 資料夾）。0.4.3 起。

大部分新版只改 app\（Python 程式與網頁，約 3 MB），不用重新下載整包（300～530 MB）。
GitHub Release 多附兩個檔（tools\build.py --zip 產生）：
  update.json                          {version, runtime, app: {name, size, sha256}}
  vi-tools-recognizer-<版本>-app.zip   app\ 資料夾（zip 裡是 app/...）
runtime 是執行環境（runtime\python 的套件、runtime\models）的版本：裝的是 runtime\runtime.txt（沒有這個檔 = 1）。
新版要的 runtime 比裝的新（加了套件、換了模型）→ 不能只換 app\，請使用者下載整包。

更新：下載 app zip → 核對大小與 sha256 → 舊的 app\ 備份成 app.bak\ → 新檔寫進 app\、刪掉新版沒有的檔
     （寫到一半失敗就從 app.bak\ 還原）→ 用同樣的方式重新啟動自己（同一個連接埠）。輸出\ 不會動到。
"""
import hashlib
import io
import json
import os
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from main import VERSION

REPO = 'hurthuang/vi-tools-recognizer'
# 測試用：VITOOLS_UPDATE_API 指到假的 Release JSON（格式同 GitHub API：tag_name、html_url、assets[{name, browser_download_url}]）
API = os.environ.get('VITOOLS_UPDATE_API') or 'https://api.github.com/repos/%s/releases/latest' % REPO
APP = Path(__file__).resolve().parent
KIT = APP.parent
BAK = KIT / 'app.bak'
UA = {'User-Agent': 'vi-tools-recognizer/' + VERSION, 'Accept': 'application/vnd.github+json'}
_cache = {'t': 0, 'v': None}
_lock = threading.Lock()


def vtuple(v):
    try:
        return tuple(int(x) for x in str(v).lstrip('v').split('.'))
    except ValueError:
        return (0,)


def runtime_version():
    try:
        return int((KIT / 'runtime' / 'runtime.txt').read_text(encoding='utf-8').strip())
    except (OSError, ValueError):
        return 1


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
        return r.read()


def latest(force=False):
    """GitHub 上最新的 Release → {version, url, update（update.json 的內容，沒有就 None）, assets}；一小時查一次。"""
    with _lock:
        if not force and _cache['v'] and time.time() - _cache['t'] < 3600:
            return _cache['v']
        rel = json.loads(_get(API))
        assets = {a['name']: a['browser_download_url'] for a in rel.get('assets', [])}
        upd = json.loads(_get(assets['update.json'])) if 'update.json' in assets else None
        v = {'version': (upd or {}).get('version') or rel.get('tag_name', '').lstrip('v'), 'url': rel.get('html_url'),
             'update': upd, 'assets': assets}
        _cache.update(t=time.time(), v=v)
        return v


def status(force=False):
    """{version, latest, newer, can_update, need_full, url}；查不到（沒網路）→ 多一個 error。"""
    out = {'version': VERSION, 'runtime': runtime_version(), 'latest': None, 'newer': False, 'can_update': False,
           'need_full': False, 'url': 'https://github.com/%s/releases/latest' % REPO}
    try:
        L = latest(force)
    except Exception as e:  # noqa: BLE001
        out['error'] = '查不到新版（%s）' % type(e).__name__
        return out
    out['latest'], out['url'] = L['version'], L['url'] or out['url']
    out['newer'] = vtuple(L['version']) > vtuple(VERSION)
    u = L['update']
    if out['newer']:
        ok = bool(u and u.get('app') and u['app'].get('name') in L['assets'] and int(u.get('runtime', 1)) <= runtime_version())
        out['can_update'], out['need_full'] = ok, not ok
    return out


def _safe(name):
    p = Path(name)
    return name.startswith('app/') and not p.is_absolute() and '..' not in p.parts and ':' not in name


def apply():
    """下載並換掉 app\\。成功回 {version}；失敗丟例外（app\\ 不變或已還原）。"""
    st = status(force=True)
    if not st['newer']:
        raise RuntimeError('已經是最新版（%s）' % VERSION)
    if not st['can_update']:
        raise RuntimeError('這一版要下載整包（執行環境也換了）：%s' % st['url'])
    L = latest()
    info = L['update']['app']
    data = _get(L['assets'][info['name']], timeout=300)
    if len(data) != int(info['size']) or hashlib.sha256(data).hexdigest() != info['sha256'].lower():
        raise RuntimeError('下載的檔案不完整（大小或雜湊值不符），沒有更新')
    z = zipfile.ZipFile(io.BytesIO(data))
    names = [n for n in z.namelist() if not n.endswith('/')]
    if not names or not all(_safe(n) for n in names) or 'app/main.py' not in names:
        raise RuntimeError('更新檔的內容不對，沒有更新')
    if BAK.exists():
        shutil.rmtree(BAK)
    shutil.copytree(APP, BAK, ignore=shutil.ignore_patterns('__pycache__'))
    keep = {n[4:].replace('/', os.sep) for n in names}
    try:
        for n in names:
            dst = APP / n[4:]
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(z.read(n))
        for p in sorted(APP.rglob('*'), reverse=True):  # 新版沒有的檔（舊版留下的）刪掉
            rel = str(p.relative_to(APP))
            if p.is_file() and '__pycache__' not in p.parts and rel not in keep:
                p.unlink()
    except Exception:
        _restore()
        raise
    return {'version': L['update']['version']}


def _restore():
    for p in BAK.rglob('*'):
        if p.is_file():
            dst = APP / p.relative_to(BAK)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dst)


def restart(httpd, delay=1.0):
    """回應送出後：用同樣的 Python 再開一個辨識套件（同一個連接埠、不開瀏覽器），這個結束。
    在命令提示字元裡開的，新的會接著用同一個視窗；桌面版開的會留在同一個 Job 物件裡，app 關掉時一起關。"""
    def go():
        time.sleep(delay)
        env = dict(os.environ, VITOOLS_PORT=str(httpd.server_address[1]))
        subprocess.Popen([sys.executable, '-X', 'utf8', str(APP / 'main.py'), '--no-browser'], cwd=str(KIT), env=env)
        httpd.shutdown()
        os._exit(0)
    threading.Thread(target=go, daemon=True).start()
