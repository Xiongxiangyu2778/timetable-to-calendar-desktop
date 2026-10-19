# -*- coding: utf-8 -*-
"""PDF 课表识别（桌面版 / 网页版同一套思路）

PDF 里没有「表格」，只有一堆带坐标的文字。所以识别分四步：
  ① 按 y 坐标把文字聚成「行」；
  ② 找出含「星期一~星期日」的那一行当表头，用每个表头字的 x 中心算出列边界；
  ③ 左边那一列（节次列）按 y 聚成一个个「行区间」，每行给出它管的是第几节；
  ④ 把右边各列文字按 y 塞进对应的节次行 → 得到一个个「单元格文字」，
     再拆成 课程名 / 教室 / 教师 / 周次。

依赖：pip install pymupdf
（选 PyMuPDF 而不是 pdfplumber：中文不乱码、能读坐标、安装只要一个轮子。）

对外主要函数：
    read_pdf(path)            → [{'page':0,'lines':[...],'table':rows,'courses':[...]}, ...]
    pdf_courses(path)         → 排好序的课程列表（给 课表转日历.py 用）
    page_text_rows(path)      → 纯文字行（认不出表格时的兜底，通用 PDF 也能转）
"""

import re
import sys

try:
    import pymupdf  # PyMuPDF >= 1.24 的新包名
except ImportError:  # 老版本叫 fitz
    try:
        import fitz as pymupdf
    except ImportError:
        pymupdf = None

WD_RE = re.compile(r'^(?:星期|周|礼拜)\s*([一二三四五六日天])$')
WEEK_CN = {'一': 0, '二': 1, '三': 2, '四': 3, '五': 4, '六': 5, '日': 6, '天': 6}
WEEK_ALIAS = {'周一': 0, '星期一': 0, '礼拜一': 0, '周二': 1, '星期二': 1, '礼拜二': 1,
              '周三': 2, '星期三': 2, '礼拜三': 2, '周四': 3, '星期四': 3, '礼拜四': 3,
              '周五': 4, '星期五': 4, '礼拜五': 4, '周六': 5, '星期六': 5, '礼拜六': 5,
              '周日': 6, '星期日': 6, '周天': 6, '礼拜日': 6, '礼拜天': 6}
CN_NUM = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}

# 教室常见特征（用来从单元格里认出「哪一行是教室」）
ROOM_RE = re.compile(r'(楼|室|馆|场|中心|机房|实验室|线上|腾讯会议|钉钉|超星|学习通|教\s*\d)')
TEACHER_RE = re.compile(r'^[\u4e00-\u9fa5]{2,4}$')
TEACHER_HINT = re.compile(r'(老师|教师|教授|讲师)')


# ───────────────────────── 基础解析（和网页版一致） ─────────────────────────

def parse_weekday(s):
    t = str(s or '').strip()
    for k, v in WEEK_ALIAS.items():
        if k in t:
            return v
    m = re.search(r'[周星期礼拜]\s*([一二三四五六日天1-7])', t)
    if m:
        c = m.group(1)
        return int(c) - 1 if c.isdigit() else WEEK_CN.get(c)
    if re.fullmatch(r'[1-7]', t):
        return int(t) - 1
    return None


def parse_label_periods(label):
    """节次列的文字 → 节次列表：'1-2' → [1,2]；'第3节' → [3]"""
    s = re.sub(r'\s+', '', str(label or ''))
    d = re.search(r'(\d{1,2})\s*[-~至]\s*(\d{1,2})', s)
    if d:
        a, b = int(d.group(1)), int(d.group(2))
        return list(range(min(a, b), max(a, b) + 1))
    d2 = re.search(r'(\d{1,2})', s)
    if d2:
        return [int(d2.group(1))]
    cn = re.search(r'第?([一二三四五六七八九十]+)节', s)
    if cn:
        nums = [CN_NUM[c] for c in cn.group(1) if c in CN_NUM]
        if nums:
            return sorted(set(nums))
    return None


def parse_weeks(s):
    """'1-16周' / '1-16周(双)' / '1,3,5周' → 周次列表。数字在前、周在后才算周次，避免把「周一」当周次。"""
    t = str(s or '')
    core = re.sub(r'\[[^\]]*\]', ' ', t)
    m = re.search(r'([\d,，\-~至\s]*\d[\d,，\-~至\s]*)[（(]?\s*[单双]?\s*周', core)
    if m:
        core = m.group(1)
    parity = 1 if re.search(r'单周?', t) else (0 if re.search(r'双周?', t) else None)
    core = re.sub(r'[单双]', '', core)
    out = set()
    for part in re.split(r'[,，、\s]+', core):
        if not part:
            continue
        r = re.fullmatch(r'(\d{1,2})\s*[-~至]\s*(\d{1,2})', part)
        if r:
            a, b = int(r.group(1)), int(r.group(2))
            for w in range(min(a, b), max(a, b) + 1):
                if parity is None or w % 2 == parity:
                    out.add(w)
        elif re.fullmatch(r'\d{1,2}', part):
            w = int(part)
            if parity is None or w % 2 == parity:
                out.add(w)
    return sorted(out)


def parse_cell_text(text, weekday, periods):
    """一个单元格的文字 → 若干门课（有的格子一格里塞两门课，用 ---- 或空行隔开）"""
    if not text or not str(text).strip():
        return []
    out = []
    for block in re.split(r'\n?\s*-{4,}\s*\n?|\n{2,}', str(text)):
        lines = [s.strip() for s in block.split('\n') if s.strip()]
        if not lines:
            continue
        name = room = teacher = week_line = ''
        for ln in lines:
            if not week_line and re.search(r'\d', ln) and '周' in ln:
                week_line = ln
                continue
            if not name:
                name = ln
                continue
            if not room and ROOM_RE.search(ln):
                room = ln
                continue
            if not teacher and TEACHER_RE.match(ln):
                teacher = ln
                continue
            if not teacher and TEACHER_HINT.search(ln):
                teacher = re.sub(r'[（(].*?[）)]', '', ln).strip()
                continue
        name = re.sub(r'\s*[（(]?\d{1,2}\s*[-~至]\s*\d{1,2}\s*(?:周|\(周\)).*$', '', name)
        name = re.sub(r'\s*[（(][A-Za-z]?[）)]\s*$', '', name).strip()
        weeks = parse_weeks(week_line or block)
        if not (name and weeks and periods):
            continue
        out.append({'name': name, 'weekday': weekday, 'periods': list(periods),
                    'weeks': weeks, 'room': room, 'teacher': teacher})
    return out


# ───────────────────────── 坐标 → 行 → 单元格 ─────────────────────────

def _words(page):
    items = []
    for w in page.get_text('words'):
        x0, y0, x1, y1, t = w[0], w[1], w[2], w[3], w[4]
        if not t.strip():
            continue
        items.append({'x0': x0, 'x1': x1, 'y0': y0, 'y1': y1, 't': t, 'h': max(y1 - y0, 1.0)})
    return items


def _to_lines(items):
    """按 y0 聚行：同一行里文字的 y0 差小于半个字高。"""
    lines = []
    for it in sorted(items, key=lambda a: a['y0']):
        tol = max(2.5, it['h'] * 0.45)
        for ln in lines:
            if abs(ln['y'] - it['y0']) <= tol:
                ln['items'].append(it)
                ln['h'] = max(ln['h'], it['h'])
                break
        else:
            lines.append({'y': it['y0'], 'h': it['h'], 'items': [it]})
    for ln in lines:
        ln['items'].sort(key=lambda a: a['x0'])
    lines.sort(key=lambda a: a['y'])
    return lines


def _line_text(items):
    out, last_end = '', None
    for it in items:
        if last_end is not None and it['x0'] - last_end > 2.5:
            out += ' '
        out += it['t']
        last_end = it['x1']
    return re.sub(r'\s+', ' ', out).strip()


def _cluster_cells(items, gap_limit):
    """同一列里按 y 间距切成「单元格」：格内行距小，跨格会有明显空隙。"""
    out, cur, last = [], None, None
    for it in sorted(items, key=lambda a: a['y0']):
        if cur is None or (last is not None and it['y0'] - last > gap_limit):
            cur = []
            out.append(cur)
        cur.append(it)
        last = it['y0']
    return out


def _cell_text(items):
    """一个单元格里的多行文字 → 用 \n 连起来（行内再按 x 间距补空格）"""
    rows = []
    for it in sorted(items, key=lambda a: a['y0']):
        for r in rows:
            if abs(r['y'] - it['y0']) <= 2.5:
                r['items'].append(it)
                break
        else:
            rows.append({'y': it['y0'], 'items': [it]})
    rows.sort(key=lambda a: a['y'])
    texts = []
    for r in rows:
        r['items'].sort(key=lambda a: a['x0'])
        t = _line_text(r['items'])
        if t:
            texts.append(t)
    return '\n'.join(texts)


def grid_from_lines(lines):
    """核心：认表头 → 定列 → 定行 → 取格。返回 (表格二维数组, 课程列表)"""
    header = None
    for ln in lines:
        cells = [it for it in ln['items'] if WD_RE.match(it['t'].strip())]
        if len(cells) >= 3:
            header = {'line': ln, 'cells': cells}
            break
    if not header:
        return None, []

    weekdays = [parse_weekday(c['t'].strip()) for c in header['cells']]
    centers = [(c['x0'] + c['x1']) / 2 for c in header['cells']]
    bounds = [(centers[i] + centers[i + 1]) / 2 for i in range(len(centers) - 1)]
    label_right = centers[0] - (centers[1] - centers[0]) / 2 if len(centers) > 1 else centers[0] - 40

    below = [ln for ln in lines if ln['y'] > header['line']['y'] + 1]
    label_items = []
    col_items = [[] for _ in weekdays]
    for ln in below:
        for it in ln['items']:
            c = (it['x0'] + it['x1']) / 2
            if c < label_right:
                label_items.append(dict(it, y=ln['y']))
                continue
            ci = 0
            while ci < len(bounds) and c > bounds[ci]:
                ci += 1
            col_items[ci].append(dict(it, y=ln['y']))
    if not label_items:
        return None, []

    h = header['line']['h'] or 10

    # 行锚点：节次列里形如「1-2」「第 3 节」的文字。用它们切行最稳——
    # 光按 y 间距聚类会把「1-2 / 08:00- / 09:40」这三行粘成一格（间距不稳定）。
    anchors = [it for it in label_items
               if re.fullmatch(r'第?\s*\d{1,2}(\s*[-~至]\s*\d{1,2})?\s*节?', it['t'].strip())
               or re.fullmatch(r'第[一二三四五六七八九十]+\s*节', it['t'].strip())]
    anchors.sort(key=lambda a: a['y'])
    # 去掉同一行里重复的锚点（左列有时会同时出现「1-2」和时间）
    uniq = []
    for a in anchors:
        if uniq and abs(a['y'] - uniq[-1]['y']) <= max(3, h * 0.5):
            continue
        uniq.append(a)

    # 行锚点 = 每行的「第几节」。课表的行与行之间几乎没有留白（下一行的课程名
    # 紧贴上一行的周次），所以不能按固定间距硬切行——那样会把「1-16周」切到下一行去，
    # 导致整门课因为读不到周次被丢弃。改成：先把每列的连续文字聚成「格子」，
    # 再按格子中心离哪个行锚点最近，判断它属于第几节。
    if len(uniq) >= 2:
        anchor_ys = [a['y'] for a in uniq]
        label_texts = [a['t'] for a in uniq]
        row_h = (anchor_ys[-1] - anchor_ys[0]) / max(1, len(anchor_ys) - 1)
    else:
        groups = _cluster_cells(label_items, h * 3.2)
        label_texts = [t for t in (_cell_text(c) for c in groups) if t]
        if len(label_texts) < 2:
            return None, []
        anchor_ys = [sum(it['y'] for it in c) / len(c) for c in groups if _cell_text(c)]
        row_h = h * 4

    # 行内行距（约等于一个字高）用于区分「同一格的多行」和「不同行」
    gaps = []
    for items in col_items + [label_items]:
        ys = sorted({round(it['y'], 1) for it in items})
        for i in range(1, len(ys)):
            d = ys[i] - ys[i - 1]
            if 0 < d < row_h * 0.8:
                gaps.append(d)
    line_gap = sorted(gaps)[len(gaps) // 2] if gaps else h
    cell_gap = min(line_gap * 1.5, row_h * 0.7)

    def nearest_row(center_y):
        best, bd = 0, 1e9
        for i, ay in enumerate(anchor_ys):
            d = abs(center_y - ay)
            if d < bd:
                best, bd = i, d
        return best

    head_row = ['节次'] + ['星期' + '一二三四五六日'[w] for w in weekdays]
    table = [head_row] + [[''] * (len(weekdays) + 1) for _ in anchor_ys]
    for i, t in enumerate(label_texts):
        table[i + 1][0] = t.replace('\n', ' ')

    courses = []
    for ci, items in enumerate(col_items):
        for cell in _cluster_cells(items, cell_gap):
            text = _cell_text(cell)
            if not text:
                continue
            ys = [it['y'] for it in cell]
            center = (min(ys) + max(ys)) / 2
            ri = nearest_row(center)
            ps = parse_label_periods(label_texts[ri].split('\n')[0]) or parse_label_periods(label_texts[ri])
            if not ps:
                ps = [ri + 1]
            courses.extend(parse_cell_text(text, weekdays[ci], ps))
            cell_flat = text.replace('\n', ' ')
            old = table[ri + 1][ci + 1]
            table[ri + 1][ci + 1] = (old + ' / ' + cell_flat) if old else cell_flat
    return table, courses


def text_rows(lines):
    """兜底：按行 + 横向间距切列（认不出课表结构时用，任何 PDF 都能出点东西）"""
    rows = []
    for ln in lines:
        row, cur, last_end = [], '', None
        gap_limit = max(6, ln['h'] * 0.8)
        for it in ln['items']:
            if last_end is not None and it['x0'] - last_end > gap_limit:
                row.append(cur.strip())
                cur = ''
            cur += it['t']
            last_end = it['x1']
        if cur.strip():
            row.append(cur.strip())
        if any(c for c in row):
            rows.append(row)
    return rows


def _ruled_table(page):
    """有框线的 PDF（教务系统常见）可以直接让 MuPDF 找表格，最省事也最准。"""
    try:
        tabs = page.find_tables()
    except Exception:
        return None
    best = None
    for tb in tabs.tables:
        data = [[(c or '').strip() for c in row] for row in tb.extract()]
        if len(data) >= 2 and best is None:
            best = data
    return best


WD_BARE = re.compile(r'^(?:星期|周|礼拜)?\s*([一二三四五六日天])$')


def _wd_of_cell(s):
    t = str(s or '').strip()
    if WD_RE.match(t):
        return parse_weekday(t)
    m = WD_BARE.match(t)
    if m:
        return WEEK_CN.get(m.group(1))
    return None


def courses_from_table(rows):
    """把二维表格（find_tables 出的）当课表再解析一遍。

    教务系统的框线表常常把表头写成「节次 / 一 / 二 / 三 …」，不是「星期一」，
    坐标法认不出来；但表格本身是规整的，这里按「表头行 + 节次列」直接读。
    """
    head_i = None
    for i, row in enumerate(rows):
        hits = [_wd_of_cell(c) for c in row]
        if sum(1 for h in hits if h is not None) >= 3:
            head_i = i
            break
    if head_i is None:
        return []

    head = rows[head_i]
    col_wd = {}
    for ci, cell in enumerate(head):
        w = _wd_of_cell(cell)
        if w is not None:
            col_wd[ci] = w
    if len(col_wd) < 3:
        return []

    first_col = min(col_wd)
    out = []
    for row in rows[head_i + 1:]:
        if not any(str(c).strip() for c in row):
            continue
        ps = None
        for ci in range(first_col):
            if ci < len(row):
                ps = parse_label_periods(str(row[ci]).split('\n')[0]) or parse_label_periods(str(row[ci]))
                if ps:
                    break
        if not ps:
            continue
        for ci, wd in col_wd.items():
            if ci >= len(row):
                continue
            text = str(row[ci]).strip()
            if text:
                out.extend(parse_cell_text(text, wd, ps))
    return out



def read_pdf(path):
    """整份 PDF → 每页的 {lines, table, courses}"""
    if pymupdf is None:
        raise RuntimeError('缺少 PyMuPDF，请先运行：pip install pymupdf')
    pages = []
    with pymupdf.open(path) as doc:
        for pno, page in enumerate(doc):
            items = _words(page)
            if not items:
                pages.append({'page': pno + 1, 'lines': [], 'table': None,
                              'courses': [], 'text': page.get_text('text')})
                continue
            lines = _to_lines(items)
            table, courses = grid_from_lines(lines)
            if table is None:
                table = _ruled_table(page)
                if table:
                    courses = courses_from_table(table)
            pages.append({'page': pno + 1, 'lines': lines, 'table': table,
                          'courses': courses, 'text': page.get_text('text')})
    return pages


def _dedupe(courses):
    seen, out = set(), []
    for c in courses:
        key = (c['name'], c['weekday'], tuple(c['periods']), tuple(c['weeks']),
               c.get('room', ''), c.get('teacher', ''))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def pdf_courses(path):
    """给 课表转日历.py 用：PDF → 课程列表（已去重、按星期/节次排序）"""
    all_c = []
    for p in read_pdf(path):
        all_c.extend(p['courses'])
    all_c = _dedupe(all_c)
    all_c.sort(key=lambda c: (c['weekday'], c['periods'][0], c['name']))
    return all_c


def page_text_rows(path):
    rows = []
    for p in read_pdf(path):
        if p.get('table'):
            rows.extend(p['table'])
        elif p['lines']:
            rows.extend(text_rows(p['lines']))
    return rows


def readable_text(path):
    """扫描件检测：能取到文字才叫「能识别」，否则是图片版 PDF。"""
    total = ''
    for p in read_pdf(path):
        total += p.get('text') or ''
    return len(re.sub(r'\s', '', total))


def has_text(path):
    return readable_text(path) > 20


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法：python pdf课表识别.py 课表.pdf')
        sys.exit(1)
    p = sys.argv[1]
    pages = read_pdf(p)
    n = 0
    for pg in pages:
        print('第 %d 页：%d 行文字，识别出 %d 门课' %
              (pg['page'], len(pg['lines']), len(pg['courses'])))
        n += len(pg['courses'])
        for c in pg['courses']:
            print('  ', c['name'], '周' + '一二三四五六日'[c['weekday']],
                  '%d-%d 节' % (c['periods'][0], c['periods'][-1]),
                  '周次%s' % c['weeks'], c['room'], c['teacher'])
    print('合计 %d 门' % n)
