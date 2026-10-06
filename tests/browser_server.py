"""Local synthetic server fixture; not a provider or deployed-site test."""
import json
import os
from pathlib import Path
import sys
import homebase_chat as c
import homebase_probe as p
from chat_fixture import FakeHTTP,registration

class BrowserHTTP(FakeHTTP):
    def request(self,method,url,**kw):
        if url.endswith('/responses'):
            query=kw['json']['input'][-1]['content']
            type(self).mode='drop' if query.startswith('fail') else 'success'
            type(self).delay=.7 if query.startswith('slow') else .15
        return super().request(method,url,**kw)

if __name__=='__main__':
    os.umask(0o077);root=Path(sys.argv[1]);store=p.Store(root/'state');key=registration(store)
    app=c.App(store,key,'http://127.0.0.1:8769',http_factory=BrowserHTTP,recover=True)
    server=c.serve(app,0);app.origin=f'http://127.0.0.1:{server.server_port}';app.host=f'127.0.0.1:{server.server_port}'
    cap=root/'pairing.txt';cap.write_text(app.state.pairing());cap.chmod(0o600)
    print(json.dumps({'origin':app.origin,'account':key}),flush=True)
    with server:server.serve_forever()
