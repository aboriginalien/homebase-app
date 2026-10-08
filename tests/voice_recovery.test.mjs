import test from 'node:test';import assert from 'node:assert/strict';
import {validatePending,reconcilePending,Resampler} from '../static/voice-recovery.mjs';
const payload={thread:'11111111-1111-4111-8111-111111111111',request:'22222222-2222-4222-8222-222222222222',source:'voice',draft_revision:4,text:'Keep this exact request'};
const record={owner:'owner',origin:'https://homebase.test',created:1000,phase:'uncertain',payload};
test('final payload frozen; expired and changed contexts rejected',()=>{
 assert.ok(Object.isFrozen(validatePending(record,'owner',record.origin,2000).payload));
 for(const [o,u,t] of [['other',record.origin,2000],['owner','https://other.test',2000],['owner',record.origin,86402000]])assert.throws(()=>validatePending(record,o,u,t));
});
test('accepted stopped incomplete UUIDs never resend',()=>{
 for(const status of ['working','completed','stopped','incomplete'])assert.equal(reconcilePending(record,{id:payload.thread,messages:[{role:'user',request:payload.request,text:payload.text,source:'voice'},{role:'assistant',request:payload.request,status}]}),status);
 assert.equal(reconcilePending(record,{id:payload.thread,messages:[]}), 'absent');
});
test('different thread and payload fail closed',()=>{
 assert.throws(()=>reconcilePending(record,{id:'wrong',messages:[]}));
 assert.throws(()=>reconcilePending(record,{id:payload.thread,messages:[{role:'user',request:payload.request,text:'changed',source:'typed'}]}));
});
test('resampling continuous across uneven blocks',()=>{
 for(const rate of [16000,44100,48000]){
 const input=Float32Array.from({length:4000},(_,i)=>Math.sin(i/20)),single=new Resampler(rate).push(input),r=new Resampler(rate);
 const split=Float32Array.from([...r.push(input.slice(0,137)),...r.push(input.slice(137,1069)),...r.push(input.slice(1069))]);
 assert.equal(single.length,split.length);for(let i=0;i<single.length;i++)assert.ok(Math.abs(single[i]-split[i])<1e-5);
 }
});
