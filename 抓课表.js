/**
 * 自动抓取教务系统课表（早八自动化）
 *
 * 原理五步：
 * 1) 连上你已经登录的 Edge（调试端口 9222），不碰你的密码；
 * 2) 遍历所有标签页的所有 iframe（强智/正方等系统常把课表放在 iframe 里）；
 * 3) 找「表头含 星期一~星期日」的表格，按 rowspan/colspan 还原成网格；
 * 4) 列 = 星期，行 = 第几节（支持「第一二节」这种两节一行的大节版式）；
 * 5) 每个格子拆成「课程名 / 周次 / 教室 / 教师」，输出成课表.txt 同款格式。
 *
 * 用法：
 *   node 抓课表.js
 *   CHX_TEST_URL=file:///... node 抓课表.js    # 离线测试某个 HTML
 */
const fs = require('fs');
const path = require('path');
const pw = require('playwright-core');

const PORT = process.env.CHX_PORT || '9222';
const TEST_URL = process.env.CHX_TEST_URL || '';
const WAIT = parseInt(process.env.CHX_WAIT_SECONDS || '300', 10);
const EDGE = ['C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe']
  .find(p => { try { return fs.existsSync(p); } catch (e) { return false; } });
const HERE = __dirname;
const log = m => console.log('[抓课表] ' + m);
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* ───────── 页面内：把表格还原成网格（在 frame 里执行） ───────── */
function extractTables() {
  const tables = [...document.querySelectorAll('table')];
  const results = [];
  for (const t of tables) {
    const rows = [...t.rows];
    if (rows.length < 2) continue;
    const grid = [];
    rows.forEach((tr, ri) => {
      grid[ri] = grid[ri] || [];
      let ci = 0;
      [...tr.cells].forEach(td => {
        while (grid[ri][ci] !== undefined) ci++;
        const rs = td.rowSpan || 1, cs = td.colSpan || 1;
        const text = (td.innerText || '').replace(/\u00a0/g, ' ').trim();
        for (let r = 0; r < rs; r++) {
          grid[ri + r] = grid[ri + r] || [];
          for (let c = 0; c < cs; c++) grid[ri + r][ci + c] = text;
        }
        ci += cs;
      });
    });
    // 表头行：含最多「星期X / 周X」的一行
    let best = { row: -1, count: 0, cols: {} };
    grid.forEach((row, ri) => {
      const cols = {};
      row.forEach((cell, ci) => {
        const m = String(cell || '').match(/(?:星期|周)\s*([一二三四五六日天])/);
        if (m) {
          const map = { 一: 0, 二: 1, 三: 2, 四: 3, 五: 4, 六: 5, 日: 6, 天: 6 };
          cols[ci] = map[m[1]];
        }
      });
      const n = Object.keys(cols).length;
      if (n > best.count) best = { row: ri, count: n, cols };
    });
    if (best.count < 3) continue;
    // 打分：星期列数 × 10 + 非空课程格数（避免选中页面上的小挂件表格）
    let nonEmpty = 0;
    for (let r = best.row + 1; r < grid.length; r++) {
      for (const c of Object.keys(best.cols)) {
        const v = grid[r][parseInt(c, 10)];
        if (v && v.length > 1 && !/^(星期|周)/.test(v)) nonEmpty++;
      }
    }
    results.push({ grid, headerRow: best.row, cols: best.cols,
      score: best.count * 10 + nonEmpty, rows: grid.length });
  }
  results.sort((a, b) => b.score - a.score);
  return results[0] || null;
}

/* ───────── 周次 ───────── */
function weekSpec(weeks) {
  if (!weeks.length) return '';
  weeks = [...new Set(weeks)].sort((a, b) => a - b);
  if (weeks.length >= 4) {
    const odd = weeks.every(w => w % 2 === 1);
    const even = weeks.every(w => w % 2 === 0);
    const step2 = weeks.every((w, i) => i === 0 || w - weeks[i - 1] === 2);
    if (step2 && (odd || even)) return `${weeks[0]}-${weeks[weeks.length - 1]}${odd ? '单' : '双'}`;
  }
  const parts = [];
  let s = weeks[0], p = weeks[0];
  for (let i = 1; i < weeks.length; i++) {
    if (weeks[i] === p + 1) { p = weeks[i]; continue; }
    parts.push(s === p ? String(s) : `${s}-${p}`);
    s = p = weeks[i];
  }
  parts.push(s === p ? String(s) : `${s}-${p}`);
  return parts.join(',');
}

function parseWeeks(text) {
  const t = String(text || '');
  // 兼容三种写法：1-16周 / 1-16(周) / 1-4,6,8,10,12(周)[01-02节]
  let core = t.replace(/\[[^\]]*\]/g, ' ');
  const m = core.match(/([\d,，\-~至\s]*\d[\d,，\-~至\s]*)[（(]?\s*[单双]?\s*周/);
  if (m) core = m[1];
  core = core.replace(/[单双]/g, '');
  const parity = /单周?/.test(t) ? 1 : /双周?/.test(t) ? 0 : null;
  const out = new Set();
  core.split(/[,，、\s]+/).forEach(part => {
    if (!part) return;
    const r = part.match(/^(\d{1,2})\s*[-~至]\s*(\d{1,2})$/);
    if (r) {
      for (let w = Math.min(+r[1], +r[2]); w <= Math.max(+r[1], +r[2]); w++) {
        if (parity === null || w % 2 === parity) out.add(w);
      }
    } else if (/^\d{1,2}$/.test(part)) {
      const w = +part;
      if (parity === null || w % 2 === parity) out.add(w);
    }
  });
  return [...out].sort((a, b) => a - b);
}

/* ───────── 节次标签：「第一二节」→ [1,2] ───────── */
const CN_NUM = { 一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10 };
function parseLabelPeriods(label) {
  const s = String(label || '').replace(/\s+/g, '');
  const d = s.match(/(\d{1,2})\s*[-~至]\s*(\d{1,2})/);
  if (d) {
    const a = +d[1], b = +d[2], out = [];
    for (let i = Math.min(a, b); i <= Math.max(a, b); i++) out.push(i);
    return out;
  }
  const d2 = s.match(/(\d{1,2})/);
  if (d2) return [+d2[1]];
  const cn = s.match(/第?([一二三四五六七八九十]+)节/);
  if (cn) {
    const nums = [...cn[1]].map(ch => CN_NUM[ch]).filter(Boolean);
    if (nums.length) return [...new Set(nums)].sort((a, b) => a - b);
  }
  return null;
}

/* ───────── 单元格解析 ───────── */
function parseCell(text, weekday, periods) {
  if (!text || !text.trim()) return [];
  // 用连续短横线或空行分隔同格里的多门课
  const blocks = text.split(/\n?\s*-{4,}\s*\n?|\n{2,}/).map(s => s.trim()).filter(Boolean);
  const items = [];
  for (const block of blocks) {
    const lines = block.split('\n').map(s => s.trim()).filter(Boolean);
    let name = '', room = '', teacher = '';
    let weekLine = '';
    for (const ln of lines) {
      // 1) 周次行（含数字 + 周），优先摘出来
      if (!weekLine && /\d/.test(ln) && /周/.test(ln)) { weekLine = ln; continue; }
      // 2) 第一行永远是课程名（避免「大学物理实验」被当成教室）
      if (!name) { name = ln; continue; }
      // 3) 教室（只认「实验室」，不认课程名里的「实验」）
      if (!room && (/(楼|室|馆|场|中心|机房|实验室|线上|腾讯会议|钉钉|超星|学习通)/.test(ln) || /教\s*\d/.test(ln))) {
        room = ln; continue;
      }
      // 4) 教师：2-4 个纯中文，或带「老师/教师」
      if (!teacher && /^[\u4e00-\u9fa5]{2,4}$/.test(ln)) { teacher = ln; continue; }
      if (!teacher && /(老师|教师|教授|讲师)/.test(ln)) { teacher = ln.replace(/[（(].*?[）)]/g, '').trim(); continue; }
    }
    name = name.replace(/\s*[（(]?\d{1,2}\s*[-~至]\s*\d{1,2}\s*(?:周|\(周\)).*$/, '').trim();
    name = name.replace(/\s*[（(][A-Za-z]?[）)]\s*$/, '').trim();
    if (!name) continue;
    const weeks = parseWeeks(weekLine || block);
    if (!weeks.length) continue;
    if (!periods || !periods.length) continue;
    items.push({ name, weekday, periods: [...periods], weeks, room, teacher });
  }
  return items;
}

async function main() {
  let browser, page, own = false;
  if (TEST_URL) {
    browser = await pw.chromium.launch({ executablePath: EDGE, headless: true });
    page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
    await page.goto(TEST_URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
    own = true;
  } else {
    browser = await pw.chromium.connectOverCDP('http://127.0.0.1:' + PORT);
    log('已连接浏览器，共 ' + browser.contexts()[0].pages().length + ' 个标签页');
    log('请在浏览器里打开你的课表页面（能看到 星期一~星期日 那张表）');
    let found = false;
    let lastCount = -1;
    for (let i = 0; i < WAIT; i += 2) {
      // 关键：每轮都重新取一次标签页，否则用户新开的页面永远看不到
      const pages = browser.contexts()[0].pages();
      if (pages.length !== lastCount) {
        lastCount = pages.length;
        log('当前标签页数：' + pages.length);
      }
      for (const p of pages) {
        for (const f of p.frames()) {
          try {
            const ok = await f.evaluate(() => {
              const ts = [...document.querySelectorAll('table')];
              return ts.some(t => /(?:星期|周)\s*[一二三四五六日天]/.test(t.innerText || ''));
            });
            if (ok) { found = true; break; }
          } catch (e) {}
        }
        if (found) break;
      }
      if (found) break;
      if (i && i % 20 === 0) log('仍在等待…已等 ' + i + ' 秒（最多 ' + WAIT + ' 秒）');
      await sleep(2000);
    }
    if (!found) { log('✗ 没等到含星期表头的表格，先打开课表页再重跑'); process.exit(2); }
    log('✓ 找到含星期表头的表格');
  }

  // 遍历所有标签页 + 所有 iframe，取分数最高的那张表
  let best = null;
  const ctx = browser.contexts()[0];
  for (const p of ctx.pages()) {
    for (const f of p.frames()) {
      if (!/^(https?|file):/i.test(f.url() || '')) continue;
      const data = await f.evaluate(extractTables).catch(() => null);
      if (data && (!best || data.score > best.score)) {
        best = { ...data, frameUrl: f.url(), pageUrl: p.url(), page: p };
      }
    }
  }
  if (!best) { log('✗ 页面里没有找到课表表格'); process.exit(2); }

  // ── 自动识别学期与当前周次，推算第一周周一（不用手填开学日期）──
  let term = '', currentWeek = null;
  for (const f of best.page.frames()) {
    try {
      const info = await f.evaluate(() => {
        const t = (document.body ? document.body.innerText : '').replace(/\s+/g, ' ');
        const m1 = t.match(/学期\s*[:：]?\s*(20\d{2})\s*[-—–]\s*(20\d{2})\s*[-—–]\s*([12])/);
        const m2 = t.match(/第\s*(\d{1,2})\s*周\s*\/\s*\d{1,2}\s*周/);
        return { term: m1 ? (m1[1] + '-' + m1[2] + '-' + m1[3]) : '', week: m2 ? +m2[1] : null };
      });
      if (info.week && !currentWeek) currentWeek = info.week;
      if (info.term && !term) term = info.term;
    } catch (e) {}
  }
  let detectedStart = null;
  if (currentWeek) {
    const now = new Date(); now.setHours(0, 0, 0, 0);
    const monday = new Date(now);
    monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7));
    const start = new Date(monday);
    start.setDate(start.getDate() - (currentWeek - 1) * 7);
    detectedStart = start;
    log('识别到学期：' + (term || '未标注') + '，当前第 ' + currentWeek + ' 周 → 第一周周一 = '
        + start.getFullYear() + '-' + String(start.getMonth() + 1).padStart(2, '0') + '-' + String(start.getDate()).padStart(2, '0'));
  }

  log('命中页面：' + best.frameUrl.slice(0, 80));
  log('表头在第 ' + (best.headerRow + 1) + ' 行，识别到 ' + Object.keys(best.cols).length + ' 个星期列，共 ' + best.rows + ' 行');

  const items = [];
  const fallbackTwo = (best.rows - best.headerRow - 1) <= 6;  // 行数少 → 大节版式（一行两节）
  for (let ri = best.headerRow + 1; ri < best.grid.length; ri++) {
    const row = best.grid[ri];
    const label = row[0] || '';
    let periods = parseLabelPeriods(label);
    if (!periods) {
      const idx = ri - best.headerRow;
      periods = fallbackTwo ? [idx * 2 - 1, idx * 2] : [idx];
    }
    for (const ciStr of Object.keys(best.cols)) {
      const text = row[parseInt(ciStr, 10)];
      if (text) items.push(...parseCell(text, best.cols[ciStr], periods));
    }
  }
  if (!items.length) { log('✗ 表格里没解析出课程（可能是图片课表或特殊控件）'); process.exit(2); }

  // 合并同一门课的连续节次
  const merged = [];
  const keyOf = it => [it.name, it.weekday, it.room, it.teacher].join('|');
  const map = {};
  for (const it of items.sort((a, b) => a.weekday - b.weekday || a.periods[0] - b.periods[0])) {
    const k = keyOf(it);
    const p1 = it.periods[0], p2 = it.periods[it.periods.length - 1];
    if (map[k]) {
      const m = map[k];
      if (p1 <= m.p2 + 1 && p2 >= m.p1 - 1) {
        m.p1 = Math.min(m.p1, p1);
        m.p2 = Math.max(m.p2, p2);
        m.weeks = [...new Set([...m.weeks, ...it.weeks])].sort((a, b) => a - b);
        continue;
      }
    }
    const obj = { ...it, p1, p2, weeks: [...it.weeks] };
    map[k] = obj;
    merged.push(obj);
  }

  const wd = ['一', '二', '三', '四', '五', '六', '日'];
  log('解析出 ' + merged.length + ' 门次课程：');
  merged.forEach(c => {
    const ps = c.p1 === c.p2 ? String(c.p1) : `${c.p1}-${c.p2}`;
    log(`  周${wd[c.weekday]} 第${ps}节  ${c.name}  ${weekSpec(c.weeks)}周  ${c.room || ''} ${c.teacher || ''}`);
  });

  // 读取 课表.txt 的头部（学期开始 + 节次时间），保留用户设置
  const baseFile = path.join(HERE, '课表.txt');
  let header = '';
  if (fs.existsSync(baseFile)) {
    const lines = fs.readFileSync(baseFile, 'utf-8').split(/\r?\n/);
    const buf = [];
    for (const ln of lines) {
      const isComment = ln.trim().startsWith('#');
      if (!isComment && ln.includes('|')) break;
      buf.push(ln);
    }
    header = buf.join('\n').trimEnd() + '\n\n';
  } else {
    header = '# 未找到 课表.txt，请补上 学期开始: YYYY-MM-DD\n\n';
  }
  // 自动识别的开学日期优先覆盖（识别不到就保留手填值）
  if (detectedStart) {
    const ds = detectedStart.getFullYear() + '-' + String(detectedStart.getMonth() + 1).padStart(2, '0')
      + '-' + String(detectedStart.getDate()).padStart(2, '0');
    if (/^学期开始\s*[:：]/m.test(header)) {
      header = header.replace(/^学期开始\s*[:：].*$/m, '学期开始: ' + ds);
    } else {
      header = '学期开始: ' + ds + '\n' + header;
    }
    header = header.replace(/^# .*$/m, '# ' + (term ? term + ' 学期' : '') + ' 课表（自动抓取）');
  }
  const body = merged.map(c => {
    const ps = c.p1 === c.p2 ? String(c.p1) : `${c.p1}-${c.p2}`;
    return [c.name, '周' + wd[c.weekday], ps, weekSpec(c.weeks), c.room || '', c.teacher || ''].join(' | ');
  }).join('\n');

  const out = path.join(HERE, '课表_抓取.txt');
  fs.writeFileSync(out, header + body + '\n', 'utf-8');
  log('✓ 已写入 ' + out);
  log('接下来：python 课表转日历.py 课表_抓取.txt');

  if (own) await browser.close();
  process.exit(0);   // CDP 模式下连接会保持事件循环，必须显式退出
}

main().catch(e => { console.error('[抓课表] FATAL', e.message); process.exit(1); });
