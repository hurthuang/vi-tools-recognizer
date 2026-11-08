vi-tools 辨識套件使用的開源軟體

Audiveris 5.11.0（AGPL-3.0）
  原始碼：https://github.com/Audiveris/audiveris
  位置：runtime\audiveris（內含 Java 執行環境，授權見 runtime\audiveris\runtime\legal）
  授權全文：Audiveris_LICENSE.txt

homr 0.7.0（AGPL-3.0）
  原始碼：https://github.com/liebharc/homr
  授權全文：homr_LICENSE.txt

Pix2Text 1.5 的算式模型（試卷辨識用；模型卡為 MIT License）
  原始碼與模型：https://github.com/breezedeus/Pix2Text
  位置：runtime\models\mfd（找算式，YOLO11m）、runtime\models\mfr（算式轉 LaTeX）
  ※ mfd 是用 Ultralytics YOLO11 訓練的，模型檔內建的授權欄位寫 AGPL-3.0；本程式整體本來就以 AGPL-3.0 釋出。
  app\exam\pix2text_post.py 取自 Pix2Text（MIT License，breezedeus）的 LaTeX 後處理。

MathJax 3（Apache License 2.0）
  https://www.mathjax.org/
  位置：app\web\exam\tex-svg.js（版面調整畫面顯示算式）
  授權全文：MathJax_LICENSE.txt

Python 3.12.10 嵌入式版本（PSF License）
  https://www.python.org/
  位置：runtime\python（授權見 runtime\python\LICENSE.txt）

其他 Python 套件（onnxruntime、OpenCV、NumPy、Pillow、pypdfium2、RapidOCR、tokenizers 等）
  各自的授權在 runtime\python\Lib\site-packages\<套件>.dist-info 資料夾裡。

本程式（app 資料夾）呼叫上面的工具，以 AGPL-3.0 釋出。
