"""Synthetic registration and provider only. Never reads a real OAuth session."""
import base64
import json
import time
import threading
from pathlib import Path
import homebase_probe as p

def b64(obj):return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b'=').decode()
def registration(store):
    client='oaiapp_synthetic';subject='synthetic-owner'
    now=int(time.time())
    record={'client_id':client,'issuer':p.ISSUER,'subject':subject,'scopes':p.SCOPES.split(),
            'expires_at':now+3600,'access_token':'SYNTHETIC-ACCESS-SENTINEL',
            'refresh_token':'SYNTHETIC-REFRESH-SENTINEL','id_token':b64({'alg':'RS256','kid':'fake'})+'.'+
            b64({'iss':p.ISSUER,'aud':client,'sub':subject,'iat':now,'exp':now+3600})+'.ZmFrZQ'}
    key=p.account_key(record)
    with store.locked():
        data=store.load();data['accounts'][key]=record;data['active']=key;store.save(data)
    return key

class Response:
    def __init__(self,events,delay=0):self.events=events;self.delay=delay;self.status_code=200
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def iter_lines(self,**kwargs):
        for e in self.events:
            time.sleep(self.delay)
            yield 'data: '+json.dumps(e)
            yield ''

class FakeHTTP:
    calls=[]
    mode='success'
    delay=0
    def json(self,method,url,**kw):
        if url.endswith('openid-configuration'):
            return {'issuer':p.ISSUER,'id_token_signing_alg_values_supported':['RS256'],
                'authorization_endpoint':p.ISSUER+'/authorize','token_endpoint':p.ISSUER+'/token',
                'revocation_endpoint':p.ISSUER+'/revoke','jwks_uri':p.ISSUER+'/jwks'}
        if url.endswith('/models'):
            return {'models':[{'slug':'gpt-5.6-sol','display_name':'GPT-5.6 Sol','visibility':'list'}]}
        raise AssertionError('Unexpected synthetic request')
    def request(self,method,url,**kw):
        if url.endswith('/revoke'):return Response([])
        assert url==p.RESOURCE+'/responses'
        type(self).calls.append(kw['json'])
        mode=type(self).mode
        if mode=='quota':raise p.ProbeError('Provider request failed (HTTP 429); synthetic')
        if mode=='auth':raise p.ProbeError('Provider request failed (HTTP 401); synthetic')
        text='Synthetic reply: '+kw['json']['input'][-1]['content']
        events=[{'type':'response.output_text.delta','delta':text[:20]},
                {'type':'response.output_text.delta','delta':text[20:]}]
        if mode=='failed':events.append({'type':'response.failed'})
        elif mode!='drop':
            events.append({'type':'response.completed','response':{'status':'completed',
                'model':'wrong-model' if mode=='wrong' else 'gpt-5.6-sol',
                'reasoning':{'effort':'high'},'service_tier':'default'}})
        return Response(events,type(self).delay)
