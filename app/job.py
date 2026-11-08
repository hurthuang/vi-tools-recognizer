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


def run_text(input_path, pages='', use_text_layer=False, workdir=None, log=print, display_name=None, progress=None,
             cancel=None):
    """文字辨識：寫出 文字.txt 與 報告.txt。回傳 dict：workdir、text、report。"""
    import ocr
    workdir = Path(workdir) if workdir else new_workdir(input_path)
    log('輸出資料夾：%s' % workdir)
    log('文字辨識中（每頁約 3～10 秒；第一次要載入模型）…')
    results = ocr.run(input_path, pages, use_text_layer, log, progress, cancel)
    if not results:
        raise ValueError('已停止，還沒有做完任何一頁。')
    src = (display_name or Path(input_path).name) + ('（第 %s 頁）' % pages if pages else '')
    text, report = ocr.format_results(results, src)
    (workdir / '文字.txt').write_text(text, encoding='utf-8-sig')
    (workdir / '報告.txt').write_text('vi-tools 辨識套件：文字辨識報告\n' + report, encoding='utf-8-sig')
    log('完成。文字：%s' % (workdir / '文字.txt'))
    return {'workdir': workdir, 'text': text, 'report': report}


EXAM_DIR = '試卷資料'


def run_exam(input_path, pages='', use_text_layer=True, workdir=None, log=print, display_name=None, progress=None,
             cancel=None):
    """試卷（數學）辨識：寫出 試卷.txt、報告.txt 與 試卷資料\\（版面調整用）。回傳 dict：workdir、text、report。"""
    from exam import review
    workdir = Path(workdir) if workdir else new_workdir(input_path)
    log('輸出資料夾：%s' % workdir)
    log('試卷辨識中（每頁約 10～30 秒；第一次要載入模型）…')
    d = workdir / EXAM_DIR
    st = review.process(input_path, pages, d, use_text_layer, log, progress, cancel)
    pg, bl = review.exam_state(d)
    text = review.write_text(d, pg, bl)
    nq = sum(1 for p in bl for b in p if b['type'] == 'question')
    nf = sum(1 for p in bl for b in p if b['type'] in ('figure', 'table'))
    src = (display_name or Path(input_path).name) + ('（第 %s 頁）' % pages if pages else '')
    report = '\n'.join([
        'vi-tools 辨識套件：試卷辨識報告',
        '來源：%s' % src,
        '頁數：%d　題目：%d　附圖、附表：%d　算式：%d' % (len(st['pages']), nq, nf, st['math']),
        '文字：%s' % (('PDF 文字層 %d 頁、整頁辨識 %d 頁；附圖裡辨識出的字 %d 行' % (
            st['text_pages'], len(st['pages']) - st['text_pages'], st['image_lines'])) if st['text_layer'] else '辨識（OCR）'),
        '信心較低的算式：%d 個（版面調整畫面裡塗黃色、標「信心低」，請對照原卷）' % st['low'],
        '',
        '算式用 \\( \\) 寫法（LaTeX），可以直接貼到 vi-tools 文字轉點字。',
        '自動分出的題目、選項、附圖不一定對，請按「版面調整」對照原卷修改；改過的內容會自動存回 試卷.txt。',
    ]) + '\n'
    (workdir / '報告.txt').write_text(report, encoding='utf-8-sig')
    log('完成。試卷：%s' % (workdir / '試卷.txt'))
    return {'workdir': workdir, 'text': text, 'report': report}


def exam_dir(name):
    """版面調整用：輸出資料夾名稱 → 試卷資料夾（名稱不合法或不存在回傳 None）。"""
    if not name or any(c in name for c in '/\\:') or name.startswith('.'):
        return None
    d = OUTPUT / name / EXAM_DIR
    return d if (d / 'p01.json').exists() else None


def exam_names():
    """有試卷資料的輸出資料夾，新的在前面。"""
    if not OUTPUT.exists():
        return []
    return sorted((p.name for p in OUTPUT.iterdir() if (p / EXAM_DIR / 'p01.json').exists()), reverse=True)


def run(input_path, pages='', refs=(), workdir=None, log=print, use=('Audiveris', 'homr'), display_name=None,
        progress=None, cancel=None):
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
    progress = progress or (lambda *a: None)
    for k, (label, im) in enumerate(imgs):
        if cancel is not None and cancel.is_set() and items:
            log('已停止：做完 %d／%d 張' % (k, len(imgs)))
            break
        progress(k, len(imgs))
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
        progress(k + 1, len(imgs))
    report = 'vi-tools 辨識套件報告\n來源：%s%s\n\n' % (display_name or Path(input_path).name, '（第 %s 頁）' % pages if pages else '') + '\n'.join(reports)
    (workdir / '報告.txt').write_text(report, encoding='utf-8-sig')
    log('完成。報告：%s' % (workdir / '報告.txt'))
    return {'workdir': workdir, 'items': items, 'report': report}
