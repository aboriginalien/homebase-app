import contextlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch
import homebase_probe as p
import homebase_chat as c
from memory_store import State
from chat_fixture import FakeHTTP,registration

class ChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.store=p.Store(self.root/'state')
        self.key=registration(self.store);FakeHTTP.mode='success';FakeHTTP.delay=0;FakeHTTP.calls=[]
        self.app=c.App(self.store,self.key,'http://127.0.0.1:8769',http_factory=FakeHTTP)
        self.thread=self.app.state.new_thread()
    def tearDown(self):
        deadline=time.time()+4
        while self.app.jobs and time.time()<deadline:time.sleep(.01)
        self.tmp.cleanup()
    def wait(self,identity):
        deadline=time.time()+4
        while self.app.jobs and time.time()<deadline:time.sleep(.01)
        self.assertFalse(self.app.jobs)
        return next(r for r in self.app.state.thread(self.thread)['messages'] if r['id']==identity)
    def send(self,text):return self.app.send(self.thread,str(uuid.uuid4()),text)['message']
    def test_payload_and_completed(self):
        row=self.wait(self.send('hello'))
        self.assertEqual(row['status'],'completed')
        payload=FakeHTTP.calls[0]
        self.assertEqual(set(payload),{'model','input','instructions','store','stream','reasoning','service_tier'})
        self.assertEqual(payload['model'],'gpt-5.6-sol');self.assertFalse(payload['store']);self.assertTrue(payload['stream'])
        self.assertEqual(payload['reasoning'],{'effort':'high'});self.assertEqual(payload['service_tier'],'default')
        self.assertEqual(payload['input'],[{'role':'user','content':'hello'}])
    def test_inconsistent_terminal_and_dropped_stream(self):
        for mode in ('wrong','drop','failed','quota','auth'):
            FakeHTTP.mode=mode
            row=self.wait(self.send(mode));self.assertEqual(row['status'],'incomplete')
            self.assertEqual(self.app.state.thread(self.thread)['draft'],mode)
            self.assertNotIn('SYNTHETIC',row['error'])
    def test_duplicate_and_busy(self):
        FakeHTTP.delay=.1;request=str(uuid.uuid4());a=self.app.send(self.thread,request,'one')
        b=self.app.send(self.thread,request,'one');self.assertEqual(a['message'],b['message']);self.assertFalse(b['started'])
        with self.assertRaises(p.ProbeError):self.send('second')
        self.wait(a['message']);again=self.app.send(self.thread,request,'one');self.assertFalse(again['started']);self.assertEqual(len(FakeHTTP.calls),1)
    def test_stop_is_durable(self):
        FakeHTTP.delay=.15;a=self.send('stop me');time.sleep(.05);self.app.stop(self.thread)
        row=self.wait(a);self.assertEqual(row['status'],'stopped');self.assertNotEqual(self.app.state.thread(self.thread)['draft'],'')
    def test_restart_and_fresh_isolation(self):
        self.wait(self.send('old private thread fact'))
        s=State(self.store.directory,self.key,recover=True)
        self.assertIn('old private thread fact',s.thread(self.thread)['messages'][0]['text'])
        fresh=s.new_thread();context=s.context(fresh,'hello')
        self.assertNotIn('old private thread fact',json.dumps(context))
        self.assertIn('old private thread fact',json.dumps(s.context(self.thread,'recall')))
    def test_process_restart_persistence(self):
        self.wait(self.send('process durable detail'))
        code='from pathlib import Path; from memory_store import State; import sys; s=State(Path(sys.argv[1]),sys.argv[2],recover=True); print(s.thread(sys.argv[3])["messages"][0]["text"])'
        r=subprocess.run([str(Path(os.sys.executable)),'-c',code,str(self.store.directory),self.key,self.thread],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(r.stdout.strip(),'process durable detail')
    def test_restart_interrupted_message(self):
        ident,_=self.app.state.begin(self.thread,str(uuid.uuid4()),'pending')
        State(self.store.directory,self.key,recover=True)
        row=self.app.state.thread(self.thread)['messages'][-1]
        self.assertEqual(row['status'],'incomplete');self.assertIn('restarted',row['error'])
    def test_shared_memory_selective_correct_forget(self):
        s=self.app.state
        a=s.change('remember',title='Travel',text='Train preference: sleeper',scope='shared')
        b=s.change('remember',title='Cooking',text='No pepper in recipes',scope='shared')
        fresh=s.new_thread();ctx=s.context(fresh,'travel plans train')
        self.assertIn('sleeper',ctx['instructions']);self.assertNotIn('No pepper',ctx['instructions'])
        s.change('correct',identity=a['id'],revision=1,text='Train preference: daytime')
        ctx=s.context(fresh,'train');self.assertIn('daytime',ctx['instructions']);self.assertNotIn('sleeper',ctx['instructions'])
        with self.assertRaises(p.ProbeError):s.change('correct',identity=a['id'],revision=1,text='stale')
        s.change('forget',identity=a['id'],revision=2)
        ctx=s.context(fresh,'train');self.assertNotIn(a['id'],ctx['instructions']);self.assertNotIn('daytime',ctx['instructions'])
    def test_thread_memory_scope(self):
        r=self.app.state.change('remember',title='Local',text='Private thread clue',scope='thread',thread=self.thread)
        fresh=self.app.state.new_thread();self.assertNotIn('Private thread clue',self.app.state.context(fresh,'clue')['instructions'])
        with self.assertRaises(p.ProbeError):self.app.state.change('forget',identity=r['id'],revision=1,thread=fresh)
    def test_explicit_memory_commands_no_provider_no_stale_history(self):
        self.wait(self.send('/remember shared Color | My color is sapphire'))
        row=self.app.state.memory()[0];self.wait(self.send(f"/correct {row['id']} 1 | My color is amber"))
        self.wait(self.send(f"/forget {row['id']} 2"))
        context=self.app.state.context(self.thread,'color')
        self.assertNotIn('sapphire',json.dumps(context));self.assertNotIn('amber',json.dumps(context));self.assertEqual(FakeHTTP.calls,[])
    def test_context_is_bounded_and_keeps_transcript(self):
        for i in range(30):
            mid,_=self.app.state.begin(self.thread,str(uuid.uuid4()),str(i)+'x'*3000)
            self.app.state.finish(mid,'y'*4000)
        ctx=self.app.state.context(self.thread,'current')
        self.assertLess(len(json.dumps(ctx)),26000);self.assertEqual(len(ctx['input']),13)
        self.assertEqual(len(self.app.state.thread(self.thread)['messages']),60)
        self.assertNotEqual(self.app.state.thread(self.thread)['summary'],'')
    def test_seed_atomic_and_private(self):
        doc={'version':1,'records':[{'id':'role','title':'Role','text':'Generic role','source':'approved seed','pinned':True},
                                  {'id':'bad','title':'','text':'bad','source':'seed','pinned':False}]}
        with self.assertRaises(p.ProbeError):self.app.state.seed(doc)
        self.assertEqual(self.app.state.memory(),[])
        doc['records'].pop();self.app.state.seed(doc);self.assertIn('Generic role',self.app.state.context(self.thread,'hi')['instructions'])
        with self.assertRaises(p.ProbeError):self.app.state.seed(doc)
        self.assertEqual(self.app.state.path.stat().st_mode&0o777,0o600)
    def test_new_draft_not_erased_by_completion(self):
        mid,_=self.app.state.begin(self.thread,str(uuid.uuid4()),'submitted')
        self.app.state.draft(self.thread,'new unsent')
        self.app.state.finish(mid,'reply')
        self.assertEqual(self.app.state.thread(self.thread)['draft'],'new unsent')
    def test_forget_invalidates_transcript_and_summary_context(self):
        r=self.app.state.change('remember',title='Cuisine',text='purple potatoes',scope='shared')
        mid,_=self.app.state.begin(self.thread,str(uuid.uuid4()),'food')
        ctx=self.app.state.context(self.thread,'food')
        self.app.state.references(mid,ctx['references']);self.app.state.finish(mid,'You prefer purple potatoes')
        for i in range(8):
            mid,_=self.app.state.begin(self.thread,str(uuid.uuid4()),'other '+str(i))
            ctx=self.app.state.context(self.thread,'other '+str(i))
            self.app.state.references(mid,ctx['references']);self.app.state.finish(mid,'ordinary reply')
        self.app.state.change('forget',identity=r['id'],revision=1)
        ctx=self.app.state.context(self.thread,'food')
        self.assertNotIn('purple potatoes',json.dumps(ctx))
        self.assertIn('purple potatoes',json.dumps(self.app.state.thread(self.thread)))
    def test_changed_index_title_invalidates_echo(self):
        r=self.app.state.change('remember',title='Favorite food purple potatoes',text='unmatched',scope='shared')
        mid,_=self.app.state.begin(self.thread,str(uuid.uuid4()),'hi')
        ctx=self.app.state.context(self.thread,'hi');self.assertEqual(ctx['selection'],[])
        self.app.state.references(mid,ctx['references']);self.app.state.finish(mid,'purple potatoes')
        self.app.state.change('forget',identity=r['id'],revision=1)
        self.assertNotIn('purple potatoes',json.dumps(self.app.state.context(self.thread,'hi')))
    def test_owner_binding(self):
        with self.assertRaises(p.ProbeError):State(self.store.directory,'another-owner')
    def test_pair_one_use_expiry_sessions(self):
        cap=self.app.state.pairing();token,csrf=self.app.state.pair(cap)
        self.assertEqual(self.app.state.session(token),csrf)
        with self.assertRaises(p.ProbeError):self.app.state.pair(cap)
        self.app.state.revoke_sessions();self.assertIsNone(self.app.state.session(token))
    def test_https_only_remote(self):
        with self.assertRaises(p.ProbeError):c.App(self.store,self.key,'http://public.example',http_factory=FakeHTTP)
    def test_signout_clears_local_tokens_and_browser(self):
        token,_=self.app.state.pair(self.app.state.pairing());result=self.app.signout()
        self.assertIn('confirmed',result);self.assertIsNone(self.app.state.session(token))
        with self.store.locked():self.assertNotIn('access_token',self.store.load()['accounts'][self.key])

class Routes(unittest.TestCase):
    def setUp(self):
        ChatTests.setUp(self);self.server=c.serve(self.app,0);self.port=self.server.server_port
        self.app.origin=f'http://127.0.0.1:{self.port}';self.app.host=f'127.0.0.1:{self.port}'
        self.server_thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.server_thread.start();self.cookie='';self.csrf=''
    def tearDown(self):self.server.shutdown();self.server.server_close();ChatTests.tearDown(self)
    def request(self,path,body=None,origin=None,csrf=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        headers={'Cookie':self.cookie}
        if body is not None:headers.update({'Origin':origin or self.app.origin,'Content-Type':'application/json','X-CSRF-Token':csrf or self.csrf})
        conn.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,headers)
        r=conn.getresponse();data=r.read();status=r.status;cookie=r.getheader('Set-Cookie');conn.close()
        return status,data,cookie
    def pair(self):
        status,raw,cookie=self.request('/api/pair',{'capability':self.app.state.pairing()});self.assertEqual(status,200)
        self.cookie=cookie.split(';')[0];self.csrf=json.loads(raw)['csrf'];self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Strict',cookie)
    def test_anonymous_routes_denied(self):
        for path in ('/api/status','/api/threads','/api/thread?id='+self.thread,'/api/memory'):
            status,body,_=self.request(path);self.assertEqual(status,401);self.assertNotIn(b'SYNTHETIC',body)
        status,_,_=self.request('/api/send',{'thread':self.thread,'request':str(uuid.uuid4()),'text':'bad'});self.assertEqual(status,401)
    def test_owner_csrf_origin_and_no_leak(self):
        self.pair();status,raw,_=self.request('/api/status');self.assertEqual(status,200);self.assertNotIn(b'SENTINEL',raw)
        status,_,_=self.request('/api/new',{},csrf='wrong');self.assertEqual(status,401)
        status,_,_=self.request('/api/new',{},origin='https://evil.invalid');self.assertEqual(status,409)
        status,raw,_=self.request('/api/thread?id='+self.thread);self.assertEqual(status,200);self.assertNotIn(b'SENTINEL',raw)
    def test_https_cookie(self):
        self.app.secure=True;self.app.cookie='__Host-homebase';self.pair();self.assertIn('__Host-homebase',self.cookie)
    def test_ui_safe_text_source(self):
        status,body,_=self.request('/app.js');self.assertEqual(status,200)
        self.assertNotIn(b'innerHTML',body);self.assertNotIn(b'localStorage',body)
    def test_dropped_browser_request_still_persists(self):
        self.pair();status,_,_=self.request('/api/send',{'thread':self.thread,'request':str(uuid.uuid4()),'text':'durable'})
        self.assertEqual(status,202);time.sleep(.1)
        status,raw,_=self.request('/api/thread?id='+self.thread);self.assertEqual(json.loads(raw)['messages'][-1]['status'],'completed')

if __name__=='__main__':unittest.main()
