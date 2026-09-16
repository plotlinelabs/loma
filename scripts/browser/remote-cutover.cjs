/* Deterministic remote-cutover smoke: LOMA_REMOTE_WORKERS=on with no worker
   host configured must fail chat closed with a visible error — never run a
   local CLI runtime. No provider calls, no mocked routes. */
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
function localConfig(file) {
  return Object.fromEntries(fs.readFileSync(file, 'utf8').split('\n').filter(l => /^[A-Z_]+=/.test(l)).map(l => {
    const i = l.indexOf('=');
    return [l.slice(0, i), l.slice(i + 1).trim().replace(/^["']|["']$/g, '')];
  }));
}
const backend = localConfig(path.join(root, '.env'));
assert(backend.OBSERVABILITY_DB_NAME?.startsWith('loma_local_'), 'Throwaway database required');
assert.equal(backend.LOMA_REMOTE_WORKERS, 'on', 'Cutover flag must be on for this smoke');
assert(!backend.LOMA_WORKER_URL, 'This smoke requires an unconfigured worker transport');
const base = process.env.LOMA_CHAT_BASE_URL || 'http://localhost:13001';
assert(process.env.LOMA_SMOKE_EVIDENCE, 'LOMA_SMOKE_EVIDENCE required');
const dash = localConfig(path.join(root, 'dashboard/.env'));
for (const key of ['USER_NAME', 'PASSWORD']) assert(dash[key], `dashboard/.env ${key} required`);
async function login(page) {
  await page.goto(`${base}/`, { waitUntil: 'domcontentloaded', timeout: 120000 });
  if (await page.locator('#signin-email').count()) {
    for (let i = 0; i < 20; i++) {
      await page.fill('#signin-email', dash.USER_NAME); await page.fill('#signin-password', dash.PASSWORD);
      if (await page.locator('#setup-token').count()) await page.fill('#setup-token', dash.LOMA_SETUP_TOKEN || '');
      await page.waitForTimeout(500);
      if (await page.inputValue('#signin-email') === dash.USER_NAME && await page.inputValue('#signin-password') === dash.PASSWORD) break;
    }
    await page.locator('button[type="submit"]').first().click();
    await page.locator('#signin-email').waitFor({ state: 'detached', timeout: 60000 });
  }
  await page.goto(`${base}/chat`, { waitUntil: 'domcontentloaded', timeout: 120000 });
  await page.locator('textarea:visible').last().waitFor({ timeout: 90000 });
  // Composer is only ready once the model catalog has loaded; sending earlier
  // races a re-render that clears the textarea.
  await page.locator('button[title="Choose model"]:not([disabled]):visible').waitFor({ timeout: 90000 });
  assert(!new URL(page.url()).pathname.includes('/login'), 'Authenticated chat required');
}
async function settleOnPersistedError(page) {
  // The chat rewrites the URL to /chat?continue=<id> and re-reads the
  // conversation from Mongo once the stream ends. Reload explicitly so the
  // screenshot proves the persisted state (not a transient render) shows
  // the same error.
  await page.waitForURL(u => /^[0-9a-f-]{36}$/.test(u.searchParams.get('continue') || ''), { timeout: 60000 });
  await page.reload({ waitUntil: 'domcontentloaded', timeout: 120000 });
  await page.getByText(/remote worker platform is not available/i).last().waitFor({ timeout: 60000 });
  await page.waitForLoadState('networkidle', { timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(1000);
  assert(await page.getByText(/remote worker platform is not available/i).last().isVisible(), 'Persisted error visible after settle');
}
async function sendAndCapture(page, message) {
  const responsePromise = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chat' && r.request().method() === 'POST', { timeout: 120000 });
  const box = page.locator('textarea:visible').last();
  await box.fill(message);
  await box.press('Enter');
  const response = await responsePromise;
  assert.equal(response.status(), 200, 'Chat request accepted (error is streamed, not thrown)');
  return response;
}
const out = process.env.LOMA_SMOKE_EVIDENCE;
fs.mkdirSync(out, { recursive: true });
const checks = [];
function check(value, label) { assert(value, label); checks.push(label); console.log(`PASS ${label}`); }
(async () => {
  const browser = await chromium.launch({ ...(process.env.LOMA_CHROMIUM_PATH ? { executablePath: process.env.LOMA_CHROMIUM_PATH } : {}) });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  let captureBody;
  await page.exposeBinding('recordSmokeStream', (_, body) => captureBody?.(body));
  await page.addInitScript(() => {
    const original = window.fetch.bind(window);
    window.fetch = async (...args) => {
      const response = await original(...args);
      if (new URL(response.url).pathname === '/api/chat') {
        response.clone().text().then(body => window.recordSmokeStream(body)).catch(() => {});
      }
      return response;
    };
  });
  try {
    await login(page);
    check(true, 'Logged-in chat composer loaded with remote mode on');
    const poolStatus = await page.evaluate(async () => { const r = await fetch('/api/pool-status'); return { status: r.status, body: await r.json() }; });
    check(poolStatus.status === 200 && poolStatus.body.remote_workers?.enabled === true && poolStatus.body.pool_size === 0, 'Pool status reports remote mode without a local pool');
    const bodyPromise = new Promise(resolve => { captureBody = resolve; });
    await sendAndCapture(page, 'Remote cutover smoke: is the remote worker transport configured?');
    const body = await Promise.race([bodyPromise, new Promise((_, reject) => {
      const timer = setTimeout(() => reject(Error('Chat stream timeout')), 120000); timer.unref();
    })]);
    assert(body.includes('data: [DONE]'), 'SSE completed');
    const events = body.split('\n').filter(l => l.startsWith('data: ') && l !== 'data: [DONE]')
      .map(l => JSON.parse(l.slice(6)));
    const text = events.filter(e => e.type === 'text').map(e => e.text || '').join('');
    check(/remote worker platform is not available/i.test(text), 'Fail-closed remote error surfaced to the user');
    check(/LOMA_WORKER_URL/.test(text), 'Error names the missing configuration');
    check(!/pool|opencode|codex app-server/i.test(text), 'No local runtime output leaked into the reply');
    await page.getByText(/remote worker platform is not available/i).last().waitFor({ timeout: 30000 });
    check(true, 'Fail-closed error rendered in the browser');
    await settleOnPersistedError(page);
    check(true, 'Persisted conversation re-renders the same fail-closed error after reload');
    await page.screenshot({ path: path.join(out, 'remote-cutover-desktop.png'), fullPage: true });
    // Separate context: must authenticate on its own, never inherit desktop cookies.
    const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await login(mobile);
    check(!new URL(mobile.url()).pathname.includes('/login'), 'Mobile session authenticated (not on /login)');
    await sendAndCapture(mobile, 'Remote cutover smoke (mobile): is the remote worker transport configured?');
    await mobile.getByText(/remote worker platform is not available/i).last().waitFor({ timeout: 60000 });
    check(true, 'Mobile chat fails closed with the visible remote error');
    await settleOnPersistedError(mobile);
    await mobile.screenshot({ path: path.join(out, 'remote-cutover-mobile.png'), fullPage: true });
    fs.writeFileSync(path.join(out, 'remote-cutover-results.json'), JSON.stringify({ passed: true, checks }, null, 2));
  } catch (error) {
    fs.writeFileSync(path.join(out, 'remote-cutover-results.json'), JSON.stringify({ passed: false, checks, error: error.message }, null, 2));
    if (!new URL(page.url()).pathname.includes('/login') && page.url() !== 'about:blank')
      await page.screenshot({ path: path.join(out, 'remote-cutover-failure.png'), fullPage: true }).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e.message); process.exitCode = 1; });
