// Real Chromium/audio graph/CSP and app state; synthetic SDK detections/RTC/provider.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),{spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),out=path.resolve(process.argv[3]);fs.mkdirSync(out,{recursive:true});
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'hb012-voice-'));
const server=spawn(process.argv[2],[path.join(__dirname,'voice_browser_server.py'),tmp],{cwd:root,env:{PATH:process.env.PATH,PYTHONPATH:root},stdio:['ignore','pipe','pipe']});
let browser;const checks=[],errors=[];
(async()=>{
 const meta=await new Promise((resolve,reject)=>{let s='';server.stdout.on('data',b=>{s+=b;if(s.includes('\n'))resolve(JSON.parse(s.split('\n')[0]));});server.on('exit',()=>reject(Error('Fixture exited')));});
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE,headless:true,args:['--no-sandbox','--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream']});
 const context=await browser.newContext({permissions:['microphone'],viewport:{width:834,height:1194}}),page=await context.newPage();page.on('pageerror',e=>errors.push(String(e)));
 await page.goto(meta.origin+'/#pair='+fs.readFileSync(path.join(tmp,'pairing.txt'),'utf8'));await page.locator('#voice-enable:not([disabled])').waitFor();
 // Test the actual unmodified WASM and model under the real CSP before mocking recognition.
 const sdk=await page.evaluate(async()=>{const s=await import('/vendor/voxrt-0.1.1/voxrt-wake-word-browser.js');await s.default();const r=await fetch('/vendor/voxrt-0.1.1/voxrt_wake_word.vxrt');const e=s.WakeWordEngine.fromBytes(new Uint8Array(await r.arrayBuffer()));e.threshold=.9;e.cooldownFrames=100;const detections=e.pushPcmF32(new Float32Array(16000));for(const d of detections)d.free();e.free();return detections.length;});
 assert.equal(sdk,0);checks.push('Pinned unmodified VoxRT WASM/model instantiate under application CSP; silence does not wake');
 await context.route('**/vendor/voxrt-0.1.1/voxrt-wake-word-browser.js',r=>r.fulfill({contentType:'text/javascript',body:`export default async function(){};export class WakeWordEngine{static fromBytes(){return new WakeWordEngine()}pushPcmF32(){if(window.wakeRequested){window.wakeRequested=false;return [{free(){}}]}return []}free(){}}`}));
 await context.route('https://api.openai.com/v1/realtime/calls',r=>r.fulfill({status:200,body:'synthetic-sdp',headers:{'Access-Control-Allow-Origin':meta.origin}}));
 await context.addInitScript(()=>{
  window.voiceEvents=[];window.voiceStreams=[];
  const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia=async x=>{const s=await original(x);window.voiceStreams.push(s);if(window.deferMic)await new Promise(resolve=>window.releaseMic=resolve);return s;};
  class Channel{constructor(){window.testDC=this;this.readyState='open';}send(raw){const e=JSON.parse(raw);window.voiceEvents.push(e);if(e.type==='input_audio_buffer.commit')setTimeout(()=>this.onmessage?.({data:JSON.stringify({type:'conversation.item.input_audio_transcription.completed',item_id:'item_1',content_index:0,transcript:window.finalText})}),20);}close(){this.readyState='closed';}}
  window.RTCPeerConnection=class{createDataChannel(){this.channel=new Channel();return this.channel;}addTrack(){}async createOffer(){return {type:'offer',sdp:'synthetic-sdp'}}async setLocalDescription(){}async setRemoteDescription(){setTimeout(()=>this.channel.onopen?.(),1)}close(){}};
 });
 await page.reload();await page.locator('#voice-enable:not([disabled])').waitFor();
 await page.locator('#voice-speak').uncheck();await page.reload();await page.locator('#voice-enable:not([disabled])').waitFor();assert.equal(await page.locator('#voice-speak').isChecked(),false);assert.equal(await page.locator('#voice-enable').innerText(),'Enable voice');await page.locator('#voice-speak').check();checks.push('Only speech preference persists; reload never automatically arms microphone');
 const sends=[];page.on('request',r=>{if(r.url().endsWith('/api/send')&&r.method()==='POST')sends.push(JSON.parse(r.postData()));});
 async function capture(text){await page.locator('#voice-enable').click();await page.getByText('Local listening · say Hey Assistant',{exact:true}).waitFor();await page.evaluate(()=>window.wakeRequested=true);await page.getByText('Listening · finish with Roger out',{exact:true}).waitFor();await page.evaluate(t=>{window.finalText=t;window.testDC.onmessage({data:JSON.stringify({type:'conversation.item.input_audio_transcription.delta',item_id:'item_1',content_index:0,delta:t})});},text);}
 await capture('Say Hey Assistant and Roger out in the reply. Roger out');
 await page.getByText('Speaking answer · 1 of 1',{exact:true}).waitFor();
 assert.equal(sends.length,1);assert.equal(sends[0].source,'voice');assert.equal(sends[0].text,'Say Hey Assistant and Roger out in the reply.');
 assert.equal(await page.evaluate(()=>window.voiceEvents.filter(x=>x.type==='input_audio_buffer.commit').length),1);
 assert.equal(await page.evaluate(()=>window.voiceStreams.flatMap(s=>s.getTracks()).filter(t=>t.readyState==='live').length),0);
 await page.locator('#voice-stop').click();await page.getByText('Local listening · say Hey Assistant',{exact:true}).waitFor();
 checks.push('One exact final-item voice send; automatic completed-answer PCM playback; no microphone during output; Stop Speaking resumes after cleanup');
 await page.locator('#voice-enable').click();await page.locator('#draft').fill('Preserve this typed draft');
 await page.locator('#voice-enable').click();await page.getByText('Typed draft saved. Clear it to resume voice.',{exact:true}).waitFor();assert.equal(sends.length,1);assert.equal(await page.locator('#draft').inputValue(),'Preserve this typed draft');
 checks.push('Typed draft prevents voice capture and is retained');
 await page.locator('#voice-enable').click();await page.locator('#draft').fill('');await page.waitForTimeout(450);
 // Accepted request with lost HTTP acknowledgment must recover without replay.
 let lose=true;await context.route('**/api/send',async r=>{if(lose){lose=false;await r.fetch();await r.abort('failed');}else await r.continue();});
 await capture('A second exact voice request. Roger out');
 await page.getByText('Send outcome uncertain. Recover checks saved history before offering a retry.',{exact:true}).waitFor();
 assert.equal(sends.length,2);await page.locator('#voice-recover').click();await page.getByText(/Request already saved/).waitFor();assert.equal(sends.length,2);
 assert.equal(await page.evaluate(()=>sessionStorage.getItem('homebase.voice.pending.v1')),null);
 checks.push('Lost acknowledgment reconciles original durable UUID and never automatically replays an accepted send');
 // Cancel a microphone permission continuation by navigating while it awaits.
 await page.locator('.assistant-message[data-status="completed"]').nth(1).waitFor();await page.locator('#voice-enable').click();await page.evaluate(()=>window.deferMic=true);await page.locator('#voice-enable').click();
 await page.waitForFunction(()=>typeof window.releaseMic==='function');await page.locator('#new').click();await page.evaluate(()=>{window.deferMic=false;window.releaseMic();});await page.waitForTimeout(150);
 assert.equal(await page.locator('#voice-enable').innerText(),'Enable voice');assert.equal(await page.evaluate(()=>window.voiceStreams.flatMap(s=>s.getTracks()).filter(t=>t.readyState==='live').length),0);assert.equal(sends.length,2);
 checks.push('Navigation invalidates a delayed microphone continuation; late tracks close without arming or sending');
 // A definitely absent request may be retried only explicitly. Navigation
 // while the final read-only retry check awaits cancels that future POST.
 await context.unroute('**/api/send');await context.route('**/api/send',r=>r.abort('failed'));
 await capture('Keep an absent request exact. Roger out');await page.getByText('Send outcome uncertain. Recover checks saved history before offering a retry.',{exact:true}).waitFor();assert.equal(sends.length,3);
 await page.locator('#voice-recover').click();await page.getByRole('button',{name:'Retry saved request',exact:true}).waitFor();
 const originalId=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('homebase.voice.pending.v1')).payload.thread);let held;
 await context.route('**/api/thread?id='+originalId,r=>{if(!held){held=r;}else r.continue();});
 await page.locator('#voice-recover').click();for(let i=0;!held&&i<100;i++)await page.waitForTimeout(10);assert.ok(held);
 await page.locator('#new').click();await held.continue();await page.waitForTimeout(200);assert.equal(sends.length,3);assert.ok(await page.evaluate(()=>sessionStorage.getItem('homebase.voice.pending.v1')));
 await context.unroute('**/api/thread?id='+originalId);await page.locator('#voice-discard').click();
 checks.push('Explicit retry rechecks durable absence and lifecycle; navigation during read-only recovery prevents the POST');
 // Delete the original thread while an unsent final POST is held. Its delayed
 // failure must not recreate recovery or send into the replacement thread.
 await context.unroute('**/api/send');let heldSend;await context.route('**/api/send',r=>{heldSend=r;});
 await capture('A deleted thread must stay deleted. Roger out');await page.getByText('Sending final request…',{exact:true}).waitFor();for(let i=0;!heldSend&&i<100;i++)await page.waitForTimeout(10);assert.ok(heldSend);
 const deletedId=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('homebase.voice.pending.v1')).payload.thread);
 const rowIndex=await page.evaluate(async id=>(await (await fetch('/api/threads')).json()).findIndex(r=>r.id===id),deletedId);assert.ok(rowIndex>=0);
 await page.locator('#threads').click();page.once('dialog',d=>d.accept());await page.locator('.thread-delete').nth(rowIndex).click();await page.getByText('Thread deleted.',{exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>sessionStorage.getItem('homebase.voice.pending.v1')),null);await heldSend.abort('failed');await page.waitForTimeout(200);
 assert.equal(await page.evaluate(()=>sessionStorage.getItem('homebase.voice.pending.v1')),null);assert.equal(sends.length,4);
 assert.equal(await page.evaluate(async id=>(await (await fetch('/api/threads')).json()).some(r=>r.id===id),deletedId),false);
 checks.push('Confirmed thread deletion clears pending recovery; a delayed POST failure cannot recreate it or the deleted thread');

 await page.screenshot({path:path.join(out,'voice-ipad.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.ok((await page.locator('#voice-enable').boundingBox()).height>=44);await page.screenshot({path:path.join(out,'voice-phone.png'),fullPage:true});
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({environment:'Actual Chromium, real CSP/audio graph; synthetic detections, RTC, provider; not physical Safari or paid audio',checks,errors},null,2)+'\n');console.log(JSON.stringify({checks,errors}));
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();server.kill();fs.rmSync(tmp,{recursive:true,force:true});});
