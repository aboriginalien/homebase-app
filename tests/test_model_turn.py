import copy
import json
import sys
import threading
import time
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from model_turn import Round, TurnError, continuation

MODEL='gpt-5.6-sol'
CALL={'type':'function_call','name':'get_file_contents','namespace':'github','call_id':'call1',
      'id':'item1','status':'completed','arguments':'{"owner":"synthetic","repo":"test"}'}
REASON={'type':'reasoning','id':'reason1','summary':[],'encrypted_content':'OPAQUE_SYNTHETIC'}
def terminal(output):
    return {'type':'response.completed','response':{'model':MODEL,'status':'completed',
        'reasoning':{'effort':'high'},'service_tier':'default','output':output}}

class RoundTests(unittest.TestCase):
    def round(self):return Round(MODEL,threading.Event(),time.monotonic()+2)
    def test_preserves_reasoning_namespace_ids_and_multiple_calls(self):
        r=self.round();second={**CALL,'call_id':'call2','id':'item2'}
        r.event(terminal([REASON,CALL,second]));output,calls,text,usage=r.result()
        self.assertEqual(len(calls),2);self.assertEqual(text,'')
        results=[{'type':'function_call_output','call_id':c['call_id'],'output':'{}'} for c in [CALL,second]]
        value=continuation([{'role':'user','content':'synthetic'}],output,results)
        self.assertEqual(value[1],REASON);self.assertEqual(value[2]['namespace'],'github')
    def test_stream_reconstruction_requires_done_and_terminal(self):
        r=self.round()
        r.event({'type':'response.output_item.added','output_index':0,'item':{**CALL,'arguments':''}})
        r.event({'type':'response.function_call_arguments.delta','output_index':0,'delta':'{"owner":'})
        r.event({'type':'response.function_call_arguments.delta','output_index':0,'delta':'"synthetic","repo":"test"}'})
        with self.assertRaises(TurnError):r.result()
        r.event({'type':'response.output_item.done','output_index':0,'item':CALL});r.event(terminal([]))
        self.assertEqual(r.result()[1][0][1]['repo'],'test')
    def test_unfinished_output_not_dispatchable(self):
        r=self.round();r.event({'type':'response.output_item.added','output_index':0,'item':CALL})
        with self.assertRaises(TurnError):r.event(terminal([]))
    def test_failed_missing_terminal_bad_settings_and_namespace_remain_visible(self):
        r=self.round();r.event({'type':'response.output_text.delta','delta':'Draft'})
        with self.assertRaises(TurnError):r.result()
        event=terminal([CALL]);event['response']['model']='other-model'
        with self.assertRaises(TurnError):self.round().event(event)
        with self.assertRaises(TurnError):self.round().event({'type':'response.failed'})
    def test_malformed_oversize_duplicate_arguments(self):
        for raw in ['{','[]','"string"','x'*(128*1024+1)]:
            r=self.round();r.event(terminal([{**CALL,'arguments':raw}]))
            with self.assertRaises(TurnError):r.result()
        r=self.round();r.event(terminal([CALL,CALL]))
        with self.assertRaises(TurnError):r.result()
    def test_stop_and_total_deadline_prevent_dispatch(self):
        r=self.round();r.event(terminal([CALL]));r.stop.set()
        with self.assertRaises(TurnError):r.result()
        r=self.round();r.deadline=time.monotonic()-1
        with self.assertRaises(TurnError):r.event(terminal([CALL]))
    def test_outputs_must_match_exact_order(self):
        with self.assertRaises(TurnError):continuation([], [CALL], [{'type':'function_call_output','call_id':'different','output':'{}'}])
    def test_plain_text_and_terminal_text(self):
        r=self.round();r.event({'type':'response.output_text.delta','delta':'Hello'});r.event(terminal([]))
        self.assertEqual(r.result()[2],'Hello')
        r=self.round();r.event(terminal([{'type':'message','content':[{'type':'output_text','text':'Final'}]}]))
        self.assertEqual(r.result()[2],'Final')

if __name__=='__main__':unittest.main()
