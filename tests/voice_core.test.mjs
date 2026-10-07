import test from 'node:test';
import assert from 'node:assert/strict';
import {detectCompletion,finalizedRequest,FinalTurn} from '../static/voice-core.mjs';
const identity={generation:1,threadId:'original_thread',draftRevision:3,requestId:'44aa1122-1234-4abc-9abc-123456789012'};
test('tested Roger out punctuation and trailing-only rule; other prototype phrases excluded',()=>{
  for (const tail of ['Roger out','Roger, out.']) {
    assert.equal(finalizedRequest('Please remember my appointment. '+tail),'Please remember my appointment.');
  }
  for(const text of ['Please say Roger out tomorrow','over and out',"that’s all",'go ahead and send it']) assert.equal(detectCompletion(text),null);
  assert.throws(()=>finalizedRequest('Roger out'));
});
test('no truncation when final request exceeds supported limit',()=>{
  assert.equal(finalizedRequest('a'.repeat(8000)+' Roger out').length,8000);
  assert.throws(()=>finalizedRequest('a'.repeat(8001)+' Roger out'));
});
test('commit requires exact generation and can occur only once',()=>{
  const turn=new FinalTurn(identity);
  assert.equal(turn.commit({generation:0,itemId:'item',contentIndex:0}),false);
  assert.equal(turn.commit({generation:1,itemId:'item',contentIndex:0}),true);
  assert.equal(turn.commit({generation:1,itemId:'other',contentIndex:0}),false);
});
test('final event is matched to item/content index and yields one frozen original-thread payload',()=>{
  const turn=new FinalTurn(identity);turn.commit({generation:1,itemId:'item',contentIndex:0});
  for(const e of [{generation:0,itemId:'item',contentIndex:0},{generation:1,itemId:'other',contentIndex:0},{generation:1,itemId:'item',contentIndex:1}])
    assert.equal(turn.complete({...e,transcript:'Do this. Roger out'}),null);
  const e={generation:1,itemId:'item',contentIndex:0,transcript:'Do this. Roger out'};
  const payload=turn.complete(e);assert.equal(payload.thread_id,'original_thread');assert.equal(payload.draft_revision,3);assert.equal(payload.source,'voice');
  assert.ok(Object.isFrozen(payload));assert.equal(turn.complete(e),null);
  assert.equal(turn.accept('wrong'),false);assert.equal(turn.accept(identity.requestId),true);assert.equal(turn.cancel(),false);
});
test('cancel and failed finalization never manufacture a send',()=>{
  const turn=new FinalTurn(identity);turn.commit({generation:1,itemId:'item',contentIndex:0});
  assert.throws(()=>turn.complete({generation:1,itemId:'item',contentIndex:0,transcript:'unfinished request'}));
  assert.equal(turn.phase,'finalizing');turn.cancel();
  assert.equal(turn.complete({generation:1,itemId:'item',contentIndex:0,transcript:'Do this. Roger out'}),null);
});
