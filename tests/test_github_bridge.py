"""Repository races and durable outcomes with synthetic Git objects, no credentials."""
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import uuid
from urllib.parse import parse_qs, unquote, urlsplit
from unittest.mock import patch
from github_bridge import Bridge, BridgeError, Context, Reads, safe_path, write_scope
from memory_store import State
from tool_journal import Journal

def blob(text):
    raw=text.encode();return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()

class FakeReads:
    def __init__(self,*args):
        self.heads={('homebase','main'):'a'*40,('other','main'):'a'*40}
        self.commits={'a'*40:{'docs/note.md':'original'}};self.parents={'a'*40:[]};self.modes={};self.calls=[]
    def get(self,path,ctx=None):
        self.calls.append(path)
        if ctx:ctx.check();ctx.http_calls+=1
        u=urlsplit(path);parts=unquote(u.path).split('/');q=parse_qs(u.query)
        if parts[1]=='installation':return {'total_count':2,'repositories':[{'name':n,'id':i,'owner':{'login':'aboriginalien'}} for n,i in [('homebase',1),('other',2)]]}
        repo=parts[3]
        if len(parts)==4:return {'id':1 if repo=='homebase' else 2,'owner':{'login':'aboriginalien'}}
        suffix='/'.join(parts[4:])
        if suffix.startswith('git/ref/heads/'):return {'object':{'sha':self.heads[(repo,suffix[14:])]}} if (repo,suffix[14:]) in self.heads else None
        if suffix.startswith('git/commits/'):
            head=suffix[12:]
            return {'tree':{'sha':head+':root'},'parents':[{'sha':p} for p in self.parents[head]]} if head in self.commits else None
        if suffix.startswith('git/trees/'):
            head,folder=suffix[10:].split(':',1);folder='' if folder=='root' else folder+'/'
            entries={}
            for path,text in self.commits[head].items():
                if not path.startswith(folder):continue
                name=path[len(folder):].split('/')[0];directory='/' in path[len(folder):]
                entries[name]={'path':name,'mode':self.modes.get(folder+name,'040000' if directory else '100644'),
                               'sha':head+':'+folder+name if directory else blob(text)}
            return {'tree':list(entries.values()),'truncated':False}
        if suffix.startswith('contents/'):
            path=suffix[9:];head=q['ref'][0];files=self.commits[head]
            if path in files:return {'type':'file','encoding':'base64','content':base64.b64encode(files[path].encode()).decode(),'sha':blob(files[path])}
            if not path:return [{'name':p.split('/')[0],'path':p.split('/')[0],'type':'dir','sha':'b'*40} for p in files]
            return None
        if suffix=='pulls':return getattr(self,'pr',[])
        raise AssertionError(path)
    def close(self):pass

class FakeTransport:
    protocol='2025-11-25';failed=False
    def __init__(self,reads):self.reads=reads;self.calls=[];self.after=None;self.fail=False
    def call(self,name,args):
        self.calls.append((name,args))
        r=self.reads
        if name=='create_branch':r.heads[(args['repo'],args['branch'])]=r.heads[(args['repo'],args['from_branch'])]
        if name in ('create_or_update_file','push_files'):
            old=r.heads[(args['repo'],args['branch'])];new=hashlib.sha1((old+json.dumps(args)).encode()).hexdigest()
            r.commits[new]=dict(r.commits[old]);r.parents[new]=[old]
            files=[args] if name=='create_or_update_file' else args['files']
            for file in files:r.commits[new][file['path']]=file['content']
            r.heads[(args['repo'],args['branch'])]=new
        if self.after:self.after()
        if self.fail:raise BridgeError('timeout')
        return {'content':[{'type':'text','text':'SYNTHETIC-URL-CREDENTIAL-MUST-NOT-REACH-MODEL'}],'isError':False}
    def close(self):self.failed=True

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name);root.chmod(0o700)
        self.state=State(root,'synthetic');self.thread=self.state.new_thread();self.turn=str(uuid.uuid4())
        self.message,_=self.state.begin(self.thread,self.turn,'Update homebase docs')
        self.journal=Journal(self.state);self.journal.begin(self.turn,self.thread,self.message,{},time.time()+180)
        self.ctx=Context(self.turn,'Update homebase docs',threading.Event(),time.monotonic()+180)
        self.bridge=Bridge(root/'github/config.json');self.rest=FakeReads();self.transport=FakeTransport(self.rest)
        self.bridge.rest=self.rest;self.bridge.transport=self.transport;self.bridge.configured=lambda:{'enabled':True}
        self.bridge.refresh_inventory();self.count=0
    def tearDown(self):self.temp.cleanup()
    def call(self,name,args):
        self.count+=1;return json.loads(self.bridge.execute(name,args,'call-'+str(self.count),self.ctx,self.journal))
    def read(self,path='docs/note.md',branch='main'):
        return self.call('get_file_contents',{'owner':'aboriginalien','repo':'homebase','path':path,'ref':branch})
    def write(self,content='changed',sha=None,branch='main',path='docs/note.md'):
        args={'owner':'aboriginalien','repo':'homebase','path':path,'branch':branch,'message':'Update synthetic note','content':content}
        if sha is not None:args['sha']=sha
        return self.call('create_or_update_file',args)
    def branch(self):
        name='homebase/test-'+self.turn[:8]
        value=self.call('create_branch',{'owner':'aboriginalien','repo':'homebase','branch':name,'from_branch':'main'})
        self.assertTrue(value['verified']);return name
    def test_read_is_verified_from_git_blob_and_strips_mcp_envelopes(self):
        result=self.read();self.assertEqual(result['content'],'original');self.assertEqual(result['sha'],blob('original'))
        self.assertNotIn('CREDENTIAL',json.dumps(result));self.assertEqual(self.journal.activity(self.message)[0]['evidence']['blob'],blob('original'))
    def test_existing_file_needs_fresh_branch_read_and_sha(self):
        self.assertEqual(self.write(sha=blob('original'))['error'],'conflict');self.assertEqual(len(self.transport.calls),0)
        self.read();value=self.write(content='new authorized edit',sha=blob('original'));self.assertTrue(value['verified'])
        self.assertEqual(self.rest.commits[value['commit']]['docs/note.md'],'new authorized edit')
    def test_stale_file_conflict_dispatches_no_write(self):
        self.read();self.rest.commits['b'*40]={'docs/note.md':'someone else'};self.rest.parents['b'*40]=['a'*40];self.rest.heads[('homebase','main')]='b'*40
        self.assertEqual(self.write(sha=blob('original'))['error'],'conflict');self.assertEqual(len(self.transport.calls),1)
    def test_new_file_and_task_branch_can_be_committed(self):
        branch=self.branch();result=self.read('docs/new.md',branch);self.assertFalse(result['exists'])
        self.assertTrue(self.write(path='docs/new.md',branch=branch)['verified'])
    def test_wrong_owner_uninstalled_repo_and_extra_argument_are_refused(self):
        for changes in ({'owner':'outside'},{'repo':'uninstalled'},{'url':'https://outside.invalid'}):
            result=self.call('get_file_contents',{'owner':'aboriginalien','repo':'homebase','path':'docs/note.md','ref':'main',**changes})
            self.assertIn('error',result)
        self.assertFalse(self.transport.calls)
    def test_symlink_file_or_parent_is_never_read_or_written(self):
        for path in ('docs','docs/note.md'):
            self.rest.modes={path:'120000'};self.ctx.trees={};self.ctx.failed.clear()
            self.assertEqual(self.read()['error'],'forbidden')
        self.assertFalse(self.transport.calls)
    def test_missing_write_authority_is_refused(self):
        self.read();self.ctx.query='Read homebase docs; do not update anything'
        self.assertEqual(self.write(sha=blob('original'))['error'],'authorization_required')
    def test_main_application_code_requires_new_branch(self):
        self.read('app.py');self.assertEqual(self.write(path='app.py')['error'],'authorization_required')
    def test_timeout_after_accepted_write_is_uncertain_and_never_replayed(self):
        self.read();self.transport.fail=True
        result=self.write(sha=blob('original'));self.assertEqual(result['error'],'uncertain_write')
        self.assertEqual(self.journal.activity(self.message)[-1]['status'],'uncertain')
        count=len(self.transport.calls);self.write(sha=blob('original'));self.assertEqual(len(self.transport.calls),count)
        self.transport.fail=False;self.ctx.failed.clear();self.read()
        self.assertEqual(self.journal.activity(self.message)[1]['status'],'succeeded')
    def test_stop_during_accepted_write_records_success_without_continuing(self):
        self.read();self.transport.after=lambda:(self.ctx.stop.set(),self.state.stop(self.thread))
        result=self.write(sha=blob('original'));self.assertTrue(result['verified'])
        self.assertEqual(self.state.thread(self.thread)['messages'][-1]['status'],'stopped')
        self.assertEqual(self.journal.activity(self.message)[-1]['status'],'succeeded')
    def test_error_mcp_read_cannot_be_reported_as_success(self):
        self.transport.call=lambda *a:{'isError':True,'content':[]}
        self.assertEqual(self.read()['error'],'unavailable');self.assertEqual(self.journal.activity(self.message)[0]['status'],'failed')
    def test_mcp_missing_file_error_is_confirmed_as_absence_before_creation(self):
        original=self.transport.call
        self.transport.call=lambda name,args: {'isError':True} if name=='get_file_contents' else original(name,args)
        self.assertFalse(self.read('docs/new.md')['exists']);self.assertTrue(self.write(path='docs/new.md')['verified'])
    def test_search_results_are_filtered_to_installed_owner_repository_ids(self):
        self.transport.call=lambda name,args:{'structuredContent':{'items':[
            {'full_name':'aboriginalien/homebase','id':1,'name':'homebase','private':True},
            {'full_name':'outsider/repo','id':1,'name':'repo'},
            {'full_name':'aboriginalien/new','id':99,'name':'new'}]}}
        value=self.call('search_repositories',{'query':'homebase'})
        self.assertEqual([i['full_name'] for i in value['items']],['aboriginalien/homebase'])
        self.assertTrue(value['search_index_not_inventory'])
    def test_search_escape_or_external_scope_is_refused(self):
        for query in ('homebase OR repo:outside/other','user:outside','-user:aboriginalien','repo:aboriginalien/uninstalled'):
            self.assertEqual(self.call('search_code',{'query':query})['error'],'forbidden')
        self.assertFalse(self.transport.calls)
    def test_official_code_search_repository_string_is_checked_and_retained(self):
        self.transport.call=lambda name,args:{'structuredContent':{'items':[{'name':'note.md','path':'docs/note.md','sha':blob('original'),'repository':'aboriginalien/homebase'},
                                                                          {'name':'outside','repository':'outside/other'}]}}
        value=self.call('search_code',{'query':'note'})
        self.assertEqual(len(value['items']),1);self.assertEqual(value['items'][0]['repository'],'aboriginalien/homebase')
    def test_expired_turn_or_stop_dispatches_no_operation(self):
        self.ctx.deadline=time.monotonic()-1;self.assertEqual(self.read()['error'],'timeout')
        self.ctx.deadline=time.monotonic()+180;self.ctx.stop.set();self.ctx.failed.clear()
        self.assertEqual(self.read()['error'],'cancelled');self.assertFalse(self.transport.calls)
    def test_result_budget_does_not_change_verified_write_to_uncertain(self):
        self.read();self.ctx.result_bytes=256*1024
        with self.assertRaises(BridgeError):self.write(sha=blob('original'))
        self.assertEqual(self.journal.activity(self.message)[-1]['status'],'succeeded')
    def test_unicode_escaping_cannot_exceed_result_bound(self):
        result=self.bridge.bounded({'data':'\"漢'*80000},self.ctx)
        self.assertLessEqual(len(result.encode()),64*1024);self.assertTrue(json.loads(result)['truncated'])
    def test_directory_and_write_secret_paths_are_refused(self):
        for path in ('../docs/a','/etc/passwd','docs/%2e%2e/a','.github/workflows/a.yml','.env','docs/key.pem','secrets/key','a\\b'):
            with self.assertRaises(BridgeError):safe_path(path)
    def test_owner_write_scope_does_not_come_from_question_or_other_repository(self):
        self.assertFalse(write_scope('How do I edit other?','other'));self.assertFalse(write_scope('Write a homebase note','other'))
        self.assertTrue(write_scope('Please update other docs','other'))
    def test_explicit_repo_file_and_main_denial_constrain_accepted_writes(self):
        query='Create a task branch in aboriginalien/homebase and write docs/tests/check.md. Do not modify main or any other file.'
        self.assertTrue(write_scope(query,'homebase','homebase/task-123','docs/tests/check.md'))
        self.assertFalse(write_scope(query,'homebase','main','docs/tests/check.md'))
        self.assertFalse(write_scope(query,'homebase','homebase/task-123','AGENTS.md'))
        self.assertFalse(write_scope('Update aboriginalien/other docs','homebase','main','docs/note.md'))
    def test_explicit_read_only_modify_denial_blocks_writes(self):
        self.assertFalse(write_scope('Read homebase files. Do not modify anything.','homebase','main','docs/note.md'))

if __name__=='__main__':unittest.main()
