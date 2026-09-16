/* Isolated integration smoke. Boot with remote-automation-bootstrap.py against
   a fresh loma_local_utility_* DB with Slack/scheduling disabled. Only model and
   provider transport are synthetic; login, utility authorization, proposal
   persistence and the signed control-plane routes are real. */
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
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
  await page.goto(`${base}/login`, { waitUntil: 'domcontentloaded', timeout: 120000 });
  await page.waitForLoadState('networkidle', {timeout:60000});
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

const out = process.env.LOMA_SMOKE_EVIDENCE;
fs.mkdirSync(out, { recursive: true });
(async () => {
 const browser=await chromium.launch();
 try {
  for (const [label, viewport] of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]) {
   const page=await browser.newPage({viewport});
   await login(page);
   // Seed only the throwaway local DB, through the actual proposal gateway.
   execFileSync(path.join(root,'.venv/bin/python'),[path.join(root,'scripts/browser/remote-automation-seed.py'),label],{cwd:root,stdio:'pipe'});
   const organized=await page.evaluate(async()=>{
    const r=await fetch('/api/skills-organize',{method:'POST'});
    if(!r.ok) throw new Error('Organize failed '+r.status);
    return await r.json();
   });
   assert.equal(organized.organized,1);
   await page.goto(`${base}/skills`,{waitUntil:'networkidle',timeout:90000});
   await page.getByText('Engineering',{exact:true}).first().waitFor({timeout:30000});
   await page.getByText('Engineering',{exact:true}).first().click();
   await page.getByText('Remote utility QA',{exact:true}).first().waitFor();
   await page.screenshot({path:path.join(out,`utilities-${label}.png`),fullPage:true});
   await page.goto(`${base}/agents/proposals`,{waitUntil:'networkidle',timeout:90000});
   await page.getByText('GitHub action',{exact:false}).first().waitFor({timeout:30000});
   await page.getByRole('button',{name:'Review exact action'}).first().click();
   const dialog=page.getByRole('dialog');
   await dialog.getByText('QA approved comment',{exact:false}).waitFor();
   await dialog.getByRole('button',{name:'Edit proposal'}).click();
   // Editing must preserve the numeric PR identifier, not turn it into a string.
   await dialog.getByRole('button',{name:'Save new version'}).click();
   await page.getByText('New version saved. Review it again before approving.').waitFor();
   await page.getByRole('button',{name:'Review exact action'}).first().click();
   await page.getByRole('dialog').getByText('QA approved comment',{exact:false}).waitFor();
   await page.waitForTimeout(500); // let the dialog finish its mobile entrance transition
   await page.screenshot({path:path.join(out,`proposal-${label}.png`),fullPage:true,animations:'disabled'});
   await page.getByRole('dialog').getByRole('button',{name:'Approve & apply'}).click();
   await page.getByText('Approved and executed. The receipt is in history.').waitFor();
   const listing=await page.evaluate(async()=>{const r=await fetch('/work-api/proposals'); if(!r.ok) throw new Error('List failed '+r.status);return r.json();});
   const match=listing.proposals.find(p=>p.reason===`Browser ${label}`);
   assert.equal(match.status,'executed');assert.equal(match.args.number,191);assert.equal(match.version,2);
   assert.equal(match.receipt.id,191001);
   await page.close();
   console.log(`${label}: remote organization, exact proposal edit, numeric preservation, approval and synthetic receipt passed`);
  }
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
