'use strict';
const $=id=>document.getElementById(id);
// Local, pinned CommonMark renderer. Raw HTML and automatic media loads are disabled.
const markdown=window.markdownit?.({html:false,linkify:false,typographer:false});
if(markdown)markdown.renderer.rules.image=(tokens,index)=>markdown.utils.escapeHtml(tokens[index].content);
function messageBody(body,message){
 if(message.role!=='assistant'||!markdown){body.textContent=message.text;return;}
 body.innerHTML=markdown.render(message.text);
 for(const link of body.querySelectorAll('a')){
  try{const url=new URL(link.getAttribute('href'),location.origin);
   if(!['https:','http:','mailto:'].includes(url.protocol)){link.removeAttribute('href');continue;}
   link.target='_blank';link.rel='noopener noreferrer';
  }catch{link.removeAttribute('href');}
 }
}
function messageTime(message){
 const label=document.createElement('time');label.className='who';
 const completed=message.role==='assistant'&&message.status==='completed'&&Number.isFinite(message.completed);
 const timestamp=completed?message.completed:message.created;
 const speaker=message.role==='user'?'You':'Homebase';
 if(!Number.isFinite(timestamp)){label.setAttribute('aria-label',speaker+'; time unavailable');return label;}
 const date=new Date(timestamp*1000);
 if(!Number.isFinite(date.getTime())){label.setAttribute('aria-label',speaker+'; time unavailable');return label;}
 const time=date.toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit',hour12:true}).replace('AM','a.m.').replace('PM','p.m.');
 label.textContent=(date.getMonth()+1)+'-'+date.getDate()+', '+time;label.dateTime=date.toISOString();
 const kind=message.role==='user'?'Sent':completed?'Reply completed':'Reply started';
 label.title=kind+' '+date.toLocaleString(undefined,{dateStyle:'medium',timeStyle:'long'});
 label.setAttribute('aria-label',speaker+' · '+label.title);return label;
}
let csrf='',thread='',working=false,sending=false,deleting=false,authenticated=false,poll=null,draftTimer=null,pendingId='',activeRequest='',dirtyDraft=false,draftVersion=0,pendingText='',renderedSignature='',navigation=0,navigating=false;
async function api(path,body){
 const options={credentials:'same-origin',cache:'no-store'};
 if(body!==undefined){options.method='POST';options.headers={'Content-Type':'application/json','X-CSRF-Token':csrf};options.body=JSON.stringify(body);}
 const r=await fetch(path,options);const value=await r.json();
 if(!r.ok){if(r.status===401){authenticated=false;clearPrivate();controls();}throw new Error(value.error||'Request failed; your draft is retained.');}return value;
}
function panel(name,visible){$(name+'-panel').hidden=!visible;$(name==='thread'?'threads':'memory').setAttribute('aria-expanded',String(visible));}
function clearPrivate(){ $('messages').replaceChildren();$('records').replaceChildren();$('thread-panel').replaceChildren();panel('memory',false);panel('thread',false);$('draft').value='';$('connection').textContent='Disconnected';$('github-connection').textContent='';$('github-connection').hidden=true;renderedSignature=''; }
function status(text){$('status').textContent=text;}
async function githubStatus(){
 try{const value=await api('/api/github/status');if(!authenticated)return;const label=$('github-connection');
  label.hidden=value.state==='off';
  label.textContent=value.state==='ready'?'GitHub connected · '+value.repository_count+' repositories':value.state==='configured'?'GitHub configured · connects when needed':'GitHub unavailable · chat is still available';
 }catch{/* GitHub readiness must never block the conversation. */}
}
function activity(message,opened){
 if(!message.activity?.length)return null;
 const box=document.createElement('details');box.className='activity';box.dataset.message=message.id;box.open=opened.has(String(message.id));
 const label=document.createElement('summary');label.textContent='Activity · '+message.activity.length+' GitHub '+(message.activity.length===1?'operation':'operations');box.append(label);
 const list=document.createElement('ol');
 for(const action of message.activity){const row=document.createElement('li');const target=action.target||{};
  const names={get_file_contents:'Read',search_repositories:'Find repositories',search_code:'Search code',create_branch:'Create branch',create_or_update_file:'Save file',push_files:'Save files',create_pull_request:'Prepare pull request'};
  const states={succeeded:'verified',dispatched:'in progress',prepared:'queued',uncertain:'outcome unknown',cancelled:'cancelled',failed:'failed',interrupted:'interrupted'};
  row.textContent=(names[action.name]||'GitHub operation')+' · '+target.repo+(target.path?' / '+target.path:target.branch?' / '+target.branch:'')+' · '+(states[action.status]||action.status);
  if(action.evidence?.url){try{const url=new URL(action.evidence.url);const prefix='/'+target.owner+'/'+target.repo+'/';
   if(url.protocol==='https:'&&url.host==='github.com'&&url.pathname.startsWith(prefix)&&!url.search&&!url.hash){const link=document.createElement('a');link.href=url.href;link.textContent='View';link.target='_blank';link.rel='noopener noreferrer';row.append(' · ',link);}
  }catch{}}
  list.append(row);
 }box.append(list);return box;
}
function controls(){
 $('draft').disabled=!authenticated||!thread||navigating||deleting;$('send').disabled=!authenticated||!thread||navigating||deleting||working||sending||!$('draft').value.trim();
 $('stop').hidden=!working;$('new').disabled=!authenticated||sending||navigating||deleting;
 for(const id of ['threads','memory','signout'])$(id).disabled=!authenticated||navigating||deleting;
 for(const b of $('thread-panel').querySelectorAll('button'))b.disabled=!authenticated||navigating||deleting||sending;
}
function render(data){
 const nearBottom=window.innerHeight+window.scrollY>=document.body.scrollHeight-120;
 const signature=JSON.stringify([data.id,data.messages]);
 if(signature!==renderedSignature){renderedSignature=signature;
 const messages=$('messages');const opened=new Set([...messages.querySelectorAll('details[open]')].map(x=>x.dataset.message));messages.replaceChildren();
 for(const m of data.messages){const block=document.createElement('article');block.className='message '+(m.role==='user'?'user-message':'assistant-message');block.dataset.status=m.status;
 const who=messageTime(m);
 const body=document.createElement('div');body.className='body';messageBody(body,m);
 block.append(who,body);if(m.role==='assistant'&&m.status!=='completed'){const info=document.createElement('div');info.className='state';const phases={reading:'Reading GitHub…',writing:'Saving to GitHub…',verifying:'Checking GitHub result…',validating:'Checking request…'};info.textContent=m.status==='working'?(phases[m.phase]||'Working…'):m.error||m.status;block.append(info);}const actions=activity(m,opened);if(actions)block.append(actions);messages.append(block);}}
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
async function refresh(){if(!authenticated||!thread||sending||deleting||navigating)return;const current=thread;
 try{const data=await api('/api/thread?id='+encodeURIComponent(current));if(current===thread)render(data);}
 catch(e){status(e.message+' Reconnecting…');}
}
async function list(){const rows=await api('/api/threads');$('thread-panel').replaceChildren();for(const row of rows){
 const group=document.createElement('div');group.className='thread-row';
 const b=document.createElement('button');b.className='thread-open';b.textContent=row.title;b.onclick=()=>openThread(row.id);
 const remove=document.createElement('button');remove.className='thread-delete';remove.textContent='Delete';remove.setAttribute('aria-label','Delete thread: '+row.title);remove.onclick=()=>deleteThread(row);
 group.append(b,remove);$('thread-panel').append(group);}controls();return rows;}
async function deleteThread(row){
 if(!authenticated||navigating||deleting||sending)return;
 if(!window.confirm('Delete “'+row.title+'”? This removes its conversation and thread notes. Shared memories stay. This cannot be undone.'))return;
 deleting=true;navigating=true;++navigation;clearTimeout(draftTimer);controls();status('Deleting thread…');
 let removed=false;
 try{
  await api('/api/delete-thread',{thread:row.id});removed=true;
  if(row.id===thread){thread='';pendingId='';activeRequest='';pendingText='';dirtyDraft=false;working=false;renderedSignature='';$('draft').value='';$('messages').replaceChildren();}
  const rows=await list();
  if(!thread){const next=rows[0]?.id||(await api('/api/new',{})).id;deleting=false;await openThread(next);await list();}
  status('Thread deleted.');
 }catch(e){status((removed?'Thread deleted; reopening failed. ':'')+e.message);}
 finally{deleting=false;navigating=false;controls();if(thread&&dirtyDraft)saveDraft().catch(e=>status(e.message));}
}
async function saveDraft(){if(!authenticated||!thread)return;await api('/api/draft',{thread,text:$('draft').value});}
async function openThread(id){
 if(sending||deleting)return;const generation=++navigation;navigating=true;controls();status('Loading thread…');
 try{if(thread&&dirtyDraft)await saveDraft();if(generation!==navigation)return;thread=id;pendingId='';dirtyDraft=false;
 const data=await api('/api/thread?id='+encodeURIComponent(id));if(generation!==navigation||thread!==id)return;$('draft').value=data.draft||'';render(data);
 panel('thread',false);status(working?'Working…':'');
 if(!$('memory-panel').hidden)await memory();}catch(e){status(e.message);}finally{if(generation===navigation){navigating=false;controls();}}
}
async function newThread(){if(navigating||sending||deleting)return;navigating=true;controls();status('Opening a new thread…');try{if(thread&&dirtyDraft)await saveDraft();const value=await api('/api/new',{});await openThread(value.id);await list();}catch(e){status(e.message);}finally{navigating=false;controls();}}
async function send(event){event?.preventDefault();if(working||sending||navigating||deleting||!authenticated||!$('draft').value.trim())return;
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
$('threads').onclick=async()=>{try{await list();panel('thread',$('thread-panel').hidden);}catch(e){status(e.message);}};
$('stop').onclick=async()=>{try{await api('/api/stop',{thread});await refresh();}catch(e){status(e.message);}};
async function memory(){const rows=await api('/api/memory?id='+encodeURIComponent(thread));$('records').replaceChildren();for(const row of rows){
 const block=document.createElement('div');block.className='record';const title=document.createElement('div');title.textContent=row.title+' · '+row.scope+' · revision '+row.revision;
 const metadata=document.createElement('p');metadata.className='quiet';metadata.textContent=row.id+' · '+row.source;
 const text=document.createElement('textarea');text.value=row.text;text.maxLength=1500;text.setAttribute('aria-label','Edit '+row.title);
 const save=document.createElement('button');save.textContent='Correct';save.onclick=()=>changeMemory({action:'correct',id:row.id,revision:row.revision,text:text.value});
 const forget=document.createElement('button');forget.textContent='Forget';forget.onclick=()=>changeMemory({action:'forget',id:row.id,revision:row.revision});block.append(title,metadata,text,save,forget);$('records').append(block);}}
async function changeMemory(value){try{const r=await api('/api/memory',{...value,thread});status('Memory updated · revision '+r.revision);await memory();}catch(e){status(e.message);}}
$('memory').onclick=async()=>{try{await memory();panel('memory',$('memory-panel').hidden);}catch(e){status(e.message);}};
$('memory-form').onsubmit=async e=>{e.preventDefault();await changeMemory({action:'remember',scope:$('memory-scope').value,title:$('memory-title').value,text:$('memory-text').value});};
$('signout').onclick=async()=>{try{const result=await api('/api/signout',{});authenticated=false;clearInterval(poll);clearPrivate();status(result.message);controls();}catch(e){status(e.message);}};
async function boot(){
 controls();try{
  if(location.hash.startsWith('#pair=')){const capability=location.hash.slice(6);history.replaceState(null,'',location.pathname);await api('/api/pair',{capability});}
  const s=await api('/api/status');csrf=s.csrf;authenticated=true;$('connection').textContent='Connected · '+s.account+' · using your ChatGPT plan';
  const rows=await api('/api/threads');if(rows.length)await openThread(rows[0].id);else await newThread();
  controls();poll=setInterval(refresh,700);await list();await githubStatus();
  setInterval(()=>{if(authenticated)githubStatus();},15000);
 }catch(e){status(e.message);controls();}
}
window.addEventListener('pagehide',()=>{if(authenticated&&thread&&dirtyDraft){fetch('/api/draft',{method:'POST',credentials:'same-origin',keepalive:true,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({thread,text:$('draft').value})}).catch(()=>{});}});
boot();
