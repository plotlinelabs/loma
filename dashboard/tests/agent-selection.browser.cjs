// Run against the isolated local stack from run-loma-local, with both test env files loaded (dashboard env last).
// NODE_PATH=<playwright install>/node_modules node tests/agent-selection.browser.cjs
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { MongoClient } = require('mongodb');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
    page.setDefaultTimeout(15000);
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const origin = process.env.TEST_ORIGIN || 'http://localhost:13001';
    assert.ok((process.env.OBSERVABILITY_DB_NAME || '').startsWith('loma_local_'), 'A throwaway database is required');
    assert.equal(new URL(origin).hostname, 'localhost', 'Run only against an isolated local stack');
    const client = await new MongoClient(process.env.OBSERVABILITY_MONGODB_URI).connect();
    try {
      const db = client.db(process.env.OBSERVABILITY_DB_NAME);
      for (const [slug, name, scope, folder] of [
        ['test-personal', 'Personal writing', 'personal', 'Writing'],
        ['test-org', 'Support triage', 'workspace', 'Support'],
        ['test-system', 'Core review', 'system', 'Engineering'],
        ['test-loose', 'General helper', 'workspace', null],
      ]) {
        await db.collection('skills').updateOne({ slug }, { $set: {
          name, scope, folder, description: 'Local UI test fixture',
          created_by: process.env.USER_NAME, enabled: true,
        } }, { upsert: true });
      }
      // Status-only fixtures; no integration credentials or external calls.
      for (const provider of ['github', 'mongodb']) {
        await db.collection('integrations').updateOne({ provider }, { $set: {
          status: 'active', connected_by: process.env.USER_NAME, connected_at: new Date(),
        } }, { upsert: true });
      }
    } finally { await client.close(); }
    await page.goto(`${origin}/login`, { waitUntil: 'domcontentloaded', timeout: 90000 });
    await page.waitForLoadState('networkidle');
    await page.fill('#signin-email', process.env.USER_NAME);
    await page.fill('#signin-password', process.env.PASSWORD);
    await page.fill('#setup-token', process.env.LOMA_SETUP_TOKEN);
    await page.click('button[type="submit"]');
    await page.waitForURL((url) => !url.pathname.includes('/login'), { timeout: 60000 });
    await page.goto(`${origin}/agents`, { waitUntil: 'networkidle', timeout: 90000 });
    const existing = await (await page.request.get(`${origin}/api/agent-identities`)).json();
    for (const agent of existing.agents.filter((agent) => agent.name === 'Checkbox tree QA')) {
      await page.request.delete(`${origin}/api/agent-identities/${agent.agent_id}`);
    }
    await page.reload({ waitUntil: 'networkidle' });
    await page.getByRole('button', { name: 'New agent', exact: true }).first().click();
    const dialog = page.getByRole('dialog');
    await dialog.locator('#agent-name').fill('Checkbox tree QA');
    await dialog.locator('#agent-description').fill('Isolated browser test for nested skill and tool selection');
    const skills = dialog.getByRole('region', { name: 'Skills', exact: true });
    const tools = dialog.getByRole('region', { name: 'Tools', exact: true });
    assert.equal(await skills.getByText('All (no restriction)', { exact: true }).count(), 1);
    await skills.getByRole('button', { name: 'Personal / Writing', exact: true }).click();
    await skills.getByRole('checkbox', { name: 'Personal writing', exact: true }).check();
    await skills.getByRole('button', { name: 'Organisation / Support', exact: true }).click();
    await skills.getByRole('checkbox', { name: 'Support triage', exact: true }).check();
    await skills.getByRole('button', { name: 'Organisation / System', exact: true }).click();
    await skills.getByRole('button', { name: 'Organisation / System / Engineering', exact: true }).click();
    await skills.getByRole('checkbox', { name: 'Core review', exact: true }).check();
    await tools.getByRole('button', { name: 'Personal / Google', exact: true }).click();
    await tools.getByRole('checkbox', { name: 'Gmail', exact: true }).check();
    await tools.getByRole('button', { name: 'Organisation / Engineering', exact: true }).click();
    await tools.getByRole('checkbox', { name: 'GitHub', exact: true }).check();
    // Search exposes collapsed ancestors and does not mutate selections.
    await skills.getByRole('textbox', { name: 'Search skills' }).fill('Core review');
    assert.equal(await skills.getByRole('checkbox').count(), 1);
    assert.equal(await skills.getByRole('checkbox').isChecked(), true);
    await skills.getByRole('textbox', { name: 'Search skills' }).fill('no-such-skill');
    assert.equal(await skills.getByText('No matching skills').count(), 1);
    await skills.getByRole('textbox', { name: 'Search skills' }).fill('');
    const shots = process.env.TEST_SCREENSHOTS || '/tmp/agent-checkbox-shots';
    fs.mkdirSync(shots, { recursive: true });
    await skills.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${shots}/create.png` });
    let response = page.waitForResponse((r) => r.url().endsWith('/api/agent-identities') && r.request().method() === 'POST');
    await dialog.getByRole('button', { name: 'Create agent', exact: true }).click();
    const createdResponse = await response;
    assert.equal(createdResponse.ok(), true);
    const created = (await createdResponse.json()).agent;
    assert.deepEqual(created.skills, ['test-personal', 'test-org', 'test-system']);
    assert.deepEqual(created.tools, ['gmail', 'GitHub']);
    await page.getByRole('button', { name: 'Edit agent', exact: true }).click();
    await skills.getByRole('textbox', { name: 'Search skills' }).fill('Personal writing');
    assert.equal(await skills.getByRole('checkbox', { name: 'Personal writing', exact: true }).isChecked(), true);
    // Keyboard deselection as well as pointer selection.
    await skills.getByRole('checkbox', { name: 'Personal writing', exact: true }).focus();
    await page.keyboard.press('Space');
    assert.equal(await skills.getByRole('checkbox', { name: 'Personal writing', exact: true }).isChecked(), false);
    await skills.getByRole('textbox', { name: 'Search skills' }).fill('');
    await tools.getByRole('textbox', { name: 'Search tools' }).fill('Gmail');
    await tools.getByRole('checkbox', { name: 'Gmail', exact: true }).uncheck();
    await tools.getByRole('textbox', { name: 'Search tools' }).fill('');
    await skills.getByRole('button', { name: 'Organisation / Support', exact: true }).click();
    await skills.getByRole('button', { name: 'Organisation / System', exact: true }).click();
    await skills.getByRole('button', { name: 'Organisation / System / Engineering', exact: true }).click();
    await tools.getByRole('button', { name: 'Organisation / Engineering', exact: true }).click();
    await tools.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${shots}/edit.png` });
    response = page.waitForResponse((r) => r.url().endsWith(`/api/agent-identities/${created.agent_id}`) && r.request().method() === 'PATCH');
    await dialog.getByRole('button', { name: 'Save changes', exact: true }).click();
    const edited = (await (await response).json()).agent;
    assert.deepEqual(edited.skills, ['test-org', 'test-system']);
    assert.deepEqual(edited.tools, ['GitHub']);
    // Legacy/unavailable values survive unchanged until explicitly removed.
    const patched = await page.request.patch(`${origin}/api/agent-identities/${created.agent_id}`, { data: { skills: ['test-system'], tools: ['legacy-tool'] } });
    assert.equal(patched.ok(), true);
    // Simulate a catalogue that no longer includes a saved skill.
    await page.route('**/api/skills', async (route) => {
      const response = await route.fetch();
      const data = await response.json();
      data.skills = data.skills.filter((skill) => skill.slug !== 'test-system');
      await route.fulfill({ response, json: data });
    });
    await page.reload({ waitUntil: 'networkidle' });
    await page.getByRole('button', { name: 'Edit agent', exact: true }).click();
    assert.equal(await skills.getByRole('checkbox', { name: 'test-system', exact: true }).isChecked(), true);
    assert.equal(await tools.getByRole('checkbox', { name: 'legacy-tool', exact: true }).isChecked(), true);
    await skills.getByRole('checkbox', { name: 'test-system', exact: true }).click();
    await tools.getByRole('checkbox', { name: 'legacy-tool', exact: true }).click();
    assert.equal(await tools.getByText('All (no restriction)', { exact: true }).count(), 1);
    await page.setViewportSize({ width: 390, height: 844 });
    await tools.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${shots}/mobile.png` });
    assert.equal(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth), true);
    response = page.waitForResponse((r) => r.url().endsWith(`/api/agent-identities/${created.agent_id}`) && r.request().method() === 'PATCH');
    await dialog.getByRole('button', { name: 'Save changes', exact: true }).click();
    const unrestricted = (await (await response).json()).agent;
    assert.deepEqual(unrestricted.skills, []);
    assert.deepEqual(unrestricted.tools, []);
    assert.deepEqual(errors, []);
    console.log('PASS: create/edit persistence, nested scopes/folders, search, keyboard, saved legacy values, empty=all, mobile overflow, no JS errors');
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
