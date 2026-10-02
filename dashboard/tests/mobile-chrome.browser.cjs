// Isolated local-stack E2E for the compact phone chrome: one 48px top bar
// (menu, page title, page actions), hide-on-scroll, no bottom nav inside a
// conversation, one-row composers, and dropdowns that open on a completed tap
// instead of on first touch (a scroll that started on a card's Priority tag
// used to open its menu).
// Real auth/UI; the conversation is a stubbed API fixture so no model runs.
// Source dashboard/.env, then NODE_PATH=<playwright node_modules> node tests/mobile-chrome.browser.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium, devices } = require('playwright');

const base = process.env.AUTH_URL;
assert.match(base, /^http:\/\/localhost:/);
const shots = process.env.SHOTS_DIR || '/tmp/mobile-chrome-shots';
fs.mkdirSync(shots, { recursive: true });

const CONV_ID = 'qa-mobile-chrome-1';
const para = 'Retention rose over the last two sprints and three campaigns moved the needle: the streak reminder, the spin the wheel offer and the onboarding tooltips. ';
const fixture = (userName) => ({
  conversation: {
    conversation_id: CONV_ID, prompt: 'Summarise the Q3 mobile engagement numbers.', final_response: '', status: 'completed',
    title: 'Q3 mobile engagement summary', project_id: null,
    messages: [{ role: 'user', content: 'Summarise the Q3 mobile engagement numbers.', timestamp: '2026-09-17T00:00:00Z' }],
    metadata: { user_name: userName, visibility: 'private' },
  },
  turns: [1, 2, 3, 4, 5, 6].map((n) => ({ _id: `t${n}`, conversation_id: CONV_ID, turn_number: n, timestamp: '2026-09-17T00:00:05Z', message_type: 'assistant', text_blocks: [{ text: `Part ${n}. ${para.repeat(3)}` }] })),
  artifacts: [],
});
const TITLES = ['Draft the Q3 business review deck', 'Check why the streak widget is not showing', 'Reply to the infosec questionnaire', 'Summarise yesterday’s customer calls', 'Review the SDK release digest', 'Prepare the RFP response outline', 'Audit push notification templates', 'Look into the MTU count mismatch', 'Write release notes for the web SDK', 'Plan the onboarding walkthrough', 'Compare impression usage by customer', 'Investigate the journey pause behaviour', 'Draft the pricing follow-up email', 'Collect feature requests for tooltips', 'Update the integration checklist', 'Triage the open SDK bugs'];

// `next dev` pins its "N" indicator to the bottom-left corner, on top of the
// one-row composer's "+" button. It does not exist in a production build.
const hideDevIndicator = (context) => context.addInitScript(() => {
  addEventListener('DOMContentLoaded', () => {
    const style = document.createElement('style');
    style.textContent = 'nextjs-portal{display:none!important}';
    document.head.appendChild(style);
  });
});

async function login(context) {
  await hideDevIndicator(context);
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  await page.goto(`${base}/login`, { timeout: 120000, waitUntil: 'networkidle' });
  await page.fill('#signin-email', process.env.USER_NAME);
  await page.fill('#signin-password', process.env.PASSWORD);
  await page.fill('#setup-token', process.env.LOMA_SETUP_TOKEN);
  await page.click('button[type=submit]');
  await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 90000 });
  await page.route(`**/api/conversations/${CONV_ID}`, (r) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fixture(process.env.USER_NAME)) }));
  await page.route(`**/api/conversations/${CONV_ID}/cost*`, (r) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ total_cost_usd: 0.42, input_tokens: 12000, output_tokens: 3400, cache_read_tokens: 0, cache_creation_tokens: 0, total_turns: 6 }) }));
  return page;
}

// A real finger drag (touchstart, moves, touchend) through CDP. `dy < 0` drags
// the finger up, which scrolls the content down.
async function drag(cdp, page, x, y, dy, steps = 14) {
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
  for (let i = 1; i <= steps; i++) {
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x, y: y + (dy * i) / steps }] });
    await page.waitForTimeout(16);
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await page.waitForTimeout(600);
}
const menuCount = (page) => page.locator('[data-slot=dropdown-menu-content]').count();
const bar = (page) => page.evaluate(() => {
  const el = document.querySelector('[data-slot=mobile-top-bar]');
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { top: Math.round(r.top), bottom: Math.round(r.bottom), h: Math.round(r.height), hidden: el.dataset.hidden === 'true', display: getComputedStyle(el).display };
});

(async () => {
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  const results = [];
  const record = (name, ok, detail) => { results.push({ name, ok: !!ok, detail }); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ' — ' + detail : ''}`); };
  try {
    // ── Phone: small Android-class viewport, touch
    const phone = await browser.newContext({ ...devices['Pixel 5'], viewport: { width: 360, height: 740 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
    const page = await login(phone);
    page.on('pageerror', (e) => errors.push(e.message));
    const cdp = await phone.newCDPSession(page);

    await page.goto(`${base}/tasks`, { waitUntil: 'networkidle', timeout: 120000 });
    await page.evaluate(async (titles) => {
      const board = await (await fetch('/api/tasks')).json();
      if (board.tasks.length >= titles.length) return;
      const lane = (board.lanes[0] || { id: 'todo' }).id;
      for (const title of titles) {
        await fetch('/api/tasks', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ prompt: title, title, lane }) });
      }
    }, TITLES);
    await page.reload({ waitUntil: 'networkidle' });
    await page.getByTitle('Set priority').first().waitFor();
    await page.waitForTimeout(800);

    // 01 top bar
    const b0 = await bar(page);
    record('tasks: one 48px top bar', b0 && b0.h === 48 && b0.top === 0 && !b0.hidden, JSON.stringify(b0));
    const inBar = (name) => page.locator('[data-slot=mobile-top-bar]').getByRole('button', { name, exact: true });
    record('tasks: board switcher, search and actions sit in the top bar',
      await inBar('Switch board').isVisible() && await inBar('Search tasks').isVisible() && await inBar('Board actions').isVisible());
    record('tasks: no second header row on the page', !(await page.locator('main').getByRole('button', { name: 'Switch board' }).count()) && !(await page.getByRole('button', { name: 'New task' }).isVisible()));
    for (const name of ['Toggle menu', 'Search tasks', 'Board actions']) {
      const box = await inBar(name).boundingBox();
      record(`tasks: "${name}" is at least 40px and inside the bar`, box.width >= 40 && box.height >= 40 && box.y >= 0 && box.y + box.height <= 48, JSON.stringify(box));
    }
    const visible = await page.evaluate(() => {
      const composer = document.querySelector('[data-slot=task-composer]').getBoundingClientRect();
      const cards = [...document.querySelectorAll('main .group.rounded-xl.border')];
      return { full: cards.filter((c) => { const r = c.getBoundingClientRect(); return r.top >= 48 && r.bottom <= composer.top; }).length, composerH: Math.round(composer.height), firstTop: Math.round(cards[0].getBoundingClientRect().top) };
    });
    record('tasks: at least 3 whole cards visible at 360x740', visible.full >= 3, JSON.stringify(visible));
    record('tasks: capture box is one row', visible.composerH <= 64, `height=${visible.composerH}px`);
    await page.screenshot({ path: `${shots}/01-tasks.png` });

    // 02 board actions menu
    await inBar('Board actions').tap();
    const newTask = page.getByRole('menuitem', { name: 'New task' });
    await newTask.waitFor();
    record('tasks: overflow menu holds the board actions', await newTask.isVisible() && await page.getByRole('menuitem', { name: 'Board settings' }).isVisible() && await page.getByRole('menuitem', { name: 'Add existing chat' }).isVisible());
    await page.waitForTimeout(500); // let the open animation finish
    await page.screenshot({ path: `${shots}/02-tasks-menu.png` });
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);

    // 03 search opens from the bar
    const search = page.getByRole('textbox', { name: 'Search tasks' });
    record('tasks: search field is hidden until asked for', !(await search.isVisible()));
    await inBar('Search tasks').tap();
    await page.waitForTimeout(300);
    record('tasks: search opens focused from the top bar', await search.isVisible() && await search.evaluate((el) => document.activeElement === el));
    await search.fill('pricing');
    await page.waitForTimeout(900);
    await page.screenshot({ path: `${shots}/03-tasks-search.png` });
    await inBar('Search tasks').tap();
    await page.waitForTimeout(900);
    record('tasks: closing search clears it', !(await search.isVisible()) && (await page.getByTitle('Set priority').count()) > 3);

    // 04 a scroll that starts on a Priority tag scrolls; it must not open the menu
    const scroller = () => page.evaluate(() => Math.max(...[...document.querySelectorAll('main *')].map((e) => e.scrollTop)));
    const tag = await page.getByTitle('Set priority').nth(1).boundingBox();
    await drag(cdp, page, tag.x + tag.width / 2, tag.y + tag.height / 2, -150);
    const afterTagDrag = { menus: await menuCount(page), scrollTop: Math.round(await scroller()) };
    record('scroll starting on a Priority tag does not open its menu', afterTagDrag.menus === 0, JSON.stringify(afterTagDrag));
    record('scroll starting on a Priority tag scrolls the list', afterTagDrag.scrollTop > 60, JSON.stringify(afterTagDrag));
    await page.screenshot({ path: `${shots}/04-scroll-from-priority.png` });

    // 05 the top bar slides away on the way down and returns on the way up
    const b1 = await bar(page);
    record('top bar hides on scroll down', b1.hidden && b1.bottom <= 0, JSON.stringify(b1));
    const chips = await page.getByRole('button', { name: /^Todo/ }).boundingBox();
    record('column chips stay pinned at the top while the bar is hidden', chips && chips.y >= 0 && chips.y <= 16, JSON.stringify(chips));
    // Same for a scroll that starts on a card's overflow button.
    const more = await page.locator('main .group.rounded-xl.border button[aria-haspopup=menu]').nth(3).boundingBox();
    await drag(cdp, page, more.x + more.width / 2, more.y + more.height / 2, -120);
    record('scroll starting on a card overflow button does not open its menu', (await menuCount(page)) === 0);
    await drag(cdp, page, 180, 300, 90);
    const b2 = await bar(page);
    record('top bar returns on scroll up', !b2.hidden && b2.top === 0, JSON.stringify(b2));

    // 06 a real tap still opens both menus
    await page.getByTitle('Set priority').nth(4).tap();
    await page.getByRole('menuitem', { name: /High/ }).waitFor();
    record('tap on a Priority tag opens its menu', (await menuCount(page)) === 1);
    await page.waitForTimeout(500); // let the open animation finish
    await page.screenshot({ path: `${shots}/05-priority-menu.png` });
    await page.getByRole('menuitem', { name: /High/ }).tap();
    await page.waitForTimeout(600);
    record('picking a priority from the tap-opened menu applies it', (await menuCount(page)) === 0 && (await page.getByTitle('Set priority').filter({ hasText: 'High' }).count()) === 1);
    await page.locator('main .group.rounded-xl.border button[aria-haspopup=menu]:not([title])').nth(4).tap();
    await page.getByRole('menuitem', { name: 'Fork' }).waitFor();
    record('tap on a card overflow button opens its menu', true);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);

    // 07 capture box options live behind "+"
    await page.getByRole('button', { name: 'Task options', exact: true }).tap();
    const taskSheet = page.getByRole('dialog', { name: 'Task options', exact: true });
    await taskSheet.waitFor();
    record('task capture: attach, model, tools and skills sit behind "+"',
      await taskSheet.getByRole('button', { name: 'Attach files' }).isVisible() && await taskSheet.getByRole('button', { name: /Tools:/ }).isVisible() && await taskSheet.getByRole('button', { name: /Skills:/ }).isVisible());
    await page.waitForTimeout(500); // let the open animation finish
    await page.screenshot({ path: `${shots}/06-task-options.png` });
    await page.keyboard.press('Escape');
    await taskSheet.waitFor({ state: 'hidden' });

    // 08 inside a conversation: title in the bar, no bottom nav, one-row composer
    await page.goto(`${base}/chat?continue=${CONV_ID}`, { waitUntil: 'networkidle' });
    await page.getByText('Part 6.').first().waitFor();
    await page.waitForTimeout(1200);
    const chat = await page.evaluate(() => {
      const scroller = [...document.querySelectorAll('main div')].find((e) => getComputedStyle(e).overflowY === 'auto' && e.scrollHeight > e.clientHeight + 50);
      const form = document.querySelector('main form');
      const title = document.querySelector('[data-slot=mobile-top-bar-title]');
      return {
        nav: document.querySelectorAll('main > nav').length, title: title.textContent,
        readingPct: Math.round((100 * scroller.getBoundingClientRect().height) / innerHeight), scrollTop: Math.round(scroller.scrollTop),
        composerH: Math.round(form.getBoundingClientRect().height), composerBottom: Math.round(form.getBoundingClientRect().bottom), vh: innerHeight,
      };
    });
    record('chat: bottom nav is gone inside a conversation', chat.nav === 0, JSON.stringify(chat));
    record('chat: conversation title sits in the top bar', chat.title.includes('Q3 mobile engagement summary'), chat.title);
    record('chat: cost and actions sit in the top bar', await page.locator('[data-slot=mobile-top-bar-actions]').getByText('$0.42').isVisible() && await page.locator('[data-slot=mobile-top-bar-actions]').getByTitle('More actions').isVisible());
    record('chat: composer is one row', chat.composerH <= 64 && chat.composerBottom <= chat.vh, JSON.stringify(chat));
    record('chat: messages get at least 80% of the screen', chat.readingPct >= 80, `${chat.readingPct}%`);
    const b3 = await bar(page);
    record('chat: opening at the newest message does not hide the top bar', chat.scrollTop > 100 && !b3.hidden, JSON.stringify({ scrollTop: chat.scrollTop, ...b3 }));
    await page.screenshot({ path: `${shots}/07-chat.png` });
    await page.locator('[data-slot=mobile-top-bar-actions]').getByTitle('More actions').tap();
    await page.getByRole('menuitem', { name: /Rename/ }).waitFor();
    record('chat: actions menu opens from the top bar', (await menuCount(page)) === 1);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
    await page.getByRole('button', { name: 'Chat options', exact: true }).tap();
    const chatSheet = page.getByRole('dialog', { name: 'Chat options', exact: true });
    await chatSheet.waitFor();
    record('chat: attach, model, agent, tools and skills sit behind "+"',
      await chatSheet.getByRole('button', { name: 'Attach files' }).isVisible() && await chatSheet.getByRole('button', { name: /Tools:/ }).isVisible() && await chatSheet.getByRole('button', { name: /Skills:/ }).isVisible());
    await page.waitForTimeout(500); // let the open animation finish
    await page.screenshot({ path: `${shots}/08-chat-options.png` });
    await page.keyboard.press('Escape');
    await chatSheet.waitFor({ state: 'hidden' });
    const composer = page.locator('main form textarea');
    await composer.fill('Thanks. Can you break that down by campaign?');
    await page.waitForTimeout(300);
    record('chat: Send replaces the mic once there is text', await page.getByRole('button', { name: 'Send message' }).isVisible() && !(await page.getByRole('button', { name: 'Start dictation' }).isVisible()));
    await composer.fill(Array(12).fill('A longer message that wraps across the phone.').join('\n'));
    await page.waitForTimeout(300);
    const grown = await page.evaluate(() => { const f = document.querySelector('main form').getBoundingClientRect(); return { h: Math.round(f.height), bottom: Math.round(f.bottom), vh: innerHeight }; });
    record('chat: composer grows with the text and stays on screen', grown.h > 100 && grown.h <= 190 && grown.bottom <= grown.vh, JSON.stringify(grown));
    await page.screenshot({ path: `${shots}/09-chat-typing.png` });
    await composer.fill('');
    await composer.blur();
    // Reading back through the chat: the bar hides while moving forward again.
    await drag(cdp, page, 180, 300, 260);
    await drag(cdp, page, 180, 420, -160);
    const b4 = await bar(page);
    record('chat: top bar hides while scrolling down through messages', b4.hidden, JSON.stringify(b4));
    await page.screenshot({ path: `${shots}/10-chat-scrolled.png` });
    await drag(cdp, page, 180, 300, 90);
    record('chat: top bar returns on scroll up', !(await bar(page)).hidden);

    // 09 routes that set no title still get a full bar, and keep the bottom nav
    await page.goto(`${base}/chat`, { waitUntil: 'networkidle' });
    await page.getByPlaceholder('Ask Loma anything...').waitFor();
    await page.waitForTimeout(500);
    record('new chat: top bar shows the Loma mark', await page.locator('[data-slot=mobile-top-bar]').getByText('Loma', { exact: true }).isVisible());
    record('new chat: bottom nav stays', (await page.locator('main > nav').count()) === 1);
    await page.screenshot({ path: `${shots}/11-new-chat.png` });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
    record('no horizontal overflow at 360px', overflow);
    // Dark mode screenshots (the bar and composers use theme tokens only).
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.goto(`${base}/tasks`, { waitUntil: 'networkidle' });
    await page.getByTitle('Set priority').first().waitFor();
    await page.waitForTimeout(600);
    await page.screenshot({ path: `${shots}/14-tasks-dark.png` });
    await page.goto(`${base}/chat?continue=${CONV_ID}`, { waitUntil: 'networkidle' });
    await page.getByText('Part 6.').first().waitFor();
    await page.waitForTimeout(800);
    await page.screenshot({ path: `${shots}/15-chat-dark.png` });
    await phone.close();

    // ── Desktop regression: no phone bar, header row and mouse menus unchanged
    const desktop = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const dpage = await login(desktop);
    dpage.on('pageerror', (e) => errors.push(e.message));
    await dpage.goto(`${base}/tasks`, { waitUntil: 'networkidle' });
    await dpage.getByTitle('Set priority').first().waitFor();
    const dbar = await bar(dpage);
    record('desktop: phone top bar is not displayed', dbar && dbar.display === 'none', JSON.stringify(dbar));
    record('desktop: header row keeps the board switcher and New task', await dpage.locator('main').getByRole('button', { name: 'Switch board' }).isVisible() && await dpage.getByRole('button', { name: 'New task' }).isVisible() && await dpage.getByRole('button', { name: 'Board settings' }).isVisible());
    record('desktop: search field is always visible', await dpage.getByRole('textbox', { name: 'Search tasks' }).isVisible());
    await dpage.getByTitle('Set priority').first().click();
    await dpage.getByRole('menuitem', { name: /Urgent/ }).waitFor();
    record('desktop: Priority menu opens with the mouse', (await menuCount(dpage)) === 1);
    await dpage.keyboard.press('Escape');
    await dpage.waitForTimeout(200);
    await dpage.getByTitle('Set priority').first().focus();
    await dpage.keyboard.press('Enter');
    await dpage.waitForTimeout(300);
    record('desktop: Priority menu opens from the keyboard', (await menuCount(dpage)) === 1);
    await dpage.keyboard.press('Escape');
    await dpage.waitForTimeout(500);
    await dpage.screenshot({ path: `${shots}/12-desktop-tasks.png` });
    await dpage.goto(`${base}/chat?continue=${CONV_ID}`, { waitUntil: 'networkidle' });
    await dpage.getByText('Part 6.').first().waitFor();
    record('desktop: chat keeps its full composer toolbar', await dpage.getByRole('button', { name: /Tools:/ }).isVisible() && !(await dpage.getByRole('button', { name: 'Chat options', exact: true }).count()));
    await dpage.screenshot({ path: `${shots}/13-desktop-chat.png` });
    await desktop.close();

    record('no page errors', errors.length === 0, errors.join(' | ').slice(0, 300));
  } finally {
    await browser.close();
  }
  fs.writeFileSync(`${shots}/results.json`, JSON.stringify(results, null, 2));
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed; screenshots in ${shots}`);
  if (failed.length) process.exit(1);
})().catch((e) => { console.error('mobile-chrome E2E failed:', e); process.exit(1); });
