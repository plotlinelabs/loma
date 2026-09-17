// Real local auth + app UI. Keyboard viewport signals are simulated, not a real OS keyboard.
// Source isolated dashboard/.env; NODE_PATH=<playwright node_modules> node tests/mobile-composer.browser.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium, devices } = require('playwright');
const base = process.env.AUTH_URL;
assert.match(base, /^http:\/\/localhost:/);
const shots = process.env.SHOTS_DIR || '/tmp/mobile-composer-shots';
fs.mkdirSync(shots, { recursive: true });
(async () => {
  const browser = await chromium.launch();
  const results = [], errors = [];
  const check = (name, ok) => { assert.ok(ok, name); results.push(name); console.log('PASS', name); };
  try {
    const context = await browser.newContext({ ...devices['iPhone 13'], viewport: { width: 390, height: 844 } });
    const page = await context.newPage();
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(`${base}/login`, { waitUntil: 'networkidle', timeout: 90000 });
    await page.fill('#signin-email', process.env.USER_NAME);
    await page.fill('#signin-password', process.env.PASSWORD);
    await page.fill('#setup-token', process.env.LOMA_SETUP_TOKEN);
    await page.click('button[type=submit]');
    await page.waitForURL(u => !u.pathname.includes('/login'), { timeout: 60000 });
    await page.goto(`${base}/chat`, { waitUntil: 'networkidle' });
    const input = page.getByPlaceholder('Ask Loma anything...');
    await input.waitFor();
    await page.waitForTimeout(1000);
    const nav = page.locator('main > nav');
    for (const width of [320, 360, 390, 430]) {
      await page.setViewportSize({ width, height: 844 });
      await page.waitForTimeout(300);
      check(`${width}px: no horizontal overflow`, await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      const form = input.locator('xpath=ancestor::form');
      const bounds = await form.boundingBox();
      check(`${width}px: compact composer under 120px`, bounds.height <= 120);
      const controls = await Promise.all(['Attach files', 'Chat settings', 'Start dictation'].map(name => page.getByRole('button', {name, exact: true}).boundingBox()));
      check(`${width}px: 44px controls in one row`, controls.every(b => b && b.height >= 44 && Math.abs(b.y-controls[0].y)<2));
      check(`${width}px: toolbar contained`, controls.every(b => b.x>=bounds.x && b.x+b.width<=bounds.x+bounds.width));
      await page.screenshot({ path: `${shots}/idle-${width}.png` });
    }
    check('mobile text is 16px (no Safari focus zoom)', await input.evaluate(el => getComputedStyle(el).fontSize === '16px'));
    await page.getByRole('button', {name:'Chat settings',exact:true}).click();
    const settings = page.getByRole('dialog', {name:'Chat settings',exact:true});
    await settings.waitFor();
    await settings.getByRole('button', { name: /Tools:/ }).click();
    const toolsDialog = page.getByRole('dialog', {name:'Tools selection'});
    await toolsDialog.waitFor();
    // The desktop popover is role=dialog with the same name, so assert the sheet itself.
    check('Tools reachable through settings as a bottom sheet', await toolsDialog.evaluate(el => el.dataset.slot === 'sheet-content' && el.dataset.side === 'bottom'));
    await page.keyboard.press('Escape');
    await settings.getByRole('button', { name: /Skills:/ }).click();
    const skillsDialog = page.getByRole('dialog', {name:'Skills selection'});
    await skillsDialog.waitFor();
    check('Skills reachable through settings as a bottom sheet', await skillsDialog.evaluate(el => el.dataset.slot === 'sheet-content' && el.dataset.side === 'bottom'));
    await page.keyboard.press('Escape');
    await page.screenshot({ path:`${shots}/settings.png` });
    await page.keyboard.press('Escape');
    await settings.waitFor({state:'hidden'});
    await page.waitForTimeout(300);
    await input.fill('Help me review the mobile experience');
    await page.getByRole('button', {name:'Start dictation',exact:true}).waitFor({state:'hidden'});
    await page.waitForTimeout(500);
    check('Send replaces the idle mic', await page.getByRole('button', {name:'Send message',exact:true}).isVisible() && !await page.getByRole('button', {name:'Start dictation',exact:true}).isVisible());
    check('hardware-keyboard focus keeps navigation', await nav.isVisible());
    await input.focus();
    check('typing input owns focus', await input.evaluate(el=>document.activeElement===el));
    // Inject dimensions and dispatch actual events consumed by both viewport hooks.
    // Do NOT set --app-h: the production ViewportHeightSync must do that itself.
    const resize = async (layout, visual, scale=1) => {
      await page.evaluate(({layout,visual,scale}) => {
        Object.defineProperty(window, 'innerHeight', {configurable:true,value:layout});
        Object.defineProperty(window.visualViewport, 'height', {configurable:true,value:visual});
        Object.defineProperty(window.visualViewport, 'scale', {configurable:true,value:scale});
        window.dispatchEvent(new Event('resize'));
        window.visualViewport.dispatchEvent(new Event('resize'));
      }, {layout,visual,scale});
      await page.waitForTimeout(400);
    };
    for (const [label,layout] of [['safari',844],['android',480]]) {
      await resize(layout,480);
      check(`${label}: keyboard hides bottom nav`, !await nav.count());
      check(`${label}: keyboard hides hero`, !await page.getByText('A little help.', {exact:false}).isVisible());
      const box = await input.locator('xpath=ancestor::form').boundingBox();
      check(`${label}: composer stays above keyboard`, box.y>=0 && box.y+box.height<=480);
      await page.screenshot({path:`${shots}/typing-${label}.png`,clip:{x:0,y:0,width:430,height:480}});
      await resize(844,844);
      check(`${label}: navigation returns on keyboard close while input focused`, await nav.isVisible());
    }
    await resize(844,780);
    check('browser toolbar resize is not a keyboard', await nav.isVisible());
    await resize(844,420,2);
    check('pinch zoom is not a keyboard', await nav.isVisible());
    await resize(844,844);
    await input.fill(Array(15).fill('A longer message that wraps across the phone.').join('\n'));
    check('multiline input caps at 160px', (await input.boundingBox()).height <= 160);
    await resize(480,480);
    check('multiline composer remains above keyboard', await input.locator('xpath=ancestor::form').evaluate(el => el.getBoundingClientRect().bottom <=480));
    await page.screenshot({path:`${shots}/multiline.png`});
    await resize(844,844);
    await input.fill('');
    await input.blur();
    await page.emulateMedia({ colorScheme:'dark' });
    await page.screenshot({path:`${shots}/dark.png`});
    const desktop = await browser.newContext({viewport:{width:1440,height:900}, storageState: await context.storageState()});
    const dp = await desktop.newPage();
    await dp.goto(`${base}/chat`,{waitUntil:'networkidle'});
    await dp.getByPlaceholder('What do you need to get done?').waitFor();
    check('desktop keeps full toolbar', await dp.getByRole('button',{name:/Tools:/}).isVisible() && await dp.getByRole('button',{name:/Skills:/}).isVisible());
    check('desktop has no compact settings trigger', !await dp.getByRole('button',{name:'Chat settings',exact:true}).count());
    await dp.screenshot({path:`${shots}/desktop.png`});
    check('no browser exceptions', errors.length===0);
    fs.writeFileSync(`${shots}/results.json`,JSON.stringify({passed:true,checks:results,errors},null,2));
  } finally { await browser.close(); }
})().catch(e => { console.error(e);process.exitCode=1; });
