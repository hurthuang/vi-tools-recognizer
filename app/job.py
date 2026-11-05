"""一次辨識工作：準備圖 → 兩個工具同時跑 → 比對 → 寫報告。命令列與網頁都用這裡。"""
import datetime
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import compare
import engines
import prepare

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / '輸出'


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|\s]+', '_', s).strip('_') or '樂譜'


def new_workdir(name):
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    d = OUTPUT / ('%s_%s' % (stamp, safe_name(Path(name).stem)[:40]))
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_text(input_path, pages='', use_text_layer=False, workdir=None, log=print, display_name=None):
    """文字辨識：寫出 文字.txt 與 報告.txt。回傳 dict：workdir、text、report。"""
    import ocr
    workdir = Path(workdir) if workdir else new_workdir(input_path)
    log('輸出資料夾：%s' % workdir)
    log('文字辨識中（每頁約 3～10 秒；第一次要載入模型）…')
    results = ocr.run(input_path, pages, use_text_layer, log)
    src = (display_name or Path(input_path).name) + ('（第 %s 頁）' % pages if pages else '')
    text, report = ocr.format_results(results, src)
    (workdir / '文字.txt').write_text(text, encoding='utf-8-sig')
    (workdir / '報告.txt').write_text('豪點樂譜辨識：文字辨識報告\n' + report, encoding='utf-8-sig')
    log('完成。文字：%s' % (workdir / '文字.txt'))
    return {'workdir': workdir, 'text': text, 'report': report}


def run(input_path, pages='', refs=(), workdir=None, log=print, use=('Audiveris', 'homr'), display_name=None):
    """回傳 dict：workdir、items（每張圖的結果）、report（全部報告文字）。"""
    workdir = Path(workdir) if workdir else new_workdir(input_path)
    log('輸出資料夾：%s' % workdir)
    imgs = prepare.load_inputs(input_path, pages, log)
    if not imgs:
        raise ValueError('沒有找到可以辨識的樂譜。')
    ref = None
    if refs:
        ref = compare.load_many(refs)
        log('手打參考：%d 行譜（%s）' % (len(ref), '、'.join(Path(r).name for r in refs)))
        if len(imgs) > 1:
            log('※ 有手打參考時只跟第一張圖比對。')
    items = []
    reports = []
    lock = threading.Lock()
    for k, (label, im) in enumerate(imgs):
        g, il, scale = prepare.normalize(im)
        img_path = workdir / ('%s.png' % safe_name(label))
        g.save(img_path)
        log('%s：線距 %s px，放大 %.1f 倍，開始辨識（約 10～60 秒）' % (label, '%.1f' % il if il else '？', scale))
        res = {}

        def go(name):
            fn = engines.audiveris if name == 'Audiveris' else engines.homr
            try:
                p = fn(img_path, workdir)
            except Exception as e:  # noqa: BLE001
                p = None
                with lock:
                    log('%s 執行失敗：%s' % (name, e))
            with lock:
                log('%s：%s' % (name, '完成' if p else '沒有辨識出結果（記錄檔在輸出資料夾）'))
            return name, p

        with ThreadPoolExecutor(max_workers=2) as ex:
            for name, p in ex.map(go, use):
                res[name] = p
        eng = {n: (compare.load(p) if p else []) for n, p in res.items()}
        text, stats = compare.compare(eng, ref if k == 0 else None)
        head = '=== %s ===\n' % label
        reports.append(head + text)
        items.append({'label': label, 'image': img_path, 'results': res, 'stats': stats})
    report = '豪點樂譜辨識報告\n來源：%s%s\n\n' % (display_name or Path(input_path).name, '（第 %s 頁）' % pages if pages else '') + '\n'.join(reports)
    (workdir / '報告.txt').write_text(report, encoding='utf-8-sig')
    log('完成。報告：%s' % (workdir / '報告.txt'))
    return {'workdir': workdir, 'items': items, 'report': report}
