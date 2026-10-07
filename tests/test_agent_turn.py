"""Actual App worker with synthetic function rounds and durable GitHub actions."""
import json
from pathlib import Path
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch
import homebase_chat as chat
import homebase_probe as p
from chat_fixture import FakeHTTP, Response, registration
from github_bridge import SCHEMAS

class ToolHTTP(FakeHTTP):
    calls=[];mode='success';delay=0;program=[]
    def request(self,method,url,**kwargs):
        if not url.endswith('/responses'):return super().request(method,url,**kwargs)
        type(self).calls.append(kwargs['json']);item=type(self).program.pop(0)
        if isinstance(item,Exception):raise item
        events=[]
        for output in item:
            if output['type']=='message':events.append({'type':'response.output_text.delta','delta':output['content'][0]['text']})
        events.append({'type':'response.completed','response':{'status':'completed','model':'gpt-5.6-sol',
            'reasoning':{'effort':'high'},'service_tier':'default','output':item,'usage':{'input_tokens':100,'output_tokens':10}}})
        return Response(events)

def call(name='get_file_contents',namespace='github',identity='call-one'):
    return {'type':'function_call','name':name,'namespace':namespace,'call_id':identity,'arguments':json.dumps({'owner':'aboriginalien','repo':'homebase','path':'docs/test.md','ref':'main'})}
def final():return {'type':'message','content':[{'type':'output_text','text':'Verified synthetic result.'}]}

class StubBridge:
    def __init__(self):self.calls=[];self.stopping=False;self.app=None;self.in_lock=lambda:False
    def definitions(self):return [{'type':'namespace','name':'github','tools':[{'type':'function','name':n,'parameters':s,'strict':False} for n,s in SCHEMAS.items()]}]
    def execute(self,name,args,identity,ctx,journal):
        assert not self.in_lock(),'OAuth lock was held during GitHub work'
        self.calls.append((name,args,identity))
        journal.prepare(ctx.turn,identity,name,{'owner':'aboriginalien','repo':'homebase'},args)
        journal.dispatch(ctx.turn,identity);journal.outcome(ctx.turn,identity,'succeeded',{'blob':'a'*40})
        if self.stopping:self.app.stop(ctx_thread(self.app))
        return json.dumps({'verified':True,'blob':'a'*40})
def ctx_thread(app):return next(iter(app.jobs.values()))['thread']

class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=p.Store(Path(self.temp.name)/'state');key=registration(self.store)
        self.bridge=StubBridge();self.app=chat.App(self.store,key,'http://127.0.0.1:8769',http_factory=ToolHTTP,bridge=self.bridge)
        self.bridge.app=self.app;self.thread=self.app.state.new_thread();ToolHTTP.calls=[]
    def tearDown(self):self.temp.cleanup()
    def send(self):
        identity=self.app.send(self.thread,str(uuid.uuid4()),'Read homebase docs')['message'];deadline=time.time()+3
        while self.app.jobs and time.time()<deadline:time.sleep(.01)
        self.assertFalse(self.app.jobs)
        return next(m for m in self.app.state.thread(self.thread)['messages'] if m['id']==identity)
    def test_namespaced_call_and_encrypted_reasoning_are_faithfully_continued(self):
        reasoning={'type':'reasoning','id':'opaque','encrypted_content':'SYNTHETIC-OPAQUE','summary':[]}
        ToolHTTP.program=[[reasoning,call()],[final()]]
        row=self.send();self.assertEqual(row['status'],'completed');self.assertEqual(len(self.bridge.calls),1)
        second=ToolHTTP.calls[1]['input'];self.assertEqual(second[-3],reasoning);self.assertEqual(second[-2],call())
        self.assertEqual(second[-1]['call_id'],'call-one');self.assertEqual(second[-1]['type'],'function_call_output')
        self.assertEqual(ToolHTTP.calls[0]['include'],['reasoning.encrypted_content']);self.assertNotIn('previous_response_id',ToolHTTP.calls[1])
        with self.app.state.connect() as db:
            self.assertEqual(db.execute('SELECT continuation FROM tool_turns').fetchone()[0],'')
        self.assertNotIn('SYNTHETIC-OPAQUE',json.dumps(row));self.assertEqual(row['activity'][0]['status'],'succeeded')
    def test_invalid_namespace_or_unknown_function_dispatches_nothing(self):
        for item in (call(namespace='outside'),call(name='delete_repository')):
            ToolHTTP.program=[[item]];self.assertEqual(self.send()['status'],'incomplete')
        self.assertFalse(self.bridge.calls)
    def test_quota_after_verified_tool_preserves_activity_and_draft(self):
        ToolHTTP.program=[[call()],p.ProbeError('HTTP 429')]
        row=self.send();self.assertEqual(row['status'],'incomplete');self.assertEqual(row['activity'][0]['status'],'succeeded')
        self.assertIn('quota',row['error']);self.assertEqual(self.app.state.thread(self.thread)['draft'],'Read homebase docs')
    def test_stop_between_tool_and_continuation_prevents_second_model_round(self):
        self.bridge.stopping=True;ToolHTTP.program=[[call()],[final()]]
        row=self.send();self.assertEqual(row['status'],'stopped');self.assertEqual(len(ToolHTTP.calls),1)
        self.assertEqual(row['activity'][0]['status'],'succeeded')
    def test_same_call_id_in_another_round_is_not_executed_twice(self):
        ToolHTTP.program=[[call()],[call()],[final()]]
        self.assertEqual(self.send()['status'],'incomplete');self.assertEqual(len(self.bridge.calls),1)
    def test_model_round_limit_preserves_prior_verified_operations(self):
        ToolHTTP.program=[[call(identity='call-'+str(i))] for i in range(6)]
        row=self.send();self.assertEqual(row['status'],'incomplete');self.assertEqual(len(ToolHTTP.calls),6)
        self.assertEqual(len(self.bridge.calls),5);self.assertEqual(len(row['activity']),5)
    def test_oauth_lock_is_released_before_github_call(self):
        original=self.store.locked;depth=[0]
        import contextlib
        @contextlib.contextmanager
        def counted():
            with original():
                depth[0]+=1
                try:yield
                finally:depth[0]-=1
        self.store.locked=counted;self.bridge.in_lock=lambda:bool(depth[0])
        ToolHTTP.program=[[call()],[final()]];self.assertEqual(self.send()['status'],'completed')

if __name__=='__main__':unittest.main()
