"""組出可攜版資料夾 dist\\vi-tools辨識套件\\ 並壓成 zip。

需要（放在 build\\）：
  dl\\python-3.12.10-embed-amd64.zip   python.org 的嵌入式 Python
  site-packages\\                      python -m pip install --target build\\site-packages homr==0.7.0 onnxruntime "pypdfium2>=4,<6"
                                       （homr 的模型要先下載進去：跑一次 homr --init，或從已下載的地方複製 .onnx）
  audiveris\\                          Audiveris 5.11.0 Console 版 msi 用 msiexec /a 解開後的 Audiveris 資料夾
  models\\mfd、models\\mfr              試卷辨識的算式模型（Pix2Text mfd-1.5-onnx、mfr-1.5-onnx，從 %APPDATA%\\pix2text\\1.1 複製）
  site-packages 另外要有 tokenizers   試卷辨識的 MFR 用（不需要 huggingface_hub）
用法：python tools\\build.py [--zip]
"""
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
B = ROOT / 'build'
NAME = 'vi-tools辨識套件'
DIST = ROOT / 'dist' / NAME
PY_ZIP = B / 'dl' / 'python-3.12.10-embed-amd64.zip'


def copytree(src, dst):
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))


def main():
    for need in (PY_ZIP, B / 'site-packages', B / 'audiveris' / 'Audiveris.exe', B / 'site-packages' / 'tokenizers',
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
        shutil.copytree(B / 'site-packages', sp, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'bin'))
    au = DIST / 'runtime' / 'audiveris'
    if not (au / 'Audiveris.exe').exists():
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
    print('完成：', DIST)
    if '--zip' in sys.argv:
        z = ROOT / 'dist' / (NAME + '.zip')
        print('壓縮中…')
        with zipfile.ZipFile(z, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for p in DIST.rglob('*'):
                rel = p.relative_to(DIST.parent)
                if p.is_file() and rel.parts[1] != '輸出' and '__pycache__' not in rel.parts and p.name != 'sitecustomize.py':
                    zf.write(p, rel.as_posix())
        print('zip：', z, '%.0f MB' % (z.stat().st_size / 1e6))


if __name__ == '__main__':
    main()
