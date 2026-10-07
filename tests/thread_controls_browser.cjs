// Real Chromium + loopback app + synthetic provider. No live account or owner data.
// PLAYWRIGHT_MODULE optionally names an installed Playwright module.
// node tests/thread_controls_browser.cjs /path/to/app/python /path/to/results
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),output=path.resolve(process.argv[3]);
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'hb008-browser-'));fs.chmodSync(temp,0o700);fs.mkdirSync(output,{recursive:true});
const server=spawn(process.argv[2],[path.join(__dirname,'browser_server.py'),temp],{cwd:root,env:{PATH:process.env.PATH,PYTHONPATH:root},stdio:['ignore','pipe','pipe']});
const checks=[],errors=[];let browser;
async function run(){
 const meta=await new Promise((resolve,reject)=>{let data='';server.stdout.on('data',b=>{data+=b;if(data.includes('\n')){try{resolve(JSON.parse(data.split('\n')[0]));}catch(e){reject(e);}}});server.on('exit',()=>reject(new Error('Synthetic fixture exited before startup.')));});
 const origin=meta.origin;
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE,headless:true,args:['--no-sandbox']});
 // Bypass CSP only for Playwright's predicate instrumentation; app policy is unchanged.
 const context=await browser.newContext({bypassCSP:true,viewport:{width:834,height:1194}}),page=await context.newPage();
 page.on('pageerror',e=>errors.push(String(e)));
 await page.goto(origin+'/#pair='+fs.readFileSync(path.join(temp,'pairing.txt'),'utf8'));
 await page.waitForFunction(()=>!document.querySelector('#draft').disabled);
 async function submit(text){await page.locator('#draft').fill(text);await page.locator('#send').click();await page.waitForFunction(()=>!!document.querySelector('.message[data-status="completed"]')&&!document.querySelector('.message[data-status="working"]'));}
 async function toggle(id){await page.locator('#'+id).click();}
 async function expanded(id,value){await page.waitForFunction(([id,value])=>document.getElementById(id).getAttribute('aria-expanded')===String(value),[id,value]);assert.equal(await page.locator('#'+(id==='threads'?'thread':'memory')+'-panel').isVisible(),value);}
 await submit('Original thread');
 await toggle('memory');await expanded('memory',true);await expanded('threads',false);
 await toggle('threads');await expanded('threads',true);await expanded('memory',true);
 assert.notEqual(await page.locator('#threads').evaluate(e=>getComputedStyle(e).backgroundColor),await page.locator('#new').evaluate(e=>getComputedStyle(e).backgroundColor));
 await toggle('threads');await expanded('threads',false);await expanded('memory',true);
 await toggle('memory');await expanded('memory',false);
 checks.push('independent panel toggles, aria-expanded/controls and computed active highlight');
 await page.locator('#new').click();await page.waitForFunction(()=>!document.querySelector('#draft').disabled&&!document.querySelector('.message'));
 await submit('Keep current thread');
 await page.locator('#draft').fill('keep unsent draft');
 await toggle('threads');
 const original=page.locator('.thread-row').filter({has:page.getByRole('button',{name:'Original thread',exact:true})});
 page.once('dialog',async dialog=>{assert.match(dialog.message(),/Original thread/);await dialog.dismiss();});await original.locator('.thread-delete').click();
 assert.equal(await original.count(),1);assert.equal(await page.locator('#draft').inputValue(),'keep unsent draft');
 checks.push('confirmation Cancel preserves conversation and draft');
 page.once('dialog',dialog=>dialog.accept());await original.locator('.thread-delete').click();await page.waitForFunction(()=>document.querySelector('#status').textContent==='Thread deleted.');
 assert.equal(await original.count(),0);assert.equal(await page.locator('#draft').inputValue(),'keep unsent draft');assert.match(await page.locator('#messages').innerText(),/Keep current thread/);
 checks.push('confirmed background deletion keeps current conversation and unsent draft');
 await page.locator('#new').click();await page.waitForFunction(()=>!document.querySelector('#draft').disabled&&!document.querySelector('.message'));
 await submit('/remember thread Local | erase this note');
 await toggle('memory');await page.waitForSelector('.record');
 await toggle('threads');
 let local=page.locator('.thread-row').filter({has:page.getByRole('button',{name:'/remember thread Local | erase this note',exact:true})});
 page.once('dialog',dialog=>dialog.accept());await local.locator('.thread-delete').click();await page.waitForFunction(()=>document.querySelector('#status').textContent==='Thread deleted.');
 assert.match(await page.locator('#messages').innerText(),/Keep current thread/);assert.equal(await page.locator('.record').count(),0);await expanded('threads',false);await expanded('memory',true);
 checks.push('current deletion restores remaining conversation, removes its notes, resets Threads indicator');
 await toggle('threads');
 page.once('dialog',dialog=>dialog.accept());await page.locator('.thread-delete').click();await page.waitForFunction(()=>document.querySelector('#status').textContent==='Thread deleted.');
 assert.equal(await page.locator('.message').count(),0);assert.equal(await page.locator('#draft').inputValue(),'');
 await toggle('threads');assert.equal(await page.locator('.thread-open').count(),1);assert.equal(await page.locator('.thread-open').innerText(),'New thread');
 checks.push('last-thread deletion creates one usable blank conversation');
 await page.locator('#draft').fill('slow active reply');await page.locator('#send').click();await page.waitForSelector('.message[data-status="working"]');
 page.once('dialog',dialog=>dialog.accept());await page.locator('.thread-delete').click();
 await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('before deleting this thread'));
 assert.equal(await page.locator('.thread-open').count(),1);
 await page.locator('#stop').click();await page.waitForSelector('.message[data-status="stopped"]');
 checks.push('active reply deletion refuses safely and leaves Stop usable');
 for(const size of [{width:390,height:844},{width:834,height:500}]){await page.setViewportSize(size);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.ok((await page.locator('.thread-delete').boundingBox()).height>=44);}
 await page.screenshot({path:path.join(output,'thread-controls.png'),fullPage:true});
 checks.push('phone and keyboard-height layouts fit; Delete touch target >=44px');
 await toggle('memory');await page.locator('#signout').click();await page.waitForFunction(()=>document.querySelector('#draft').disabled);
 await expanded('threads',false);await expanded('memory',false);assert.equal(await page.locator('.thread-row').count(),0);
 checks.push('sign-out clears private panels and both indicators');
 assert.deepEqual(errors,[]);
 fs.writeFileSync(path.join(output,'browser-results.json'),JSON.stringify({environment:'Actual Chromium with local synthetic provider; not physical Safari or live inference',checks,page_errors:errors},null,2)+'\n');
 console.log(JSON.stringify({checks,page_errors:errors}));
}
run().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();server.kill();fs.rmSync(temp,{recursive:true,force:true});});
