"""Durable, private GitHub activity and conservative mutation recovery.

The executor must validate policy and verify remote effects. This journal never
executes or replays an operation. Dispatch is recorded before network I/O.
"""
import hashlib
import json
import re
import time
from urllib.parse import urlsplit

from homebase_probe import ProbeError

READS = {'search_repositories', 'search_code', 'get_file_contents'}
WRITES = {'create_branch', 'create_or_update_file', 'push_files', 'create_pull_request'}
MAX_MODEL_ROUNDS = 12
PHASES = {'model', 'validating', 'reading', 'writing', 'verifying',
          'completed', 'incomplete', 'stopped', 'interrupted'}
TERMINAL = {'completed', 'incomplete', 'stopped', 'interrupted'}
ERRORS = {'', 'unavailable', 'forbidden', 'invalid_arguments', 'conflict', 'quota',
          'timeout', 'uncertain_write', 'budget_exceeded', 'authorization_required',
          'interrupted', 'cancelled'}

def encoded(value, limit):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    if len(raw.encode()) > limit:
        raise ProbeError('GitHub activity exceeds its storage limit.')
    return raw

def target_checked(target):
    if not isinstance(target, dict) or set(target) - {'owner', 'repo', 'branch', 'path', 'paths', 'base', 'head'}:
        raise ProbeError('Invalid GitHub activity target.')
    for key in ('owner', 'repo'):
        if not isinstance(target.get(key), str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', target[key]):
            raise ProbeError('Invalid GitHub activity repository.')
    for key, value in target.items():
        if key == 'paths':
            if not isinstance(value, list) or not 1 <= len(value) <= 5:
                raise ProbeError('Invalid GitHub activity paths.')
            values = value
        else:
            values = [value]
        if any(not isinstance(v, str) or len(v) > 512 or any(ord(c) < 32 for c in v) for v in values):
            raise ProbeError('Invalid GitHub activity target text.')
    return encoded(target, 4096)

def evidence_checked(evidence, target):
    if not isinstance(evidence, dict) or set(evidence) - {'commit', 'blob', 'url', 'head'}:
        raise ProbeError('Invalid GitHub verification evidence.')
    for key in ('commit', 'blob', 'head'):
        if key in evidence and not re.fullmatch(r'[a-f0-9]{40,64}', evidence[key]):
            raise ProbeError('Invalid GitHub verification hash.')
    if 'url' in evidence:
        u = urlsplit(evidence['url'])
        prefix = '/' + target['owner'] + '/' + target['repo'] + '/'
        if (u.scheme != 'https' or u.netloc != 'github.com' or u.query or u.fragment
                or not u.path.startswith(prefix) or not re.match(r'(blob|tree|commit|pull)/', u.path[len(prefix):])):
            raise ProbeError('GitHub verification link does not match the repository.')
    return encoded(evidence, 4096)

class Journal:
    def __init__(self, state):
        self.state = state

    @staticmethod
    def migrate(db, recover=False):
        schema = '''
        CREATE TABLE IF NOT EXISTS tool_turns(
            turn_id TEXT PRIMARY KEY, request TEXT NOT NULL UNIQUE, thread_id TEXT NOT NULL,
            assistant_message_id INTEGER NOT NULL, phase TEXT NOT NULL,
            created REAL NOT NULL, updated REAL NOT NULL, deadline REAL NOT NULL,
            rounds INTEGER NOT NULL DEFAULT 0, calls INTEGER NOT NULL DEFAULT 0,
            settings TEXT NOT NULL, continuation TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS tool_calls(
            turn_id TEXT NOT NULL, call_id TEXT NOT NULL, name TEXT NOT NULL,
            ordinal INTEGER NOT NULL, target TEXT NOT NULL, intent_hash TEXT NOT NULL,
            payload TEXT NOT NULL, expected TEXT NOT NULL, mutation INTEGER NOT NULL,
            status TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '{}',
            error TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL,
            PRIMARY KEY(turn_id,call_id), UNIQUE(turn_id,ordinal));
        CREATE INDEX IF NOT EXISTS tool_calls_status ON tool_calls(status,mutation);
        '''
        for statement in schema.split(';'):
            if statement.strip():
                db.execute(statement)
        if recover:
            now = time.time()
            db.execute("UPDATE tool_calls SET status='cancelled',error='cancelled',payload='',updated=? WHERE status='prepared'", (now,))
            db.execute("UPDATE tool_calls SET status='uncertain',error='uncertain_write',updated=? WHERE status='dispatched' AND mutation=1", (now,))
            db.execute("UPDATE tool_calls SET status='interrupted',error='interrupted',payload='',updated=? WHERE status='dispatched' AND mutation=0", (now,))
            db.execute("UPDATE tool_turns SET phase='interrupted',continuation='',updated=? WHERE phase NOT IN ('completed','incomplete','stopped','interrupted')", (now,))

    def begin(self, turn_id, thread, assistant, settings, deadline):
        if not isinstance(turn_id, str) or not 1 <= len(turn_id) <= 256:
            raise ProbeError('Invalid GitHub turn identifier.')
        raw = encoded(settings, 2048)
        with self.state.connect() as db:
            row = db.execute("SELECT request FROM messages WHERE id=? AND thread=? AND role='assistant' AND status='working'", (assistant, thread)).fetchone()
            if not row:
                raise ProbeError('GitHub activity needs a working reply.')
            old = db.execute('SELECT * FROM tool_turns WHERE request=?', (row['request'],)).fetchone()
            if old:
                if old['turn_id'] != turn_id or old['assistant_message_id'] != assistant:
                    raise ProbeError('GitHub turn is already recorded.')
                return False
            now = time.time()
            if not isinstance(deadline, (int, float)) or not now < deadline <= now + 181:
                raise ProbeError('Invalid GitHub turn deadline.')
            db.execute('INSERT INTO tool_turns(turn_id,request,thread_id,assistant_message_id,phase,created,updated,deadline,settings) VALUES(?,?,?,?,?,?,?,?,?)',
                       (turn_id, row['request'], thread, assistant, 'model', now, now, deadline, raw))
            return True

    def _active(self, db, turn_id):
        row = db.execute('SELECT t.*,m.status AS message_status FROM tool_turns t JOIN messages m ON m.id=t.assistant_message_id WHERE t.turn_id=?', (turn_id,)).fetchone()
        if not row or row['phase'] in TERMINAL or row['message_status'] != 'working':
            raise ProbeError('GitHub turn stopped or is no longer active.')
        if row['deadline'] <= time.time():
            raise ProbeError('GitHub turn reached its time limit.')
        return row

    def phase(self, turn_id, phase, continuation=None, summary=''):
        if phase not in PHASES or not isinstance(summary, str) or len(summary) > 1000:
            raise ProbeError('Invalid GitHub turn phase.')
        raw = None if continuation is None else encoded(continuation, 1536 * 1024)
        with self.state.connect() as db:
            row = db.execute('SELECT phase FROM tool_turns WHERE turn_id=?', (turn_id,)).fetchone()
            if not row:
                raise ProbeError('GitHub turn does not exist.')
            if row['phase'] in TERMINAL:
                return False
            if phase not in TERMINAL:
                self._active(db, turn_id)
            if phase in TERMINAL:
                raw = ''
            db.execute('UPDATE tool_turns SET phase=?,updated=?,continuation=COALESCE(?,continuation),summary=? WHERE turn_id=?',
                       (phase, time.time(), raw, summary, turn_id))
            return True

    def next_round(self, turn_id):
        with self.state.connect() as db:
            row = self._active(db, turn_id)
            if row['rounds'] >= MAX_MODEL_ROUNDS:
                raise ProbeError('GitHub reply reached its model-round limit.')
            db.execute('UPDATE tool_turns SET rounds=rounds+1,updated=? WHERE turn_id=?', (time.time(), turn_id))

    def prepare(self, turn_id, call_id, name, target, arguments, expected=None):
        if name not in READS | WRITES or not isinstance(call_id, str) or not 1 <= len(call_id) <= 256:
            raise ProbeError('Invalid GitHub tool call.')
        target_raw = target_checked(target)
        args_raw = encoded(arguments, 256 * 1024)
        expected_raw = encoded(expected or {}, 4096)
        mutation = name in WRITES
        intent = hashlib.sha256((name + '\n' + target_raw + '\n' + args_raw).encode()).hexdigest()
        with self.state.connect() as db:
            turn = self._active(db, turn_id)
            old = db.execute('SELECT * FROM tool_calls WHERE turn_id=? AND call_id=?', (turn_id, call_id)).fetchone()
            if old:
                if old['intent_hash'] != intent:
                    raise ProbeError('Tool call identifier was reused for different work.')
                return {'new': False, 'status': old['status'], 'evidence': json.loads(old['evidence'])}
            if turn['calls'] >= 12:
                raise ProbeError('GitHub reply reached its tool-call limit.')
            if mutation:
                # An unknown write blocks replacement writes to that repository,
                # even with a new call ID, request, path or branch. Reads may
                # reconcile it; the executor must prove an outcome first.
                for row in db.execute("SELECT target FROM tool_calls WHERE mutation=1 AND status IN ('dispatched','uncertain')"):
                    old_target = json.loads(row['target'])
                    if (old_target['owner'].casefold(), old_target['repo'].casefold()) == (target['owner'].casefold(), target['repo'].casefold()):
                        raise ProbeError('A previous GitHub save outcome is unknown; verification is required before another write.')
            now = time.time()
            db.execute('INSERT INTO tool_calls(turn_id,call_id,name,ordinal,target,intent_hash,payload,expected,mutation,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (turn_id, call_id, name, turn['calls'] + 1, target_raw, intent, args_raw if mutation else '', expected_raw, int(mutation), 'prepared', now, now))
            db.execute('UPDATE tool_turns SET calls=calls+1,updated=? WHERE turn_id=?', (now, turn_id))
            return {'new': True, 'status': 'prepared', 'evidence': {}}

    def dispatch(self, turn_id, call_id):
        with self.state.connect() as db:
            self._active(db, turn_id)
            if not db.execute("UPDATE tool_calls SET status='dispatched',updated=? WHERE turn_id=? AND call_id=? AND status='prepared'", (time.time(), turn_id, call_id)).rowcount:
                raise ProbeError('GitHub operation cannot be dispatched again.')

    def outcome(self, turn_id, call_id, status, evidence=None, error=''):
        if status not in {'succeeded', 'failed', 'uncertain', 'cancelled', 'interrupted'} or error not in ERRORS:
            raise ProbeError('Invalid GitHub operation outcome.')
        with self.state.connect() as db:
            row = db.execute('SELECT * FROM tool_calls WHERE turn_id=? AND call_id=?', (turn_id, call_id)).fetchone()
            if not row:
                raise ProbeError('GitHub operation does not exist.')
            allowed = {'prepared': {'failed', 'cancelled'}, 'dispatched': {'succeeded', 'failed', 'uncertain', 'interrupted'}, 'uncertain': {'succeeded', 'failed'}}
            if status not in allowed.get(row['status'], set()):
                raise ProbeError('GitHub operation outcome is already final or invalid.')
            if status == 'uncertain' and not row['mutation']:
                raise ProbeError('Only a mutation can have an uncertain write outcome.')
            if row['mutation'] and status == 'interrupted':
                raise ProbeError('An interrupted mutation must remain uncertain.')
            raw = evidence_checked(evidence or {}, json.loads(row['target']))
            db.execute('UPDATE tool_calls SET status=?,evidence=?,error=?,payload=?,updated=? WHERE turn_id=? AND call_id=?',
                       (status, raw, error, row['payload'] if status == 'uncertain' else '', time.time(), turn_id, call_id))

    def activity(self, assistant):
        with self.state.connect() as db:
            return self.activity_rows(db, assistant)

    @staticmethod
    def activity_rows(db, assistant):
        return [dict(row) | {'target': json.loads(row['target']), 'evidence': json.loads(row['evidence'])}
                for row in db.execute('''SELECT c.name,c.ordinal,c.target,c.status,c.evidence,c.error,c.created,c.updated
                    FROM tool_calls c JOIN tool_turns t ON t.turn_id=c.turn_id
                    WHERE t.assistant_message_id=? ORDER BY c.ordinal''', (assistant,))]

    @staticmethod
    def stop(db, thread):
        now = time.time()
        db.execute("UPDATE tool_calls SET status='cancelled',error='cancelled',payload='',updated=? WHERE status='prepared' AND turn_id IN (SELECT turn_id FROM tool_turns WHERE thread_id=?)", (now, thread))
        db.execute("UPDATE tool_turns SET phase='stopped',continuation='',updated=? WHERE thread_id=? AND phase NOT IN ('completed','incomplete','stopped','interrupted')", (now, thread))

    @staticmethod
    def delete_thread(db, thread):
        db.execute('DELETE FROM tool_calls WHERE turn_id IN (SELECT turn_id FROM tool_turns WHERE thread_id=?)', (thread,))
        db.execute('DELETE FROM tool_turns WHERE thread_id=?', (thread,))
