'use strict';
const $=id=>document.getElementById(id);
let csrf='',thread='',working=false,sending=false,authenticated=false,poll=null,draftTimer=null,pendingId='',activeRequest='',dirtyDraft=false,draftVersion=0,pendingText='',renderedSignature='',navigation=0,navigating=false;
async function api(path,body){
 const options={credentials:'same-origin',cache:'no-store'};
 if(body!==undefined){options.method='POST';options.headers={'Content-Type':'application/json','X-CSRF-Token':csrf};options.body=JSON.stringify(body);}
 const r=await fetch(path,options);const value=await r.json();
 if(!r.ok){if(r.status===401){authenticated=false;clearPrivate();controls();}throw new Error(value.error||'Request failed; your draft is retained.');}return value;
}
function clearPrivate(){ $('messages').replaceChildren();$('records').replaceChildren();$('thread-panel').replaceChildren();$('memory-panel').hidden=true;$('thread-panel').hidden=true;$('draft').value='';$('connection').textContent='Disconnected';renderedSignature=''; }
function status(text){$('status').textContent=text;}
function controls(){
 $('draft').disabled=!authenticated||navigating;$('send').disabled=!authenticated||navigating||working||sending||!$('draft').value.trim();
 $('stop').hidden=!working;$('new').disabled=!authenticated||sending||navigating;
 for(const id of ['threads','memory','signout'])$(id).disabled=!authenticated||navigating;
 for(const b of $('thread-panel').querySelectorAll('button'))b.disabled=navigating;
}
function render(data){
 const nearBottom=window.innerHeight+window.scrollY>=document.body.scrollHeight-120;
 const signature=JSON.stringify([data.id,data.messages]);
 if(signature!==renderedSignature){renderedSignature=signature;
 const messages=$('messages');messages.replaceChildren();
 for(const m of data.messages){const block=document.createElement('article');block.className='message';block.dataset.status=m.status;
 const who=document.createElement('div');who.className='who';who.textContent=m.role==='user'?'You':'Homebase';
 const body=document.createElement('div');body.className='body';body.textContent=m.text;
 block.append(who,body);if(m.role==='assistant'&&m.status!=='completed'){const info=document.createElement('div');info.className='state';info.textContent=m.status==='working'?'Working…':m.error||m.status;block.append(info);}messages.append(block);}}
 working=data.messages.some(m=>m.status==='working');
 const active=data.messages.find(m=>m.status==='working');activeRequest=active?active.request:'';
 if(!dirtyDraft&&!sending)$('draft').value=data.draft||'';
 if(!working&&!sending){
  const last=data.messages.at(-1);
  if(last?.role==='assistant'&&last.request===pendingId){
   if(last.status==='completed'){if(!dirtyDraft||$('draft').value===pendingText){$('draft').value='';dirtyDraft=false;}pendingId='';status('');}
   else {status(last.error||'Reply is incomplete. Your draft is saved.');pendingId='';}
  }
 }
 if(working)status('Working… Reply text is being saved.');controls();
 if(nearBottom&&working)window.scrollTo({top:document.body.scrollHeight,behavior:'instant'});
}
async function refresh(){if(!authenticated||!thread||sending)return;const current=thread;
 try{const data=await api('/api/thread?id='+encodeURIComponent(current));if(current===thread)render(data);}
 catch(e){status(e.message+' Reconnecting…');}
}
async function list(){const rows=await api('/api/threads');$('thread-panel').replaceChildren();for(const row of rows){const b=document.createElement('button');b.textContent=row.title;b.onclick=()=>openThread(row.id);$('thread-panel').append(b);}}
async function saveDraft(){if(!authenticated||!thread)return;await api('/api/draft',{thread,text:$('draft').value});}
async function openThread(id){
 if(sending)return;const generation=++navigation;navigating=true;controls();status('Loading thread…');
 try{if(thread&&dirtyDraft)await saveDraft();if(generation!==navigation)return;thread=id;pendingId='';dirtyDraft=false;
 const data=await api('/api/thread?id='+encodeURIComponent(id));if(generation!==navigation||thread!==id)return;$('draft').value=data.draft||'';render(data);
 $('thread-panel').hidden=true;$('threads').setAttribute('aria-expanded','false');status(working?'Working…':'');
 if(!$('memory-panel').hidden)await memory();}catch(e){status(e.message);}finally{if(generation===navigation){navigating=false;controls();}}
}
async function newThread(){if(navigating||sending)return;navigating=true;controls();status('Opening a new thread…');try{if(thread&&dirtyDraft)await saveDraft();const value=await api('/api/new',{});await openThread(value.id);await list();}catch(e){status(e.message);}finally{navigating=false;controls();}}
async function send(event){event?.preventDefault();if(working||sending||!authenticated||!$('draft').value.trim())return;
 const text=$('draft').value;const current=thread;const version=draftVersion;
 sending=true;controls();status('Sending…');pendingId=pendingId||crypto.randomUUID();pendingText=text;
 try{await api('/api/send',{thread:current,request:pendingId,text});if(draftVersion===version)dirtyDraft=false;else await api('/api/draft',{thread:current,text:$('draft').value});}
 catch(e){status(e.message+' Your draft is retained.');}
 finally{sending=false;controls();await refresh();}
}
$('composer').addEventListener('submit',send);
$('draft').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();send();}});
$('draft').addEventListener('input',()=>{dirtyDraft=true;draftVersion++;if(!working&&!sending)pendingId='';controls();clearTimeout(draftTimer);draftTimer=setTimeout(()=>saveDraft().catch(e=>status(e.message+' Draft remains in this browser.')),350);});
$('new').onclick=newThread;
$('threads').onclick=async()=>{try{await list();$('thread-panel').hidden=!$('thread-panel').hidden;$('threads').setAttribute('aria-expanded',String(!$('thread-panel').hidden));}catch(e){status(e.message);}};
$('stop').onclick=async()=>{try{await api('/api/stop',{thread});await refresh();}catch(e){status(e.message);}};
async function memory(){const rows=await api('/api/memory?id='+encodeURIComponent(thread));$('records').replaceChildren();for(const row of rows){
 const block=document.createElement('div');block.className='record';const title=document.createElement('div');title.textContent=row.title+' · '+row.scope+' · revision '+row.revision;
 const metadata=document.createElement('p');metadata.className='quiet';metadata.textContent=row.id+' · '+row.source;
 const text=document.createElement('textarea');text.value=row.text;text.maxLength=1500;text.setAttribute('aria-label','Edit '+row.title);
 const save=document.createElement('button');save.textContent='Correct';save.onclick=()=>changeMemory({action:'correct',id:row.id,revision:row.revision,text:text.value});
 const forget=document.createElement('button');forget.textContent='Forget';forget.onclick=()=>changeMemory({action:'forget',id:row.id,revision:row.revision});block.append(title,metadata,text,save,forget);$('records').append(block);}}
async function changeMemory(value){try{const r=await api('/api/memory',{...value,thread});status('Memory updated · revision '+r.revision);await memory();}catch(e){status(e.message);}}
$('memory').onclick=async()=>{try{await memory();$('memory-panel').hidden=!$('memory-panel').hidden;$('memory').setAttribute('aria-expanded',String(!$('memory-panel').hidden));}catch(e){status(e.message);}};
$('memory-form').onsubmit=async e=>{e.preventDefault();await changeMemory({action:'remember',scope:$('memory-scope').value,title:$('memory-title').value,text:$('memory-text').value});};
$('signout').onclick=async()=>{try{const result=await api('/api/signout',{});authenticated=false;clearInterval(poll);$('messages').replaceChildren();$('records').replaceChildren();$('thread-panel').replaceChildren();$('draft').value='';$('memory-panel').hidden=true;$('connection').textContent='Disconnected';status(result.message);controls();}catch(e){status(e.message);}};
async function boot(){
 controls();try{
  if(location.hash.startsWith('#pair=')){const capability=location.hash.slice(6);history.replaceState(null,'',location.pathname);await api('/api/pair',{capability});}
  const s=await api('/api/status');csrf=s.csrf;authenticated=true;$('connection').textContent='Connected · '+s.account+' · using your ChatGPT plan';
  const rows=await api('/api/threads');if(rows.length)await openThread(rows[0].id);else await newThread();
  controls();poll=setInterval(refresh,700);await list();
 }catch(e){status(e.message);controls();}
}
window.addEventListener('pagehide',()=>{if(authenticated&&thread&&dirtyDraft){fetch('/api/draft',{method:'POST',credentials:'same-origin',keepalive:true,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({thread,text:$('draft').value})}).catch(()=>{});}});
boot();
