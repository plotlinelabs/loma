/* Opt-in live-provider smoke test. See docs/history-recall.md. No mocked routes. */
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '../..');
function localConfig(file) {
  return Object.fromEntries(fs.readFileSync(file, 'utf8').split('\n').filter(l => /^[A-Z_]+=/.test(l)).map(l => {
    const i = l.indexOf('=');
    return [l.slice(0, i), l.slice(i + 1).trim().replace(/^[\"']|[\"']$/g, '')];
  }));
}
const backend = localConfig(path.join(root, '.env'));
const dashboard = localConfig(path.join(root, 'dashboard/.env'));
assert(backend.OBSERVABILITY_DB_NAME?.startsWith('loma_local_'), 'Throwaway database required');
assert.equal(dashboard.OBSERVABILITY_DB_NAME, backend.OBSERVABILITY_DB_NAME, 'Dashboard and backend DB must match');
assert.equal(backend.LOMA_ENABLE_SLACK, 'false');
assert.equal(backend.LOMA_ENABLE_SCHEDULER, 'false');
assert.equal(backend.WEBHOOK_PORT, '13000');
assert.equal(backend.OPENCODE_PORT, '14097');
assert.equal(dashboard.BACKEND_URL, 'http://localhost:13000');
const base = process.env.LOMA_CHAT_BASE_URL || 'http://localhost:13001';
const url = new URL(base);
assert(['localhost', '127.0.0.1'].includes(url.hostname) && url.port === '13001', 'Local isolated dashboard only');
assert.equal(process.env.LOMA_CHAT_E2E, '1', 'Explicit live-model opt-in required');
for (const key of ['LOMA_EMAIL', 'LOMA_PASSWORD', 'LOMA_TOKEN', 'LOMA_CHAT_MODEL', 'LOMA_CHAT_EVIDENCE']) {
  assert(process.env[key], `${key} required`);
}
const out = process.env.LOMA_CHAT_EVIDENCE;
fs.mkdirSync(out, { recursive: true });
const report = { commit: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(), recallEnabled: backend.LOMA_RECALL_ENABLED || 'false', model: process.env.LOMA_CHAT_MODEL, mocked: false, checks: [], passed: false };
function check(value, label) { assert(value, label); report.checks.push(label); console.log(`PASS ${label}`); }
function parseStream(body) {
  assert(body.includes('data: [DONE]'), 'SSE must complete');
  const events = body.split('\n').filter(l => l.startsWith('data: ') && l !== 'data: [DONE]')
    .map(l => JSON.parse(l.slice(6)));
  assert(!events.some(e => e.type === 'error' || e.error), 'No SSE error event');
  const text = events.filter(e => e.type === 'text').map(e => e.text || '').join('');
  assert(!/encountered.*error|no .*accounts connected|client pool not initialized|usage limit|invalid api key/i.test(text), 'No provider failure disguised as assistant text');
  return { events, text };
}
(async () => {
  const browser = await chromium.launch({ ...(process.env.LOMA_CHROMIUM_PATH ? { executablePath: process.env.LOMA_CHROMIUM_PATH } : {}) });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  let captureBody;
  await page.exposeBinding('recordSmokeStream', (_, body) => captureBody?.(body));
  // Observe a clone of the actual response in-page. CDP response.text() can
  // lose SSE bodies when Next.js changes the conversation URL. No route mocks.
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
    await page.addInitScript(model => localStorage.setItem('dashboard-chat-selected-model', model), process.env.LOMA_CHAT_MODEL);
    await page.goto(`${base}/login`, { timeout: 120000 });
    await page.waitForLoadState('networkidle');
    await page.fill('#signin-email', process.env.LOMA_EMAIL);
    await page.fill('#signin-password', process.env.LOMA_PASSWORD);
    await page.fill('#setup-token', process.env.LOMA_TOKEN);
    await page.click('button[type="submit"]');
    await page.waitForURL(u => !u.pathname.includes('/login'), { timeout: 60000 });
    await page.goto(`${base}/chat`, { timeout: 120000 });
    await page.locator('button[title="Choose model"]:not([disabled]):visible').waitFor({ timeout: 90000 });
    check(true, 'Logged-in chat composer loaded');
    async function send(message, expected) {
      await page.locator('button[title="Choose model"]:not([disabled]):visible').waitFor({ timeout: 90000 });
      const bodyPromise = new Promise(resolve => { captureBody = resolve; });
      const responsePromise = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chat' && r.request().method() === 'POST', { timeout: 180000 });
      const box = page.locator('textarea:visible').last();
      await box.fill(message);
      await box.press('Enter');
      const response = await responsePromise;
      assert.equal(response.status(), 200, 'Chat request succeeded');
      const request = response.request().postDataJSON();
      assert.equal(request.model, process.env.LOMA_CHAT_MODEL, 'Requested real model selected');
      const body = await Promise.race([bodyPromise, new Promise((_, reject) => {
        const timer = setTimeout(() => reject(Error('Chat completion timeout')), 180000); timer.unref();
      })]);
      const stream = parseStream(body);
      check(stream.text.trim() === expected, 'Real assistant returned expected answer');
      await page.getByText(expected, { exact: true }).last().waitFor({ timeout: 30000 });
      check(true, 'Assistant answer rendered in browser');
      return { stream, request };
    }
    const nonce = crypto.randomBytes(8).toString('hex');
    const first = await send(`This is a chat smoke test. Do not use tools or external resources. Remember the test code ${nonce}. Reply with exactly ACK-${nonce}, nothing else.`, `ACK-${nonce}`);
    const firstId = first.stream.events.find(e => e.type === 'conversation_id')?.conversation_id;
    check(Boolean(firstId), 'Real conversation ID returned');
    await page.locator('button[title="Choose model"]:not([disabled]):visible').waitFor({ timeout: 90000 });
    await page.screenshot({ path: path.join(out, 'chat-first-response.png'), fullPage: true });
    await page.reload();
    await page.getByText(`ACK-${nonce}`, { exact: true }).last().waitFor({ timeout: 30000 });
    check(true, 'First response persisted after reload');
    const follow = await send('Without tools, return the test code from my previous message prefixed with REMEMBERED-. Reply with that string only.', `REMEMBERED-${nonce}`);
    check(follow.request.conversation_id === firstId, 'Follow-up reuses same conversation');
    check(follow.request.conversation_history.some(m => m.content.includes(nonce)), 'Persisted prior context sent on follow-up');
    await page.reload();
    await page.getByText(`REMEMBERED-${nonce}`, { exact: true }).last().waitFor({ timeout: 30000 });
    check(true, 'Follow-up response persisted after reload');
    await page.locator('button[title="Choose model"]:not([disabled]):visible').waitFor({ timeout: 90000 });
    await page.screenshot({ path: path.join(out, 'chat-follow-up.png'), fullPage: true });
    report.passed = true;
  } catch (error) {
    report.error = error.message;
    // Do not screenshot a failed login: it can still contain credentials.
    if (!new URL(page.url()).pathname.includes('/login') && page.url() !== 'about:blank')
      await page.locator('button[title="Choose model"]:not([disabled]):visible').waitFor({ timeout: 90000 });
    await page.screenshot({ path: path.join(out, 'chat-failure.png'), fullPage: true }).catch(() => {});
    throw error;
  } finally {
    fs.writeFileSync(path.join(out, 'chat-results.json'), JSON.stringify(report, null, 2));
    await browser.close();
  }
})().catch(e => { console.error(e.message); process.exitCode = 1; });
