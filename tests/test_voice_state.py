"""Voice acceptance uses SQLite transactions; only synthetic owner data."""
import json,sys,tempfile,threading,unittest,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from memory_store import State
from homebase_probe import ProbeError
class VoiceStateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.state=State(self.temp.name,'synthetic-owner')
        self.thread=self.state.new_thread()
    def begin(self,request=None,revision=None,text='spoken request'):
        return self.state.begin(self.thread,request or str(uuid.uuid4()),text,source='voice',draft_revision=self.state.thread(self.thread)['draft_revision'] if revision is None else revision)
    def test_voice_preserves_new_typed_draft_even_identical_to_voice_request(self):
        mid,_=self.begin();self.assertEqual(self.state.thread(self.thread)['draft'],'')
        self.state.draft(self.thread,'spoken request');self.state.finish(mid,'answer')
        self.assertEqual(self.state.thread(self.thread)['draft'],'spoken request')
        self.assertEqual({m['source'] for m in self.state.thread(self.thread)['messages']},{'voice'})
    def test_nonempty_draft_rejects_voice_without_message_write(self):
        revision=self.state.draft(self.thread,'unfinished')
        with self.assertRaises(ProbeError):self.begin(revision=revision)
        data=self.state.thread(self.thread);self.assertEqual(data['draft'],'unfinished');self.assertEqual(data['messages'],[])
    def test_empty_again_does_not_hide_draft_change(self):
        revision=self.state.thread(self.thread)['draft_revision']
        self.state.draft(self.thread,'temporary');self.state.draft(self.thread,'')
        with self.assertRaises(ProbeError):self.begin(revision=revision)
        self.assertEqual(self.state.thread(self.thread)['messages'],[])
    def test_duplicate_frozen_payload_reconciles_after_draft_changed(self):
        request=str(uuid.uuid4());revision=self.state.thread(self.thread)['draft_revision']
        mid,_=self.begin(request,revision);self.state.draft(self.thread,'new unsent')
        self.assertEqual(self.begin(request,revision),(mid,False))
        self.assertEqual(len(self.state.thread(self.thread)['messages']),2)
    def test_uuid_cannot_rebind_text_source_or_revision(self):
        request=str(uuid.uuid4());mid,_=self.begin(request,0)
        for args in [dict(source='voice',draft_revision=0,text='changed'),dict(source='typed',text='spoken request'),dict(source='voice',draft_revision=1,text='spoken request')]:
            with self.assertRaises(ProbeError):self.state.begin(self.thread,request,**args)
        self.assertEqual(len(self.state.thread(self.thread)['messages']),2)
    def test_typed_completion_keeps_same_text_edited_and_restored(self):
        mid,_=self.state.begin(self.thread,str(uuid.uuid4()),'submitted')
        self.state.draft(self.thread,'new');self.state.draft(self.thread,'submitted')
        self.state.finish(mid,'answer')
        self.assertEqual(self.state.thread(self.thread)['draft'],'submitted')
    def test_typed_completion_normally_clears_its_original_draft(self):
        mid,_=self.state.begin(self.thread,str(uuid.uuid4()),'submitted')
        self.state.finish(mid,'answer');self.assertEqual(self.state.thread(self.thread)['draft'],'')
    def test_concurrent_draft_write_never_gets_overwritten_by_voice_acceptance(self):
        barrier=threading.Barrier(2);outcomes=[];revision=0
        def capture():
            barrier.wait()
            try:outcomes.append(self.begin(revision=revision))
            except ProbeError:outcomes.append('rejected')
        worker=threading.Thread(target=capture);worker.start();barrier.wait()
        self.state.draft(self.thread,'keep this');worker.join()
        self.assertEqual(self.state.thread(self.thread)['draft'],'keep this')
        data=self.state.thread(self.thread)
        if data['messages']:self.state.finish(data['messages'][-1]['id'],'answer')
        self.assertEqual(self.state.thread(self.thread)['draft'],'keep this')
    def test_invalid_revision_or_source_cannot_submit(self):
        for revision in [None,True,1.5,-1,'0']:
            with self.assertRaises(ProbeError):self.state.begin(self.thread,str(uuid.uuid4()),'voice',source='voice',draft_revision=revision)
        with self.assertRaises(ProbeError):self.state.begin(self.thread,'request','voice',source='unknown')
    def test_reopen_preserves_owner_memory_draft_and_session(self):
        self.state.draft(self.thread,'keep unsent')
        self.state.change('remember',scope='shared',title='Test',text='synthetic fact')
        token,csrf=self.state.pair(self.state.pairing())
        before=self.state.thread(self.thread)
        reopened=State(self.temp.name,'synthetic-owner')
        self.assertEqual(reopened.thread(self.thread),before)
        self.assertEqual(reopened.session(token),csrf)
        self.assertEqual(reopened.memory()[0]['text'],'synthetic fact')
if __name__=='__main__':unittest.main()

