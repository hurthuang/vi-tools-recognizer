"""豪點樂譜辨識：沒有參數時開本機網頁；有參數時用命令列。

命令列：豪點樂譜辨識.cmd 試題.pdf --pages 8,10 [--ref 手打.musicxml ...]
"""
import argparse
import sys

VERSION = '0.2.0'


def cli(argv):
    import job
    ap = argparse.ArgumentParser(prog='豪點樂譜辨識', description='用 Audiveris 與 homr 辨識樂譜（離線），並交叉比對。')
    ap.add_argument('input', help='PDF 或圖檔')
    ap.add_argument('--pages', default='', help='PDF 頁碼，例如 8,10 或 8-10；不填是全部')
    ap.add_argument('--ref', nargs='*', default=[], help='手打的 MusicXML（可多個，依行譜順序）')
    ap.add_argument('--text', action='store_true', help='文字辨識（輸出 文字.txt），不辨識樂譜')
    ap.add_argument('--text-layer', action='store_true', help='文字辨識時，PDF 有文字層就直接用')
    a = ap.parse_args(argv)
    if a.text:
        r = job.run_text(a.input, a.pages, a.text_layer)
        print()
        print(r['text'])
        print(r['report'])
        return
    r = job.run(a.input, a.pages, a.ref)
    print()
    print(r['report'])


def main():
    if len(sys.argv) > 1 and sys.argv[1] not in ('--serve', '--no-browser'):
        return cli(sys.argv[1:])
    import server
    server.serve(open_browser='--no-browser' not in sys.argv)


if __name__ == '__main__':
    main()
