// Real local auth, DB and backend. Keep scheduler disabled: no agent/model/provider calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
const { MongoClient } = require('mongodb');
(async () => {
  assert.match(process.env.OBSERVABILITY_DB_NAME, /^loma_local_/);
  assert.equal(process.env.LOMA_WORK_GATEWAY_SECRET, '');
  const base = process.env.AUTH_URL;
  assert.match(base, /^http:\/\/localhost:13001$/);
  const mongo = await new MongoClient(process.env.OBSERVABILITY_MONGODB_URI).connect();
  const db = mongo.db(process.env.OBSERVABILITY_DB_NAME);
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH });
  const shots = process.env.SHOTS_DIR || '/tmp/gateway-setup-shots';
  fs.mkdirSync(shots, { recursive: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.setDefaultTimeout(60000);
    await page.goto(base + '/login', { timeout: 120000, waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2000);
    await page.fill('#signin-email', process.env.USER_NAME);
    await page.fill('#signin-password', process.env.PASSWORD);
    await page.fill('#setup-token', process.env.LOMA_SETUP_TOKEN);
    await page.click('button[type=submit]');
    await page.waitForURL(u => !u.pathname.includes('/login'));
    await page.goto(base + '/admin', { timeout: 120000, waitUntil: 'domcontentloaded' });
    await page.getByRole('tab', { name: 'Environment' }).click();
    const card = page.locator('[data-slot="card"]').filter({ hasText: 'Human task approvals' });
    await card.getByText('Gateway not configured.', { exact: false }).waitFor();
    await card.screenshot({ path: shots + '/before.png' });
    await card.getByRole('button', { name: 'Set up approvals' }).click();
    await card.getByText('Gateway connected.', { exact: false }).waitFor();
    await card.getByText('Agent continuation is not running.', { exact: false }).waitFor();
    const first = await db.collection('gateway_config').findOne({ _id: 'human-session' });
    assert.equal(first.secret.length, 64);
    assert.equal(first.created_by, process.env.USER_NAME.toLowerCase());
    assert.ok(!(await page.content()).includes(first.secret));
    const results = await page.evaluate(async () => Promise.all(Array.from({ length: 8 }, async () => {
      const r = await fetch('/gateway-setup', { method: 'POST' }); return { status: r.status, body: await r.json() };
    })));
    assert.ok(results.every(r => r.status === 200 && r.body.connected && !r.body.scheduler_running));
    const second = await db.collection('gateway_config').findOne({ _id: 'human-session' });
    assert.equal(second.secret, first.secret);
    assert.equal(await db.collection('gateway_config').countDocuments({}), 1);
    const denied = await page.request.post(base + '/gateway-setup', { headers: { origin: 'https://evil.test' } });
    assert.equal(denied.status(), 403);
    await card.screenshot({ path: shots + '/connected-desktop.png' });
    await page.setViewportSize({ width: 390, height: 844 });
    await card.screenshot({ path: shots + '/connected-mobile.png' });
    await db.collection('users').updateOne({ email: process.env.USER_NAME.toLowerCase() }, { $set: { system_role: 'operator' } });
    const forbidden = await page.evaluate(async () => (await fetch('/gateway-setup', { method: 'POST' })).status);
    assert.equal(forbidden, 403);
    await db.collection('users').updateOne({ email: process.env.USER_NAME.toLowerCase() }, { $set: { system_role: 'admin' } });
    console.log('PASS: real setup, signed backend check, no secret exposure, concurrent retry, cross-origin rejection, role revocation, desktop/mobile. Scheduler safely disabled.');
  } finally { await browser.close(); await mongo.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
