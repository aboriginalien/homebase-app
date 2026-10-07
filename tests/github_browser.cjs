// Actual browser rendering/accessibility checks with synthetic activity only.
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),temporary=fs.mkdtempSync(path.join(os.tmpdir(),'hb011-browser-'));fs.chmodSync(temporary,0o700);
const server=spawn(process.argv[2],[path.join(__dirname,'browser_server.py'),temporary],{cwd:root,env:{PATH:process.env.PATH,PYTHONPATH:root},stdio:['ignore','pipe','pipe']});let browser;
(async()=>{
 const meta=await new Promise((resolve,reject)=>{let text='';server.stdout.on('data',b=>{text+=b;if(text.includes('\n'))resolve(JSON.parse(text.split('\n')[0]));});server.on('exit',()=>reject(Error('Fixture failed')));});
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE,headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:834,height:1194}}),errors=[];page.on('pageerror',e=>errors.push(String(e)));
 await page.goto(meta.origin+'/#pair='+fs.readFileSync(path.join(temporary,'pairing.txt'),'utf8'));await page.locator('#draft:not([disabled])').waitFor();
 await page.evaluate(()=>clearInterval(poll));
 const fixture={id:'fixture',draft:'',messages:[{id:123,role:'assistant',status:'working',phase:'verifying',created:1791338580,text:'Checking your save.',activity:[
  {name:'create_or_update_file',status:'succeeded',target:{owner:'aboriginalien',repo:'homebase',path:'docs/test.md'},evidence:{url:'https://github.com/aboriginalien/homebase/commit/'+'a'.repeat(40)}},
  {name:'get_file_contents',status:'failed',target:{owner:'aboriginalien',repo:'<img src=x onerror=alert(1)>'},evidence:{url:'javascript:alert(1)'}}]}]};
 await page.evaluate(data=>render(data),fixture);assert.equal(await page.locator('details.activity[open]').count(),0);
 assert.equal(await page.locator('.state').innerText(),'Checking GitHub result…');await page.locator('details.activity summary').click();
 assert.equal(await page.locator('details.activity li').count(),2);assert.equal(await page.locator('details.activity a').count(),1);
 assert.equal(await page.locator('details.activity img,[onerror]').count(),0);assert.match(await page.locator('details.activity li').first().innerText(),/verified/);
 fixture.messages[0].text='Updated stream';await page.evaluate(data=>render(data),fixture);assert.equal(await page.locator('details.activity[open]').count(),1);
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 assert.ok((await page.locator('details.activity summary').boundingBox()).height>=44);assert.deepEqual(errors,[]);
 console.log(JSON.stringify({activity_collapsed:true,expanded_state_preserved:true,verified_link:true,html_escaped:true,unsafe_link_rejected:true,mobile_layout:true,touch_target:true,page_errors:errors}));
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();server.kill();fs.rmSync(temporary,{recursive:true,force:true});});
