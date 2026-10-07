"""Bounded Responses round parsing and faithful client-side function continuation.

Provider output, including opaque reasoning, stays private. Tool execution is
separate and occurs only after a verified completed model round.
"""
import json
import time
from homebase_probe import ProbeError

MAX_TEXT = 32000
MAX_ARGUMENT_BYTES = 128 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
SUPPORTED = {'message', 'reasoning', 'function_call'}

class TurnError(ProbeError):
    pass

class Round:
    def __init__(self, model, stop, deadline, on_text=lambda text: None):
        self.model, self.stop, self.deadline, self.on_text = model, stop, deadline, on_text
        self.items = {}; self.done = set(); self.arguments = {}; self.text = ''
        self.completed = None

    def check(self):
        if self.stop.is_set(): raise TurnError('Reply stopped. Any accepted GitHub changes remain recorded in Activity.')
        if time.monotonic() >= self.deadline: raise TurnError('Reply reached its time limit; verified changes remain recorded in Activity.')

    def event(self, event):
        self.check()
        if len(json.dumps(event).encode()) > MAX_OUTPUT_BYTES:
            raise TurnError('Provider output exceeded the supported size.')
        kind = event.get('type')
        if kind in ('response.failed', 'response.incomplete', 'error'):
            raise TurnError('Provider reply failed or is incomplete. Any GitHub effects remain in Activity.')
        if kind == 'response.output_text.delta':
            delta = event.get('delta')
            if not isinstance(delta, str) or len(self.text) + len(delta) > MAX_TEXT:
                raise TurnError('Reply exceeded the text limit; incomplete.')
            self.text += delta; self.on_text(self.text)
        elif kind in ('response.output_item.added', 'response.output_item.done'):
            index, item = event.get('output_index'), event.get('item')
            if type(index) is not int or index < 0 or index > 24 or not isinstance(item, dict):
                raise TurnError('Provider output item is invalid.')
            if item.get('type') not in SUPPORTED: raise TurnError('Provider output item is unsupported.')
            self.items[index] = item
            if kind.endswith('.done'): self.done.add(index)
        elif kind == 'response.function_call_arguments.delta':
            index, delta = event.get('output_index'), event.get('delta')
            if type(index) is not int or not isinstance(delta, str): raise TurnError('Tool argument stream is invalid.')
            raw = self.arguments.get(index, '') + delta
            if len(raw.encode()) > MAX_ARGUMENT_BYTES: raise TurnError('Tool arguments exceeded the supported size.')
            self.arguments[index] = raw
        elif kind == 'response.completed':
            r = event.get('response')
            if not isinstance(r, dict) or (r.get('status') != 'completed' or r.get('model') != self.model
                or r.get('reasoning', {}).get('effort') != 'high' or r.get('service_tier') != 'default'):
                raise TurnError('Returned model/high/standard settings were not verified; reply is incomplete.')
            output = r.get('output')
            if output is None or output == []:
                if set(self.items) != self.done: raise TurnError('Provider output did not finish.')
                output = [self.items[i] for i in sorted(self.items)]
            if not isinstance(output, list) or len(json.dumps(output).encode()) > MAX_OUTPUT_BYTES:
                raise TurnError('Provider output is invalid or oversized.')
            for item in output:
                if not isinstance(item, dict) or item.get('type') not in SUPPORTED:
                    raise TurnError('Provider output item is unsupported.')
            self.completed = {**r, 'output':output}

    def result(self):
        self.check()
        if self.completed is None: raise TurnError('Stream ended without response.completed; reply is incomplete.')
        calls = []; seen = set()
        for item in self.completed['output']:
            if item['type'] != 'function_call': continue
            if item.get('status') not in (None, 'completed'):
                raise TurnError('Tool call is incomplete.')
            identity = item.get('call_id')
            if not isinstance(identity, str) or not identity or len(identity)>256 or identity in seen:
                raise TurnError('Tool call identifier is invalid or duplicated.')
            raw = item.get('arguments')
            if not isinstance(raw, str) or len(raw.encode()) > MAX_ARGUMENT_BYTES:
                raise TurnError('Tool arguments are invalid or oversized.')
            try: arguments = json.loads(raw)
            except ValueError: raise TurnError('Tool arguments are invalid JSON.') from None
            if not isinstance(arguments, dict): raise TurnError('Tool arguments must be an object.')
            seen.add(identity); calls.append((item, arguments))
        if not calls and not self.text.strip():
            # Some transports carry completed text only in the final output item.
            self.text = ''.join(part.get('text', '') for item in self.completed['output']
                if item['type']=='message' for part in item.get('content', []) if part.get('type')=='output_text')
            if not self.text.strip() or len(self.text)>MAX_TEXT:
                raise TurnError('Completed response had no supported text.')
        return self.completed['output'], calls, self.text, self.completed.get('usage') or {}

def continuation(history, output, results):
    """Retain output order and every call's original ID, including reasoning."""
    expected=[i['call_id'] for i in output if i.get('type')=='function_call']
    if expected != [i.get('call_id') for i in results]: raise TurnError('Tool results do not match the original calls.')
    if any(i.get('type')!='function_call_output' or not isinstance(i.get('output'),str) for i in results):
        raise TurnError('Tool result format is invalid.')
    value = history + output + results
    if len(json.dumps(value).encode()) > 1536*1024: raise TurnError('Tool conversation exceeded the supported size.')
    return value
