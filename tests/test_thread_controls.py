"""Synthetic durable-state/API tests; no owner state or provider account used."""
import json
import threading
import unittest
import uuid
import homebase_probe as p
from memory_store import State
import test_chat as fixture

class Deletion(unittest.TestCase):
    setUp=fixture.ChatTests.setUp
    tearDown=fixture.ChatTests.tearDown
    wait=fixture.ChatTests.wait
    send=fixture.ChatTests.send
    def test_deletion_survives_reopen_and_preserves_other_data(self):
        self.wait(self.send('erase this conversation'))
        s=self.app.state
        s.draft(self.thread,'erase this draft');s.context(self.thread,'make summary')
        local=s.change('remember',title='Local',text='erase this note',scope='thread',thread=self.thread)
        shared=s.change('remember',title='Shared',text='keep shared fact')
        other=s.new_thread();s.draft(other,'keep other draft')
        note=s.change('remember',title='Other',text='keep other note',scope='thread',thread=other)
        self.assertEqual(self.app.delete_thread(self.thread),{'deleted':True})
        reopened=State(self.store.directory,self.key)
        self.assertEqual([r['id'] for r in reopened.threads()],[other])
        self.assertEqual(reopened.thread(other)['draft'],'keep other draft')
        self.assertEqual({r['id'] for r in reopened.memory(other)},{shared['id'],note['id']})
        with reopened.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM messages WHERE thread=?',(self.thread,)).fetchone()[0],0)
            self.assertIsNone(db.execute('SELECT 1 FROM memory WHERE id=?',(local['id'],)).fetchone())
        with self.assertRaises(p.ProbeError):reopened.thread(self.thread)
        with self.assertRaises(p.ProbeError):self.app.delete_thread(self.thread)

    def test_working_and_stopping_jobs_refuse_deletion(self):
        with self.app.guard:
            self.app.jobs[777]={'thread':self.thread,'stop':threading.Event(),'response':None}
        try:
            with self.assertRaises(p.ProbeError):self.app.delete_thread(self.thread)
            self.app.stop(self.thread)
            with self.assertRaises(p.ProbeError):self.app.delete_thread(self.thread)
            self.assertEqual(self.app.state.thread(self.thread)['id'],self.thread)
        finally:self.app.jobs.clear()
        self.app.delete_thread(self.thread)

    def test_orphan_working_state_refuses_deletion(self):
        mid,_=self.app.state.begin(self.thread,str(uuid.uuid4()),'interrupted')
        with self.assertRaises(p.ProbeError):self.app.delete_thread(self.thread)
        self.app.state.stop(self.thread);self.app.delete_thread(self.thread)

    def test_other_active_job_does_not_block_background_deletion(self):
        other=self.app.state.new_thread()
        self.app.jobs[777]={'thread':other,'stop':threading.Event(),'response':None}
        try:
            self.app.delete_thread(self.thread)
            self.assertEqual(self.app.state.thread(other)['id'],other)
        finally:self.app.jobs.clear()

    def test_transaction_rolls_back_on_partial_failure(self):
        self.wait(self.send('keep on failure'))
        note=self.app.state.change('remember',title='Local',text='also keep',scope='thread',thread=self.thread)
        with self.app.state.connect() as db:
            db.execute("CREATE TRIGGER refuse_thread_delete BEFORE DELETE ON threads BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        with self.assertRaises(Exception):self.app.delete_thread(self.thread)
        self.assertEqual(len(self.app.state.thread(self.thread)['messages']),2)
        self.assertEqual(self.app.state.memory(self.thread)[0]['id'],note['id'])

    def test_delete_serializes_against_send_guard(self):
        started=threading.Event();done=threading.Event()
        def remove():
            started.set();self.app.delete_thread(self.thread);done.set()
        with self.app.guard:
            worker=threading.Thread(target=remove);worker.start();self.assertTrue(started.wait(1))
            self.assertFalse(done.wait(.05))
        worker.join(2);self.assertTrue(done.is_set())

    def test_invalid_ids_leave_all_data_intact(self):
        for identity in (None,[],{},'not-a-thread',str(uuid.uuid4())):
            with self.assertRaises(p.ProbeError):self.app.delete_thread(identity)
        self.assertEqual(len(self.app.state.threads()),1)

class DeleteRoutes(unittest.TestCase):
    setUp=fixture.Routes.setUp
    tearDown=fixture.Routes.tearDown
    request=fixture.Routes.request
    pair=fixture.Routes.pair
    def test_delete_requires_session_origin_and_csrf(self):
        self.assertEqual(self.request('/api/delete-thread',{'thread':self.thread})[0],401)
        self.pair()
        self.assertEqual(self.request('/api/delete-thread',{'thread':self.thread},csrf='wrong')[0],401)
        self.assertEqual(self.request('/api/delete-thread',{'thread':self.thread},origin='https://evil.invalid')[0],409)
        self.assertEqual(len(self.app.state.threads()),1)
        status,raw,_=self.request('/api/delete-thread',{'thread':self.thread})
        self.assertEqual(status,200);self.assertEqual(json.loads(raw),{'deleted':True})
        self.assertEqual(self.request('/api/thread?id='+self.thread)[0],400)
        self.assertEqual(self.request('/api/delete-thread',{'thread':self.thread})[0],409)
        self.assertEqual(self.request('/api/threads')[0],200)

if __name__=='__main__':unittest.main()
