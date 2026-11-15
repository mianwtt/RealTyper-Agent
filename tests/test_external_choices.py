import tempfile
import unittest
from unittest.mock import Mock,patch
from pinyin_agent import windows as win
from pinyin_agent.domain import Chunk
from pinyin_agent.engine import Agent
from pinyin_agent.external import ExternalBackend
from pinyin_agent.planner import make_plan
from pinyin_agent.scope import InsertionScope
from test_scope import Buffer,NoDelay


class ExternalChoiceTests(unittest.TestCase):
    def run_case(self,text,english_first=False,success_on=2):
        buffer=Buffer('前😀后文',2)
        backend=ExternalBackend(InsertionScope(buffer),1)
        backend.guard=backend.editor.guard
        backend.checkpoint=backend.guard
        backend.rhythm=NoDelay()
        backend.after_chunk=lambda chunk,checkpoint:None
        backend.read_commit=lambda *args,**kwargs:backend.read()
        backend.recover_chinese_mode=Mock(return_value='shift')
        state={'trial':None,'probes':[]}
        backend.type_pinyin=lambda chunk,checkpoint:state.update(trial=chunk)
        def key(vk,*args):
            if 0x31<=vk<=0x39:
                state['probes'].append((state['trial'].text,vk-0x30))
                if len(state['probes'])<success_on:buffer.write(state['trial'].keys+'1' if english_first and len(state['probes'])==1 else '是')
                else:buffer.write(state['trial'].text)
            elif vk==8:buffer.key(vk)
        copied=[]
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(win,'send_key',side_effect=key),patch('pinyin_agent.windows.time.sleep'):
                with patch.object(win,'set_clipboard_text',side_effect=lambda value,owner:copied.append(value)),patch.object(win,'clipboard_text',side_effect=lambda:copied[-1]):
                    Agent(backend,run_dir=directory).run(text,make_plan(text))
        self.assertEqual(buffer.text,'前😀'+text+'后文')
        self.assertEqual(copied,[text])
        self.assertEqual(len(state['probes']),success_on)
        return backend,state,buffer

    def test_suffix_word_hint_only_removes_owned_extra_prefix(self):
        _,state,buffer=self.run_case('式')
        self.assertTrue(state['probes'][1][0].endswith('式'))
        self.assertEqual(buffer.caret,(3,3))

    def test_third_attempt_uses_a_new_candidate_after_hint_failure(self):
        _,state,_=self.run_case('式',success_on=3)
        self.assertEqual(state['probes'],[('式',1),('方式',1),('式',2)])

    def test_english_recovery_and_word_hint_share_two_attempts(self):
        backend,state,_=self.run_case('已',english_first=True)
        self.assertEqual(state['probes'][1],('已经',1))
        backend.recover_chinese_mode.assert_called_once_with(False)

    def test_phrase_hint_disambiguates_fali_within_two_attempts(self):
        _,state,_=self.run_case('法力')
        self.assertEqual(state['probes'][1][0],'法力无边')

    def test_idiom_hint_disambiguates_fatigue_within_two_attempts(self):
        _,state,_=self.run_case('乏力')
        self.assertEqual(state['probes'][1][0],'回天乏力')
