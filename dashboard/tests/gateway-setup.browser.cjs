// Real local auth, DB and backend. Keep scheduler disabled: no agent/model/provider calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
const { MongoClient } = require('mongodb');
(async () => {
  assert.match(process.env.OBSERVABILITY_DB_NAME, /^loma_local_/);
  assert.equal(process.env.LOMA_WORK_GATEWAY_SECRET, '');
  const base = process.env.AUTH_URL;
  assert.match(base, /^http:\/\/(localhost:13001|127\.0\.0\.1)$/); // second form: behind tests/nginx-like-proxy.cjs
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
    await card.getByLabel('Enable bounded agent work').check();
    await card.getByLabel('Ashby allowed users (one email per line)').fill(process.env.USER_NAME);
    await card.getByRole('button', { name: 'Save agent settings' }).click();
    await page.waitForTimeout(1500);
    let config = await db.collection('gateway_config').findOne({ _id: 'runtime-settings' });
    assert.equal(config.bounded_work_enabled, true);
    assert.deepEqual(config.ashby_allowed_users, [process.env.USER_NAME.toLowerCase()]);
    await card.getByRole('button', { name: 'Refresh status' }).click();
    await page.waitForTimeout(500);
    assert.ok(await card.getByLabel('Enable bounded agent work').isChecked());
    await card.screenshot({ path: shots + '/connected-desktop.png' });
    await card.getByLabel('Enable bounded agent work').uncheck();
    await card.getByLabel('Ashby allowed users (one email per line)').fill('');
    await card.getByRole('button', { name: 'Save agent settings' }).click();
    await page.waitForTimeout(1500);
    config = await db.collection('gateway_config').findOne({ _id: 'runtime-settings' });
    assert.equal(config.bounded_work_enabled, false);
    assert.deepEqual(config.ashby_allowed_users, []);
    await page.setViewportSize({ width: 390, height: 844 });
    await card.screenshot({ path: shots + '/connected-mobile.png' });
    await db.collection('users').updateOne({ email: process.env.USER_NAME.toLowerCase() }, { $set: { system_role: 'operator' } });
    const forbidden = await page.evaluate(async () => (await fetch('/gateway-setup', { method: 'POST' })).status);
    assert.equal(forbidden, 403);
    await db.collection('users').updateOne({ email: process.env.USER_NAME.toLowerCase() }, { $set: { system_role: 'admin' } });
    // Invoke the real agent CLI with test-only credentials, then answer in the real UI.
    const { execFileSync } = require('node:child_process');
    const ids = JSON.parse(execFileSync(process.env.TEST_PYTHON, ['-c', [
      'import asyncio,json,os',
      'from motor.motor_asyncio import AsyncIOMotorClient',
      'from tools._auth_token import create_user_auth_token',
      'from tools.human_tasks import parser,execute',
      'async def main():',
      ' client=AsyncIOMotorClient(os.environ["OBSERVABILITY_MONGODB_URI"]);db=client[os.environ["OBSERVABILITY_DB_NAME"]]',
      ' email=os.environ["USER_NAME"].lower()',
      ' await db.conversations.insert_one({"conversation_id":"cleanup-source","title":"AR fixture","metadata":{"user_name":email},"status":"completed","messages":[]})',
      ' result=[]',
      ' for kind,key in [("approval","approve"),("approval","reject"),("information","information")]:',
      '  args=parser().parse_args(["--user-email",email,"--auth-token",create_user_auth_token(email),"create","--source-conversation-id","cleanup-source","--assignee",email,"--title","Review fixture invoice "+key,"--details","Fixture only: approve INR 100 for TEST-123. No real invoice will be changed.","--request-key",key,"--kind",kind])',
      '  item=await execute(args);result.append(item["task"]["conversation_id"])',
      ' print(json.dumps(result));client.close()',
      'asyncio.run(main())',
    ].join('\n')], { encoding: 'utf8', env: process.env }));
    await page.setViewportSize({ width: 1440, height: 1000 });
    for (let i = 0; i < ids.length; i++) {
      await page.goto(base + '/chat?continue=' + ids[i], { timeout: 120000, waitUntil: 'domcontentloaded' });
      const panel = page.getByRole('region', { name: 'Human task', exact: true });
      await panel.getByText('Fixture only:', { exact: false }).waitFor();
      if (i === 0) await panel.screenshot({ path: shots + '/approval-desktop.png' });
      if (i === 2) {
        await page.setViewportSize({ width: 390, height: 844 });
        await panel.screenshot({ path: shots + '/information-mobile.png' });
        assert.equal(await panel.getByRole('button', { name: 'Approve', exact: true }).count(), 0);
      }
      await panel.getByLabel('Your decision notes or requested information').fill('Fixture decision only');
      await panel.getByRole('button', { name: ['Approve', 'Reject', 'Provide information'][i], exact: true }).click();
      await panel.getByText('Response saved:', { exact: false }).waitFor();
      const stored = await db.collection('conversations').findOne({ conversation_id: ids[i] });
      assert.equal(stored.human_task.decision, ['approve', 'reject', 'provide_information'][i]);
      assert.equal(stored.human_task.resume_state, 'queued');
      if (i === 0) await panel.screenshot({ path: shots + '/approved-desktop.png' });
    }

    console.log('PASS: real setup, signed backend check, no secret exposure, concurrent retry, cross-origin rejection, role revocation, desktop/mobile. Scheduler safely disabled.');
  } finally { await browser.close(); await mongo.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
