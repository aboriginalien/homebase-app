"""Offline migration tests: only synthetic tokens and temporary local files."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

import homebase_probe as p


class HandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signing = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # macOS may expose TMPDIR through /var -> /private/var. Keep the
        # synthetic fixture canonical without relaxing production path checks.
        root = Path(self.tmp.name).resolve()
        self.helper = p.Store(root / 'helper')
        self.vm = p.Store(root / 'vm')
        self.transport = root / 'transport'; self.transport.mkdir(mode=0o700)
        self.file = self.transport / 'registration.json'
        self.record = self.registration()
        self.key = p.account_key(self.record)
        with self.helper.locked():
            self.local = self.helper.load()
            self.local['accounts'][self.key] = self.record
            self.local['active'] = self.key
            self.helper.save(self.local)
        with self.vm.locked(): self.remote = self.vm.load()
        self.no_network = patch.object(p, 'HTTP', side_effect=AssertionError('Offline only'))
        self.no_network.start(); self.addCleanup(self.no_network.stop)
        self.addCleanup(self.tmp.cleanup)

    def registration(self, subject='synthetic-owner', client='oaiapp_synthetic', claims=None):
        now = int(time.time())
        fields = {'iss': p.ISSUER, 'sub': subject, 'aud': client, 'iat': now, 'exp': now + 3600}
        fields.update(claims or {})
        return {'client_id': client, 'issuer': p.ISSUER, 'subject': subject,
                'scopes': p.SCOPES.split(), 'expires_at': now + 3600,
                'access_token': 'FAKE-ACCESS-NOT-A-CREDENTIAL',
                'refresh_token': 'FAKE-REFRESH-NOT-A-CREDENTIAL',
                'id_token': jwt.encode(fields, self.signing, algorithm='RS256', headers={'kid': 'synthetic'})}

    def export(self, destination=None, key=None, target=None):
        with self.helper.locked():
            p.export_registration(self.helper, self.helper.load(), key or self.key,
                                  destination or self.file, target or self.remote['host_id'])

    def ingest(self, key=None, client='oaiapp_synthetic', source=None):
        with self.vm.locked():
            p.import_registration(self.vm, self.vm.load(), key or self.key, source or self.file, client)

    def document(self):
        return {'version': 1, 'kind': 'homebase-siwc-registration',
                'source_host_id': self.local['host_id'], 'target_host_id': self.remote['host_id'],
                'account': self.key, 'registration': copy.deepcopy(self.record)}

    def write(self, document):
        self.file.write_text(json.dumps(document)); self.file.chmod(0o600)

    def assert_rejected_without_vm_change(self, document):
        before = self.vm.path.read_bytes()
        self.write(document)
        with self.assertRaises(p.ProbeError): self.ingest()
        self.assertEqual(self.vm.path.read_bytes(), before)
        self.assertTrue(self.file.exists())

    def test_round_trip_preserves_vm_identity_and_exact_record(self):
        self.export(); self.ingest()
        saved = self.vm.load()
        self.assertEqual(saved['host_id'], self.remote['host_id'])
        self.assertNotEqual(saved['host_id'], self.local['host_id'])
        self.assertEqual(saved['accounts'], {self.key: self.record})
        self.assertEqual(saved['active'], self.key)
        self.assertFalse(self.file.exists())
        self.assertEqual(stat.S_IMODE(self.vm.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.vm.directory.stat().st_mode), 0o700)

    def test_export_only_selected_account_and_no_helper_tokens(self):
        other = self.registration('synthetic-other', 'oaiapp_other'); key = p.account_key(other)
        data = self.helper.load(); data['accounts'][key] = other; data['active'] = key; self.helper.save(data)
        self.export()
        transfer = json.loads(self.file.read_text())
        self.assertEqual(transfer['registration'], self.record)
        self.assertNotIn('accounts', transfer)
        saved = self.helper.load()
        self.assertEqual(saved['accounts'][key], other); self.assertEqual(saved['active'], key)
        self.assertEqual(saved['host_id'], self.local['host_id'])
        self.assertEqual(saved['accounts'][self.key]['handoff']['phase'], 'exported')
        self.assertFalse(any(f in saved['accounts'][self.key] for f in p.TOKEN_FIELDS))
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)
        self.assertEqual(self.file.stat().st_nlink, 1)

    def test_explicit_selection_and_client_required(self):
        before = self.helper.path.read_bytes()
        for key in (None, 'missing'):
            with self.subTest(case='missing-selection'), self.helper.locked(), self.assertRaises(p.ProbeError):
                p.export_registration(self.helper, self.helper.load(), key, self.file, self.remote['host_id'])
        self.assertEqual(self.helper.path.read_bytes(), before); self.assertFalse(self.file.exists())
        self.write(self.document())
        for key, client in [(None, 'oaiapp_synthetic'), ('wrong-label', 'oaiapp_synthetic'), (self.key, 'oaiapp_wrong')]:
            with self.subTest(case='wrong-expected-metadata'), self.vm.locked(), self.assertRaises(p.ProbeError):
                p.import_registration(self.vm, self.vm.load(), key, self.file, client)
        self.assertEqual(self.vm.load(), self.remote)

    def test_import_preserves_unrelated_active_session(self):
        other = self.registration('synthetic-other', 'oaiapp_other'); key = p.account_key(other)
        d = self.vm.load(); d['accounts'][key] = other; d['active'] = key; self.vm.save(d)
        self.export(); self.ingest()
        saved = self.vm.load()
        self.assertEqual(saved['accounts'][key], other); self.assertEqual(saved['active'], key)
        self.assertEqual(saved['accounts'][self.key], self.record)

    def test_existing_same_account_never_overwritten(self):
        d = self.vm.load(); d['accounts'][self.key] = {**self.record, 'refresh_token': 'FAKE-NEWER-ROTATION'}
        d['active'] = self.key; self.vm.save(d); before = self.vm.path.read_bytes()
        self.export()
        with self.assertRaises(p.ProbeError): self.ingest()
        self.assertEqual(self.vm.path.read_bytes(), before); self.assertTrue(self.file.exists())

    def test_wrong_or_same_host_rejected(self):
        for field, value in [('target_host_id', self.local['host_id']), ('source_host_id', self.remote['host_id']),
                             ('target_host_id', 'urn:uuid:not-a-uuid'), ('source_host_id', True)]:
            doc = self.document(); doc[field] = value
            with self.subTest(field=field): self.assert_rejected_without_vm_change(doc)
        with self.assertRaises(p.ProbeError): self.export(target=self.local['host_id'])

    def test_mismatched_identity_claims_and_metadata_rejected(self):
        for changes in [{'iss': 'https://wrong.invalid'}, {'sub': 'wrong'}, {'aud': 'oaiapp_wrong'},
                        {'aud': ['oaiapp_synthetic', 'other'], 'azp': 'other'},
                        {'aud': ['oaiapp_synthetic', 3]}, {'iat': True}, {'exp': 0}]:
            doc = self.document(); doc['registration'] = self.registration(claims=changes)
            with self.subTest(case='claim-mismatch'): self.assert_rejected_without_vm_change(doc)
        for name, value in [('issuer', 'https://wrong.invalid'), ('subject', ''),
                            ('client_id', 'dynamic_agent_client'), ('id_token', 'not-a-jwt')]:
            doc = self.document(); doc['registration'][name] = value
            with self.subTest(field=name): self.assert_rejected_without_vm_change(doc)

    def test_missing_grants_bad_tokens_expiry_and_unknown_fields(self):
        for name, value in [('scopes', ['openid', 'profile']), ('scopes', [True]),
                            ('expires_at', True), ('expires_at', float('nan')), ('expires_at', 0),
                            ('access_token', ''), ('access_token', 'fake\u2603'), ('access_token', 'fake token'),
                            ('refresh_token', 3), ('refresh_token', 'fake\nheader'),
                            ('extra', 'unexpected')]:
            doc = self.document(); doc['registration'][name] = value
            with self.subTest(field=name): self.assert_rejected_without_vm_change(doc)
        doc = self.document(); del doc['registration']['id_token']
        self.assert_rejected_without_vm_change(doc)

    def test_expired_retained_id_and_access_metadata_are_preserved(self):
        doc = self.document(); doc['registration'] = self.registration(claims={'iat': int(time.time()) - 7200,
                                                                               'exp': int(time.time()) - 3600})
        doc['registration']['expires_at'] = time.time() - 60
        self.write(doc); self.ingest()
        self.assertEqual(self.vm.load()['accounts'][self.key], doc['registration'])

    def test_native_cache_bad_versions_and_multi_account_envelope_rejected(self):
        for doc in [{'tokens': {'access_token': 'FAKE-NATIVE-CACHE'}},
                    {**self.document(), 'version': True}, {**self.document(), 'version': 2},
                    {**self.document(), 'kind': 'native-codex'}, {**self.document(), 'accounts': {}}]:
            with self.subTest(case='format'): self.assert_rejected_without_vm_change(doc)

    def test_duplicate_keys_malformed_json_and_size_limit(self):
        before = self.vm.path.read_bytes()
        for raw in [b'{"version":1,"version":1}', b'not-json', b'\xff', b'x' * (p.HANDOFF_LIMIT + 1)]:
            self.file.write_bytes(raw); self.file.chmod(0o600)
            with self.subTest(case='encoding-or-size'), self.assertRaises(p.ProbeError): self.ingest()
            self.assertEqual(self.vm.path.read_bytes(), before)

    def test_export_collision_preserves_helper_and_existing_file(self):
        self.file.write_text('synthetic existing data'); self.file.chmod(0o600)
        before = self.helper.path.read_bytes()
        with self.assertRaises(p.ProbeError): self.export()
        self.assertEqual(self.helper.path.read_bytes(), before)
        self.assertEqual(self.file.read_text(), 'synthetic existing data')

    def test_export_symlink_and_public_directory_rejected(self):
        victim = Path(self.tmp.name) / 'victim'; victim.write_text('untouched')
        self.file.symlink_to(victim)
        with self.assertRaises(p.ProbeError): self.export()
        self.assertEqual(victim.read_text(), 'untouched'); self.file.unlink()
        self.transport.chmod(0o755)
        with self.assertRaises(p.ProbeError): self.export()
        self.assertNotIn('handoff', self.helper.load()['accounts'][self.key])

    def test_import_symlinks_hardlinks_modes_and_directory_rejected(self):
        before = self.vm.path.read_bytes(); self.write(self.document())
        self.file.chmod(0o644)
        with self.assertRaises(p.ProbeError): self.ingest()
        self.file.chmod(0o600)
        linked = self.transport / 'linked'; os.link(self.file, linked)
        with self.assertRaises(p.ProbeError): self.ingest()
        linked.unlink()
        alias = self.transport / 'alias'; alias.symlink_to(self.file)
        with self.assertRaises(p.ProbeError): self.ingest(source=alias)
        self.file.unlink(); self.file.mkdir(mode=0o700)
        with self.assertRaises(p.ProbeError): self.ingest()
        self.assertEqual(self.vm.path.read_bytes(), before)

    def test_source_and_session_path_collisions_rejected(self):
        for path in [self.helper.path, self.helper.directory / 'session.lock', p.SOURCE / 'handoff.json']:
            with self.subTest(case='collision'), self.assertRaises(p.ProbeError): self.export(destination=path)
        for path in [self.vm.path, self.vm.directory / 'session.lock']:
            with self.subTest(case='collision'), self.assertRaises(p.ProbeError): self.ingest(source=path)

    def test_symlink_parent_rejected(self):
        alias = Path(self.tmp.name) / 'alias'; alias.symlink_to(self.transport, target_is_directory=True)
        with self.assertRaises(p.ProbeError): self.export(destination=alias / 'out.json')
        self.assertFalse(self.file.exists())

    def test_durable_freeze_precedes_file_and_retry_after_write_failure(self):
        def failed_write(*args):
            saved = self.helper.load()['accounts'][self.key]
            self.assertEqual(saved['handoff']['phase'], 'frozen')
            with self.assertRaises(p.ProbeError): p.renew(Mock(), {}, self.helper, self.helper.load(), self.key)
            raise OSError('FAKE-ACCESS-NOT-A-CREDENTIAL')
        with patch.object(p, 'write_handoff', side_effect=failed_write), self.assertRaises(p.ProbeError) as error:
            self.export()
        self.assertNotIn(self.record['access_token'], str(error.exception))
        self.assertFalse(self.file.exists()); self.export(); self.ingest()
        self.assertEqual(self.vm.load()['accounts'][self.key], self.record)

    def test_final_export_commit_failure_keeps_helper_frozen(self):
        save = self.helper.save; calls = []
        def fail_second(data):
            calls.append(1)
            if len(calls) == 2: raise OSError('synthetic final-save failure')
            return save(data)
        with patch.object(self.helper, 'save', side_effect=fail_second), self.assertRaises(p.ProbeError): self.export()
        saved = self.helper.load()['accounts'][self.key]
        self.assertEqual(saved['handoff']['phase'], 'frozen'); self.assertTrue(self.file.exists())
        with self.assertRaises(p.ProbeError): p.require_local_owner(saved)
        self.ingest(); self.assertEqual(self.vm.load()['accounts'][self.key], self.record)

    def test_frozen_export_recovery_checks_existing_file_and_clears_tokens(self):
        save = self.helper.save; calls = []
        def fail_second(data):
            calls.append(1)
            if len(calls) == 2: raise OSError('synthetic final-save failure')
            return save(data)
        with patch.object(self.helper, 'save', side_effect=fail_second), self.assertRaises(p.ProbeError): self.export()
        before = self.file.read_bytes(); self.export()
        self.assertEqual(self.file.read_bytes(), before)
        saved = self.helper.load()['accounts'][self.key]
        self.assertEqual(saved['handoff']['phase'], 'exported')
        self.assertFalse(any(name in saved for name in p.TOKEN_FIELDS))

    def test_failed_first_freeze_creates_no_export(self):
        before = self.helper.path.read_bytes()
        with patch.object(self.helper, 'save', side_effect=OSError('synthetic freeze failure')), self.assertRaises(p.ProbeError):
            self.export()
        self.assertEqual(self.helper.path.read_bytes(), before); self.assertFalse(self.file.exists())

    def test_fifo_and_wrong_file_owner_rejected_without_blocking(self):
        os.mkfifo(self.file, mode=0o600)
        with self.assertRaises(p.ProbeError): self.ingest()
        self.file.unlink(); self.write(self.document())
        with patch.object(p.os, 'getuid', return_value=os.getuid() + 1), self.assertRaises(p.ProbeError): self.ingest()
        self.assertEqual(self.vm.load(), self.remote)

    def test_oversized_encoded_export_rejected_before_freeze(self):
        data = self.helper.load(); data['accounts'][self.key]['refresh_token'] = '\u2603' * 65536
        self.helper.save(data); before = self.helper.path.read_bytes()
        with self.assertRaises(p.ProbeError): self.export()
        self.assertEqual(self.helper.path.read_bytes(), before); self.assertFalse(self.file.exists())

    def test_frozen_helper_commands_never_contact_or_revoke_provider(self):
        self.export()
        for command in ['login', 'probe', 'signout']:
            with self.subTest(command=command), self.assertRaises(p.ProbeError):
                p.run(['--data-dir', str(self.helper.directory), '--account', self.key, command])
        with self.assertRaises(p.ProbeError):
            p.signout(Mock(), {}, self.helper, self.helper.load(), self.key)

    def test_import_atomic_commit_failure_preserves_vm_and_input(self):
        self.export(); before = self.vm.path.read_bytes()
        with patch.object(p.os, 'replace', side_effect=OSError('synthetic replace failure')), self.assertRaises(p.ProbeError):
            self.ingest()
        self.assertEqual(self.vm.path.read_bytes(), before); self.assertTrue(self.file.exists())
        self.assertFalse(list(self.vm.directory.glob('.session-*')))

    def test_import_cleanup_failure_is_not_false_success_or_reimport(self):
        self.export()
        with patch.object(Path, 'unlink', side_effect=OSError('synthetic cleanup failure')), self.assertRaises(p.ProbeError):
            self.ingest()
        self.assertEqual(self.vm.load()['accounts'][self.key], self.record)
        before = self.vm.path.read_bytes()
        with self.assertRaises(p.ProbeError): self.ingest()
        self.assertEqual(self.vm.path.read_bytes(), before); self.assertTrue(self.file.exists())

    def test_collision_during_atomic_export_cannot_replace_existing_data(self):
        link = os.link
        def racing_link(source, destination, **kwargs):
            self.file.write_text('synthetic concurrent destination'); self.file.chmod(0o600)
            return link(source, destination, **kwargs)
        with patch.object(p.os, 'link', side_effect=racing_link), self.assertRaises(p.ProbeError): self.export()
        self.assertEqual(self.file.read_text(), 'synthetic concurrent destination')
        self.assertEqual(self.helper.load()['accounts'][self.key]['handoff']['phase'], 'frozen')
        self.assertFalse(list(self.transport.glob('.handoff-*')))

    def test_lock_prevents_duplicate_export(self):
        with self.helper.locked(), self.assertRaises(p.ProbeError): self.export()
        self.assertFalse(self.file.exists())

    def test_cli_round_trip_and_status_do_not_print_credentials(self):
        logs = io.StringIO()
        with contextlib.redirect_stdout(logs), contextlib.redirect_stderr(logs):
            self.assertEqual(p.run(['--data-dir', str(self.helper.directory), '--account', self.key,
                                    'export', '--file', str(self.file), '--target-host-id', self.remote['host_id']]), 0)
            self.assertEqual(p.run(['--data-dir', str(self.helper.directory), 'status']), 0)
            self.assertEqual(p.run(['--data-dir', str(self.vm.directory), '--account', self.key,
                                    'import', '--file', str(self.file), '--expected-client-id', self.record['client_id']]), 0)
        for token in [*self.record.values(), self.local['host_id'], self.remote['host_id']]:
            if isinstance(token, str): self.assertNotIn(token, logs.getvalue())
        self.assertIn('NOT verified', logs.getvalue())

    def test_import_requires_preinitialized_host(self):
        fresh = Path(self.tmp.name) / 'uninitialized'; self.write(self.document())
        with self.assertRaises(p.ProbeError):
            p.run(['--data-dir', str(fresh), '--account', self.key, 'import', '--file', str(self.file),
                   '--expected-client-id', self.record['client_id']])
        self.assertFalse((fresh / 'session.json').exists())


if __name__ == '__main__': unittest.main()
