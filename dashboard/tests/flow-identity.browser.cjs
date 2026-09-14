// Isolated-stack E2E. Requires a fresh loma_local_* DB and local-auth env.
// All writes are local fixtures. No agent/provider execution is requested.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require('playwright');
const {MongoClient} = require('mongodb');
(async () => {
  assert.match(process.env.OBSERVABILITY_DB_NAME, /^loma_local_/);
  const base = process.env.AUTH_URL;
  assert.match(base, /^http:\/\/localhost:/);
  const out = process.env.SHOTS_DIR || '/tmp/foundation-shots';
  fs.mkdirSync(out, {recursive:true});
  const mongo = new MongoClient(process.env.OBSERVABILITY_MONGODB_URI);
  await mongo.connect();
  const db = mongo.db(process.env.OBSERVABILITY_DB_NAME);
  const browser = await chromium.launch({headless:true});
  async function login(email) {
    const context = await browser.newContext({viewport:{width:1440,height:1000}});
    const page = await context.newPage();
    await page.goto(`${base}/login`, {timeout:90000});
    await page.waitForLoadState('networkidle');
    await page.request.get(`${base}/api/auth/csrf`);
    await page.fill('#signin-email',email);
    await page.fill('#signin-password',process.env.PASSWORD);
    await page.fill('#setup-token',process.env.LOMA_SETUP_TOKEN);
    await page.click('button[type=submit]');
    await page.waitForURL(u=>!u.pathname.includes('/login'),{timeout:60000});
    return page;
  }
  try {
    const page = await login(process.env.USER_NAME);
    const admin = await db.collection('users').findOne({email:process.env.USER_NAME});
    assert(admin && admin.system_role==='admin');
    for (const [email,status] of [['qa-operator@example.com','active'],['qa-inactive@example.com','inactive']]) {
      await db.collection('users').updateOne({email}, {$set:{email,name:'QA account',system_role:'operator',status,local_auth:admin.local_auth}}, {upsert:true});
    }
    const created = await page.request.post(`${base}/api/flows`, {data:{name:'Invoice follow-up (QA)',prompt:'Draft only. No external actions.',schedule_type:'recurring',cron:'0 9 * * 1-5',status:'paused',visibility:'shared',created_by:{source:'qa-operator@example.com'}}});
    assert.equal(created.status(),201,await created.text());
    const flow=(await created.json()).flow;
    assert.equal(flow.run_as,admin.email);
    assert.equal(flow.created_by.source,admin.email);
    const path=`${base}/flows/${flow.flow_id}`;
    await page.goto(path,{timeout:90000});
    const section=page.getByRole('region',{name:'Execution account'});
    await section.waitFor();
    await page.screenshot({path:`${out}/desktop.png`,fullPage:true});
    async function choose(email) {
      await section.getByRole('combobox',{name:'Execution account'}).click();
      assert.equal(await page.getByRole('option',{name:'qa-inactive@example.com',exact:true}).count(),0);
      await page.getByRole('option',{name:email,exact:true}).click();
    }
    await choose('qa-operator@example.com');
    assert.equal((await db.collection('flows').findOne({flow_id:flow.flow_id})).run_as,admin.email);
    await section.getByRole('button',{name:'Cancel',exact:true}).click();
    assert.equal(await section.getByRole('button',{name:'Save account',exact:true}).count(),0);
    await choose('qa-operator@example.com');
    await page.screenshot({path:`${out}/account-review.png`,fullPage:true});
    await section.getByRole('button',{name:'Save account',exact:true}).click();
    await section.getByRole('status').waitFor();
    assert.equal((await db.collection('flows').findOne({flow_id:flow.flow_id})).run_as,'qa-operator@example.com');
    // The account is revoked after selection. The real backend must reject Save.
    await choose(admin.email);
    await db.collection('users').updateOne({email:admin.email},{$set:{status:'inactive'}});
    await section.getByRole('button',{name:'Save account',exact:true}).click();
    await section.getByRole('alert').waitFor();
    assert.match(await section.getByRole('alert').innerText(),/inactive/);
    assert.equal((await db.collection('flows').findOne({flow_id:flow.flow_id})).run_as,'qa-operator@example.com');
    await db.collection('users').updateOne({email:admin.email},{$set:{status:'active'}});
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(() => document.querySelector('main').getBoundingClientRect().x < 1 && document.querySelector('.loma-sidebar').getBoundingClientRect().right <= 1);
    await page.screenshot({path:`${out}/mobile-error.png`,fullPage:true});
    assert((await section.boundingBox()).width >= 350, 'Account controls must use the mobile width');
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth), 'Mobile page overflows');
    await section.getByRole('button',{name:'Cancel',exact:true}).click();
    // Keyboard can enter the account picker without accidentally saving.
    const picker=section.getByRole('combobox',{name:'Execution account'});
    await picker.focus(); await page.keyboard.press('Space'); await page.keyboard.press('Escape');
    const operator=await login('qa-operator@example.com');
    await operator.goto(path,{timeout:90000});
    await operator.getByText('View only. Ask the owner or an admin to make changes.').waitFor();
    assert.equal(await operator.getByRole('button',{name:'Delete',exact:true}).count(),0);
    assert.equal(await operator.getByRole('button',{name:'Resume',exact:true}).count(),0);
    const denied=await operator.request.patch(`${base}/api/flows/${flow.flow_id}`,{data:{prompt:'Changed'}});
    assert.equal(denied.status(),403);
    for (const action of ['pause','resume','run-now']) assert.equal((await operator.request.post(`${base}/api/flows/${flow.flow_id}/${action}`)).status(),403);
    assert.equal((await operator.request.delete(`${base}/api/flows/${flow.flow_id}`)).status(),403);
    await operator.screenshot({path:`${out}/read-only.png`,fullPage:true});
    console.log('PASS: local login, creator binding, explicit save, cancel, persistence, inactive-account rejection, mobile width, keyboard picker, shared read-only UI and denied mutations.');
  } finally {await browser.close(); await mongo.close();}
})().catch(e=>{console.error(e);process.exit(1)});
