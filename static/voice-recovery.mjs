// Store only a final frozen request, never audio, provisional text or credentials.
export const RECOVERY_KEY='homebase.voice.pending.v1';
export function validatePending(value,owner,origin,now=Date.now()) {
 if(!value||value.owner!==owner||value.origin!==origin||!Number.isFinite(value.created)||now-value.created>86400000||value.created>now+60000)throw new Error('Voice recovery context expired or changed.');
 const p=value.payload;
 if(!p||p.source!=='voice'||typeof p.text!=='string'||!p.text.trim()||p.text.length>8000||!Number.isSafeInteger(p.draft_revision)||p.draft_revision<0||
 !/^[0-9a-f-]{36}$/i.test(p.thread)||!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(p.request)||!['prepared','dispatched','uncertain'].includes(value.phase))throw new Error('Invalid saved voice request.');
 return Object.freeze({...value,payload:Object.freeze({...p})});
}
export function reconcilePending(record,data) {
 if(data.id!==record.payload.thread)throw new Error('Recovery returned a different thread.');
 const rows=data.messages.filter(m=>m.request===record.payload.request);
 if(!rows.length)return 'absent';
 const user=rows.find(m=>m.role==='user'),assistant=rows.find(m=>m.role==='assistant');
 if(!user||!assistant||user.text!==record.payload.text||user.source!=='voice')throw new Error('The saved request does not match durable history. Do not resend.');
 return assistant.status;
}
export class Resampler {
 constructor(rate,target=16000){this.ratio=rate/target;this.position=0;this.tail=[];}
 push(input){
  const values=this.tail.concat(Array.from(input)),out=[];
  while(this.position+1<values.length){const i=Math.floor(this.position),f=this.position-i;out.push(values[i]*(1-f)+values[i+1]*f);this.position+=this.ratio;}
  const used=Math.min(Math.floor(this.position),Math.max(0,values.length-1));this.tail=values.slice(used);this.position-=used;
  return Float32Array.from(out);
 }
}
