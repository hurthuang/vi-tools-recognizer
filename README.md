# vi-tools 辨識套件

[vi-tools](https://hurthuang.github.io/vi-tools/)（視障教學工具）的選裝套件：在自己的電腦上把試卷、講義的 PDF 或圖檔辨識成文字與算式（LaTeX），也可以辨識五線譜（MusicXML）。開著它時，vi-tools「版面整理」可以整頁辨識算式、讀掃描檔，結果存成中繼檔，再接數學點字、文字轉點字、朗讀與音檔、觸圖製作。

- 全部在本機執行，試卷不會傳到網路上；不需要網路，闈場也能用。
- Windows 10／11 64 位元的可攜版：到 [Releases](https://github.com/hurthuang/vi-tools-recognizer/releases) 下載 zip，解壓縮後雙擊 `vi-tools辨識套件.cmd`。兩種：試卷版（`…-exam-portable.zip`，約 300 MB，不含樂譜辨識）、完整版（`…-portable.zip`，約 530 MB）。使用說明在 zip 裡的 `說明.txt`（原檔 `dist-files/說明.txt`）。
- 只聽 127.0.0.1，只接受 vi-tools 網頁的來源呼叫 `/api/ocr`、`/api/doc`、`/api/word`（用這台電腦的 Word 把 .doc／.docx 另存 PDF，0.4.1）、`/api/omr`（一塊譜例的圖辨識成 MusicXML，給 vi-tools 文字辨識頁的樂譜模式；只有完整版，0.4.2）。

## 辨識用的工具

| 用途 | 工具 |
|---|---|
| 試卷文字 | PDF 文字層（pypdfium2）；掃描檔、圖檔用 [RapidOCR](https://github.com/RapidAI/RapidOCR)（PaddleOCR 的 PP-OCR 模型） |
| 算式 | [Pix2Text](https://github.com/breezedeus/Pix2Text) 的 MFD（找算式）、MFR（算式轉 LaTeX）模型，用 onnxruntime 執行 |
| 五線譜 | [Audiveris](https://github.com/Audiveris/audiveris) 與 [homr](https://github.com/liebharc/homr)，兩個結果交叉比對 |

## 結構

| 路徑 | 內容 |
|---|---|
| `app/` | 主程式：`main.py`（命令列／啟動網頁）、`server.py`（只聽 127.0.0.1 的本機服務，含給 vi-tools 的 `/api/ocr`、`/api/doc`、`/api/word`、`/api/omr`）、`omr_region.py`（一塊譜例）、`job.py`（一次辨識）、`ocr.py`（文字辨識）、`exam/`（試卷：版面、文字層、算式、直書、中繼檔）、`prepare.py`、`engines.py`、`compare.py`（樂譜）、`web/`（操作頁面） |
| `dist-files/` | 放進可攜版的啟動檔、說明、授權 |
| `tools/build.py` | 組出 `dist/vi-tools辨識套件/` 與 zip（`vi-tools-recognizer-版本-portable.zip`） |
| `build/`（不進版控） | 嵌入式 Python zip、`pip install --target` 的套件（含 homr 模型）、Audiveris 解開的資料夾、算式模型 |
| `docs/notes.md` | 開發筆記 |

## 授權

AGPL-3.0（`LICENSE`）。Audiveris、homr 是 AGPL-3.0；其他元件的授權見 `dist-files/授權/README.txt`。
