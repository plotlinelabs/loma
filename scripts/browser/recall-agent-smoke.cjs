/* Live-model chat AND headless-task recall. Synthetic source records, no mocked replies. */
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const { MongoClient } = require(path.join(root, 'dashboard/node_modules/mongodb'));
function config(file) {
  return Object.fromEntries(fs.readFileSync(file, 'utf8').split('\n').filter(l => /^[A-Z_]+=/.test(l)).map(l => {
    const i=l.indexOf('='); return [l.slice(0,i),l.slice(i+1).trim().replace(/^["']|["']$/g,'')];
  }));
}
const env=config(path.join(root,'.env'));
const dash=config(path.join(root,'dashboard/.env'));
assert.equal(process.env.LOMA_RECALL_AGENT_E2E,'1');
assert(env.OBSERVABILITY_DB_NAME.startsWith('loma_local_'));
assert.equal(dash.OBSERVABILITY_DB_NAME,env.OBSERVABILITY_DB_NAME);
assert.equal(env.WEBHOOK_PORT,'13000'); assert.equal(env.OPENCODE_PORT,'14097');
assert.equal(dash.BACKEND_URL,'http://localhost:13000');
assert.equal(env.LOMA_ENABLE_SLACK,'false'); assert.equal(env.LOMA_ENABLE_SCHEDULER,'false');
for(const key of ['LOMA_EMAIL','LOMA_PASSWORD','LOMA_TOKEN','LOMA_CHAT_MODEL','LOMA_CHAT_EVIDENCE']) assert(process.env[key],key+' required');
const out=process.env.LOMA_CHAT_EVIDENCE;fs.mkdirSync(out,{recursive:true});
const report={commit:execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim(),model:process.env.LOMA_CHAT_MODEL,mocked:false,checks:[],passed:false};
function check(ok,label){assert(ok,label);report.checks.push(label);console.log('PASS '+label)}
const source='recall-smoke-'+crypto.randomUUID(), topic='launch-'+crypto.randomBytes(5).toString('hex'), answer='violet-'+crypto.randomBytes(5).toString('hex');
const created=[source];
(async()=>{
 const mongo=await new MongoClient(env.OBSERVABILITY_MONGODB_URI).connect();
 const db=mongo.db(env.OBSERVABILITY_DB_NAME);const browser=await chromium.launch();
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 try {
  await page.goto('http://localhost:13001/login');await page.waitForLoadState('networkidle');
  await page.fill('#signin-email',process.env.LOMA_EMAIL);await page.fill('#signin-password',process.env.LOMA_PASSWORD);await page.fill('#setup-token',process.env.LOMA_TOKEN);await page.click('button[type=submit]');
  await page.waitForURL(u=>!u.pathname.includes('/login'),{timeout:60000});
  await db.collection('conversations').insertOne({conversation_id:source,source:'dashboard',status:'completed',title:'Synthetic recall source',metadata:{user_name:process.env.LOMA_EMAIL},messages:[{role:'user',content:`For ${topic}, the agreed launch code is ${answer}.`,timestamp:new Date()}]});
  async function post(url,body){return page.evaluate(async({url,body})=>{const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});return {status:r.status,text:await r.text()}},{url,body})}
  const prompt=`Find the launch code we agreed for ${topic} in my previous chat. Use search_history then fetch_history and cite the source conversation. Use no shell or external tools.`;
  const chat=await post('/api/chat',{message:prompt,model:process.env.LOMA_CHAT_MODEL,conversation_id:crypto.randomUUID()});
  check(chat.status===200&&chat.text.includes('data: [DONE]'),'Chat stream completed');
  const events=chat.text.split('\n').filter(l=>l.startsWith('data: ')&&l!=='data: [DONE]').map(l=>JSON.parse(l.slice(6)));
  const cid=events.find(e=>e.type==='conversation_id').conversation_id;created.push(cid);
  const text=events.filter(e=>e.type==='text').map(e=>e.text||'').join('');
  check(!events.some(e=>e.type==='error'||e.error),'No runtime error');
  for(const tool of ['search_history','fetch_history']) check(events.some(e=>e.type==='tool_call'&&e.name.includes(tool)),'Live chat invoked '+tool);
  check(text.includes(answer)&&text.includes('/conversations/'+source),'Live chat recalled unpredictable fact and cited source');
  await page.goto('http://localhost:13001/conversations/'+cid);await page.waitForTimeout(1500);await page.screenshot({path:path.join(out,'chat-recall.png'),fullPage:true});
  const task=await post('/api/tasks',{prompt,model:process.env.LOMA_CHAT_MODEL,start:true,title:'Synthetic live recall task'});
  check(task.status===201,'Headless task started');const tid=JSON.parse(task.text).task.conversation_id;created.push(tid);
  let data;for(let i=0;i<300;i++){await page.waitForTimeout(2000);data=await page.evaluate(async id=>(await fetch('/api/conversations/'+id)).json(),tid);if(['completed','error'].includes(data.conversation?.status))break;}
  check(data.conversation.status==='completed','Headless task completed');
  check(data.conversation.final_response.includes(answer)&&data.conversation.final_response.includes('/conversations/'),'Task recalled unpredictable fact and cited history');
  for(const tool of ['search_history','fetch_history'])check(JSON.stringify(data.turns).includes(tool),'Live task invoked '+tool);
  await page.goto('http://localhost:13001/conversations/'+tid);await page.waitForTimeout(1500);await page.screenshot({path:path.join(out,'task-recall.png'),fullPage:true});
  report.passed=true;
 } finally {
  fs.writeFileSync(path.join(out,'recall-result.json'),JSON.stringify(report,null,2));
  await browser.close();
  await db.collection('conversations').deleteMany({conversation_id:{$in:created}});
  await db.collection('turns').deleteMany({conversation_id:{$in:created}});
  await db.collection('recall_index').deleteMany({conversation_id:{$in:created}});
  await mongo.close();
 }
})().catch(e=>{console.error(e.message);process.exitCode=1});
