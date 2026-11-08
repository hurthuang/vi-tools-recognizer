r"""試卷辨識（數學）：題目、選項、算式（LaTeX），自動分區塊，可以在版面調整畫面修改。

模型（Pix2Text 1.5，只用 onnxruntime）：
  mfd\pix2text-mfd-1.5.onnx            找算式（YOLO11m）
  mfr\encoder_model.onnx、decoder_model.onnx、tokenizer.json   算式 → LaTeX
放在 runtime\models\（可攜版）或 build\models\（開發）；都沒有就用 %APPDATA%\pix2text\1.1\ 下載過的。
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def model_path(name):
    for d in (ROOT / 'runtime' / 'models' / name, ROOT / 'build' / 'models' / name,
              Path(os.environ.get('APPDATA', '')) / 'pix2text' / '1.1' / f'{name}-1.5-onnx'):
        if d.is_dir():
            return d
    raise FileNotFoundError(f'找不到算式辨識的模型（{name}），請確認 runtime\\models\\{name} 資料夾還在。')


def models_ready():
    try:
        model_path('mfd')
        model_path('mfr')
        return True
    except FileNotFoundError:
        return False
