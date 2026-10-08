"""Synthetic browser fixture. No credentials or paid audio provider."""
import base64,json,os,sys
from pathlib import Path
import homebase_chat as c
import homebase_probe as p
from browser_server import BrowserHTTP
from chat_fixture import registration
class FakeVoice:
 def __init__(self,state):self.state=state;self.capture=None;self.calls=[]
 def status(self):return {'enabled':True}
 def start(self,thread,revision,identity):
  self.calls.append('capture');self.capture=identity;return {'value':'ek_synthetic','capture':identity,'max_seconds':300}
 def close(self,identity=None):
  self.calls.append('close');self.capture=None;return {'closed':True}
 def render(self,thread,request,index,operation):
  if self.capture:raise p.ProbeError('Microphone capture was not released.')
  row=next(m for m in self.state.thread(thread)['messages'] if m['role']=='assistant' and m['request']==request)
  if row['status']!='completed':raise p.ProbeError('Answer not completed.')
  self.calls.append('speech');return {'pcm':base64.b64encode(b'\0\0'*60000).decode(),'rate':24000,'index':index,'count':1,'verified':True}
if __name__=='__main__':
 os.umask(0o077);root=Path(sys.argv[1]);store=p.Store(root/'state');key=registration(store)
 app=c.App(store,key,'http://127.0.0.1:8769',http_factory=BrowserHTTP,recover=True);app.voice=FakeVoice(app.state)
 server=c.serve(app,0);app.origin=f'http://127.0.0.1:{server.server_port}';app.host=f'127.0.0.1:{server.server_port}'
 (root/'pairing.txt').write_text(app.state.pairing());print(json.dumps({'origin':app.origin}),flush=True)
 with server:server.serve_forever()
