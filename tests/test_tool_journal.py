"""Private synthetic SQLite recovery, dispatch and deletion guarantees."""
import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from homebase_probe import ProbeError
from memory_store import State
from tool_journal import Journal

class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.directory.chmod(0o700)
        self.state = State(self.directory, 'synthetic')
        self.thread = self.state.new_thread()
        self.message, _ = self.state.begin(self.thread, str(uuid.uuid4()), 'Synthetic request')
        self.journal = Journal(self.state)
        self.turn = str(uuid.uuid4())
        self.journal.begin(self.turn, self.thread, self.message,
                           {'model': 'gpt-5.6-sol', 'reasoning': 'high', 'speed': 'standard'}, time.time() + 180)
        self.target = {'owner': 'fixture', 'repo': 'private', 'branch': 'homebase/test', 'path': 'docs/test.md'}

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self, call='one', name='create_or_update_file', target=None):
        return self.journal.prepare(self.turn, call, name, target or self.target,
                                    {'content': 'SYNTHETIC-RECONCILIATION-PAYLOAD'}, {'blob': 'a' * 40})

    def test_dispatch_is_durable_and_never_repeated(self):
        self.assertTrue(self.prepare()['new'])
        self.journal.dispatch(self.turn, 'one')
        reopened = State(self.directory, 'synthetic')
        self.assertEqual(Journal(reopened).activity(self.message)[0]['status'], 'dispatched')
        self.assertFalse(self.prepare()['new'])
        with self.assertRaises(ProbeError):
            self.journal.dispatch(self.turn, 'one')
        with self.assertRaises(ProbeError):
            self.journal.prepare(self.turn, 'one', 'create_or_update_file', self.target, {'content': 'changed'})

    def test_restart_cancels_prepared_and_preserves_uncertain_payload(self):
        self.prepare('prepared')
        self.prepare('write')
        self.journal.dispatch(self.turn, 'write')
        self.prepare('read', 'get_file_contents')
        self.journal.dispatch(self.turn, 'read')
        reopened = State(self.directory, 'synthetic', recover=True)
        activity = Journal(reopened).activity(self.message)
        self.assertEqual([a['status'] for a in activity], ['cancelled', 'uncertain', 'interrupted'])
        self.assertEqual(reopened.thread(self.thread)['messages'][-1]['status'], 'incomplete')
        with reopened.connect() as db:
            rows = list(db.execute('SELECT call_id,payload FROM tool_calls ORDER BY ordinal'))
            self.assertEqual(rows[0]['payload'], '')
            self.assertIn('SYNTHETIC-RECONCILIATION-PAYLOAD', rows[1]['payload'])
            self.assertEqual(rows[2]['payload'], '')
            self.assertEqual(db.execute('SELECT continuation FROM tool_turns').fetchone()[0], '')

    def test_uncertain_write_blocks_new_call_and_new_turn_until_reconciled(self):
        self.prepare(); self.journal.dispatch(self.turn, 'one')
        self.journal.outcome(self.turn, 'one', 'uncertain', error='uncertain_write')
        with self.assertRaises(ProbeError): self.prepare('replacement')
        self.state.finish(self.message, '', 'incomplete')
        self.message, _ = self.state.begin(self.thread, str(uuid.uuid4()), 'Try new request')
        old_turn = self.turn; self.turn = str(uuid.uuid4())
        self.journal.begin(self.turn, self.thread, self.message, {}, time.time() + 180)
        with self.assertRaises(ProbeError): self.prepare('new-id')
        other = {**self.target, 'repo': 'different'}
        self.assertTrue(self.prepare('other-repo', target=other)['new'])
        self.journal.outcome(old_turn, 'one', 'succeeded', {'blob': 'b' * 40, 'url': 'https://github.com/fixture/private/blob/' + 'b' * 40 + '/docs/test.md'})
        self.assertTrue(self.prepare('after-proof')['new'])

    def test_success_prunes_payload_and_browser_activity_has_no_arguments(self):
        self.prepare(); self.journal.dispatch(self.turn, 'one')
        self.journal.outcome(self.turn, 'one', 'succeeded', {'commit': 'b' * 40})
        activity = self.state.thread(self.thread)['messages'][-1]['activity']
        self.assertNotIn('SYNTHETIC-RECONCILIATION-PAYLOAD', json.dumps(activity))
        self.assertNotIn('payload', activity[0]); self.assertNotIn('intent_hash', activity[0])
        with self.state.connect() as db:
            self.assertEqual(db.execute('SELECT payload FROM tool_calls').fetchone()[0], '')
        with self.assertRaises(ProbeError): self.journal.outcome(self.turn, 'one', 'failed')

    def test_stop_before_dispatch_cancels_prepared_and_refuses_new_dispatch(self):
        self.prepare()
        self.state.stop(self.thread)
        with self.assertRaises(ProbeError): self.journal.dispatch(self.turn, 'one')
        self.assertEqual(self.journal.activity(self.message)[0]['status'], 'cancelled')
        with self.assertRaises(ProbeError): self.prepare('after-stop')

    def test_stop_after_dispatch_allows_verified_effect_without_restarting_reply(self):
        self.prepare(); self.journal.dispatch(self.turn, 'one')
        self.state.stop(self.thread)
        self.journal.outcome(self.turn, 'one', 'succeeded', {'blob': 'c' * 40})
        self.assertEqual(self.journal.activity(self.message)[0]['status'], 'succeeded')
        self.assertEqual(self.state.thread(self.thread)['messages'][-1]['status'], 'stopped')
        with self.assertRaises(ProbeError): self.journal.next_round(self.turn)

    def test_delete_is_atomic_and_preserves_shared_memory(self):
        self.prepare(); self.state.stop(self.thread)
        memory = self.state.change('remember', title='Shared', text='Keep this')
        with self.state.connect() as db:
            db.execute("CREATE TRIGGER refuse_delete BEFORE DELETE ON threads BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with self.assertRaises(Exception): self.state.delete_thread(self.thread)
        self.assertEqual(len(self.journal.activity(self.message)), 1)
        with self.state.connect() as db: db.execute('DROP TRIGGER refuse_delete')
        self.state.delete_thread(self.thread)
        with self.state.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM tool_calls').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM tool_turns').fetchone()[0], 0)
        self.assertEqual(self.state.memory()[0]['id'], memory['id'])

    def test_link_validation_rejects_foreign_and_credential_bearing_urls(self):
        self.prepare(); self.journal.dispatch(self.turn, 'one')
        for url in ('https://github.com/fixture/other/pull/1',
                    'https://github.com@evil.example/fixture/private/pull/1',
                    'https://github.com/fixture/private/pull/1?token=secret',
                    'http://github.com/fixture/private/pull/1'):
            with self.subTest(url=url), self.assertRaises(ProbeError):
                self.journal.outcome(self.turn, 'one', 'succeeded', {'url': url})
        self.journal.outcome(self.turn, 'one', 'succeeded', {'url': 'https://github.com/fixture/private/pull/1'})

    def test_storage_and_call_round_budgets(self):
        with self.assertRaises(ProbeError):
            self.journal.prepare(self.turn, 'large', 'create_or_update_file', self.target, {'content': 'x' * (256 * 1024)})
        for i in range(12): self.prepare(str(i), 'get_file_contents')
        with self.assertRaises(ProbeError): self.prepare('over', 'get_file_contents')
        for _ in range(12): self.journal.next_round(self.turn)
        with self.assertRaises(ProbeError): self.journal.next_round(self.turn)

    def test_terminal_clear_and_expired_dispatch(self):
        self.journal.phase(self.turn, 'model', [{'type': 'reasoning', 'encrypted_content': 'PRIVATE-SENTINEL'}])
        self.prepare()
        with self.state.connect() as db:
            db.execute('UPDATE tool_turns SET deadline=?', (time.time() - 1,))
        with self.assertRaises(ProbeError): self.journal.dispatch(self.turn, 'one')
        self.journal.phase(self.turn, 'incomplete')
        with self.state.connect() as db:
            self.assertEqual(db.execute('SELECT continuation FROM tool_turns').fetchone()[0], '')

    def test_mutation_cannot_be_downgraded_to_interrupted_read(self):
        self.prepare(); self.journal.dispatch(self.turn, 'one')
        with self.assertRaises(ProbeError): self.journal.outcome(self.turn, 'one', 'interrupted')
        self.journal.outcome(self.turn, 'one', 'uncertain', error='timeout')

if __name__ == '__main__':
    unittest.main()
