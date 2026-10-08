import base64,json,tempfile,threading,unittest,uuid
from pathlib import Path
from unittest.mock import Mock
from voice_service import Voice,Budget,STT,SPEECH,fidelity,reading_text,chunks
from homebase_probe import ProbeError
class Socket:
 def __init__(self,text='Hello Wesley.',wrong=False,missing=False):
  self.sent=[];self.closed=False
  e=[{'type':'response.created','response':{'id':'r'}},{'type':'response.output_audio.delta','response_id':'r','delta':base64.b64encode(b'\0\0'*240).decode()}]
  if not missing:e.append({'type':'response.output_audio_transcript.done','response_id':'other' if wrong else 'r','transcript':text})
  e.append({'type':'response.done','response':{'id':'r','status':'completed'}});self.events=iter(e)
 def send(self,v):self.sent.append(json.loads(v))
 def recv(self):return json.dumps(next(self.events))
 def close(self):self.closed=True
class VoiceTests(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name);self.d=self.root/'voice';self.d.mkdir(mode=0o700)
  self.c={'enabled':True,'api_key':'sk-test-not-real','stt_model':STT,'speech_model':SPEECH,'monthly_authorized_cents':2000};self.save()
  self.thread=str(uuid.uuid4());self.request=str(uuid.uuid4());self.data={'id':self.thread,'draft':'','draft_revision':4,'messages':[{'role':'assistant','request':self.request,'text':'Hello Wesley.','status':'completed'}]}
  self.state=Mock();self.state.thread.side_effect=lambda _:self.data;self.http=Mock();self.http.post.return_value.status_code=200;self.http.post.return_value.json.return_value={'value':'ek_synthetic'}
  self.socket=Socket();self.voice=Voice(self.root,self.state,self.http,lambda *a,**k:self.socket)
 def save(self):p=self.d/'config.json';p.write_text(json.dumps(self.c));p.chmod(0o600)
 def speech(self,identity=None):return self.voice.render(self.thread,self.request,0,identity or str(uuid.uuid4()))
 def test_disabled_public_and_wrong_model_fail_closed(self):
  self.c['enabled']=False;self.save();self.assertEqual(self.voice.status(),{'enabled':False})
  self.c['enabled']=True;self.c['speech_model']='tts-1';self.save()
  with self.assertRaises(ProbeError):self.speech()
  self.c['speech_model']=SPEECH;self.save();(self.d/'config.json').chmod(0o644);self.assertEqual(self.voice.status(),{'enabled':False})
 def test_status_never_exposes_key_or_owner_text(self):
  value=json.dumps(self.voice.status());self.assertNotIn('sk-',value);self.assertNotIn('Wesley',value)
 def test_completed_only(self):
  self.data['messages'][0]['status']='stopped'
  with self.assertRaises(ProbeError):self.speech()
  self.assertFalse(self.socket.sent)
 def test_capture_manual_exact_model_and_concurrency(self):
  identity=str(uuid.uuid4());self.voice.start(self.thread,4,identity);s=self.http.post.call_args.kwargs['json']
  self.assertIsNone(s['session']['audio']['input']['turn_detection']);self.assertEqual(s['session']['audio']['input']['transcription']['model'],STT);self.assertEqual(s['expires_after']['seconds'],30)
  with self.assertRaises(ProbeError):self.voice.start(self.thread,4,str(uuid.uuid4()))
  with self.assertRaises(ProbeError):self.speech()
  self.voice.close(identity);self.assertEqual(self.voice.budget.total(),9)
 def test_typed_draft_and_revision_block_before_spend(self):
  for draft,revision in [('typed',4),('',3),('',True)]:
   self.data['draft']=draft
   with self.assertRaises(ProbeError):self.voice.start(self.thread,revision,str(uuid.uuid4()))
  self.http.post.assert_not_called();self.assertEqual(self.voice.budget.total(),0)
 def test_uncertain_issuance_no_replay(self):
  identity=str(uuid.uuid4());self.http.post.side_effect=TimeoutError()
  with self.assertRaises(ProbeError):self.voice.start(self.thread,4,identity)
  self.voice.last_issue=-100
  with self.assertRaises(ProbeError):self.voice.start(self.thread,4,identity)
  self.assertEqual(self.http.post.call_count,1);self.assertEqual(self.voice.budget.total(),9)
 def test_verified_audio_only_literal_no_context(self):
  result=self.speech();self.assertTrue(result['verified']);self.assertTrue(self.socket.closed);r=self.socket.sent[1]['response']
  self.assertEqual(r['input'],[]);self.assertEqual(r['conversation'],'none');self.assertEqual(r['tools'],[]);self.assertTrue(r['instructions'].endswith('Hello Wesley.'));self.assertEqual(self.voice.budget.total(),10)
 def test_paraphrase_missing_or_wrong_identity_never_returns_audio(self):
  for socket in [Socket('Hi Wesley.'),Socket(wrong=True),Socket(missing=True)]:
   self.voice.connector=lambda *a,**k:socket
   with self.assertRaises(ProbeError):self.speech()
   self.assertTrue(socket.closed)
  self.assertEqual(self.voice.budget.total(),30)
 def test_duplicate_verified_cache_and_mismatch(self):
  identity=str(uuid.uuid4());self.assertEqual(self.speech(identity),self.speech(identity));self.assertEqual(len(self.socket.sent),2);self.assertEqual(self.voice.budget.total(),10)
  self.data['messages'][0]['text']='Different answer.'
  with self.assertRaises(ProbeError):self.speech(identity)
 def test_budget_prevents_provider(self):
  self.voice.config();self.voice.budget.reserve('old','speech',1995,2000)
  with self.assertRaises(ProbeError):self.speech()
  self.assertFalse(self.socket.sent)
 def test_atomic_concurrent_budget(self):
  b=Budget(self.d/'other.sqlite3');results=[]
  def f():
   try:b.reserve(str(uuid.uuid4()),'speech',10,10);results.append(True)
   except ProbeError:results.append(False)
  ts=[threading.Thread(target=f) for _ in range(4)]
  for t in ts:t.start()
  for t in ts:t.join()
  self.assertEqual(results.count(True),1);self.assertEqual(b.total(),10)
 def test_fidelity_no_added_removed_reordered_words(self):
  self.assertTrue(fidelity('Hello, Wesley! It’s ready.',"Hello Wesley. It's ready!"))
  for s in ['Hello Wesley. It is ready.',"Wesley hello it's ready","Hello Wesley it's ready now"]:self.assertFalse(fidelity('Hello, Wesley! It’s ready.',s))
 def test_markdown_and_chunks(self):
  self.assertEqual(reading_text('# Heading\n- **First** [link](https://example.org)\n```python\nx = 1\n```'),'Heading First link x = 1')
  text=('One sentence. '*100).strip();parts=chunks(text);self.assertEqual(' '.join(parts),text);self.assertTrue(all(len(x)<=400 for x in parts))
  with self.assertRaises(ProbeError):chunks('x'*401)
