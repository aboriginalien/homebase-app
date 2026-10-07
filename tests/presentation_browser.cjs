// Actual Chromium, loopback server and synthetic provider; no owner account.
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),output=path.resolve(process.argv[3]);fs.mkdirSync(output,{recursive:true});
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'hb010-browser-'));fs.chmodSync(temp,0o700);
const server=spawn(process.argv[2],[path.join(__dirname,'browser_server.py'),temp],{cwd:root,env:{PATH:process.env.PATH,PYTHONPATH:root},stdio:['ignore','pipe','pipe']});
const checks=[],errors=[];let browser;
async function run(){
 const meta=await new Promise((resolve,reject)=>{let data='';server.stdout.on('data',b=>{data+=b;if(data.includes('\n'))resolve(JSON.parse(data.split('\n')[0]));});server.on('exit',()=>reject(Error('Fixture exited')));});
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE,headless:true,args:['--no-sandbox']});
 const context=await browser.newContext({viewport:{width:834,height:1194},timezoneId:'America/New_York'}),page=await context.newPage();
 page.on('pageerror',e=>errors.push(String(e)));
 const response=await page.goto(meta.origin+'/#pair='+fs.readFileSync(path.join(temp,'pairing.txt'),'utf8'));
 assert.match(response.headers()['content-security-policy'],/script-src 'self'/);
 await page.locator('#draft:not([disabled])').waitFor();
 const query='Please format this normally.\n\n**Clear and concise**\n\n- First point\n- Second point\n\n`literal **stars**`\n\n```text\n**literal stars**\n<script>literal code</script>\n```\n\n[Useful link](https://example.com)\n\n| Item | Status |\n| --- | --- |\n| Chat | Ready |';
 await page.locator('#draft').fill(query);await page.locator('#send').click();
 await page.locator('.assistant-message[data-status="completed"]').waitFor();
 const body=page.locator('.assistant-message .body');assert.equal(await body.locator('strong').innerText(),'Clear and concise');assert.equal(await body.locator('li').count(),2);
 assert.match(await body.locator('pre code').innerText(),/\*\*literal stars\*\*/);assert.equal(await body.locator('table').count(),1);
 assert.equal(await body.locator('a').getAttribute('rel'),'noopener noreferrer');
 assert.equal(await page.locator('.user-message strong').count(),0);assert.equal(await page.locator('.user-message .body').innerText(),query);
 checks.push('CommonMark bold, true bullets, fenced/inline literal code, table, safe links; user text remains literal');
 const stamps=page.locator('.message time');assert.equal(await stamps.count(),2);
 for(let i=0;i<2;i++){assert.match(await stamps.nth(i).innerText(),/^\d{1,2}-\d{1,2}, \d{1,2}:\d{2} [ap]\.m\.$/);assert.ok(await stamps.nth(i).getAttribute('datetime'));}
 assert.match(await stamps.last().getAttribute('aria-label'),/Homebase · Reply completed/);
 const dates=await stamps.allTextContents();await page.reload();await page.locator('.assistant-message[data-status="completed"]').waitFor();assert.deepEqual(await page.locator('.message time').allTextContents(),dates);
 checks.push('local minute timestamps replace labels; accessible roles/completion semantics and reload persistence');
 const background=await page.locator('.user-message').evaluate(e=>getComputedStyle(e).backgroundColor);assert.notEqual(background,'rgba(0, 0, 0, 0)');assert.notEqual(background,'rgb(0, 0, 0)');
 assert.equal(await page.locator('.body').last().evaluate(e=>getComputedStyle(e).fontSize),'16px');
 await page.screenshot({path:path.join(output,'ipad.png'),fullPage:true});
 for(const [name,width,height] of [['phone',390,844],['keyboard-height',834,500]]){await page.setViewportSize({width,height});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.ok((await page.locator('#send').boundingBox()).height>=44);await page.screenshot({path:path.join(output,name+'.png'),fullPage:true});}
 checks.push('shaded right-aligned user bubbles; readable 16px typography; phone/iPad/keyboard-width layouts and touch targets');
 // Exercise render boundary with adversarial and historical synthetic messages, no API mutation.
 await page.evaluate(()=>render({id:'fixture',draft:'',messages:[{id:1,role:'assistant',status:'completed',created:1791338580,completed:null,text:'<script>window.bad=1</script>\n\n<img src=x onerror="window.bad=1">\n\n[bad](javascript:alert(1))\n\n![image](https://example.com/tracker.png)'}]}));
 assert.equal(await page.locator('#messages script,#messages img,#messages [onerror]').count(),0);assert.equal(await page.evaluate(()=>window.bad),undefined);
 assert.equal(await page.locator('#messages a[href^="javascript:"]').count(),0);assert.match(await page.locator('.who').getAttribute('aria-label'),/Reply started/);
 assert.equal(await page.locator('.who').innerText(),'10-6, 10:03 p.m.');
 checks.push('HTML/XSS/unsafe URLs escaped; remote images disabled; legacy replies honestly use recorded start time');
 await page.evaluate(()=>render({id:'missing',draft:'',messages:[{id:1,role:'user',status:'saved',text:'No timestamp'}]}));assert.equal(await page.locator('.who').innerText(),'');assert.match(await page.locator('.who').getAttribute('aria-label'),/time unavailable/);
 checks.push('missing timestamps do not fabricate a date');
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(output,'browser-results.json'),JSON.stringify({environment:'Actual Chromium; CSP enabled; synthetic provider/state, not physical Safari or live inference',checks,page_errors:errors},null,2)+'\n');console.log(JSON.stringify({checks,page_errors:errors}));
}
run().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();server.kill();fs.rmSync(temp,{recursive:true,force:true});});
