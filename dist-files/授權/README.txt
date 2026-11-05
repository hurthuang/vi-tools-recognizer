豪點樂譜辨識使用的開源軟體

Audiveris 5.11.0（AGPL-3.0）
  原始碼：https://github.com/Audiveris/audiveris
  位置：runtime\audiveris（內含 Java 執行環境，授權見 runtime\audiveris\runtime\legal）
  授權全文：Audiveris_LICENSE.txt

homr 0.7.0（AGPL-3.0）
  原始碼：https://github.com/liebharc/homr
  授權全文：homr_LICENSE.txt

Python 3.12.10 嵌入式版本（PSF License）
  https://www.python.org/
  位置：runtime\python（授權見 runtime\python\LICENSE.txt）

其他 Python 套件（onnxruntime、OpenCV、NumPy、Pillow、pypdfium2、RapidOCR 等）
  各自的授權在 runtime\python\Lib\site-packages\<套件>.dist-info 資料夾裡。

本程式（app 資料夾）呼叫上面的工具，以 AGPL-3.0 釋出。
