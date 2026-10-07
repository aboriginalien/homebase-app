"""Real SQLite migration/terminal-time tests using synthetic state only."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from memory_store import State

class Timestamps(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.root.chmod(0o700)
        self.state=State(self.root,'synthetic-owner');self.thread=self.state.new_thread()

    def test_creation_completion_and_reopen(self):
        with patch('memory_store.time.time',return_value=1000):mid,_=self.state.begin(self.thread,'request','message')
        first=self.state.thread(self.thread)['messages']
        self.assertEqual([r['created'] for r in first],[1000,1000]);self.assertIsNone(first[1]['completed'])
        with patch('memory_store.time.time',return_value=1020):self.state.finish(mid,'answer')
        rows=State(self.root,'synthetic-owner').thread(self.thread)['messages']
        self.assertEqual(rows[0]['created'],1000);self.assertIsNone(rows[0]['completed'])
        self.assertEqual(rows[1]['created'],1000);self.assertEqual(rows[1]['completed'],1020)
        with patch('memory_store.time.time',return_value=2000):self.assertFalse(self.state.finish(mid,'duplicate'))
        self.assertEqual(self.state.thread(self.thread)['messages'][1]['completed'],1020)

    def test_incomplete_and_stopped_have_no_completion(self):
        for status in ('incomplete','stopped'):
            mid,_=self.state.begin(self.thread,status,'message')
            self.state.finish(mid,'partial',status)
            self.assertIsNone(self.state.thread(self.thread)['messages'][-1]['completed'])

    def test_restart_does_not_invent_completion(self):
        self.state.begin(self.thread,'interrupted','message')
        rows=State(self.root,'synthetic-owner',recover=True).thread(self.thread)['messages']
        self.assertEqual(rows[1]['status'],'incomplete');self.assertIsNone(rows[1]['completed'])

    def test_legacy_migration_preserves_every_original_column(self):
        # Rebuild the messages table in the actual deployed pre-timestamp schema.
        mid,_=self.state.begin(self.thread,'legacy','keep me');self.state.finish(mid,'old answer')
        with self.state.connect() as db:
            db.execute('ALTER TABLE messages DROP COLUMN completed')
            before=[tuple(r) for r in db.execute('SELECT * FROM messages ORDER BY id')]
        upgraded=State(self.root,'synthetic-owner')
        with upgraded.connect() as db:
            after=[tuple(r) for r in db.execute('SELECT * FROM messages ORDER BY id')]
        self.assertEqual([r[:-1] for r in after],before)
        self.assertTrue(all(r[-1] is None for r in after))
        twice=State(self.root,'synthetic-owner')
        self.assertEqual(twice.thread(self.thread)['messages'],upgraded.thread(self.thread)['messages'])

if __name__=='__main__':unittest.main()
