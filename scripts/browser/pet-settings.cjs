// Run against the isolated run-loma-local stack, never production.
// NODE_PATH=<directory containing playwright> LOMA_SETUP_TOKEN=... PET_TEST_PASSWORD=... node scripts/browser/pet-runway.cjs
const { chromium } = require('playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({args:['--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream']});
  const page = await browser.newPage({ permissions:['microphone'], viewport: { width: 1440, height: 1000 } });
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
  const picker = page.getByRole('dialog', { name: 'Your pet companion' });
  const closePicker = async () => { await picker.getByRole('button', { name: 'Close', exact: true }).click(); await picker.waitFor({state:'detached'}); };
  await page.getByRole('button', {name:'Account menu'}).click();
  await page.getByRole('menuitem', {name:'Pet settings',exact:true}).waitFor();
  await page.waitForTimeout(300);
  await page.screenshot({path:'/tmp/pet-settings-menu.png'});
  await page.getByRole('menuitem', {name:'Pet settings',exact:true}).click();
  await picker.waitFor();
  await picker.getByRole('button',{name:'Corgi',exact:true}).click();
  await picker.getByRole('button',{name:'Save pet'}).click();
  await picker.waitFor({state:'detached'});
  await page.reload();
  // Every companion is a native button, without nested links/buttons.
  const pets = page.getByRole('button',{name:'Pet settings',exact:true});
  await pets.nth(1).waitFor();
  assert.ok(await pets.count() >= 2);
  for (let i=0; i<await pets.count(); i++) {
    assert.equal(await pets.nth(i).evaluate(e => !!e.parentElement.closest('button,a')),false);
    await pets.nth(i).click(); await picker.waitFor(); await closePicker();
  }
  await pets.last().focus(); await page.keyboard.press('Enter'); await picker.waitFor();
  await page.keyboard.press('Escape'); await picker.waitFor({state:'detached'});
  assert.ok(await pets.last().evaluate(e => e===document.activeElement));
  await page.getByText('Pet runway test',{exact:true}).first().click();
  const input=page.locator('[role=dialog] textarea'); await input.waitFor();
  await input.fill('Keep this draft while changing my pet');
  const runner=page.locator('.pet-runway').getByRole('button',{name:'Pet settings'});
  const laneBox = await page.locator(".pet-runway").boundingBox();
  await page.mouse.move(laneBox.x + 16, laneBox.y + 16);
  const petBox = await runner.boundingBox();
  await page.mouse.move(petBox.x + 16, petBox.y + 16);
  await page.mouse.click(petBox.x + 16, petBox.y + 16); await picker.waitFor();
  await page.waitForTimeout(300);
  await page.screenshot({path:'/tmp/pet-settings-drawer.png'});
  await picker.getByRole('button',{name:'Rabbit',exact:true}).click();
  await picker.getByRole('button',{name:'Save pet'}).click(); await picker.waitFor({state:'detached'});
  assert.equal(await input.inputValue(),'Keep this draft while changing my pet');
  await runner.locator('[data-pet=rabbit]').waitFor();
  await runner.focus(); await page.keyboard.press('Space'); await picker.waitFor();
  await closePicker(); assert.ok(await runner.evaluate(e => e===document.activeElement));
  await page.setViewportSize({width:390,height:844});
  await runner.focus(); await runner.click(); await picker.waitFor();
  await page.waitForTimeout(300);
  await page.screenshot({path:'/tmp/pet-settings-mobile.png'});
  await closePicker(); await input.fill('Mobile input still works');
  await page.reload();
  // Hidden users can always recover the pet from the account menu.
  await preference(false);
  await page.setViewportSize({width:1440,height:1000});
  await page.getByRole('button',{name:'Account menu'}).click();
  await page.getByRole('menuitem',{name:'Pet settings',exact:true}).click(); await picker.waitFor();
  await picker.getByRole('checkbox',{name:'Show my pet throughout Loma'}).check();
  await picker.getByRole('button',{name:'Save pet'}).click(); await picker.waitFor({state:'detached'});
  await pets.first().waitFor();
  await page.goto('http://localhost:13001/chat');
  await page.getByRole('button',{name:'Start dictation',exact:true}).click();
  const listening=page.locator('button[aria-label="Pet settings"]').filter({has:page.locator('[data-state=listening]')});
  await listening.waitFor(); await listening.click(); await picker.waitFor(); await closePicker();
  await page.getByRole('button',{name:'Stop dictation and transcribe',exact:true}).waitFor();
  await page.route('**/api/transcribe',r=>r.fulfill({json:{text:'Local voice test'}}));
  await page.getByRole('button',{name:'Stop dictation and transcribe',exact:true}).click();
  await page.getByRole('button',{name:'Start dictation',exact:true}).waitFor();
  assert.deepEqual(errors,[]);
  console.log('PASS: account menu, all board/sidebar pets, keyboard/focus, running drawer picker, draft preservation, mobile, hidden-pet recovery, listening pet without stopping recording; no page errors');
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
