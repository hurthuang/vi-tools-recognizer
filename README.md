# 豪點樂譜辨識

把 PDF 或圖檔裡的五線譜辨識成 MusicXML，同時用 [Audiveris](https://github.com/Audiveris/audiveris) 與 [homr](https://github.com/liebharc/homr) 兩個開源工具辨識並交叉比對，列出要回頭看原譜的小節。全部在本機執行，不需要網路（試務闈場可用）。給 vi-tools 點字樂譜轉換器前置使用。

目前是給自己用的 Windows 可攜版（解壓即用）。使用說明見 `dist-files/說明.txt`，開發筆記見 `docs/notes.md`。

## 結構

| 路徑 | 內容 |
|---|---|
| `app/` | 主程式：`main.py`（命令列／啟動網頁）、`server.py`（只聽 127.0.0.1 的本機服務）、`job.py`（一次辨識）、`prepare.py`（PDF 抽圖、估線距、放大）、`engines.py`（呼叫兩個工具）、`compare.py`（逐小節比對、中文報告）、`web/`（操作頁面） |
| `dist-files/` | 放進可攜版的啟動檔、說明、授權 |
| `tools/build.py` | 組出 `dist/豪點樂譜辨識/` 與 zip |
| `build/`（不進版控） | 嵌入式 Python zip、`pip install --target` 的套件（含 homr 模型）、Audiveris 解開的資料夾 |

## 授權

AGPL-3.0（Audiveris、homr 皆為 AGPL-3.0）。
