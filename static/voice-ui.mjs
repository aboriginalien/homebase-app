import {FinalTurn,detectCompletion} from './voice-core.mjs';
import {RECOVERY_KEY,validatePending,reconcilePending,Resampler} from './voice-recovery.mjs';
const $=id=>document.getElementById(id);
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
export function createVoice(chat){
 let enabled=false,available=false,generation=0,phase='off',stream=null,ctx=null,engine=null,proc=null,source=null,mute=null,pc=null,dc=null,wakeLock=null,turn=null,capture='',commitTimer=null,limitTimer=null,idleTimer=null,openingTimer=null,finalTimer=null,pending=null,autoRequest='',audioNode=null,speechOperation='',waitingGeneration=0,items=new Map();
 const live=(g,t)=>enabled&&g===generation&&document.visibilityState==='visible'&&(!t||chat.context().thread===t)&&chat.context().authenticated;
 function status(value){$('voice-status').textContent=value;}
 function update(){
  const c=chat.context();$('voice-enable').disabled=!available||!c.authenticated||!c.thread;
  $('voice-enable').textContent=enabled?(phase==='paused'?'Resume voice':'Pause voice'):'Enable voice';
  $('voice-cancel').hidden=!['opening','capturing','finalizing'].includes(phase);
  $('voice-stop').hidden=phase!=='speaking';$('voice-recover').hidden=!pending;
  $('voice-discard').hidden=!pending;
 }
 function clearTimers(){for(const timer of [commitTimer,limitTimer,idleTimer,openingTimer,finalTimer])clearTimeout(timer);commitTimer=limitTimer=idleTimer=openingTimer=finalTimer=null;}
 function closeInput(){
  clearTimers();if(dc){try{dc.close();}catch{}dc=null;}if(pc){try{pc.close();}catch{}pc=null;}
  if(proc){proc.onaudioprocess=null;proc.disconnect();proc=null;}source?.disconnect();source=null;mute?.disconnect();mute=null;
  if(stream){for(const t of stream.getTracks())t.stop();stream=null;}
  if(capture){chat.api('/api/voice/close',{capture}).catch(()=>{});capture='';}
  if(engine){engine.free();engine=null;}
 }
 function clearPending(){pending=null;try{sessionStorage.removeItem(RECOVERY_KEY);}catch{}update();}
 function persist(record){sessionStorage.setItem(RECOVERY_KEY,JSON.stringify(record));if(sessionStorage.getItem(RECOVERY_KEY)!==JSON.stringify(record))throw new Error('Voice request could not be saved for safe recovery.');pending=record;update();}
 function cancel(reason='Voice paused.',disable=false){
  generation++;turn?.cancel();turn=null;closeInput();autoRequest='';
  if(audioNode){try{audioNode.stop();}catch{}audioNode=null;}
  if(speechOperation){chat.api('/api/voice/close',{capture:speechOperation}).catch(()=>{});speechOperation='';}
  if(disable){enabled=false;wakeLock?.release().catch(()=>{});wakeLock=null;}
  phase=enabled?'paused':'off';$('voice-transcript').textContent='';status(reason);update();
 }
 async function waiting(g){
  if(!live(g)||pending)return;
  const c=chat.context();if(c.working||c.draft){phase='paused';status(c.draft?'Typed draft saved. Clear it to resume voice.':'Voice will resume after the answer.');update();return;}
  phase='opening';status('Starting local wake detector…');update();
  try{
   const sdk=await import('/vendor/voxrt-0.1.1/voxrt-wake-word-browser.js');await sdk.default();
   const bytes=await (await fetch('/vendor/voxrt-0.1.1/voxrt_wake_word.vxrt',{cache:'force-cache'})).arrayBuffer();if(!live(g))return;
   engine=sdk.WakeWordEngine.fromBytes(new Uint8Array(bytes));engine.threshold=.9;engine.cooldownFrames=100;
   const acquired=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,channelCount:1}});
   if(!live(g)){for(const t of acquired.getTracks())t.stop();return;}stream=acquired;
   source=ctx.createMediaStreamSource(stream);proc=ctx.createScriptProcessor(512,1,1);mute=ctx.createGain();mute.gain.value=0;
   const resampler=new Resampler(ctx.sampleRate);proc.onaudioprocess=e=>{
    if(!live(g)||phase!=='waiting')return;
    const detections=engine.pushPcmF32(resampler.push(e.inputBuffer.getChannelData(0)));
    const found=detections.length>0;for(const d of detections)d.free();
    if(found){phase='opening';closeInput();startCapture(g).catch(e=>fail(e,g));}
   };source.connect(proc);proc.connect(mute);mute.connect(ctx.destination);phase='waiting';status('Local listening · say Hey Assistant');update();
  }catch(e){fail(e,g);}
 }
 function fail(error,g){if(g!==generation)return;cancel(error.message||'Voice unavailable. Typed chat remains ready.');}
 async function startCapture(g){
  const frozen=chat.context().thread;
  await chat.flushDraft();if(!live(g,frozen))return;
  const data=await chat.api('/api/thread?id='+encodeURIComponent(frozen));if(!live(g,frozen))return;
  if(data.draft||chat.context().draft||data.messages.some(m=>m.status==='working'))throw new Error('A typed draft or working answer prevents voice capture.');
  capture=crypto.randomUUID();const captureId=capture;turn=new FinalTurn({generation:g,threadId:frozen,draftRevision:data.draft_revision,requestId:crypto.randomUUID()});items=new Map();
  status('Connecting transcription…');phase='opening';update();
  openingTimer=setTimeout(()=>fail(new Error('Voice connection timed out; no request was sent.'),g),15000);
  const token=await chat.api('/api/voice/start',{thread:frozen,draft_revision:data.draft_revision,capture:captureId});if(!live(g,frozen)){chat.api('/api/voice/close',{capture:captureId}).catch(()=>{});return;}
  const acquired=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,channelCount:1}});
  if(!live(g,frozen)){for(const t of acquired.getTracks())t.stop();chat.api('/api/voice/close',{capture:captureId}).catch(()=>{});return;}stream=acquired;
  pc=new RTCPeerConnection();for(const track of stream.getTracks())pc.addTrack(track,stream);
  dc=pc.createDataChannel('oai-events');dc.onmessage=e=>{if(live(g,frozen))receive(e,g,frozen);};
  dc.onopen=()=>{if(!live(g,frozen))return;clearTimeout(openingTimer);phase='capturing';status('Listening · finish with Roger out');update();
   limitTimer=setTimeout(()=>fail(new Error('Five-minute capture limit reached. No request was sent.'),g),300000);resetIdle(g);};
  dc.onclose=()=>{if(live(g,frozen)&&['opening','capturing','finalizing'].includes(phase))fail(new Error('Transcription disconnected. No automatic resend was made.'),g);};
  const offer=await pc.createOffer();await pc.setLocalDescription(offer);if(!live(g,frozen))return;
  const response=await fetch('https://api.openai.com/v1/realtime/calls',{method:'POST',body:offer.sdp,headers:{Authorization:'Bearer '+token.value,'Content-Type':'application/sdp'},signal:AbortSignal.timeout(15000)});
  if(!response.ok)throw new Error('Transcription connection was refused.');const sdp=await response.text();if(live(g,frozen))await pc.setRemoteDescription({type:'answer',sdp});
 }
 function resetIdle(g){clearTimeout(idleTimer);idleTimer=setTimeout(()=>fail(new Error('Voice capture was inactive for three minutes. No request was sent.'),g),180000);}
 function receive(event,g,frozen){
  let value;try{value=JSON.parse(event.data);}catch{return;}
  if(value.type==='error'){fail(new Error('Transcription failed. No request was sent.'),g);return;}
  if(value.type==='conversation.item.input_audio_transcription.delta'){
   resetIdle(g);const key=value.item_id+':'+value.content_index;const text=(items.get(key)||'')+(value.delta||'');items.set(key,text);$('voice-transcript').textContent=text;
   clearTimeout(commitTimer);
   if(phase==='capturing'&&detectCompletion(text))commitTimer=setTimeout(()=>{
    if(!live(g,frozen)||phase!=='capturing'||!detectCompletion(items.get(key)))return;
    if(!turn.commit({generation:g,itemId:value.item_id,contentIndex:value.content_index}))return;
    phase='finalizing';for(const t of stream?.getAudioTracks()||[])t.enabled=false;
    dc.send(JSON.stringify({type:'input_audio_buffer.commit'}));status('Checking final transcript…');update();
    finalTimer=setTimeout(()=>fail(new Error('Final transcript timed out. No request was sent.'),g),15000);
   },600);
  }
  if(value.type==='conversation.item.input_audio_transcription.completed'&&phase==='finalizing'){
   try{
    const result=turn.complete({generation:g,itemId:value.item_id,contentIndex:value.content_index,transcript:value.transcript});if(!result)return;
    const payload={thread:result.thread_id,request:result.request_id,text:result.text,source:'voice',draft_revision:result.draft_revision};
    const record={owner:chat.context().owner,origin:location.origin,created:Date.now(),phase:'prepared',payload};persist(record);
    closeInput();phase='sending';$('voice-transcript').textContent=payload.text;status('Sending final request…');update();dispatch(record,g,true).catch(e=>fail(e,g));
   }catch(e){const finalText=value.transcript;fail(e,g);$('voice-transcript').textContent='Unsent final transcript — copy before leaving: '+finalText;}
  }
 }
 async function dispatch(record,g,fresh=false){
  persist({...record,phase:'dispatched'});
  try{
   await chat.api('/api/send',record.payload,10000);
   if(g!==generation)return;
   clearPending();phase='answer';autoRequest=fresh?record.payload.request:'';status('Request saved · waiting for Homebase');update();chat.refresh();
  }catch(e){
   persist({...record,phase:'uncertain'});phase='uncertain';status('Send outcome uncertain. Recover checks saved history before offering a retry.');update();
  }
 }
 async function recover(){
  if(!pending)return;const record=pending;status('Checking the original thread…');
  try{
   const data=await chat.api('/api/thread?id='+encodeURIComponent(record.payload.thread));if(pending!==record)return;
   const state=reconcilePending(record,data);
   if(state!=='absent'){clearPending();status('Request already saved · '+state+'. It will not be sent again.');chat.refresh();return;}
   status('No saved request found. Retry sends this same final request once.');$('voice-recover').textContent='Retry saved request';
   $('voice-recover').onclick=async()=>{
    if(pending!==record)return;
    // Reconcile again immediately before any explicit retry.
    const current=await chat.api('/api/thread?id='+encodeURIComponent(record.payload.thread));
    if(reconcilePending(record,current)!=='absent'){await recover();return;}
    if(chat.context().thread!==record.payload.thread)throw new Error('Open the original thread before retrying.');
    await dispatch(record,generation,false);$('voice-recover').textContent='Recover request';$('voice-recover').onclick=()=>recover().catch(e=>status(e.message));
   };
  }catch(e){status(e.message+' Saved recovery is retained.');}
 }
 async function speak(request,frozen,g){
  if(!live(g,frozen))return;const priorCapture=capture;closeInput();if(priorCapture)await chat.api('/api/voice/close',{capture:priorCapture});if(!live(g,frozen))return;phase='speaking';status('Preparing verified speech…');update();
  try{
   for(let index=0;;index++){
    if(!live(g,frozen)||phase!=='speaking')return;
    speechOperation=crypto.randomUUID();const audio=await chat.api('/api/voice/speech',{thread:frozen,request,index,operation:speechOperation},55000);
    if(!live(g,frozen)||phase!=='speaking')return;
    if(audio.verified!==true||audio.rate!==24000)throw new Error('Speech fidelity was not verified.');
    const raw=Uint8Array.from(atob(audio.pcm),c=>c.charCodeAt(0));if(raw.length%2)throw new Error('Invalid audio segment.');
    const view=new DataView(raw.buffer),buffer=ctx.createBuffer(1,raw.length/2,24000),samples=buffer.getChannelData(0);
    for(let i=0;i<samples.length;i++)samples[i]=view.getInt16(i*2,true)/32768;
    await ctx.resume();if(ctx.state!=='running')throw new Error('Tap Enable voice again to resume playback.');
    const node=ctx.createBufferSource();node.buffer=buffer;node.connect(ctx.destination);audioNode=node;
    status('Speaking answer · '+(index+1)+' of '+audio.count);
    await new Promise(resolve=>{node.onended=resolve;node.start();});audioNode=null;speechOperation='';
    if(index+1>=audio.count)break;
   }
   if(live(g,frozen)&&phase==='speaking'){phase='paused';status('Answer spoken.');await delay(700);await waiting(g);}
  }catch(e){if(g===generation){cancel(e.message);}}
 }
 function observe(data){
  if(!enabled)return;
  if(autoRequest&&phase==='answer'&&data.id===chat.context().thread){
   const m=data.messages.find(m=>m.request===autoRequest&&m.role==='assistant');
   if(m&&m.status!=='working'){
    autoRequest='';if(m.status==='completed'&&$('voice-speak').checked)speak(m.request,data.id,generation);
    else{phase='paused';status(m.status==='completed'?'Answer saved.':'Answer incomplete; speech skipped.');waiting(generation);}
   }
  }

 }
 $('voice-enable').onclick=async()=>{
  if(enabled&&phase!=='paused'){cancel('Voice paused.',true);return;}
  if(enabled&&phase==='paused'){generation++;waitingGeneration=-1;await ctx.resume();await waiting(generation);return;}
  try{
   if(document.visibilityState!=='visible')throw new Error('Keep Homebase in the foreground.');
   ctx=ctx||new (window.AudioContext||window.webkitAudioContext)({sampleRate:16000});await ctx.resume();
   const unlock=ctx.createBufferSource();unlock.buffer=ctx.createBuffer(1,1,ctx.sampleRate);unlock.connect(ctx.destination);unlock.start();
   enabled=true;generation++;waitingGeneration=-1;
   try{wakeLock=await navigator.wakeLock?.request('screen');}catch{}
   await waiting(generation);
  }catch(e){cancel(e.message,true);}
 };
 $('voice-cancel').onclick=()=>cancel('Unsent capture cancelled.');
 $('voice-stop').onclick=async()=>{cancel('Speaking stopped. The answer remains saved.');const g=generation;phase='cooldown';update();await delay(700);if(live(g))await waiting(g);};
 $('voice-recover').onclick=()=>recover().catch(e=>status(e.message));
 $('voice-discard').onclick=()=>{clearPending();status('Saved recovery discarded. Check the original thread for any accepted request.');};
 document.addEventListener('visibilitychange',()=>{if(document.visibilityState!=='visible')cancel('Voice paused when Homebase left the foreground.',true);});
 window.addEventListener('pagehide',()=>cancel('Voice paused.',true));
 async function ready(){
  try{const value=await chat.api('/api/voice/status');available=value.enabled===true;
   if(!available){status('Voice is not enabled on this release.');update();return;}
   const raw=sessionStorage.getItem(RECOVERY_KEY);if(raw){pending=validatePending(JSON.parse(raw),chat.context().owner,location.origin);status('A final voice request needs recovery.');}
  }catch(e){status(e.message);}update();
 }
 return {ready,update,observe,cancel,signout(){cancel('Voice signed out.',true);clearPending();},read(request){if(!enabled){status('Enable voice first to allow playback.');return;}cancel('Preparing answer speech.');speak(request,chat.context().thread,generation);}};
}
