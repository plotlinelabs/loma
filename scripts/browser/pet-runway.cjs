// Run against the isolated run-loma-local stack, never production.
// NODE_PATH=<directory containing playwright> LOMA_SETUP_TOKEN=... PET_TEST_PASSWORD=... node scripts/browser/pet-runway.cjs
const { chromium } = require('playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(20000);
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  let status = 'running';
  const id = 'pet-runway-fixture';
  await page.route('**/api/tasks', async route => {
    const response = await route.fetch();
    const data = await response.json();
    data.tasks = [{ conversation_id: id, title: 'Pet runway test', column: 'working', status, task_tag_ids: [], total_turns: 0 }];
    data.counts = { working: 1 };
    await route.fulfill({ response, json: data });
  });
  await page.route(`**/api/conversations/${id}`, route => route.fulfill({ json: {
    conversation: { conversation_id: id, prompt: 'Review the launch checklist', status,
      messages: [{ role: 'user', content: 'Review the launch checklist' }, { role: 'assistant', content: 'Checking the launch checklist now.' }] },
    turns: [], artifacts: [],
  } }));
  await page.goto('http://localhost:13001/login');
  await page.fill('#signin-email', 'pet-runway@example.com');
  await page.fill('#signin-password', process.env.PET_TEST_PASSWORD);
  await page.fill('#setup-token', process.env.LOMA_SETUP_TOKEN);
  await page.click('button[type=submit]');
  await page.waitForURL('**/tasks');
  async function preference(visible = true, animated = true) {
    const response = await page.request.patch('http://localhost:13001/api/governance/me/pet', {
      data: { pet_id: 'corgi', visible, animated },
    });
    assert.equal(response.status(), 200);
    await page.reload();
  }
  async function openTask() {
    await page.getByText('Pet runway test', { exact: true }).first().click();
    await page.locator('[role=dialog] textarea').waitFor();
  }
  const lane = page.locator('[role=dialog] .pet-runway');
  const sprite = lane.locator('[data-pet=corgi]');
  await preference();
  await openTask();
  await sprite.waitFor();
  const a = await sprite.boundingBox();
  await page.waitForTimeout(700);
  const b = await sprite.boundingBox();
  assert.ok(Math.abs(a.x - b.x) > 15, 'pet travels horizontally');
  const input = page.locator('[role=dialog] textarea');
  const box = await input.boundingBox();
  assert.ok(b.y + b.height <= box.y, 'pet is above the input');
  await input.fill('A follow-up draft while my pet runs');
  assert.equal(await input.inputValue(), 'A follow-up draft while my pet runs');
  // Sample both ends of the path and its return without waiting a full cycle.
  for (const time of [0, 3990, 4010, 7990]) {
    await lane.evaluate((el, t) => el.getAnimations({ subtree: true }).forEach(a => { a.pause(); a.currentTime = t; }), time);
    const pet = await sprite.boundingBox();
    const track = await lane.boundingBox();
    assert.ok(pet.x >= track.x - 1 && pet.x + pet.width <= track.x + track.width + 1, 'pet stays within lane');
  }
  await lane.evaluate(el => el.getAnimations({ subtree: true }).forEach(a => a.play()));
  await page.screenshot({ path: '/tmp/loma-pet-runway-desktop.png', fullPage: true });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  assert.equal(await lane.evaluate(el => el.getAnimations({ subtree: true }).length), 0);
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(500);
  const mobileLane = await lane.boundingBox();
  assert.ok(mobileLane.x >= 0 && mobileLane.x + mobileLane.width <= 390);
  await input.fill('Mobile draft');
  await page.screenshot({ path: '/tmp/loma-pet-runway-mobile.png', fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  for (const terminal of ['needs_input', 'completed', 'error', 'interrupted']) {
    status = terminal;
    await lane.waitFor({ state: 'hidden' });
    status = 'running';
    await page.reload(); await openTask(); await sprite.waitFor();
  }
  await preference(true, false); await openTask(); await sprite.waitFor();
  assert.equal(await lane.evaluate(el => el.getAnimations({ subtree: true }).length), 0);
  await preference(false); await openTask();
  assert.equal(await lane.count(), 0);
  assert.deepEqual(errors, []);
  console.log('PASS: running movement, lane bounds/turnaround, input interaction, reduced motion, mobile, needs input/completion/error/interruption, animation off, hidden pet, no page errors');
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
