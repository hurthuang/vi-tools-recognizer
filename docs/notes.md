# 開發筆記

## 背景（2026-11-05）

- 需求：音樂班術科（樂理）試題的譜例要轉點字，原本手打 ABC；試務單位闈場不能用網路。
- 114 南區樂理試題實測（手打 ABC 為準）：
  - homr 0.7：鋼琴譜很準（題組二 96% 音高與節奏全對），但一組超過兩行的總譜（三重奏）會把行數、小節弄亂。
  - Audiveris 5.11：總譜可以；節奏（附點十六分）常錯、和弦漏音、認不出中途換譜號（低一大截）。
  - 兩個工具交叉比對抓到手打的錯：題組一鋼琴左手 5 處、題組二左手 A♯（兩個工具一致、跟手打不同）。

## 做法要點

- **放大**：PDF 內嵌的掃描圖線距只有 8～14 px，Audiveris 會拒絕（「interline too low」）。`prepare.normalize` 估主要譜表線距（取各組最大，三重奏的弦樂譜表比鋼琴小），放大到約 32 px（1～4 倍）。
- **找五線**：每列黑點比例 > 45% 的列當譜線，連續五條等距才算一組（全寬、左半、右半各找一次容忍歪斜）；純文字頁會略過。
- **PDF**：pypdfium2 抽內嵌圖（面積 > 頁面 8%、找得到五線）；沒有就整頁 300 dpi。
- **homr 的路徑**：OpenCV 在 Windows 讀不了中文路徑，homr 在純英文的暫存資料夾跑（`engines._ascii_tempdir`）。
- **編碼**：Windows 預設 cp950，homr 依賴的 musicxml 套件讀 xsd 會錯；一律 `-X utf8`／`PYTHONUTF8=1`。
- **比對**（`compare.py`）：以音高多重集合比每行譜每小節；有手打參考時分「兩個工具一致、跟手打不同」（最可能手打錯）、「三方都不同」、「只有一個工具不同」。行數不同的工具不逐行比對。
- **離線**：已驗證兩個工具都不連網（Python 擋 socket、Java 指向不存在的 proxy，結果與連網相同）。

## 文字辨識（0.2，`app/ocr.py`）

- RapidOCR 3.10（homr 的相依套件，PP-OCRv6 small 模型已在套件裡），250 dpi；114 試題 14 頁離線 19 秒。
- **浮水印**：灰階 >= 200 改白（114 南區的浮水印約 210）。
- **譜例**：PDF 內嵌的樂譜圖範圍＋偵測到的五線（上 4、下 11 倍線距）不辨識，換成「［譜例］」。
- **長行亂碼**：整頁辨識偶爾把長行認成「「：·0」；信心 < 0.9 的行單獨切出來 `use_det=False` 再認。注意 RapidOCR 的 use_det/use_cls 參數會留在引擎上，每次呼叫都要明講。
- **圈號**：黑底 ❶ 常變 0／O 或不見。直排選項：同一組認得出一個以上就依序補齊；題目（問號結尾）下一行以 0／O 開頭當 ①。橫排選項補不了，列入「請對照原稿」。
- **文字層**：這份 PDF 的文字層沒有圈號（符號字型）也沒有譜例標記，所以預設一律 OCR，文字層是選項。

## 建置

```
python -m pip install --target build\site-packages homr==0.7.0 onnxruntime "pypdfium2>=4,<6"
（homr 模型：從已跑過 homr --init 的環境複製 homr\segmentation\*.onnx、homr\transformer\*.onnx）
build\dl\python-3.12.10-embed-amd64.zip（python.org）
build\audiveris\（Audiveris-5.11.0-windowsConsole-x86_64.msi 用 msiexec /a 解開）
python tools\build.py --zip
```

## 之後

- 外掛模式：vi-tools 轉換器偵測 `http://127.0.0.1:18760～18779/api/info`，出現「辨識樂譜」按鈕（要加 CORS 與來源白名單；Chrome 會詢問本機網路存取）。
- 瘦身（目前 zip 347 MB）。
