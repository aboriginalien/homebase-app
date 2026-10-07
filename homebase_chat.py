"""Single-owner Homebase chat: loopback server behind an HTTPS reverse proxy.

The only inference credential source is the reviewed SIWC Store. No API-key path.
"""
import argparse
import fcntl
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
import uuid
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import requests
import homebase_probe as provider
from memory_store import State

MODEL='gpt-5.6-sol'
STATIC=Path(__file__).resolve().parent / 'static'
MAX_BODY=16384

class App:
    def __init__(self, store, account, origin, http_factory=provider.HTTP, recover=False):
        u=urlsplit(origin)
        if (u.scheme not in ('http','https') or not u.hostname or u.path not in ('','/')
            or u.query or u.fragment or u.username or u.password
            or (u.scheme=='http' and u.hostname not in ('localhost','127.0.0.1'))):
            raise provider.ProbeError('Use an HTTPS public origin or explicit local loopback HTTP origin.')
        self.origin=origin.rstrip('/');self.host=u.netloc
        self.cookie='__Host-homebase' if u.scheme=='https' else 'homebase_local'
        self.secure=u.scheme=='https'
        self.store,self.account,self.http_factory=store,account,http_factory
        # Source and app-state parents must be canonical, not symlink aliases.
        if store.directory.absolute()!=store.directory.resolve():
            raise provider.ProbeError('State path must be canonical, without symlink components.')
        with store.locked():
            data=store.load();key,a=provider.select_account(data,account)
            provider.require_local_owner(a)
            provider.handoff_record(a,key,a.get('client_id'))
            if key!=account:raise provider.ProbeError('Select an explicit saved registration label.')
        self.state=State(store.directory,account,recover=recover)
        self.jobs={};self.guard=threading.Lock()

    def status(self):
        with self.store.locked():
            _,a=provider.select_account(self.store.load(),self.account)
            usable=not a.get('handoff') and provider.PLAN_SCOPES.issubset(a.get('scopes',[])) and bool(a.get('access_token'))
        return {'account':self.account,'connected':usable,'model':MODEL,'reasoning':'high',
                'speed':'standard','usage_url':provider.USAGE_URL}

    def send(self, thread, request, text):
        if not isinstance(text,str) or not text.strip() or len(text)>8000:
            raise provider.ProbeError('Message must contain 1–8000 characters.')
        try:uuid.UUID(request)
        except (ValueError,TypeError,AttributeError):raise provider.ProbeError('Invalid send identifier.') from None
        with self.guard:
            if self.jobs:
                existing=self.state.thread(thread)['messages']
                match=next((r for r in existing if r['request']==request and r['role']=='assistant'),None)
                if match:return {'message':match['id'],'started':False}
                raise provider.ProbeError('A reply is still stopping or working. Wait before sending.')
            identity,started=self.state.begin(thread,request,text)
            if started:
                stop=threading.Event();job={'stop':stop,'response':None,'thread':thread}
                self.jobs[identity]=job
                threading.Thread(target=self.work,args=(identity,thread,text,job),daemon=True).start()
            return {'message':identity,'started':started}

    def stop(self, thread):
        with self.guard:
            self.state.stop(thread)
            for job in self.jobs.values():
                if job['thread']==thread:
                    job['stop'].set()
                    # Do not synchronously close requests' reader from another thread.
                    # Status is durable immediately; worker checks between events/read timeout.

    def delete_thread(self, thread):
        try:uuid.UUID(thread)
        except (ValueError,TypeError,AttributeError):raise provider.ProbeError('Invalid thread identifier.') from None
        with self.guard:
            if any(job['thread']==thread for job in self.jobs.values()):
                raise provider.ProbeError('Stop the reply and wait for it to finish stopping before deleting this thread.')
            self.state.delete_thread(thread)
        return {'deleted':True}

    def memory_command(self, text, thread):
        if text.startswith('/remember '):
            parts=text[len('/remember '):].split('|',1)
            header=parts[0].strip().split(' ',1)
            if len(parts)!=2 or len(header)!=2:
                raise provider.ProbeError('Use /remember shared Title | fact or /remember thread Title | fact.')
            result=self.state.change('remember',scope=header[0],title=header[1],text=parts[1],thread=thread)
        elif text.startswith(('/correct ','/forget ')):
            command,_,rest=text.partition(' ');header,_,fact=rest.partition('|');parts=header.split()
            if len(parts)!=2 or not parts[1].isdigit():
                raise provider.ProbeError('Use /correct ID REVISION | fact or /forget ID REVISION.')
            result=self.state.change(command[1:],identity=parts[0],revision=int(parts[1]),text=fact,thread=thread)
        else:return None
        return 'Memory '+('forgotten' if text.startswith('/forget ') else 'saved')+f": {result['id']} · revision {result['revision']}."

    def work(self, identity, thread, query, job):
        output=''
        try:
            if job['stop'].is_set():return
            command=self.memory_command(query,thread)
            if command is not None:
                self.state.finish(identity,command);return
            context=self.state.context(thread,query)
            self.state.references(identity,context['references'])
            http=self.http_factory()
            # Same lock held across refresh + inference as the reviewed CLI. One refresh owner.
            with self.store.locked():
                data=self.store.load();key,a=provider.select_account(data,self.account)
                provider.require_local_owner(a)
                a=provider.renew(http,provider.discovery(http),self.store,data,key)
                headers={'Authorization':'Bearer '+a['access_token']}
                model=provider.choose_model(http.json('GET',provider.RESOURCE+'/models',headers=headers),MODEL)
                payload={'model':model,'input':context['input'],'instructions':context['instructions'],
                         'store':False,'stream':True,'reasoning':{'effort':'high'},'service_tier':'default'}
                if job['stop'].is_set():return
                with http.request('POST',provider.RESOURCE+'/responses',headers=headers,json=payload,stream=True) as response:
                    job['response']=response
                    for e in provider.sse_events(response.iter_lines(chunk_size=1)):
                        if job['stop'].is_set():return
                        kind=e.get('type')
                        if kind=='response.output_text.delta':
                            delta=e.get('delta')
                            if not isinstance(delta,str) or len(output)+len(delta)>32000:
                                raise provider.ProbeError('Reply exceeded the text limit; incomplete.')
                            output+=delta
                            if not self.state.update(identity,output):return
                        elif kind in ('response.failed','response.incomplete','error'):
                            raise provider.ProbeError('Provider reply failed or is incomplete. Check ChatGPT usage and authorization.')
                        elif kind=='response.completed':
                            r=e.get('response',{})
                            if (r.get('status')!='completed' or r.get('model')!=model
                                or r.get('reasoning',{}).get('effort')!='high' or r.get('service_tier')!='default'):
                                raise provider.ProbeError('Returned model/high/standard settings were not verified; reply is incomplete.')
                            if not output.strip():raise provider.ProbeError('Completed response had no supported text.')
                            self.state.finish(identity,output);return
                    raise provider.ProbeError('Stream ended without response.completed; reply is incomplete.')
        except provider.ProbeError as e:
            message=str(e)
            if 'HTTP 429' in message:message='ChatGPT-plan quota/rate limit reached. Check usage settings; your messages are saved.'
            elif 'HTTP 401' in message or 'HTTP 403' in message:message='ChatGPT authorization expired, revoked or unavailable. Messages are saved; reconnect through the operator helper.'
            self.state.finish(identity,output,'incomplete',message)
        except (requests.RequestException,OSError,ValueError,TypeError,KeyError):
            self.state.finish(identity,output,'incomplete','Connection or storage failed; reply is incomplete. Your submitted message is saved.')
        except Exception:
            self.state.finish(identity,output,'incomplete','Reply could not complete; preserve private state before retry.')
        finally:
            with self.guard:self.jobs.pop(identity,None)

    def signout(self):
        # Block every browser immediately, then stop active inference. Never grant via anonymous routes.
        self.state.revoke_sessions()
        with self.guard:
            busy=bool(self.jobs)
            for job in self.jobs.values():
                job['stop'].set();self.state.stop(job['thread'])
        if busy:
            return 'Browser sessions removed. Provider revocation is pending: after stopping completes, run the protected CLI signout or disconnect Homebase in ChatGPT settings.'
        with self.store.locked():
            data=self.store.load();key,_=provider.select_account(data,self.account);http=self.http_factory()
            try:confirmed=provider.signout(http,provider.discovery(http),self.store,data,key)
            except provider.ProbeError:
                a=data['accounts'][key]
                for f in provider.TOKEN_FIELDS:a.pop(f,None)
                a['scopes']=[];a['expires_at']=0;self.store.save(data);confirmed=False
        return 'Signed out locally. '+('Provider revocation confirmed.' if confirmed else 'Provider revocation unconfirmed; disconnect Homebase in ChatGPT settings.')

class Handler(BaseHTTPRequestHandler):
    server_version='Homebase';sys_version=''
    def log_message(self,*args):pass  # No URLs, pairing fragments, headers, content or provider details.
    def setup(self):super().setup();self.connection.settimeout(10)
    @property
    def app(self):return self.server.app
    def reply(self,status,body,kind='application/json',cookie=None):
        raw=json.dumps(body).encode() if kind=='application/json' else body
        self.send_response(status)
        self.send_header('Content-Type',kind+'; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if cookie:self.send_header('Set-Cookie',cookie)
        self.end_headers()
        try:self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError):pass
    def allowed(self):
        if self.headers.get_all('Host')!=[self.app.host]:
            raise provider.ProbeError('Invalid request host.')
        origin=self.headers.get_all('Origin')
        if origin is not None and origin!=[self.app.origin]:
            raise provider.ProbeError('Invalid request origin.')
    def auth(self,write=False):
        try:
            c=SimpleCookie();c.load(self.headers.get('Cookie',''))
            token=c[self.app.cookie].value if self.app.cookie in c else ''
        except CookieError:token=''
        csrf=self.app.state.session(token)
        if not csrf:raise PermissionError()
        if write and (self.headers.get_all('Origin')!=[self.app.origin]
                      or self.headers.get_all('X-CSRF-Token')!=[csrf]):
            raise PermissionError()
        return csrf
    def do_GET(self):
        try:
            self.allowed();u=urlsplit(self.path)
            public={'/':('index.html','text/html'),'/app.js':('app.js','text/javascript'),'/style.css':('style.css','text/css')}
            if u.path in public:
                name,kind=public[u.path];return self.reply(200,(STATIC/name).read_bytes(),kind)
            if u.path=='/health':return self.reply(200,{'status':'ok','inference':'not tested by health'})
            csrf=self.auth()
            if u.path=='/api/status':
                # Status must remain usable while the provider lock is held by a stream.
                return self.reply(200,{'account':self.app.account,'model':MODEL,'reasoning':'high',
                    'speed':'standard','usage_url':provider.USAGE_URL,'csrf':csrf})
            if u.path=='/api/threads':return self.reply(200,self.app.state.threads())
            args=parse_qs(u.query)
            thread=args.get('id',[''])[0]
            if u.path=='/api/thread':return self.reply(200,self.app.state.thread(thread))
            if u.path=='/api/memory':return self.reply(200,self.app.state.memory(thread))
            self.reply(404,{'error':'Route does not exist.'})
        except PermissionError:self.reply(401,{'error':'Open your private device-pairing link to connect.'})
        except provider.ProbeError as e:self.reply(400,{'error':str(e)})
        except Exception:self.reply(500,{'error':'Private operation failed; preserve state before retry.'})
    def do_POST(self):
        try:
            self.allowed()
            if self.headers.get_all('Origin')!=[self.app.origin]:raise PermissionError()
            if self.headers.get_all('Content-Type')!=['application/json']:raise provider.ProbeError('JSON content type required.')
            length=self.headers.get_all('Content-Length')
            if not length or len(length)!=1 or not length[0].isdigit() or not 0<int(length[0])<=MAX_BODY:
                raise provider.ProbeError('Invalid request size.')
            if self.headers.get('Transfer-Encoding'):raise provider.ProbeError('Transfer encoding is not supported.')
            data=json.loads(self.rfile.read(int(length[0])))
            if not isinstance(data,dict):raise provider.ProbeError('Expected a JSON object.')
            path=urlsplit(self.path).path
            if path=='/api/pair':
                capability=data.get('capability','')
                if not isinstance(capability,str) or not 20<=len(capability)<=128:raise provider.ProbeError('Invalid pairing link.')
                # Pairing a signed-out/revoked local grant must not reopen private records.
                if not self.app.status()['connected']:raise provider.ProbeError('Operator must reconnect the saved ChatGPT registration first.')
                token,csrf=self.app.state.pair(capability)
                cookie=f'{self.app.cookie}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=604800'
                if self.app.secure:cookie+='; Secure'
                return self.reply(200,{'csrf':csrf},cookie=cookie)
            self.auth(write=True)
            if path=='/api/new':return self.reply(200,{'id':self.app.state.new_thread()})
            if path=='/api/delete-thread':return self.reply(200,self.app.delete_thread(data.get('thread')))
            if path=='/api/send':return self.reply(202,self.app.send(data.get('thread'),data.get('request'),data.get('text')))
            if path=='/api/draft':self.app.state.draft(data.get('thread'),checked_text(data.get('text'),8000));return self.reply(200,{'saved':True})
            if path=='/api/stop':self.app.stop(data.get('thread'));return self.reply(200,{'stopped':True})
            if path=='/api/memory':
                result=self.app.state.change(data.get('action'),title=data.get('title',''),text=checked_text(data.get('text',''),1500),
                    scope=data.get('scope','shared'),thread=data.get('thread'),identity=data.get('id'),revision=data.get('revision'))
                return self.reply(200,result)
            if path=='/api/signout':
                result=self.app.signout()
                return self.reply(200,{'message':result},cookie=f'{self.app.cookie}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict'+('; Secure' if self.app.secure else ''))
            self.reply(404,{'error':'Route does not exist.'})
        except PermissionError:self.reply(401,{'error':'Owner session or request verification required.'})
        except provider.ProbeError as e:self.reply(409,{'error':str(e)})
        except (ValueError,TypeError):self.reply(400,{'error':'Invalid request format.'})
        except Exception:self.reply(500,{'error':'Private operation failed; preserve state before retry.'})

def checked_text(value,limit):
    if not isinstance(value,str) or len(value)>limit:raise provider.ProbeError('Text exceeds the supported size.')
    return value

def serve(app,port):
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler);server.app=app
    return server

def run(argv=None):
    p=argparse.ArgumentParser(description='Single-owner ChatGPT-plan chat; no API-key fallback.')
    p.add_argument('--data-dir',type=Path,required=True)
    p.add_argument('--account',required=True)
    p.add_argument('--origin',required=True,help='Approved exact HTTPS public origin; HTTP only on loopback')
    sub=p.add_subparsers(dest='command',required=True)
    s=sub.add_parser('serve');s.add_argument('--port',type=int,required=True)
    sub.add_parser('pair',help='Print one-use private link ONLY in a protected operator terminal')
    sub.add_parser('revoke-browsers',help='Remove all browser sessions; retain provider registration')
    seed=sub.add_parser('seed');seed.add_argument('--file',type=Path,required=True)
    args=p.parse_args(argv);os.umask(0o077)
    store=provider.Store(args.data_dir)
    if args.command=='serve':
        lock=store.directory/'chat-server.lock'
        fd=os.open(lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600);provider.check_private(lock)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise provider.ProbeError('Another chat server owns this private state.') from None
            app=App(store,args.account,args.origin,recover=True)
            with serve(app,args.port) as server:
                print('Homebase loopback listener ready. Health does not verify provider inference.',flush=True)
                server.serve_forever()
        finally:os.close(fd)
    else:
        app=App(store,args.account,args.origin)
        if args.command=='pair':print(app.origin+'/#pair='+app.state.pairing())
        elif args.command=='revoke-browsers':app.state.revoke_sessions();print('Browser sessions revoked.')
        elif args.command=='seed':
            provider.check_private(args.file)
            if args.file.stat().st_size>32768:raise provider.ProbeError('Private seed is oversized.')
            app.state.seed(json.loads(args.file.read_text()));print('Private seed installed atomically.')
    return 0

if __name__=='__main__':
    try:raise SystemExit(run())
    except provider.ProbeError as e:print('Homebase:',str(e),file=sys.stderr);raise SystemExit(1)
    except KeyboardInterrupt:raise SystemExit(130)
    except Exception:print('Homebase: operation failed; preserve private state. No success confirmed.',file=sys.stderr);raise SystemExit(1)
