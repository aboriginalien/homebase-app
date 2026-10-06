"""Synthetic offline security/stream fixtures. Never uses a real account."""
import base64
import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import requests

import homebase_probe as p


def document():
    return {"issuer": p.ISSUER, "id_token_signing_alg_values_supported": ["RS256"],
            "authorization_endpoint": p.ISSUER + "/api/accounts/authorize",
            "token_endpoint": p.ISSUER + "/api/accounts/oauth/token",
            "jwks_uri": p.ISSUER + "/.well-known/jwks.json",
            "revocation_endpoint": p.ISSUER + "/api/accounts/oauth/revoke"}


def complete(model=p.MODEL, **changes):
    r = {"status": "completed", "model": model, "reasoning": {"effort": "high"}, "service_tier": "default"}
    r.update(changes)
    return {"type": "response.completed", "response": r}


def events(terminal=None):
    return [{"type": "response.output_text.delta", "delta": "Hello, "},
            {"type": "response.output_text.delta", "delta": "world!"}, terminal or complete()]


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.bad_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key()))
        cls.jwk.update(kid="fixture", use="sig", alg="RS256")

    def token(self, changes=None, key=None):
        now = int(time.time())
        c = {"iss": p.ISSUER, "sub": "synthetic-subject", "aud": "oaiapp_fixture",
             "iat": now, "exp": now + 3600, "nonce": "synthetic-nonce"}
        c.update(changes or {})
        return jwt.encode(c, key or self.key, algorithm="RS256", headers={"kid": "fixture"})

    def validate(self, token):
        return p.identity(token, {"keys": [self.jwk]}, "oaiapp_fixture", "synthetic-nonce")

    def test_valid_signed_identity(self):
        self.assertEqual(self.validate(self.token())["sub"], "synthetic-subject")

    def test_invalid_claims(self):
        bad = [{"iss": "https://wrong.invalid"}, {"aud": "wrong-client"},
               {"nonce": "wrong-nonce"}, {"exp": int(time.time()) - 10},
               {"iat": int(time.time()) + 3600}, {"sub": ""},
               {"aud": ["oaiapp_fixture", "other"], "azp": "other"},
               {"azp": "wrong-client"}]
        for change in bad:
            with self.subTest(change=change), self.assertRaises(p.ProbeError):
                self.validate(self.token(change))

    def test_missing_required_claims(self):
        for field in ["iss", "aud", "sub", "iat", "exp", "nonce"]:
            c = jwt.decode(self.token(), options={"verify_signature": False})
            del c[field]
            token = jwt.encode(c, self.key, algorithm="RS256", headers={"kid": "fixture"})
            with self.subTest(field=field), self.assertRaises(p.ProbeError):
                self.validate(token)

    def test_signature_and_key_rejected(self):
        with self.assertRaises(p.ProbeError):
            self.validate(self.token(key=self.bad_key))
        with self.assertRaises(p.ProbeError):
            p.identity(self.token(), {"keys": []}, "oaiapp_fixture", "synthetic-nonce")
        with self.assertRaises(p.ProbeError):
            p.identity(self.token(), {"keys": [self.jwk, self.jwk]}, "oaiapp_fixture", "synthetic-nonce")
        with self.assertRaises(p.ProbeError):
            self.validate(jwt.encode({"sub": "fixture"}, "", algorithm="none"))

    def test_account_switch_rejected(self):
        with self.assertRaises(p.ProbeError):
            p.identity(self.token(), {"keys": [self.jwk]}, "oaiapp_fixture", subject="other")

    def test_discovery_trust_boundaries(self):
        for field, value in [("issuer", "https://wrong.invalid"),
                             ("jwks_uri", "http://auth.openai.com/key"),
                             ("token_endpoint", "https://wrong.invalid/token"),
                             ("authorization_endpoint", "https://user@auth.openai.com/authorize"),
                             ("revocation_endpoint", "https://auth.openai.com:444/revoke")]:
            d = document(); d[field] = value
            http = Mock(); http.json.return_value = d
            with self.subTest(field=field), self.assertRaises(p.ProbeError):
                p.discovery(http)

    def test_dynamic_pkce_and_reauthorization(self):
        attempt = p.pending_attempt("urn:uuid:fixture", 1455)
        q = parse_qs(urlsplit(p.authorization_url(document(), attempt)).query)
        self.assertEqual(q["agent_name_hint"], ["Homebase"])
        self.assertEqual(q["client_id"], ["dynamic_agent_client"])
        self.assertEqual(q["scope"], [p.SCOPES])
        self.assertEqual(q["resource"], [p.RESOURCE])
        self.assertEqual(q["code_challenge_method"], ["S256"])
        self.assertGreaterEqual(len(attempt["verifier"]), 43)
        digest = base64.urlsafe_b64encode(__import__('hashlib').sha256(attempt['verifier'].encode()).digest()).rstrip(b'=')
        self.assertEqual(q["code_challenge"], [digest.decode()])
        again = p.pending_attempt("urn:uuid:fixture", 54321, {"client_id": "oaiapp_fixture"})
        self.assertNotEqual(attempt["state"], again["state"])
        self.assertNotEqual(attempt["nonce"], again["nonce"])
        self.assertNotEqual(attempt["verifier"], again["verifier"])
        q = parse_qs(urlsplit(p.authorization_url(document(), again, {"id_token": "synthetic-private-token"})).query)
        self.assertNotIn("agent_name_hint", q)
        self.assertEqual(q["client_id"], ["oaiapp_fixture"])

    def test_callback_boundary(self):
        a = p.pending_attempt("urn:uuid:fixture", 1455)
        query = {"state": a["state"], "code": "synthetic-code", "client_id": "oaiapp_fixture"}
        good = "/auth/callback?" + urlencode(query)
        self.assertEqual(p.callback(good, "127.0.0.1:1455", a), ("synthetic-code", "oaiapp_fixture"))
        bad = [(good, "localhost:1455"), (good, "127.0.0.1:54321"),
               (good.replace('/auth/callback', '/callback'), "127.0.0.1:1455"),
               ("https://wrong.invalid" + good, "127.0.0.1:1455"),
               (good.replace(a["state"], "wrong"), "127.0.0.1:1455"),
               (good + "&state=duplicate", "127.0.0.1:1455"),
               (good.replace('oaiapp_fixture', 'dynamic_agent_client'), "127.0.0.1:1455")]
        for target, host in bad:
            with self.subTest(target=target), self.assertRaises(p.ProbeError):
                p.callback(target, host, a)
        a["expires_at"] = 0
        with self.assertRaises(p.ProbeError):
            p.callback(good, "127.0.0.1:1455", a)

    def test_declined_and_wrong_returning_client(self):
        a = p.pending_attempt("urn:uuid:fixture", 1455, {"client_id": "oaiapp_fixture"})
        for q in [{"state": a["state"], "error": "access_denied"},
                  {"state": a["state"], "code": "fixture", "client_id": "oaiapp_other"}]:
            with self.subTest(q=q), self.assertRaises(p.ProbeError):
                p.callback('/auth/callback?' + urlencode(q), '127.0.0.1:1455', a)

    def grant(self):
        return {"scope": p.SCOPES, "token_type": "Bearer", "expires_in": 3600,
                "access_token": "synthetic-access", "refresh_token": "synthetic-refresh",
                "id_token": self.token()}

    def test_plan_grants_required(self):
        for scope in ["openid profile email", "openid resource.invoke", "", None]:
            t = self.grant(); t["scope"] = scope
            with self.subTest(scope=scope), self.assertRaises(p.ProbeError):
                p.token_record(t, 'oaiapp_fixture', {"sub": 'fixture'})

    def test_invalid_token_response(self):
        for field, value in [("token_type", "Basic"), ("expires_in", 0),
                             ("expires_in", True), ("access_token", ""), ("refresh_token", None)]:
            t = self.grant(); t[field] = value
            with self.subTest(field=field), self.assertRaises(p.ProbeError):
                p.token_record(t, 'oaiapp_fixture', {"sub": 'fixture'})

    def test_exchange_issued_id_and_exact_callback(self):
        a = p.pending_attempt('urn:uuid:fixture', 1455); a['nonce'] = 'synthetic-nonce'
        http = Mock(); http.json.side_effect = [self.grant(), {"keys": [self.jwk]}]
        r = p.register(http, document(), a, 'synthetic-code', 'oaiapp_fixture')
        self.assertEqual(r['client_id'], 'oaiapp_fixture')
        form = http.json.call_args_list[0].kwargs['data']
        self.assertEqual(form['client_id'], 'oaiapp_fixture')
        self.assertEqual(form['redirect_uri'], a['redirect_uri'])
        self.assertEqual(form['code_verifier'], a['verifier'])
        self.assertNotIn('client_secret', form)

    def test_loopback_listener_and_callback_logs_redacted(self):
        threads = []
        def open_browser(url):
            q = parse_qs(urlsplit(url).query)
            target = urlsplit(q['redirect_uri'][0])
            def browser():
                c = http.client.HTTPConnection('127.0.0.1', target.port, timeout=5)
                query = urlencode({'state': q['state'][0], 'code': 'SYNTHETIC-SECRET-CODE', 'client_id': 'oaiapp_fixture'})
                c.request('GET', target.path + '?' + query)
                reply = c.getresponse(); reply.read(); c.close()
            t = threading.Thread(target=browser); threads.append(t); t.start(); return True
        logs = io.StringIO()
        with patch.object(p.webbrowser, 'open', side_effect=open_browser), patch.object(p, 'register', return_value={'fixture': True}), contextlib.redirect_stdout(logs), contextlib.redirect_stderr(logs):
            self.assertEqual(p.local_login(Mock(), document(), 'urn:uuid:fixture'), {'fixture': True})
        for t in threads: t.join(timeout=5); self.assertFalse(t.is_alive())
        self.assertNotIn('SYNTHETIC-SECRET-CODE', logs.getvalue())
        self.assertNotIn('authorize?', logs.getvalue())


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = p.Store(Path(self.tmp.name) / 'data')

    def tearDown(self): self.tmp.cleanup()

    def test_stable_host_and_atomic_permissions(self):
        with self.store.locked():
            d = self.store.load(); self.store.save(d)
        with self.store.locked(): self.assertEqual(self.store.load()['host_id'], d['host_id'])
        self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.store.directory.stat().st_mode), 0o700)
        with tempfile.TemporaryDirectory() as another:
            other = p.Store(Path(another) / 'data')
            with other.locked(): self.assertNotEqual(other.load()['host_id'], d['host_id'])

    def test_wrong_permissions_and_symlinks(self):
        with self.store.locked(): self.store.load()
        self.store.path.chmod(0o644)
        with self.assertRaises(p.ProbeError): self.store.load()
        self.store.path.unlink()
        self.store.path.symlink_to(Path(self.tmp.name) / 'elsewhere')
        with self.assertRaises(p.ProbeError): self.store.load()
        self.assertFalse((Path(self.tmp.name) / 'elsewhere').exists())

    def test_source_storage_forbidden(self):
        with self.assertRaises(p.ProbeError): p.Store(p.SOURCE / '.credentials')

    def test_operation_lock_serializes_refresh(self):
        with self.store.locked():
            with self.assertRaises(p.ProbeError):
                with self.store.locked(): pass

    def test_cli_initialization_has_no_provider_calls(self):
        with patch.object(p, 'HTTP') as http, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(p.run(['--data-dir', str(self.store.directory), 'status']), 0)
            http.assert_not_called()
        self.assertTrue(self.store.path.exists())

    def data(self):
        d = self.store.load()
        d['accounts'] = {'first': {'client_id': 'oaiapp_fixture', 'subject': 'fixture', 'issuer': p.ISSUER,
                                 'scopes': p.SCOPES.split(), 'access_token': 'old-access', 'refresh_token': 'old-refresh',
                                 'id_token': 'old-id', 'expires_at': 0}}
        d['active'] = 'first'
        return d

    def test_refresh_rotation_and_no_scope_escalation(self):
        with self.store.locked():
            d = self.data(); http = Mock()
            http.json.return_value = {'access_token': 'new-access', 'refresh_token': 'new-refresh',
                                      'expires_in': 3600, 'token_type': 'Bearer'}
            p.renew(http, document(), self.store, d, 'first')
            saved = self.store.load()['accounts']['first']
            self.assertEqual(saved['access_token'], 'new-access')
            self.assertEqual(saved['refresh_token'], 'new-refresh')
            form = http.json.call_args.kwargs['data']
            self.assertEqual(form['refresh_token'], 'old-refresh')
            self.assertNotIn('scope', form)
            self.assertEqual(form['client_id'], 'oaiapp_fixture')

    def test_refresh_failure_preserves_persistent_state(self):
        with self.store.locked():
            d = self.data(); self.store.save(d); http = Mock()
            http.json.side_effect = p.ProbeError('Refresh failed; no success.')
            with self.assertRaises(p.ProbeError): p.renew(http, document(), self.store, d, 'first')
            self.assertEqual(self.store.load(), d)

    def test_signout_removes_tokens_retains_mapping_and_host(self):
        with self.store.locked():
            d = self.data(); http = MagicMock(); http.request.return_value.__enter__.return_value.status_code = 200
            self.assertTrue(p.signout(http, document(), self.store, d, 'first'))
            saved = self.store.load(); self.assertEqual(saved['host_id'], d['host_id'])
            self.assertEqual(saved['accounts']['first']['client_id'], 'oaiapp_fixture')
            self.assertFalse(any(saved['accounts']['first'].get(f) for f in p.TOKEN_FIELDS))
            with self.assertRaises(p.ProbeError): p.renew(http, document(), self.store, saved, 'first')

    def test_failed_revocation_still_blocks_local_inference(self):
        with self.store.locked(), patch.object(p.time, 'sleep'):
            d = self.data(); http = Mock(); http.request.side_effect = p.ProbeError('Network failure')
            self.assertFalse(p.signout(http, document(), self.store, d, 'first'))
            with self.assertRaises(p.ProbeError): p.renew(http, document(), self.store, d, 'first')
            self.assertEqual(http.request.call_count, 3)

    def test_account_selection_and_distinct_registrations(self):
        d = self.data(); d['accounts']['second'] = {'client_id': 'oaiapp_other', 'subject': 'other', 'issuer': p.ISSUER}
        self.assertNotEqual(p.account_key(d['accounts']['first']), p.account_key(d['accounts']['second']))
        self.assertEqual(p.select_account(d, 'second')[0], 'second')
        with self.assertRaises(p.ProbeError): p.select_account(d, 'missing')


class StreamTests(unittest.TestCase):
    def test_completed_stream(self):
        out = []; r = p.consume(events(), p.MODEL, out.append)
        self.assertTrue(r['completed']); self.assertEqual(''.join(out), 'Hello, world!')

    def test_failed_incomplete_quota_and_interrupted(self):
        for terminal in [{'type': 'response.failed', 'response': {'error': {'code': 'subscription_sharing_usage_limit_exceeded'}}},
                         {'type': 'response.incomplete'}, {'type': 'error'}, {'type': 'response.created'}]:
            with self.subTest(terminal=terminal), self.assertRaises(p.ProbeError):
                p.consume(events(terminal), p.MODEL, lambda _: None)

    def test_completed_settings_must_match(self):
        for change in [{'model': 'other'}, {'reasoning': {'effort': 'medium'}},
                       {'service_tier': 'priority'}, {'service_tier': None}, {'status': 'incomplete'}]:
            with self.subTest(change=change), self.assertRaises(p.ProbeError):
                p.consume(events(complete(**change)), p.MODEL, lambda _: None)

    def test_completed_wrong_hello_fails(self):
        with self.assertRaises(p.ProbeError):
            p.consume([{'type': 'response.output_text.delta', 'delta': 'Wrong'}, complete()], p.MODEL, lambda _: None)

    def test_sse_multiline_comments_and_terminal(self):
        lines = [b': heartbeat', b'data: {"type":', b'data: "response.created"}', b'', b'data: [DONE]', b'']
        self.assertEqual(list(p.sse_events(lines)), [{'type': 'response.created'}])
        for lines in [[b'data: not-json', b''], [b'data: {'], [b'data: []', b'']]:
            with self.subTest(lines=lines), self.assertRaises(p.ProbeError): list(p.sse_events(lines))

    def test_catalog_no_silent_substitution(self):
        self.assertEqual(p.choose_model({'models': [{'slug': p.MODEL, 'visibility': 'list'}]}), p.MODEL)
        self.assertEqual(p.choose_model({'models': [{'slug': 'account-sol', 'display_name': 'GPT-6.1 Sol', 'visibility': 'list'}]}), 'account-sol')
        for catalog in [{}, {'models': []}, {'models': [{'slug': 'other', 'visibility': 'list'}]},
                        {'models': [{'slug': p.MODEL, 'visibility': 'hidden'}]}]:
            with self.subTest(catalog=catalog), self.assertRaises(p.ProbeError): p.choose_model(catalog)

    def test_provider_payload_uses_only_oauth(self):
        http = MagicMock(); http.json.return_value = {'models': [{'slug': p.MODEL, 'visibility': 'list'}]}
        response = Mock(); response.iter_lines.return_value = [b'data: ' + json.dumps(e).encode() for e in []]
        lines = []
        for e in events(): lines += [b'data: ' + json.dumps(e).encode(), b'']
        response.iter_lines.return_value = lines
        http.request.return_value.__enter__.return_value = response
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'NEVER-USE-THIS-API-KEY'}):
            self.assertTrue(p.probe(http, {'access_token': 'synthetic-oauth'}, lambda _: None)['completed'])
        self.assertEqual(http.request.call_args.args, ('POST', p.RESOURCE + '/responses'))
        k = http.request.call_args.kwargs
        self.assertEqual(k['headers']['Authorization'], 'Bearer synthetic-oauth')
        self.assertEqual(k['json']['service_tier'], 'default')
        self.assertEqual(k['json']['reasoning'], {'effort': 'high'})
        self.assertIs(k['json']['store'], False); self.assertIs(k['json']['stream'], True)
        self.assertNotIn('NEVER-USE-THIS-API-KEY', repr(k))
        self.assertNotIn('previous_response_id', k['json'])

    def test_network_error_diagnostics_redact_secret(self):
        http = p.HTTP()
        http.session.request = Mock(side_effect=requests.RequestException('SYNTHETIC-SECRET-TOKEN https://callback/?code=secret'))
        with self.assertRaises(p.ProbeError) as e: http.json('GET', p.RESOURCE + '/models')
        self.assertNotIn('SYNTHETIC-SECRET-TOKEN', str(e.exception))
        self.assertNotIn('code=', str(e.exception))


if __name__ == '__main__': unittest.main()
