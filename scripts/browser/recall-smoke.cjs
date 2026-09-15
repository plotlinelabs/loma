/* Isolated browser login and actual search/fetch proxy smoke. Not an AI test. */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
const root = path.resolve(__dirname, '../..');
const backend = require('dotenv').parse(fs.readFileSync(path.join(root, '.env')));
assert(backend.OBSERVABILITY_DB_NAME.startsWith('loma_local_'));
assert.equal(backend.LOMA_ENABLE_SLACK, 'false');
assert.equal(backend.LOMA_ENABLE_SCHEDULER, 'false');
assert.equal(backend.WEBHOOK_PORT, '13000');
const state = JSON.parse(fs.readFileSync(process.env.LOMA_RECALL_BROWSER_STATE));
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    await page.goto('http://localhost:13001/login');
    await page.waitForLoadState('networkidle');
    await page.fill('#signin-email', state.email);
    await page.fill('#signin-password', state.password);
    await page.fill('#setup-token', state.setup);
    await page.click('button[type="submit"]');
    await page.waitForURL(u => !u.pathname.includes('/login'), { timeout: 60000 });
    await page.waitForTimeout(2000);
    if (!state.capability) { console.log('PASS first-admin login'); return; }
    const data = await page.evaluate(async capability => {
      const post = async (name, body, authenticated = true) => {
        const response = await fetch(`/api/recall/${name}`, { method: 'POST',
          headers: { 'Content-Type': 'application/json', ...(authenticated ? { Authorization: `Bearer ${capability}` } : {}) },
          body: JSON.stringify(body) });
        return { status: response.status, body: await response.json() };
      };
      const search = await post('search', { query: 'RECALL-BROWSER-CANARY' });
      const hit = search.body.results?.[0];
      const fetched = hit ? await post('fetch', { conversation_id: hit.conversation_id, anchor_message_id: hit.message_id }) : null;
      const denied = await post('search', { query: 'RECALL-BROWSER-CANARY' }, false);
      return { search, fetched, denied };
    }, state.capability);
    assert.equal(data.search.status, 200);
    assert.equal(data.search.body.results.length, 1);
    assert.equal(data.fetched.status, 200);
    assert.equal(data.denied.status, 401);
    assert(!JSON.stringify(data).includes('HIDDEN_BROWSER_SECRET'));
    await page.evaluate(data => {
      const evidence = document.createElement('pre');
      evidence.textContent = 'ACTUAL SEARCH/FETCH PROXY TEST, NOT A MODEL RESPONSE\n' + JSON.stringify(data, null, 2);
      evidence.style.cssText = 'position:fixed;inset:20px;overflow:auto;background:white;color:black;padding:20px;z-index:999999;font-size:13px';
      document.body.appendChild(evidence);
    }, data);
    await page.screenshot({ path: process.env.LOMA_RECALL_SCREENSHOT, fullPage: true });
    console.log('PASS browser login, search, anchored fetch, redaction, session-only denial');
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exit(1); });
