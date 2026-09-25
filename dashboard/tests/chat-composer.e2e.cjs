// Run against an isolated local stack, never production. See docs/chat-composer-testing.md.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const PNG='iVBORw0KGgoAAAANSUhEUgAAAKAAAABkCAIAAACO1KzYAAADpklEQVR4nO3cXUhTYRzH8f+auukinJazF18DNdJppiWmlr0iJGhRhCF1U0F6oXWRYpiCFAS9EJFEUF1IdSHRRVBqWqaVUVhClEVaGviOomXq5lwXioyyUY2j7Nfvc3XO4eF5dvbdOYftYqomU44Qrnlz/QJIWQwMjoHBMTA4BgbHwOAYGBwDg2NgcAwMjoHBMTA4BgbHwOAYGBwDg2NgcAwMjoHBMTA4BgbHwOAYGBwDg2NgcAwMjoHBMTA4BgbHwOAYGBwDg2NgcAwMjoHBMTA4BgbHwOAYGBwDg2NgcAwMjoHBuczCGnfK28tvtXnoXDw8XPJPhBt83UUkOa7iYcO26TG2u3fKv5wueXO3eqOXt+bRg66bZZ9F5HVjf1S0l4jszgjYtHXxjMdP5DetjPCcnCQp2WfvvuDmt4MXzzWPm61qF1VhifHdm8EZZ5uFN2GuqJT+M9LnT/uuX/l4vjRWo1U/restu9Zy6Wqc2A18NPulf6AuePn81HS/GQfYsj3+65iMHXXnS2N9DNqaqq6q+x2nzkTbnw2P4rfosmuth3PCNFq1iMQnLlrqpxsft9oZPzpqGRmxpO30r3vU4/jqA/1jY2MWEUlKNuzOCHR8Qqej+C26peVr6IoF07sFxRH2xzfU98YnLAoI0nV2jJjNE66uDn0Es3LCDmQ+W5fkk5K6NGaNtyNTOSnFA09YZr5ezWbrof0NtruTG7U13e+bh6orO3t7Rhtf9K+NX/jna9nOmZ0TGhGl3562LGmjoba66+yptxs2Gw5mhfzreTgrxQP7B+o+NA+FGz1FxGqV4oKmopORIuLqqrp8PW56WHJchYhMWKxtbcM3bieKyLMnvfW13X8V+Kc5B/pNX9qHjVH61HS/hPWGPWmP/8PAij+Dd+0JKL3w3mSaEJHKex2TG7/T9GogJHTqfr5qtVfDkz5HllapJO9IY3fXiIgMDpp8F7s7MpuTUvwK3pKypL3te+auer3eTe/tdux4uJ3BtTXdMWunnpRarVrv7fap9VtQ8Pw/XMv2Fm2M9MzKDSsoisjLbdRo1PPUqsISoyMn4qQU/5pEc4u/ZIFjYHAMDI6BwTEwOAYGx8DgGBgcA4NjYHAMDI6BwTEwOAYGx8DgGBgcA4NjYHAMDI6BwTEwOAYGx8DgGBgcA4NjYHAMDI6BwTEwOAYGx8DgGBgcA4NjYHAMDI6BwTEwOAYGx8DgGBgcA4NjYHAMDI6BwTEwOAYGx8DgfgAuQwoBE22jNAAAAABJRU5ErkJggg==';
(async()=>{
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH || undefined,args:['--no-sandbox']});
const context=await browser.newContext({storageState:process.env.CHAT_AUTH_STATE,viewport:{width:1440,height:1000}});const page=await context.newPage();
const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto(`${process.env.CHAT_BASE_URL || 'http://localhost:13001'}/chat`,{timeout:120000});await page.waitForTimeout(4000);
await page.locator('input[type=file]').setInputFiles({name:'draft-test.png',mimeType:'image/png',buffer:Buffer.from(PNG,'base64')});
await page.locator('img[alt="draft-test.png"]').waitFor();await page.waitForTimeout(500);await page.reload();await page.locator('img[alt="draft-test.png"]').waitFor({timeout:15000});await page.waitForTimeout(4000);console.log('PASS attachment survives reload');await page.screenshot({path:'/tmp/chat-draft-restored.png'});
await page.route(/\/api\/conversations\/[^/]+$/, async route => {
 if(route.request().method()!=='GET') return route.continue();
 await route.fulfill({json:{conversation:{conversation_id:route.request().url().split('/').pop(),prompt:'',status:'completed',metadata:{user_name:'chat-test@example.com'}},turns:[],artifacts:[]}});
});
const requests=[];let release;const hold=new Promise(r=>release=r);
await page.route('**/api/chat',async route=>{const body=route.request().postDataJSON();requests.push(body);if(requests.length===1) await hold;await route.fulfill({status:200,contentType:'text/event-stream',body:'data: {"type":"text","text":"Test response"}\n\ndata: [DONE]\n\n'});});
await page.locator('button[type=submit]').click();await page.waitForTimeout(400);assert.equal(requests.length,1);assert.equal(requests[0].message,'');assert.equal(requests[0].files[0].data,PNG);console.log('PASS image-only payload delivered');
await page.locator('input[type=file]').setInputFiles({name:'queued-test.png',mimeType:'image/png',buffer:Buffer.from(PNG,'base64')});await page.locator('img[alt="queued-test.png"]').waitFor();await page.locator('button[type=submit]').click();await page.waitForTimeout(300);
// An unrelated draft must not be replaced with the queued image during drain.
await page.locator('textarea').first().fill('Keep this unsent draft');release();
for(let i=0;i<50&&requests.length<2;i++) await page.waitForTimeout(200);
assert.equal(requests.length,2);assert.equal(requests[1].files[0].name,'queued-test.png');assert.equal(requests[1].files[0].data,PNG);assert.equal(requests[1].conversation_id,requests[0].conversation_id);assert.equal(await page.locator('textarea').first().inputValue(),'Keep this unsent draft');console.log('PASS queued image-only payload + conversation id + unrelated draft preserved');
await page.waitForTimeout(1000);await page.screenshot({path:'/tmp/chat-queue-fixed.png'});// Definitive HTTP rejection must retain the file rather than enter recovery.
await page.unroute('**/api/chat');
await page.route('**/api/chat', route=>route.fulfill({status:400,json:{error:'Test upload rejection'}}));
await page.locator('input[type=file]').setInputFiles({name:'retry-test.png',mimeType:'image/png',buffer:Buffer.from(PNG,'base64')});
await page.locator('button[type=submit]').click();
await page.getByText('Error: Test upload rejection').waitFor();
assert.equal(await page.locator('form img[alt="retry-test.png"]').count(),1);
assert.equal(await page.getByText('Connection lost').count(),0);
console.log('PASS HTTP rejection restores attachments without recovery loop');
// Scroll a long draft in the conversation composer on desktop and mobile.
for (const [width,height,label] of [[1440,1000,'desktop'],[390,844,'mobile']]) {
 await page.setViewportSize({width,height});
 const textarea=page.locator('textarea').first();
 await textarea.fill(Array.from({length:100},(_,i)=>`Line ${i+1}: long draft remains scrollable`).join('\n'));
 await page.waitForTimeout(300);
 const start=await textarea.evaluate(e=>{e.scrollTop=e.scrollHeight;return e.scrollTop});
 await textarea.hover();await page.mouse.wheel(0,-900);await page.waitForTimeout(400);
 const metrics=await textarea.evaluate(e=>({height:e.clientHeight,scrollHeight:e.scrollHeight,top:e.scrollTop}));
 assert(metrics.height<=160 && metrics.scrollHeight>metrics.height && metrics.top<start);
 await page.screenshot({path:`/tmp/chat-scroll-${label}.png`});
 console.log(`PASS ${label} long-draft wheel scrolling`,metrics);
}
assert.deepEqual(errors,[]);await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});

// Tasks-board quick-add composer ("What do you need done?"): a long prompt must
// scroll inside the box with the mouse wheel (previously overflow:hidden, so the
// top was reachable only with arrow keys).
(async()=>{
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH || undefined,args:['--no-sandbox']});
const context=await browser.newContext({storageState:process.env.CHAT_AUTH_STATE});const page=await context.newPage();
for (const [width,height,label] of [[1440,900,'desktop'],[390,844,'mobile']]) {
 await page.setViewportSize({width,height});
 await page.goto(`${process.env.CHAT_BASE_URL || 'http://localhost:13001'}/tasks`,{timeout:120000});
 const box=page.locator('textarea[placeholder="What do you need done?"]').first();await box.waitFor({timeout:30000});
 await box.fill(Array.from({length:80},(_,i)=>`Line ${i+1}: long task prompt`).join('\n'));await page.waitForTimeout(300);
 const start=await box.evaluate(e=>{e.scrollTop=e.scrollHeight;return e.scrollTop});
 await box.hover();for(let i=0;i<15;i++){await page.mouse.wheel(0,-600);await page.waitForTimeout(50);}await page.waitForTimeout(300);
 const m=await box.evaluate(e=>({height:e.clientHeight,scrollHeight:e.scrollHeight,top:e.scrollTop,overflowY:getComputedStyle(e).overflowY}));
 assert(start>0 && m.top===0 && m.overflowY!=='hidden', JSON.stringify(m));
 await page.screenshot({path:`/tmp/chat-quickadd-scroll-${label}.png`});
 console.log(`PASS ${label} task quick-add wheel scrolling`,m);
}
await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
