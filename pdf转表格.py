# -*- coding: utf-8 -*-
"""PDF 转表格（课表专用）

一条命令把 PDF 课表变成三份能直接用的文件：

    课表.csv   → 用 Excel / WPS 打开就是一张排好的表格（节次 × 星期）
    课表.xlsx  → 两张工作表：「课表」原样表格 +「课程明细」一门课一行，方便筛选核对
    课表.txt   → 「课程名 | 星期 | 节次 | 周次 | 教室 | 教师」格式，可直接喂给 课表转日历.py

用法：
    python pdf转表格.py 课表.pdf                 # 输出到 PDF 同目录
    python pdf转表格.py 课表.pdf -o 输出目录
    python pdf转表格.py 课表.pdf --csv-only      # 只出 CSV
    python pdf转表格.py 某文件.pdf --raw         # 认不出课表结构时：只按行导出文字

原理：
    PDF 里没有表格，只有「文字 + 坐标」。识别逻辑全在 pdf课表识别.py：
    按 y 聚行 → 找「星期一…日」表头定列 → 按节次锚点归行 → 单元格拆字段。
    有框线的 PDF 会先用 MuPDF 自带的表格识别，无框线的退回坐标聚类。

依赖：pip install pymupdf openpyxl
"""

import argparse
import csv
import os
import sys

import pdf课表识别 as rec


def week_spec(weeks):
    """[1..16] → '1-16'；[2,4,..,16] → '2-16双'；零散 → '1,3,5'"""
    w = sorted(set(weeks))
    if not w:
        return ''
    if len(w) >= 4:
        odd = all(x % 2 == 1 for x in w)
        even = all(x % 2 == 0 for x in w)
        step2 = all(x - w[i - 1] == 2 for i, x in enumerate(w) if i > 0)
        if step2 and (odd or even):
            return '%d-%d%s' % (w[0], w[-1], '单' if odd else '双')
    parts, s, p = [], w[0], w[0]
    for x in w[1:]:
        if x == p + 1:
            p = x
            continue
        parts.append(str(s) if s == p else '%d-%d' % (s, p))
        s = p = x
    parts.append(str(s) if s == p else '%d-%d' % (s, p))
    return ','.join(parts)


def period_spec(ps):
    if not ps:
        return ''
    return str(ps[0]) if len(ps) == 1 else '%d-%d' % (ps[0], ps[-1])


def write_csv(path, rows):
    # utf-8-sig：Excel 直接双击打开不乱码（没有 BOM 的中文 CSV 会变乱码）
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        for row in rows:
            # 单元格内的换行在 CSV 里会撑破一行，换成「 / 」更好读
            w.writerow([str(c).replace('\r', ' ').replace('\n', ' / ') for c in row])


def write_xlsx(path, table, courses):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    except ImportError:
        raise RuntimeError('缺少 openpyxl，请先运行：pip install openpyxl')

    wb = Workbook()
    ws = wb.active
    ws.title = '课表'
    for row in table:
        ws.append(row)

    thin = Side(style='thin', color='BFBFBF')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill('solid', fgColor='DCE6F1')
    for r in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=ws.max_column):
        for c in r:
            c.border = border
            c.alignment = Alignment(vertical='center', wrap_text=True)
            if c.row == 1:
                c.font = Font(bold=True)
                c.fill = head_fill
                c.alignment = Alignment(horizontal='center', vertical='center')
    widths = [12] + [26] * max(0, ws.max_column - 1)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + i) if i <= 26 else 'A'].width = w
    ws.freeze_panes = 'B2'

    ws2 = wb.create_sheet('课程明细')
    ws2.append(['课程名', '星期', '节次', '周次', '教室', '教师'])
    for c in courses:
        ws2.append([c['name'], '周' + '一二三四五六日'[c['weekday']],
                    period_spec(c['periods']), week_spec(c['weeks']),
                    c.get('room', ''), c.get('teacher', '')])
    for r in ws2.iter_rows(min_row=1, max_row=ws2.max_row, max_col=6):
        for c in r:
            c.border = border
            if c.row == 1:
                c.font = Font(bold=True)
                c.fill = head_fill
    for col, w in zip('ABCDEF', [26, 8, 10, 14, 20, 12]):
        ws2.column_dimensions[col].width = w
    ws2.freeze_panes = 'A2'
    wb.save(path)


def write_txt(path, courses, start_date):
    """输出可直接给 课表转日历.py 吃的文本（| 分隔）"""
    lines = [
        '# 由 课表转表格.py 从 PDF 自动生成',
        '# 学期开始 = 第一周的周一，请按学校校历改成真实日期，再运行 课表转日历.py',
        '学期开始: %s' % start_date,
        '',
        '# 课程：课程名 | 星期 | 节次 | 周次 | 教室 | 教师',
    ]
    for c in courses:
        lines.append(' | '.join([c['name'], '周' + '一二三四五六日'[c['weekday']],
                                 period_spec(c['periods']), week_spec(c['weeks']),
                                 c.get('room', ''), c.get('teacher', '')]))
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def main():
    ap = argparse.ArgumentParser(description='PDF 课表 → CSV / Excel / 文本')
    ap.add_argument('pdf', help='课表 PDF 路径')
    ap.add_argument('-o', '--out-dir', default=None, help='输出目录（默认 PDF 同目录）')
    ap.add_argument('--name', default=None, help='输出文件名前缀（默认取 PDF 文件名）')
    ap.add_argument('--csv-only', action='store_true', help='只生成 CSV')
    ap.add_argument('--raw', action='store_true', help='不做课表识别，按行导出文字')
    ap.add_argument('--start', default=None, help='开学第一周周一（写进 .txt），如 2026-09-07')
    args = ap.parse_args()

    if not os.path.exists(args.pdf):
        print('✗ 找不到文件：%s' % args.pdf)
        return 2
    if not rec.has_text(args.pdf):
        print('✗ 这个 PDF 里没有可提取的文字（扫描件 / 图片版）。')
        print('  两条路：① 用 WPS/Adobe「另存为 Excel」或在线 OCR 转一遍；')
        print('         ② 网页版课表转日历可以直接「粘贴」文字，扫描件截图后用 OCR 提字再粘。')
        return 2

    pages = rec.read_pdf(args.pdf)
    table = None
    courses = []
    for p in pages:
        if p['table'] and (table is None or len(p['table']) > len(table)):
            table = p['table']
        courses.extend(p['courses'])
    courses = rec._dedupe(courses)
    courses.sort(key=lambda c: (c['weekday'], c['periods'][0], c['name']))

    ruled = table is not None
    if args.raw or table is None:
        rows = rec.page_text_rows(args.pdf)
        if rows:
            table = rows
        if not table:
            print('✗ PDF 里没读到文字')
            return 2

    out_dir = args.out_dir or os.path.dirname(os.path.abspath(args.pdf))
    os.makedirs(out_dir, exist_ok=True)
    stem = args.name or os.path.splitext(os.path.basename(args.pdf))[0]

    csv_path = os.path.join(out_dir, stem + '.csv')
    write_csv(csv_path, table or [])
    print('✓ CSV ：%s' % csv_path)

    if not args.csv_only:
        try:
            xlsx_path = os.path.join(out_dir, stem + '.xlsx')
            write_xlsx(xlsx_path, table or [], courses)
            print('✓ Excel：%s' % xlsx_path)
        except RuntimeError as e:
            print('⚠ %s（跳过 Excel）' % e)
        if courses:
            txt_path = os.path.join(out_dir, stem + '_课表.txt')
            write_txt(txt_path, courses, args.start or '2026-09-07')
            print('✓ 文本 ：%s' % txt_path)

    if courses:
        print('\n识别到 %d 门课：' % len(courses))
        for c in courses:
            print('  %-24s 周%s  %-5s  %-8s %s' % (
                c['name'], '一二三四五六日'[c['weekday']], period_spec(c['periods']),
                week_spec(c['weeks']), c.get('room', '')))
        if table and len(table) > 1:
            print('\n表格预览（前 3 行）：')
            for row in table[:3]:
                print('  | ' + ' | '.join(x[:18] for x in row))
    else:
        if ruled:
            print('\n⚠ 表格读出来了，但表头不是「星期一…星期日」这种课表格式，所以没拆出课程。')
            print('  表格已原样导到 CSV/Excel；课程那几列可以手动改，或在网页版粘贴文字。')
        else:
            print('\n⚠ 没识别出课表结构，已按「行 + 横向间距」导出原始文字表格。')
            print('  可以把 CSV 里认错的格子手工改一下，或换网页版粘贴。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
