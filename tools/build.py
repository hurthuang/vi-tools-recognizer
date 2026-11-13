"""組出可攜版資料夾 dist\\vi-tools辨識套件\\ 並壓成 zip。

需要（放在 build\\）：
  dl\\python-3.12.10-embed-amd64.zip   python.org 的嵌入式 Python
  site-packages\\                      python -m pip install --target build\\site-packages homr==0.7.0 onnxruntime "pypdfium2>=4,<6"
                                       （homr 的模型要先下載進去：跑一次 homr --init，或從已下載的地方複製 .onnx）
  audiveris\\                          Audiveris 5.11.0 Console 版 msi 用 msiexec /a 解開後的 Audiveris 資料夾
  models\\mfd、models\\mfr              試卷辨識的算式模型（Pix2Text mfd-1.5-onnx、mfr-1.5-onnx，從 %APPDATA%\\pix2text\\1.1 複製）
  site-packages 另外要有 tokenizers   試卷辨識的 MFR 用（不需要 huggingface_hub）
用法：python tools\\build.py [--zip] [--exam]
  --exam：試卷版（不含樂譜辨識的 Audiveris、homr，約 300 MB），組在 dist\\exam\\vi-tools辨識套件\\，
          zip 是 vi-tools-recognizer-版本-exam-portable.zip；不加是完整版
  --zip 另外產生只有 app\\ 的 vi-tools-recognizer-版本-app.zip 與 update.json（辨識套件自己更新用，app\\update.py）。
  Release 要附上這兩個檔；試卷版、完整版的 app\\ 一樣，只要一份。
"""
import hashlib
import json
import shutil
import sys
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
B = ROOT / 'build'
NAME = 'vi-tools辨識套件'
EXAM = '--exam' in sys.argv
DIST = ROOT / 'dist' / ('exam' if EXAM else '') / NAME
PY_ZIP = B / 'dl' / 'python-3.12.10-embed-amd64.zip'
# 執行環境（runtime\python 的套件、runtime\models、Audiveris）的版本：加了套件、換了模型就加 1。
# 裝的比新版要的舊 → 辨識套件不能只換 app\，請使用者下載整包（app\update.py）
RUNTIME = 1


def copytree(src, dst):
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))


def main():
    for need in (PY_ZIP, B / 'site-packages', *(() if EXAM else (B / 'audiveris' / 'Audiveris.exe',)), B / 'site-packages' / 'tokenizers',
                 B / 'models' / 'mfd' / 'pix2text-mfd-1.5.onnx', B / 'models' / 'mfr' / 'decoder_model.onnx'):
        if not need.exists():
            sys.exit('缺少 ' + str(need))
    DIST.mkdir(parents=True, exist_ok=True)
    py = DIST / 'runtime' / 'python'
    if not (py / 'python.exe').exists():
        py.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(PY_ZIP) as z:
            z.extractall(py)
    # ._pth：嵌入式 Python 的搜尋路徑（只有這些）
    pth = next(py.glob('python3*._pth'))
    pth.write_text('python312.zip\n.\nLib\\site-packages\n..\\..\\app\nimport site\n', encoding='utf-8')
    sp = py / 'Lib' / 'site-packages'
    if not sp.exists():
        print('複製 site-packages…')
        # 試卷版：樂譜辨識的 homr（含模型約 150 MB）不放
        ign = ('__pycache__', '*.pyc', 'bin') + (('homr', 'homr-*') if EXAM else ())
        shutil.copytree(B / 'site-packages', sp, ignore=shutil.ignore_patterns(*ign))
    au = DIST / 'runtime' / 'audiveris'
    if not EXAM and not (au / 'Audiveris.exe').exists():
        print('複製 Audiveris…')
        copytree(B / 'audiveris', au)
    if not (sp / 'tokenizers').exists():
        print('複製 tokenizers…')
        for p in (B / 'site-packages').glob('tokenizers*'):
            shutil.copytree(p, sp / p.name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    mo = DIST / 'runtime' / 'models'
    if not (mo / 'mfr' / 'decoder_model.onnx').exists():
        print('複製算式模型…')
        copytree(B / 'models', mo)
    copytree(ROOT / 'app', DIST / 'app')
    for f in ('vi-tools辨識套件.cmd', '說明.txt'):
        shutil.copyfile(ROOT / 'dist-files' / f, DIST / f)
    copytree(ROOT / 'dist-files' / '授權', DIST / '授權')
    (DIST / '輸出').mkdir(exist_ok=True)
    (DIST / 'runtime' / 'runtime.txt').write_text(str(RUNTIME), encoding='utf-8')
    print('完成：', DIST)
    if '--zip' in sys.argv:
        # GitHub Release 的附檔名稱會去掉中文，zip 用英文名稱（解開來的資料夾還是 vi-tools辨識套件）
        ver = re.search(r"^VERSION = '([^']+)'", (ROOT / 'app' / 'main.py').read_text(encoding='utf-8'), re.M).group(1)
        z = ROOT / 'dist' / ('vi-tools-recognizer-%s-%sportable.zip' % (ver, 'exam-' if EXAM else ''))
        print('壓縮中…')
        with zipfile.ZipFile(z, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for p in DIST.rglob('*'):
                rel = p.relative_to(DIST.parent)
                if p.is_file() and rel.parts[1] != '輸出' and '__pycache__' not in rel.parts and p.name != 'sitecustomize.py':
                    zf.write(p, rel.as_posix())
        print('zip：', z, '%.0f MB' % (z.stat().st_size / 1e6))
        app_zip(ver)


def app_zip(ver):
    """只有 app\\ 的 zip（裡面是 app/...）＋ update.json"""
    z = ROOT / 'dist' / ('vi-tools-recognizer-%s-app.zip' % ver)
    with zipfile.ZipFile(z, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in sorted((ROOT / 'app').rglob('*')):
            rel = p.relative_to(ROOT)
            if p.is_file() and '__pycache__' not in rel.parts and p.suffix != '.pyc':
                zf.write(p, rel.as_posix())
    data = z.read_bytes()
    info = {'version': ver, 'runtime': RUNTIME,
            'app': {'name': z.name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}}
    (ROOT / 'dist' / 'update.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    print('app zip：', z, '%.1f MB' % (len(data) / 1e6), '；update.json')


if __name__ == '__main__':
    main()
