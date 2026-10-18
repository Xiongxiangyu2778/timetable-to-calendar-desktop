/**
 * 自动抓取教务系统课表（早八自动化）
 *
 * 原理四步：
 * 1) 连上你已经登录的 Edge（调试端口 9222），不碰你的密码；
 * 2) 在你打开的课表页面里，找「表头含 星期一~星期日」的那张表格；
 * 3) 把表格按 rowspan/colspan 还原成网格：列 = 星期，行 = 第几节；
 * 4) 每个格子里的文字拆成「课程名 / 周次 / 教室 / 教师」，输出成课表.txt 同款格式。
 *
 * 用法：
 *   node 抓课表.js                      # 等你手动打开课表页（最多 300 秒）
 *   CHX_TEST_URL=file:///... node 抓课表.js   # 离线测试某个 HTML
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

// ── 页面内：把表格还原成网格并返回原始文本 ──
function extractTables() {
  const tables = [...document.querySelectorAll('table')];
  const results = [];
  for (const t of tables) {
    const rows = [...t.rows];
    if (!rows.length) continue;
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
    // 找表头行：含最多「星期X / 周X」的一行
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
    if (best.count >= 3) results.push({ grid, headerRow: best.row, cols: best.cols, title: document.title });
  }
  results.sort((a, b) => b.headerRow - a.headerRow);
  return results.length ? results[0] : null;
}

function weekSpec(weeks) {
  // [1,2,3,5,6,7] → "1-3,5-7"；全为等差 2 → "1-15单" / "2-16双"
  if (!weeks.length) return '';
  weeks = [...new Set(weeks)].sort((a, b) => a - b);
  if (weeks.length >= 4) {
    const odd = weeks.every(w => w % 2 === 1);
    const even = weeks.every(w => w % 2 === 0);
    const step2 = weeks.every((w, i) => i === 0 || w - weeks[i - 1] === 2);
    if (step2 && (odd || even)) {
      return `${weeks[0]}-${weeks[weeks.length - 1]}${odd ? '单' : '双'}`;
    }
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
  const out = new Set();
  const re = /(\d{1,2})\s*[-~至]\s*(\d{1,2})\s*周|第?\s*(\d{1,2})\s*周/g;
  let m;
  while ((m = re.exec(t))) {
    const a = parseInt(m[1] || m[3], 10);
    const b = parseInt(m[2] || m[3], 10);
    const parity = /单周?/.test(t) ? 1 : /双周?/.test(t) ? 0 : null;
    for (let w = Math.min(a, b); w <= Math.max(a, b); w++) {
      if (parity === null || w % 2 === parity) out.add(w);
    }
  }
  return [...out].sort((a, b) => a - b);
}

function parseCell(text, weekday, period) {
  if (!text || /^\s*$/.test(text)) return [];
  // 有些系统把多门课放在同一格，用空行或分隔线隔开
  const blocks = text.split(/\n{2,}|-{3,}|—{3,}/).map(s => s.trim()).filter(Boolean);
  const items = [];
  for (const block of blocks) {
    const lines = block.split('\n').map(s => s.trim()).filter(Boolean);
    const weeks = parseWeeks(block);
    let room = '', teacher = '', name = '';
    for (const ln of lines) {
      if ((/(楼|室|馆|场|中心|机房|实验|线上|腾讯会议|钉钉|超星|学习通)/.test(ln) || /教\s*\d/.test(ln)) && !room) { room = ln; continue; }
      if (/(老师|教师|教授|讲师)/.test(ln) && !teacher) { teacher = ln; continue; }
      if (/^\d{1,2}\s*[-~至]\s*\d{1,2}\s*周/.test(ln) || /^第?\s*\d{1,2}\s*周/.test(ln)) continue;
      if (!name) name = ln;
    }
    if (!name) continue;
    // 去掉课程名里的周次尾巴
    name = name.replace(/\s*[（(]?\d{1,2}\s*[-~至]\s*\d{1,2}\s*周[）)]?.*$/, '').trim();
    if (!name) continue;
    items.push({ name, weekday, period, weeks, room, teacher });
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
    const pages = browser.contexts()[0].pages();
    log('已连接浏览器，共 ' + pages.length + ' 个标签页');
    log('请在浏览器里打开你的课表页面（能看到 星期一~星期日 那张表）');
    let found = null;
    for (let i = 0; i < WAIT; i += 2) {
      for (const p of pages) {
        try {
          const has = await p.evaluate(() => {
            const ts = [...document.querySelectorAll('table')];
            return ts.some(t => /(?:星期|周)\s*[一二三四五六日天]/.test(t.innerText || ''));
          });
          if (has) { found = p; break; }
        } catch (e) {}
      }
      if (found) break;
      if (i % 20 === 0 && i) log('仍在等待…已等 ' + i + ' 秒（最多 ' + WAIT + ' 秒）');
      await sleep(2000);
    }
    if (!found) { log('✗ 没等到含星期表头的表格，先打开课表页再重跑'); process.exit(2); }
    page = found;
    log('✓ 找到课表表格：' + (await page.title()).slice(0, 40));
  }

  const data = await page.evaluate(extractTables);
  if (!data) { log('✗ 页面里没有找到课表表格'); process.exit(2); }
  log('表头在第 ' + (data.headerRow + 1) + ' 行，识别到 ' + Object.keys(data.cols).length + ' 个星期列');

  const items = [];
  data.grid.forEach((row, ri) => {
    if (ri <= data.headerRow) return;
    const period = ri - data.headerRow;
    Object.keys(data.cols).forEach(ciStr => {
      const ci = parseInt(ciStr, 10);
      const text = row[ci];
      if (text) items.push(...parseCell(text, data.cols[ci], period));
    });
  });
  if (!items.length) { log('✗ 表格里没解析出课程（可能是图片课表或特殊控件）'); process.exit(2); }

  // 合并同一门课的连续节次
  const merged = [];
  const keyOf = it => [it.name, it.weekday, it.weeks.join(','), it.room, it.teacher].join('|');
  const map = {};
  for (const it of items.sort((a, b) => a.weekday - b.weekday || a.period - b.period)) {
    const k = keyOf(it);
    if (map[k]) {
      const last = map[k];
      if (it.period === last.p2 + 1 && last.weekday === it.weekday) { last.p2 = it.period; continue; }
    }
    const obj = { ...it, p1: it.period, p2: it.period };
    map[k] = obj;
    merged.push(obj);
  }

  const wd = ['一', '二', '三', '四', '五', '六', '日'];
  log('解析出 ' + merged.length + ' 门次课程：');
  merged.forEach(c => {
    log(`  周${wd[c.weekday]} 第${c.p1}${c.p2 > c.p1 ? '-' + c.p2 : ''}节  ${c.name}  ${weekSpec(c.weeks)}周  ${c.room || ''}`);
  });

  // 读取 课表.txt 的头部（学期开始 + 节次时间），保留用户设置
  const baseFile = path.join(HERE, '课表.txt');
  let header = '';
  if (fs.existsSync(baseFile)) {
    const lines = fs.readFileSync(baseFile, 'utf-8').split(/\r?\n/);
    const buf = [];
    for (const ln of lines) {
      const isComment = ln.trim().startsWith('#');
      if (!isComment && ln.includes('|')) break;   // 第一门课之前都是头部（跳过注释）
      buf.push(ln);
    }
    header = buf.join('\n').trimEnd() + '\n\n';
  } else {
    header = '# 未找到 课表.txt，请补上 学期开始: YYYY-MM-DD\n\n';
  }
  const body = merged.map(c => {
    const periods = c.p1 === c.p2 ? String(c.p1) : `${c.p1}-${c.p2}`;
    return [c.name, '周' + wd[c.weekday], periods, weekSpec(c.weeks),
      c.room || '', c.teacher || ''].join(' | ');
  }).join('\n');

  const out = path.join(HERE, '课表_抓取.txt');
  fs.writeFileSync(out, header + body + '\n', 'utf-8');
  log('✓ 已写入 ' + out);
  log('接下来：python 课表转日历.py 课表_抓取.txt');

  if (own) await browser.close();
}

main().catch(e => { console.error('[抓课表] FATAL', e.message); process.exit(1); });
