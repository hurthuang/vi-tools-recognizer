"""比對樂譜辨識結果：讀 MusicXML，逐行譜、逐小節比音高（與節奏），寫成中文報告。

每個音記成 (起點, MIDI 音高, 時值, 音名)；起點與時值以四分音符為 1。
沒有手打參考時比對兩個辨識工具；有參考時三方比對，把「兩個工具一致、跟手打不同」排在最前面（最可能是手打錯）。
"""
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from fractions import Fraction as F

STEP = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
ACC = {-2: '重降', -1: '降', 0: '', 1: '升', 2: '重升'}


class Staff:
    def __init__(self, label):
        self.label = label
        self.measures = []  # [{'number': '1', 'notes': [(on, midi, dur, name)]}]


def read_xml_text(path):
    if str(path).lower().endswith('.mxl'):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(('.xml', '.musicxml')) and not n.startswith('META-INF')]
            return z.read(names[0])
    with open(path, 'rb') as f:
        return f.read()


def load(path):
    """回傳 Staff 的清單（每個 part 的每個 staff 各一個）。"""
    root = ET.fromstring(read_xml_text(path))
    names = {}
    for sp in root.iter('score-part'):
        n = sp.find('part-name')
        names[sp.get('id')] = (n.text or '').strip() if n is not None else ''
    staves = []
    for part in root.findall('part'):
        div = 1
        nst = 1
        per = {}
        numbers = []
        for mi, meas in enumerate(part.findall('measure')):
            numbers.append(meas.get('number') or str(mi + 1))
            t = F(0)
            last_on = F(0)
            for el in meas:
                if el.tag == 'attributes':
                    d = el.find('divisions')
                    if d is not None:
                        div = int(float(d.text))
                    s = el.find('staves')
                    if s is not None:
                        nst = int(s.text)
                elif el.tag == 'backup':
                    t -= F(int(float(el.find('duration').text)), div)
                elif el.tag == 'forward':
                    t += F(int(float(el.find('duration').text)), div)
                elif el.tag == 'note':
                    if el.find('grace') is not None:
                        continue
                    dd = el.find('duration')
                    dur = F(int(float(dd.text)), div) if dd is not None else F(0)
                    chord = el.find('chord') is not None
                    on = last_on if chord else t
                    st = el.find('staff')
                    st = int(st.text) if st is not None else 1
                    p = el.find('pitch')
                    if p is not None:
                        alt = p.find('alter')
                        a = int(round(float(alt.text))) if alt is not None else 0
                        step = p.find('step').text
                        octv = int(p.find('octave').text)
                        midi = 12 * (octv + 1) + STEP[step] + a
                        per.setdefault(st, {}).setdefault(mi, []).append((on, midi, dur, ACC.get(a, '') + step + str(octv)))
                    if not chord:
                        last_on = t
                        t += dur
        pname = names.get(part.get('id'), '')
        for s in range(1, nst + 1):
            label = pname or ''
            if nst > 1:
                label = (label + ' ' if label else '') + ('上行' if s == 1 else '下行' if s == 2 and nst == 2 else '第 %d 行' % s)
            stf = Staff(label)
            stf.measures = [{'number': numbers[mi], 'notes': per.get(s, {}).get(mi, [])} for mi in range(len(numbers))]
            staves.append(stf)
    return staves


def load_many(paths):
    out = []
    for p in paths:
        out.extend(load(p))
    return out


DUR_NAME = {F(4): '全音符', F(3): '附點二分', F(2): '二分', F(3, 2): '附點四分', F(1): '四分', F(3, 4): '附點八分',
            F(1, 2): '八分', F(3, 8): '附點十六分', F(1, 4): '十六分', F(1, 8): '三十二分', F(1, 3): '三連音八分', F(1, 6): '三連音十六分'}


def fmt_frac(x):
    x = F(x)
    if x.denominator == 1:
        return str(x.numerator)
    whole = x.numerator // x.denominator
    rest = x - whole
    return (str(whole) + '又' if whole else '') + '%d/%d' % (rest.numerator, rest.denominator)


def pitches(notes):
    return Counter(n[1] for n in notes)


def rhythm(notes):
    return Counter((n[0], n[1], n[2]) for n in notes)


def names_of(notes, midis):
    """把 MIDI 多重集合換成音名（用該來源自己的拼法）。"""
    need = Counter(midis)
    out = []
    for n in sorted(notes, key=lambda n: (n[0], n[1])):
        if need[n[1]] > 0:
            need[n[1]] -= 1
            out.append(n[3])
    return out


def describe(notes):
    return '、'.join(n[3] for n in sorted(notes, key=lambda n: (n[0], -n[1]))) or '（沒有音）'


def summary_line(name, staves):
    nm = max((len(s.measures) for s in staves), default=0)
    nn = sum(len(m['notes']) for s in staves for m in s.measures)
    return '%s：%d 行譜、%d 小節、%d 個音' % (name, len(staves), nm, nn)


def staff_label(i, s):
    return '第 %d 行譜' % (i + 1) + ('（%s）' % s.label if s.label else '')


def compare(engines, ref=None):
    """engines：{'Audiveris': [Staff], 'homr': [Staff]}；ref：[Staff] 或 None。回傳 (報告文字, 統計 dict)。"""
    lines = []
    stats = {}
    names = list(engines)
    lines.append('【辨識結果】')
    for n in names:
        lines.append('  ' + summary_line(n, engines[n]))
    if ref is not None:
        lines.append('  ' + summary_line('手打參考', ref))
    lines.append('')

    usable = [n for n in names if engines[n]]
    if ref is not None:
        base = ref
        base_name = '手打'
    elif usable:
        base = engines[usable[0]]
        base_name = usable[0]
    else:
        return '兩個工具都沒有辨識出樂譜。', stats

    # 行數不同的工具不逐行比對
    aligned = [n for n in usable if len(engines[n]) == len(base)]
    for n in usable:
        if n not in aligned:
            lines.append('※ %s 找到 %d 行譜，%s 有 %d 行，行數不同，不逐行比對（多行一組的總譜常見；%s 的結果仍可開來看）。'
                         % (n, len(engines[n]), base_name, len(base), n))
    if len(aligned) != len(usable):
        lines.append('')

    strong, weak, three, rhy = [], [], [], []
    if ref is not None:
        for n in aligned:
            stats[n] = {'ref_notes': 0, 'pitch_ok': 0, 'exact': 0}
        for si, rs in enumerate(ref):
            for mi, rm in enumerate(rs.measures):
                rp = pitches(rm['notes'])
                got = {}
                for n in aligned:
                    ms = engines[n][si].measures
                    em = ms[mi]['notes'] if mi < len(ms) else []
                    got[n] = em
                    st = stats[n]
                    st['ref_notes'] += len(rm['notes'])
                    st['pitch_ok'] += sum((rp & pitches(em)).values())
                    st['exact'] += sum((rhythm(rm['notes']) & rhythm(em)).values())
                where = '%s 第 %s 小節' % (staff_label(si, rs), rm['number'])
                diff = [n for n in aligned if pitches(got[n]) != rp]
                if not diff:
                    rdiff = [n for n in aligned if rhythm(got[n]) != rhythm(rm['notes'])]
                    if rdiff and len(rdiff) == len(aligned) and len(aligned) > 1 and rhythm(got[aligned[0]]) == rhythm(got[aligned[1]]):
                        rhy.append('%s：音高相同，但兩個工具的節奏都跟手打不同' % where)
                    continue
                detail = '；'.join('%s：%s' % (n, describe(got[n])) for n in aligned)
                item = '%s\n      手打：%s\n      %s' % (where, describe(rm['notes']), detail)
                if len(aligned) > 1 and len(diff) == len(aligned) and pitches(got[aligned[0]]) == pitches(got[aligned[1]]):
                    strong.append(item)
                elif len(diff) == len(aligned) and len(aligned) > 1:
                    three.append(item)
                else:
                    weak.append(item + '\n      （跟手打不同的是 %s）' % '、'.join(diff))
        lines.append('【準確率（以手打為準）】')
        for n in aligned:
            st = stats[n]
            tot = max(1, st['ref_notes'])
            lines.append('  %s：音高對 %d / %d（%d%%），音高與節奏都對 %d（%d%%）'
                         % (n, st['pitch_ok'], st['ref_notes'], 100 * st['pitch_ok'] // tot, st['exact'], 100 * st['exact'] // tot))
        lines.append('')
        sec = [('★ 兩個工具一致、跟手打不同（最可能是手打錯，請先看原譜）', strong),
               ('◆ 三方都不同（請看原譜）', three),
               ('・只有一個工具跟手打不同（多半是辨識錯，有空再看）', weak)]
        if rhy:
            sec.append(('・節奏不同', rhy))
    else:
        if len(aligned) < 2:
            lines.append('只有一個工具的結果可以逐行比對，沒有可比的對象。')
            return '\n'.join(lines), stats
        a, b = aligned[0], aligned[1]
        diffs = []
        same = 0
        total = 0
        for si, sa in enumerate(engines[a]):
            sb = engines[b][si]
            nm = max(len(sa.measures), len(sb.measures))
            for mi in range(nm):
                ma = sa.measures[mi]['notes'] if mi < len(sa.measures) else []
                mb = sb.measures[mi]['notes'] if mi < len(sb.measures) else []
                total += 1
                num = sa.measures[mi]['number'] if mi < len(sa.measures) else str(mi + 1)
                if pitches(ma) == pitches(mb):
                    same += 1
                    continue
                diffs.append('%s 第 %s 小節\n      %s：%s\n      %s：%s' % (staff_label(si, sa), num, a, describe(ma), b, describe(mb)))
        stats['agree_measures'] = same
        stats['total_measures'] = total
        lines.append('【兩個工具音高一致的小節：%d / %d】' % (same, total))
        lines.append('一致的小節大致可信；不一致的請對照原譜。小節號照 %s 的編號（弱起小節可能算第 0 或第 1 小節）。' % a)
        lines.append('')
        sec = [('◆ 兩個工具音高不同的小節', diffs)]

    for title, items in sec:
        lines.append('%s：%d 處' % (title, len(items)))
        for it in items:
            lines.append('  ' + it)
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n', stats
