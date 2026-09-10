/** Run against an isolated local stack after setup-token login.
 * NODE_PATH=<playwright install>/node_modules LOMA_TEST_STORAGE=<storage-state.json>
 *   node tests/browser/task-capture.cjs
 * This creates test tasks and starts two harmless, tool-free agent prompts.
 */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const baseURL = process.env.LOMA_TEST_URL || 'http://localhost:13001';
assert.equal(new URL(baseURL).hostname, 'localhost', 'Use only the isolated local test stack');
const output = process.env.LOMA_TEST_OUTPUT || '/tmp/loma-task-capture-screenshots';
fs.mkdirSync(output, { recursive: true });
(async () => {
  const browser = await chromium.launch();
  try {
    for (const mobile of (process.env.LOMA_MOBILE_ONLY ? [true] : [false, true])) {
      const label = mobile ? 'mobile' : 'desktop';
      const context = await browser.newContext({
        baseURL, storageState: process.env.LOMA_TEST_STORAGE,
        viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 },
        isMobile: mobile, hasTouch: mobile,
      });
      await context.addInitScript(() => localStorage.setItem('dashboard-chat-selected-model', 'codex/gpt-5.6-sol'));
      const page = await context.newPage();
      page.setDefaultTimeout(20000);
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      const settings = await context.request.put('/api/tasks/board-settings', { data: {
        lanes: [{ id: 'todo', name: 'Todo' }, { id: 'ideas', name: 'Ideas' }], prompt: '',
      }});
      assert.equal(settings.status(), 200);
      await page.goto('/tasks');
      const open = page.getByRole('button', { name: 'New task', exact: true });
      await open.waitFor();
      await page.waitForTimeout(1500);
      const board = async () => (await (await context.request.get('/api/tasks')).json());
      const initial = (await board()).tasks.length;
      assert.equal(await page.getByRole('textbox', {name:'Task details'}).count(), 0);
      await open.click();
      await page.getByRole('button', {name:'Close task composer'}).click();
      assert.equal((await board()).tasks.length, initial, 'Opening/closing must not create a blank card');

      if (mobile) await page.getByRole('button', {name:/^Ideas/}).click();
      await page.getByRole('button', {name:'Add task to Ideas'}).click();
      const details = page.getByRole('textbox', {name:'Task details'});
      await details.waitFor();
      assert.equal(await page.getByLabel('List', {exact:true}).inputValue(), 'ideas');
      const prompt = `Investigate dashboard loading times (${label} ${Date.now().toString(36).slice(-4)})`;
      await details.fill('Detailed task notes '.repeat(400));
      await details.press('Enter');
      assert.equal((await board()).tasks.length, initial, 'Enter inserts a newline rather than starting a task');
      const actionBounds = await page.getByRole('button', {name:'Add & start',exact:true}).boundingBox();
      assert.ok(actionBounds && actionBounds.y + actionBounds.height <= (mobile ? 844 : 1000), 'Large text must not hide actions');
      await details.fill(prompt);
      await page.getByRole('button', {name:'Close task composer'}).click();
      assert.equal((await board()).tasks.length, initial);
      await page.getByRole('button', {name:'Add task to Ideas'}).click();
      assert.equal(await details.inputValue(), prompt, 'Dismissal preserves unsaved text');
      if (mobile) {
        const header = page.getByRole('heading', {name:'New task', exact:true}).locator('..');
        await header.evaluate(element => {
          const start = new Touch({identifier:0,target:element,clientX:100,clientY:200});
          const end = new Touch({identifier:0,target:element,clientX:100,clientY:300});
          element.dispatchEvent(new TouchEvent('touchstart', {bubbles:true,touches:[start],targetTouches:[start],changedTouches:[start]}));
          element.dispatchEvent(new TouchEvent('touchend', {bubbles:true,changedTouches:[end]}));
        });
        await details.waitFor({state:'hidden'});
        await open.click();
        assert.equal(await details.inputValue(), prompt, 'Swipe dismissal preserves text');
        assert.equal(await page.getByLabel('List', {exact:true}).inputValue(), 'ideas', 'Header reopening preserves draft list');
      }

      await page.locator('input[type=file]').setInputFiles({ name: 'task-notes.txt', mimeType: 'text/plain', buffer: Buffer.from('Check performance without changing production.') });
      await page.getByText('task-notes.txt', {exact:true}).waitFor();
      const add = page.getByRole('button', {name:'Add task', exact:true});
      await page.waitForFunction(() => ![...document.querySelectorAll('button')].find(b=>b.textContent==='Add task')?.disabled);
      await page.screenshot({path:`${output}/${label}-composer.png`});
      // Simulated transient failure must retain text and files for retry.
      await page.route('**/api/tasks', async route => {
        if (route.request().method() !== 'POST') return route.continue();
        await route.fulfill({status:503, contentType:'application/json', body:JSON.stringify({error:'Temporary test outage'})});
      });
      await add.click();
      await page.getByRole('alert').waitFor();
      assert.equal(await details.inputValue(), prompt);
      assert.equal((await board()).tasks.length, initial);
      await page.unroute('**/api/tasks');
      let postCount = 0;
      await page.route('**/api/tasks', async route => {
        if (route.request().method() !== 'POST') return route.continue();
        postCount++;
        const response = await route.fetch();
        await new Promise(resolve => setTimeout(resolve, 300));
        await route.fulfill({response});
      });
      const savedResponse = page.waitForResponse(r=>r.url().endsWith('/api/tasks') && r.request().method()==='POST');
      await add.evaluate(button => { button.click(); button.click(); });
      const saved = await (await savedResponse).json();
      assert.equal(saved.task.title, prompt);
      assert.equal(postCount, 1, 'Repeated clicks must create only one task');
      await page.unroute('**/api/tasks');
      assert.equal(saved.task.task_lane, 'ideas');
      assert.equal(saved.task.task_status, 'todo');
      assert.equal(saved.task.started_at, null);
      await details.waitFor({state:'hidden'});
      assert.equal(new URL(page.url()).pathname, '/tasks');
      await page.getByText(prompt, {exact:true}).waitFor();
      await page.screenshot({path:`${output}/${label}-board.png`});
      // Inspect the persisted conversation: full prompt and staged attachment.
      const conversationResponse = await context.request.get(`/api/conversations/${saved.task.conversation_id}`);
      assert.equal(conversationResponse.status(), 200);
      const conversation = await conversationResponse.json();
      const doc = conversation.conversation || conversation;
      assert.equal(doc.prompt, prompt);
      assert.equal(doc.draft_files[0].name, 'task-notes.txt');

      // Existing task mode must have no new-task composer underneath it.
      await page.getByText(prompt, {exact:true}).click();
      assert.equal(await page.getByRole('textbox', {name:'Task details'}).count(), 0);
      await page.goto('/tasks');
      await open.waitFor();
      await page.waitForTimeout(500);
      await open.click();
      await details.fill('Reply exactly READY. Do not use tools. This is a local task creation smoke test.');
      await page.locator('summary').filter({hasText:'Model & tools'}).click();
      // Explicitly ensure runs use the current user's Codex connection.
      await page.getByTitle('Choose model', {exact:true}).waitFor();
      assert.match(await page.getByTitle('Choose model', {exact:true}).innerText(), /gpt-5.6-sol/i);
      await page.screenshot({path:`${output}/${label}-start.png`});
      if (mobile) {
        // Emulate a reduced visual viewport while the keyboard is open.
        await page.evaluate(()=>document.documentElement.style.setProperty('--app-h','510px'));
        await page.waitForTimeout(250);
        await page.screenshot({path:`${output}/mobile-keyboard.png`});
        const box = await page.getByRole('button',{name:'Add & start',exact:true}).boundingBox();
        assert.ok(box && box.y >= 0 && box.y + box.height <= 510, 'Actions stay above the keyboard');
        await page.screenshot({path:`${output}/mobile-keyboard.png`});
        await page.evaluate(()=>document.documentElement.style.removeProperty('--app-h'));
      }
      const startedResponse = page.waitForResponse(r=>r.url().endsWith('/api/tasks') && r.request().method()==='POST');
      await page.getByRole('button',{name:'Add & start',exact:true}).click();
      const started = await (await startedResponse).json();
      assert.equal(started.task.task_status, 'active');
      assert.ok(started.task.title && started.task.title !== 'New task');
      assert.ok(started.task.started_at);
      await details.waitFor({state:'hidden'});
      assert.equal(new URL(page.url()).pathname, '/tasks');
      assert.deepEqual(errors, []);
      console.log(`${label}: capture, lane, dismiss, attachments, failure/retry, save, clean board, existing task, start and naming passed`);
      await context.close();
    }
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1)});
