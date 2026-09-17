// Isolated local-stack mobile E2E for the shell + chat responsiveness work.
// Real auth/UI at phone viewports; the conversation with an artifact is a
// stubbed API fixture so the check needs no model run.
// Source dashboard/.env, then NODE_PATH=<playwright node_modules> node tests/mobile-shell.browser.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium, devices } = require('playwright');

const base = process.env.AUTH_URL;
assert.match(base, /^http:\/\/localhost:/);
const shots = process.env.SHOTS_DIR || '/tmp/mobile-shell-shots';
fs.mkdirSync(shots, { recursive: true });
const EXPECT_NEW = process.env.EXPECT_NEW_BEHAVIOUR !== '0'; // set 0 when running against main for "before" shots

const CONV_ID = 'qa-mobile-artifact-1';
const fixture = (userName) => ({
  conversation: {
    conversation_id: CONV_ID, prompt: 'Draft the Q3 mobile engagement report as a document I can share.',
    final_response: '', status: 'completed', title: 'Q3 mobile engagement report', project_id: null,
    messages: [{ role: 'user', content: 'Draft the Q3 mobile engagement report as a document I can share.', timestamp: '2026-09-17T00:00:00Z' }],
    metadata: { user_name: userName, visibility: 'private' },
  },
  turns: [{ _id: 't1', conversation_id: CONV_ID, turn_number: 1, timestamp: '2026-09-17T00:00:05Z', message_type: 'assistant',
    text_blocks: [{ text: 'Here is the report. I pulled the retention numbers from the last two sprints and summarised the three campaigns that moved the needle. Open the artifact to review or download it.' }] }],
  artifacts: [
    { _id: 'a1', conversation_id: CONV_ID, artifact_id: 'art-1', title: 'Q3 mobile engagement report', language: 'markdown', version: 1, timestamp: '2026-09-17T00:00:06Z', artifact_type: 'code',
      content: '# Q3 mobile engagement report\n\n## Summary\n\nDAU grew 18% quarter over quarter.\n\n| Campaign | Impressions | CTR |\n|---|---|---|\n| Streak reminder | 1,204,000 | 4.1% |\n| Spin the wheel | 812,000 | 6.8% |\n| Onboarding tooltips | 402,000 | 2.2% |\n\n```json\n{ "quarter": "Q3", "dau_growth": 0.18 }\n```\n' },
    { _id: 'a2', conversation_id: CONV_ID, artifact_id: 'art-2', title: 'Q3 mobile engagement report', language: 'markdown', version: 2, timestamp: '2026-09-17T00:00:07Z', artifact_type: 'code',
      content: '# Q3 mobile engagement report (v2)\n\nSame as v1 with a fixed CTR column.\n' },
  ],
});

async function login(context) {
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  await page.goto(`${base}/login`, { timeout: 90000, waitUntil: 'networkidle' });
  await page.fill('#signin-email', process.env.USER_NAME);
  await page.fill('#signin-password', process.env.PASSWORD);
  await page.fill('#setup-token', process.env.LOMA_SETUP_TOKEN);
  await page.click('button[type=submit]');
  await page.waitForURL(u => !u.pathname.includes('/login'), { timeout: 60000 });
  return page;
}
const noHorizontalOverflow = async (page, label) => {
  const r = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, bw: document.body.scrollWidth }));
  const ok = r.sw <= r.cw && r.bw <= r.cw;
  if (EXPECT_NEW) assert.ok(ok, `${label}: horizontal overflow ${JSON.stringify(r)}`);
  else console.log(`${ok ? 'PASS' : 'FAIL'} no horizontal overflow on ${label} — ${JSON.stringify(r)}`);
};
const hitArea = async (page, locator) => {
  // Effective touch hit area: probe elementFromPoint 4px outside each edge.
  const box = await locator.boundingBox();
  const handle = await locator.elementHandle();
  return page.evaluate(({ box, handle }) => {
    const hits = (x, y) => { const el = document.elementFromPoint(x, y); return el === handle || handle.contains(el); };
    const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
    return { box: { w: Math.round(box.width), h: Math.round(box.height) },
      left: hits(box.x - 2, cy), right: hits(box.x + box.width + 2, cy), top: hits(cx, box.y - 2), bottom: hits(cx, box.y + box.height + 2) };
  }, { box, handle });
};

(async () => {
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  const results = [];
  const record = (name, ok, detail) => { results.push({ name, ok, detail }); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ' — ' + detail : ''}`); };
  try {
    // ── Phone (iPhone 14-class, 390x844, touch)
    const phone = await browser.newContext({ ...devices['iPhone 13'], viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, colorScheme: 'light' });
    const page = await login(phone);
    page.on('pageerror', e => errors.push(e.message));
    await page.route(`**/api/conversations/${CONV_ID}`, route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fixture(process.env.USER_NAME)) }));

    const coarse = await page.evaluate(() => matchMedia('(pointer: coarse)').matches);
    record('phone context reports pointer: coarse', coarse);

    // 01 empty chat
    await page.goto(`${base}/chat`, { waitUntil: 'networkidle' });
    await page.getByPlaceholder('What do you need to get done?').waitFor();
    await page.screenshot({ path: `${shots}/01-chat-empty.png` });
    await noHorizontalOverflow(page, '/chat');
    const appH = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--app-h').trim());
    record('--app-h set in browser (non-standalone) mode', EXPECT_NEW ? appH !== '' : true, `--app-h="${appH}"`);
    const menuBtn = page.getByRole('button', { name: 'Toggle menu' });
    const menuHit = await hitArea(page, menuBtn);
    record('hamburger has expanded touch hit area', EXPECT_NEW ? (menuHit.left && menuHit.right && menuHit.top && menuHit.bottom) : true, JSON.stringify(menuHit));

    // 02 sidebar drawer
    await menuBtn.tap();
    await page.waitForTimeout(400);
    const aside = page.locator('aside.loma-sidebar');
    const asideBox = await aside.boundingBox();
    record('drawer opens at full width (not the 56px collapsed rail)', asideBox && asideBox.width >= 280, `width=${asideBox && asideBox.width}`);
    const bodyOverflow = await page.evaluate(() => document.body.style.overflow);
    record('page scroll locked behind drawer', EXPECT_NEW ? bodyOverflow === 'hidden' : true, `body.overflow="${bodyOverflow}"`);
    await page.screenshot({ path: `${shots}/02-sidebar-drawer.png` });
    await page.keyboard.press('Escape');
    await page.waitForTimeout(400);
    const asideAfter = await aside.boundingBox();
    record('Escape closes the drawer', EXPECT_NEW ? (asideAfter === null || asideAfter.x + asideAfter.width <= 0) : true, JSON.stringify(asideAfter));

    // 03 conversation with artifact
    await page.goto(`${base}/chat?continue=${CONV_ID}`, { waitUntil: 'networkidle' });
    const card = page.getByRole('button', { name: /Q3 mobile engagement report/ }).first();
    await card.waitFor();
    await page.screenshot({ path: `${shots}/03-chat-conversation.png` });
    await noHorizontalOverflow(page, '/chat?continue');
    const bubbleW = await page.locator('.chat-text.rounded-xl').first().evaluate(el => Math.round(el.getBoundingClientRect().width));
    record('user bubble uses wider phone max-width', EXPECT_NEW ? bubbleW > 300 : true, `bubble width=${bubbleW}px`);
    await card.tap();
    await page.waitForTimeout(500);
    const pane = await page.evaluate(() => { const el = document.querySelector('.bg-muted\\/30.h-full'); return el ? { w: Math.round(el.getBoundingClientRect().width), cw: Math.round(el.parentElement.getBoundingClientRect().width) } : { w: -1, cw: -1 }; });
    record('chat pane stays full width behind the artifact', EXPECT_NEW ? pane.w === pane.cw : true, `chat pane=${pane.w}px of container ${pane.cw}px`);
    const dialog = page.getByRole('dialog', { name: 'Q3 mobile engagement report' });
    const sheetBox = EXPECT_NEW ? await dialog.boundingBox() : null;
    record('artifact renders as a bottom sheet', EXPECT_NEW ? !!sheetBox && sheetBox.y > 40 && sheetBox.width === 390 : true, JSON.stringify(sheetBox));
    const moreBtn = page.getByLabel('More actions'); // the artifact overflow menu (aria-label); the chat context menu only has a title
    record('artifact toolbar collapses to one overflow menu', EXPECT_NEW ? await moreBtn.isVisible() : true);
    await page.screenshot({ path: `${shots}/04-artifact-sheet.png` });
    if (EXPECT_NEW) {
      await moreBtn.tap();
      const showCode = page.getByRole('menuitem', { name: 'Show code' });
      await showCode.waitFor();
      // Occlusion-aware: the menu portal must stack above the sheet (z-[80]).
      const menuOnTop = await showCode.evaluate(el => { const b = el.getBoundingClientRect(); const hit = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2); return el === hit || el.contains(hit); });
      record('artifact overflow menu stacks above the sheet', menuOnTop);
      await page.screenshot({ path: `${shots}/05-artifact-menu.png` });
      await page.keyboard.press('Escape');
      await page.waitForTimeout(200);
      // tap the scrim (top-left, above the sheet) to close
      await page.touchscreen.tap(20, 20);
      await page.waitForTimeout(400);
      record('tapping the scrim closes the sheet', (await dialog.count()) === 0);
    }

    // 06 keyboard-open emulation: --app-h shrinks the shell, composer must sit inside it
    await page.goto(`${base}/chat?continue=${CONV_ID}`, { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: /Q3 mobile engagement report/ }).first().waitFor();
    await page.evaluate(() => document.documentElement.style.setProperty('--app-h', '510px'));
    const composer = page.locator('textarea').last();
    await composer.tap();
    await page.waitForTimeout(400);
    const compBox = await composer.boundingBox();
    record('composer stays above an emulated keyboard (shell height 510px)', EXPECT_NEW ? compBox.y + compBox.height <= 511 : true, `composer bottom=${Math.round(compBox.y + compBox.height)}px`);
    await page.screenshot({ path: `${shots}/06-keyboard-open.png`, clip: { x: 0, y: 0, width: 390, height: 520 } });
    await page.evaluate(() => document.documentElement.style.removeProperty('--app-h'));

    // 07 composer pickers are bottom sheets
    const toolsTrigger = page.getByRole('button', { name: /^Tools:/ }).first();
    await toolsTrigger.tap();
    const sheet = page.locator('[data-slot=sheet-content]');
    await sheet.waitFor({ timeout: 10000 });
    record('tools picker opens as a bottom sheet', true);
    await page.screenshot({ path: `${shots}/07-tools-sheet.png` });
    await page.keyboard.press('Escape');
    await sheet.waitFor({ state: 'detached' });
    const modelTrigger = page.locator('button[title="Choose model"], button[title^="Model list unavailable"]').first();
    if (await modelTrigger.count() && await modelTrigger.isEnabled()) {
      await modelTrigger.tap();
      const modelSheet = page.locator('[data-slot=sheet-content]');
      const asSheet = await modelSheet.waitFor({ timeout: 5000 }).then(() => true).catch(() => false);
      record('model picker opens as a bottom sheet', EXPECT_NEW ? asSheet : true);
      if (asSheet) await page.screenshot({ path: `${shots}/08-model-sheet.png` });
      await page.keyboard.press('Escape');
    } else {
      record('model picker opens as a bottom sheet', true, 'skipped: no models loaded in the isolated stack');
    }

    // 09 tasks board: no overflow
    await page.goto(`${base}/tasks`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(800);
    await noHorizontalOverflow(page, '/tasks');
    await page.screenshot({ path: `${shots}/09-tasks.png` });
    await phone.close();

    // ── Desktop regression: split pane + full toolbar unchanged
    const desktop = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const dpage = await login(desktop);
    dpage.on('pageerror', e => errors.push(e.message));
    await dpage.route(`**/api/conversations/${CONV_ID}`, route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fixture(process.env.USER_NAME)) }));
    await dpage.goto(`${base}/chat?continue=${CONV_ID}`, { waitUntil: 'networkidle' });
    await dpage.getByRole('button', { name: /Q3 mobile engagement report/ }).first().click();
    await dpage.waitForTimeout(500);
    const dChatW = await dpage.evaluate(() => { const el = document.querySelector('.bg-muted\\/30.h-full'); return Math.round(el.getBoundingClientRect().width); });
    const dContainerW = await dpage.evaluate(() => Math.round(document.querySelector('.bg-muted\\/30.h-full').parentElement.getBoundingClientRect().width));
    record('desktop keeps the 50/50 split pane', Math.abs(dChatW / dContainerW - 0.5) < 0.03, `chat=${dChatW}px of ${dContainerW}px`);
    record('desktop keeps the full toolbar (Preview/Code visible)', await dpage.getByRole('button', { name: 'Preview' }).isVisible());
    record('desktop has no mobile overflow menu', !(await dpage.getByLabel('More actions').isVisible()));
    const dAppH = await dpage.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--app-h').trim());
    record('desktop leaves --app-h unset (100dvh fallback; no pinch-zoom shrink)', EXPECT_NEW ? dAppH === '' : true, `--app-h="${dAppH}"`);
    await dpage.screenshot({ path: `${shots}/10-desktop-artifact.png` });
    await desktop.close();

    record('no page errors', errors.length === 0, errors.join(' | ').slice(0, 300));
  } finally {
    await browser.close();
  }
  fs.writeFileSync(`${shots}/results.json`, JSON.stringify(results, null, 2));
  const failed = results.filter(r => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed; screenshots in ${shots}`);
  if (failed.length) process.exit(1);
})().catch(e => { console.error('mobile-shell E2E failed:', e); process.exit(1); });
