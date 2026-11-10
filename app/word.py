r"""用這台電腦上安裝的 Microsoft Word 把 .doc／.docx 另存成 PDF（和 .docx），給 vi-tools 版面整理當原卷。

vi-tools 網頁開 Word 檔時沒有原卷（網頁排不出 Word 的版面）；Word 自己存的 PDF 版面完全一樣。
做法：PowerShell 開 Word（COM，不顯示視窗、唯讀開檔、給一個假密碼避免有密碼的檔跳出輸入框卡住），
     另存 PDF（wdExportFormatPDF＝17），舊式 .doc 另外存一份 .docx（wdFormatXMLDocument＝16），關掉 Word。
同一時間只轉一個（Word COM 不適合同時開好幾個）。
"""
import os
import shutil
import subprocess
import tempfile
import threading
import winreg
from pathlib import Path

_LOCK = threading.Lock()

PS = r'''
param([string]$In, [string]$Pdf, [string]$Docx)
$ErrorActionPreference = 'Stop'
$w = New-Object -ComObject Word.Application
try {
  $w.Visible = $false
  $w.DisplayAlerts = 0
  # Open(FileName, ConfirmConversions, ReadOnly, AddToRecentFiles, PasswordDocument)
  $d = $w.Documents.Open($In, $false, $true, $false, 'vi-tools-no-password')
  try {
    if ($Docx) { $d.SaveAs2([ref]$Docx, [ref]16) }
    $d.ExportAsFixedFormat($Pdf, 17)
  } finally { $d.Close(0) }
} finally { $w.Quit() }
'''


def available():
    """這台電腦有沒有裝 Word（看登錄檔的 Word.Application）。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r'Word.Application\CLSID'):
            return True
    except OSError:
        return False


def convert(data, name, timeout=180):
    """Word 檔的內容 → {'pdf': bytes, 'docx': bytes}（輸入是 .docx 時 docx 就是原檔）。失敗丟出 RuntimeError（中文說明）。"""
    ext = os.path.splitext(name)[1].lower()
    if ext not in ('.doc', '.docx'):
        raise ValueError('只能轉 Word 檔（.doc、.docx）')
    if not available():
        raise RuntimeError('這台電腦沒有安裝 Microsoft Word，沒辦法自動轉成 PDF。請在別台有 Word 的電腦另存 PDF，或用 LibreOffice、Google 文件另存。')
    with _LOCK:
        d = Path(tempfile.mkdtemp(prefix='vitools-word-'))
        try:
            src = d / ('input' + ext)          # 純英文路徑，避免 COM、PowerShell 的編碼問題
            src.write_bytes(data)
            pdf, docx = d / 'output.pdf', d / 'output.docx'
            ps1 = d / 'convert.ps1'
            ps1.write_text(PS, encoding='utf-8-sig')
            args = ['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(ps1),
                    '-In', str(src), '-Pdf', str(pdf)]
            if ext == '.doc':
                args += ['-Docx', str(docx)]
            try:
                r = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except subprocess.TimeoutExpired:
                raise RuntimeError('Word 轉檔超過 %d 秒沒有完成（檔案太大，或 Word 跳出了對話框）' % timeout)
            if r.returncode != 0 or not pdf.exists():
                msg = (r.stderr or r.stdout or b'').decode('utf-8', 'replace').strip().splitlines()
                hint = next((m for m in msg if m.strip()), '')
                if 'password' in hint.lower() or '密碼' in hint:
                    raise RuntimeError('這個 Word 檔有密碼保護，請先在 Word 裡移除密碼再開。')
                raise RuntimeError('Word 轉檔失敗' + ('：' + hint[:200] if hint else ''))
            out = {'pdf': pdf.read_bytes()}
            out['docx'] = docx.read_bytes() if ext == '.doc' else data
            return out
        finally:
            shutil.rmtree(d, ignore_errors=True)
