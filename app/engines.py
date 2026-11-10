"""呼叫兩個辨識工具（都在本機跑，不用網路）。

Audiveris：runtime\\audiveris\\Audiveris.exe（自帶 Java）；匯出 .mxl，解開成 .musicxml。
homr：用同一個可攜 Python 另開行程跑（模型已在套件資料夾裡）。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDIVERIS = ROOT / 'runtime' / 'audiveris' / 'Audiveris.exe'
NO_WINDOW = 0x08000000 if os.name == 'nt' else 0


def music_available():
    '''樂譜辨識的工具在不在（試卷版沒有 Audiveris、homr）。'''
    import importlib.util
    return AUDIVERIS.exists() and importlib.util.find_spec('homr') is not None


def _run(cmd, log_path, timeout, env=None):
    with open(log_path, 'w', encoding='utf-8', errors='replace') as lf:
        p = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, timeout=timeout, env=env,
                           creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
    return p.returncode


def audiveris(img, workdir, timeout=900):
    """回傳 .musicxml 路徑；失敗回傳 None。"""
    if not AUDIVERIS.exists():
        raise FileNotFoundError('找不到 Audiveris：' + str(AUDIVERIS))
    out = Path(workdir) / 'audiveris'
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(img).stem
    log = Path(workdir) / ('audiveris_%s.log' % stem)
    _run([str(AUDIVERIS), '-batch', '-transcribe', '-export', '-output', str(out), '--', str(img)], log, timeout)
    mxl = out / (stem + '.mxl')
    if not mxl.exists():
        cands = sorted(out.glob(stem + '*.mxl'))
        if not cands:
            return None
        mxl = cands[0]
    dest = Path(workdir) / ('%s_Audiveris.musicxml' % stem)
    with zipfile.ZipFile(mxl) as z:
        names = [n for n in z.namelist() if n.lower().endswith('.xml') and not n.startswith('META-INF')]
        dest.write_bytes(z.read(names[0]))
    return dest


def _ascii_tempdir():
    """OpenCV 在 Windows 讀不了中文路徑的圖，homr 要在純英文路徑的資料夾裡跑。"""
    for base in (tempfile.gettempdir(), os.path.join(os.environ.get('PUBLIC', r'C:\Users\Public'), 'Temp'), r'C:\Temp'):
        if base.isascii():
            os.makedirs(base, exist_ok=True)
            return tempfile.mkdtemp(prefix='haodian-omr-', dir=base)
    raise RuntimeError('找不到純英文路徑的暫存資料夾，homr 無法讀圖')


def homr(img, workdir, timeout=900):
    stem = Path(img).stem
    tmp = Path(_ascii_tempdir())
    try:
        src = tmp / 'score.png'
        shutil.copyfile(img, src)
        log = Path(workdir) / ('homr_%s.log' % stem)
        env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONWARNINGS='ignore')
        code = 'import sys; from homr.main import main; sys.argv = ["homr", sys.argv[1]]; main()'
        _run([sys.executable, '-X', 'utf8', '-W', 'ignore', '-c', code, str(src)], log, timeout, env=env)
        res = tmp / 'score.musicxml'
        if not res.exists():
            return None
        dest = Path(workdir) / ('%s_homr.musicxml' % stem)
        shutil.copyfile(res, dest)
        return dest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
