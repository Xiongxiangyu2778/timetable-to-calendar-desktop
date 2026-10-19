# -*- coding: utf-8 -*-
"""课表转日历（早八自动化）

把教务系统 / Excel 里复制出来的课表，转成手机日历能直接导入的 .ics 文件。

原理三句话：
1) 每门课 = 星期几 + 第几节 + 哪几周 + 教室，这四样定下来就能算出每次上课的具体日期；
2) 节次时间表把「第几节」翻译成「几点几分」，所以脚本里有一张可改的作息表；
3) 每个「某周某天某节课」写成一条 VEVENT（日历事件），手机日历读 .ics 就会全部加进去。

支持：
- 苹果 iOS 日历、安卓 Google 日历、小米/华为/OPPO/vivo 自带日历、Windows/Outlook
- 单周、双周、指定周次（如 1,3,5,7）、连续多节（如 3-4 节）
- 上课前 15 分钟提醒

用法：
    python 课表转日历.py                 # 读同目录 课表.txt → 生成 我的课表.ics
    python 课表转日历.py 我的课表.txt -o 输出.ics
    python 课表转日历.py --preview       # 只看前两周的安排，不生成文件
"""

import argparse
import datetime as dt
import os
import re
import shutil
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT = os.path.join(HERE, '课表.txt')
DEFAULT_OUTPUT = os.path.join(HERE, '我的课表.ics')

# 默认作息（可在 课表.txt 的「节次时间」里覆盖）
DEFAULT_PERIODS = {
    1: ('08:00', '08:45'), 2: ('08:55', '09:40'),
    3: ('10:00', '10:45'), 4: ('10:55', '11:40'),
    5: ('14:00', '14:45'), 6: ('14:55', '15:40'),
    7: ('16:00', '16:45'), 8: ('16:55', '17:40'),
    9: ('19:00', '19:45'), 10: ('19:55', '20:40'),
    11: ('20:50', '21:35'), 12: ('21:45', '22:30'),
}

WEEKDAY_CN = {'一': 0, '二': 1, '三': 2, '四': 3, '五': 4, '六': 5, '日': 6, '天': 6}
WEEKDAY_ALIAS = {'周一': 0, '星期一': 0, '礼拜一': 0, 'Mon': 0,
                 '周二': 1, '星期二': 1, '礼拜二': 1, 'Tue': 1,
                 '周三': 2, '星期三': 2, '礼拜三': 2, 'Wed': 2,
                 '周四': 3, '星期四': 3, '礼拜四': 3, 'Thu': 3,
                 '周五': 4, '星期五': 4, '礼拜五': 4, 'Fri': 4,
                 '周六': 5, '星期六': 5, '礼拜六': 5, 'Sat': 5,
                 '周日': 6, '星期日': 6, '周天': 6, 'Sun': 6}


def log(msg):
    print(msg, flush=True)


# ───────────────────────── 解析 ─────────────────────────

def parse_weekday(text):
    """周一 / 星期一 / 礼拜一 / 一 / 1 → 0-6（0=周一）"""
    t = str(text).strip()
    for k, v in WEEKDAY_ALIAS.items():
        if k in t:
            return v
    m = re.search(r'[周星期礼拜]\s*([一二三四五六日天1-7])', t)
    if m:
        c = m.group(1)
        return WEEKDAY_CN.get(c, int(c) - 1 if c.isdigit() else 0)
    if t.isdigit() and 1 <= int(t) <= 7:
        return int(t) - 1
    return None


def parse_periods(text):
    """'1-2' → [1,2]；'3,4' → [3,4]；'5' → [5]"""
    t = str(text).strip().replace('节', '').replace('第', '')
    nums = []
    for part in re.split(r'[,，、\s]+', t):
        if not part:
            continue
        m = re.match(r'^(\d+)\s*[-~至]\s*(\d+)$', part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            nums.extend(range(min(a, b), max(a, b) + 1))
        elif part.isdigit():
            nums.append(int(part))
    return sorted(set(nums))


def parse_weeks(text):
    """'1-16' / '1-16单' / '2-16双' / '1,3,5' / '1-8,10-16' → 周次列表"""
    t = str(text).strip().replace('周', '').replace('第', '')
    weeks = set()
    for part in re.split(r'[,，、\s]+', t):
        if not part:
            continue
        parity = None
        if '单' in part:
            parity = 1
        elif '双' in part:
            parity = 0
        core = re.sub(r'[单双]', '', part)
        m = re.match(r'^(\d+)\s*[-~至]\s*(\d+)$', core)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            rng = range(min(a, b), max(a, b) + 1)
        elif core.isdigit():
            rng = [int(core)]
        else:
            continue
        for w in rng:
            if parity is None or w % 2 == parity:
                weeks.add(w)
    return sorted(weeks)


def parse_time_range(text):
    """'08:00-08:45' → (time, time)"""
    m = re.search(r'(\d{1,2}):(\d{2})\s*[-~至]\s*(\d{1,2}):(\d{2})', str(text))
    if not m:
        return None
    h1, m1, h2, m2 = map(int, m.groups())
    return dt.time(h1, m1), dt.time(h2, m2)


def parse_timetable(path):
    """读课表文本 → (学期开始日期, 作息表, 课程列表)"""
    with open(path, encoding='utf-8') as f:
        raw_lines = f.read().splitlines()

    start_date = None
    periods = dict(DEFAULT_PERIODS)
    courses = []
    section = None       # 'periods' / 'courses'

    for ln, raw in enumerate(raw_lines, 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        # 学期开始日期
        m = re.match(r'^(学期开始|开学日期|第一周周一)\s*[:：]\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', line)
        if m:
            start_date = dt.date(int(m.group(2)), int(m.group(3)), int(m.group(4)))
            continue
        # 区块标题
        if re.match(r'^(节次时间|作息时间)\s*[:：]?\s*$', line):
            section = 'periods'
            continue
        if re.match(r'^(课程|课表)\s*[:：]?\s*$', line):
            section = 'courses'
            continue
        # 作息：1: 08:00-08:45
        m = re.match(r'^(\d{1,2})\s*[:：]\s*(.+)$', line)
        if section == 'periods' and m and not line.startswith('20'):
            p = int(m.group(1))
            tr = parse_time_range(m.group(2))
            if tr:
                periods[p] = (tr[0].strftime('%H:%M'), tr[1].strftime('%H:%M'))
                continue
        # 课程行：课程名 | 星期 | 节次 | 周次 | 教室 | 教师
        if '|' in line:
            cols = [c.strip() for c in line.split('|')]
            if len(cols) < 4:
                log('⚠ 第 %d 行字段不足，已跳过：%s' % (ln, line))
                continue
            name = cols[0]
            wd = parse_weekday(cols[1])
            ps = parse_periods(cols[2])
            ws = parse_weeks(cols[3])
            room = cols[4] if len(cols) > 4 else ''
            teacher = cols[5] if len(cols) > 5 else ''
            if not (name and ps and ws) or wd is None:
                log('⚠ 第 %d 行解析失败，已跳过：%s' % (ln, line))
                continue
            courses.append({'name': name, 'weekday': wd, 'periods': ps,
                            'weeks': ws, 'room': room, 'teacher': teacher})
            continue

    return start_date, periods, courses


# ───────────────────────── 生成 ICS ─────────────────────────

def ics_escape(s):
    return (str(s).replace('\\', '\\\\').replace(';', r'\;')
            .replace(',', r'\,').replace('\n', r'\n'))


def fold_line(line, limit=73):
    """ICS 规定单行不超过 75 字节，超长要折行（续行以空格开头）。"""
    data = line.encode('utf-8')
    if len(data) <= limit:
        return [line]
    out, buf, size = [], '', 0
    for ch in line:
        b = len(ch.encode('utf-8'))
        if size + b > limit:
            out.append(buf)
            buf, size = ' ' + ch, 1 + b     # 续行前加一个空格
        else:
            buf += ch
            size += b
    if buf:
        out.append(buf)
    return out


def make_ics(start_date, periods, courses, cal_name='我的课表'):
    now = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//早八自动化//课表转日历//CN',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        'X-WR-CALNAME:' + ics_escape(cal_name),
        'X-WR-TIMEZONE:Asia/Shanghai',
    ]
    total = 0
    for c in courses:
        # 每节课的起止时间
        p_first, p_last = c['periods'][0], c['periods'][-1]
        if p_first not in periods or p_last not in periods:
            log('⚠ %s 的节次 %s 超出作息表，已跳过' % (c['name'], c['periods']))
            continue
        t_start = periods[p_first][0]
        t_end = periods[p_last][1]
        for w in c['weeks']:
            day = start_date + dt.timedelta(days=(w - 1) * 7 + c['weekday'])
            # ⚠ 时区修复：以前写「浮点本地时间」（不带 Z），部分手机日历会把它
            # 当成 UTC 读入 → 全部事件平移 +8 小时，下午的课跨午夜跑到第二天。
            # 现在显式换算成 UTC（带 Z 后缀），任何日历都会正确换算回北京时间。
            st = (dt.datetime.combine(day, dt.time(*map(int, t_start.split(':'))))
                  - dt.timedelta(hours=8))
            en = (dt.datetime.combine(day, dt.time(*map(int, t_end.split(':'))))
                  - dt.timedelta(hours=8))
            ds = st.strftime('%Y%m%dT%H%M%SZ')
            de = en.strftime('%Y%m%dT%H%M%SZ')
            desc = []
            if c['teacher']:
                desc.append('教师：' + c['teacher'])
            desc.append('周次：第 %d 周' % w)
            event = [
                'BEGIN:VEVENT',
                'UID:%s@zaoba-automation' % uuid.uuid4().hex,
                'DTSTAMP:' + now,
                'DTSTART:' + ds,
                'DTEND:' + de,
                'SUMMARY:' + ics_escape(c['name']),
            ]
            if c['room']:
                event.append('LOCATION:' + ics_escape(c['room']))
            if desc:
                event.append('DESCRIPTION:' + ics_escape('｜'.join(desc)))
            event += [
                'BEGIN:VALARM',
                'TRIGGER:-PT15M',
                'ACTION:DISPLAY',
                'DESCRIPTION:' + ics_escape('15 分钟后上课：' + c['name']),
                'END:VALARM',
                'END:VEVENT',
            ]
            lines.extend(event)
            total += 1
    lines.append('END:VCALENDAR')
    # 折行 + CRLF
    folded = []
    for ln in lines:
        folded.extend(fold_line(ln))
    return '\r\n'.join(folded) + '\r\n', total


def preview(start_date, periods, courses, days=7):
    if not courses:
        log('没有可预览的课程')
        return
    log('\n第一周课表预览（%s 起）' % start_date)
    log('-' * 52)
    for wd in range(days):
        day = start_date + dt.timedelta(days=wd)
        todays = [c for c in courses if c['weekday'] == wd and 1 in c['weeks']]
        if not todays:
            continue
        todays.sort(key=lambda c: c['periods'][0])
        log('%s %s' % (day.strftime('%m-%d'), '周' + '一二三四五六日'[wd]))
        for c in todays:
            p1, p2 = c['periods'][0], c['periods'][-1]
            log('   %s-%s  %s  %s' % (periods[p1][0], periods[p2][1], c['name'], c['room']))
    log('-' * 52)


def main():
    ap = argparse.ArgumentParser(description='课表转 .ics（苹果/安卓通用）')
    ap.add_argument('input', nargs='?', default=DEFAULT_INPUT, help='课表文本文件（默认 课表.txt）')
    ap.add_argument('-o', '--output', default=DEFAULT_OUTPUT, help='输出 .ics（默认 我的课表.ics）')
    ap.add_argument('--preview', action='store_true', help='只看预览，不生成文件')
    args = ap.parse_args()

    if not os.path.exists(args.input):
        log('✗ 找不到课表文件：%s' % args.input)
        log('  请把教务系统课表按 示例课表.txt 的格式填好，存成 课表.txt')
        return 2

    start_date, periods, courses = parse_timetable(args.input)
    if not start_date:
        log('✗ 缺「学期开始: 2026-02-23」这一行（第一周的周一）')
        return 2
    if start_date.weekday() != 0:
        log('⚠ 学期开始日期不是周一，已自动对齐到当周周一')
        start_date -= dt.timedelta(days=start_date.weekday())
    if not courses:
        log('✗ 没解析到任何课程，检查每行是否用 | 分隔')
        return 2

    log('学期开始：%s（周一）' % start_date.strftime('%Y-%m-%d'))
    log('课程数量：%d 门' % len(courses))
    preview(start_date, periods, courses)
    if args.preview:
        return 0

    content, total = make_ics(start_date, periods, courses)
    # 覆盖前自动备份，避免误操作把上次的课表冲掉
    if os.path.exists(args.output):
        try:
            shutil.copy2(args.output, args.output + '.bak')
            log('ℹ 已备份旧文件：%s.bak' % os.path.basename(args.output))
        except Exception:
            pass
    with open(args.output, 'w', encoding='utf-8', newline='') as f:
        f.write(content)
    size = os.path.getsize(args.output)
    log('\n✓ 已生成：%s' % args.output)
    log('  共 %d 条上课提醒（%.1f KB）' % (total, size / 1024))
    log('\n导入方法：')
    log('  iPhone：把 .ics 发到微信 → 用「其他应用打开」→ 选「日历」→ 全部添加')
    log('  安卓  ：文件管理里点开 .ics → 选「日历」导入；或网页版 Google 日历 → 设置 → 导入')
    log('  电脑  ：双击 .ics → Outlook / 系统日历自动导入')
    return 0


if __name__ == '__main__':
    sys.exit(main())
