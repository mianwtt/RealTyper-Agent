import tempfile
import unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace
from pinyin_agent import windows as win
from pinyin_agent.domain import Chunk
from pinyin_agent.engine import Agent
from pinyin_agent.external import ExternalBackend,EnglishInputDetected
from pinyin_agent.planner import make_plan
from test_core import Editor
from test_scope import NoDelay


class AttemptLimitTests(unittest.TestCase):
    def backend(self,second_succeeds=False,cached=None,success_on=None):
        backend=win.WindowsBackend.__new__(win.WindowsBackend)
        state={'text':'前文','digits':[],'keys':[]}
        backend.read=lambda:state['text']
        backend.selection=lambda:(len(state['text']),len(state['text']))
        backend.chinese_mode=Mock()
        backend.type_pinyin=Mock()
        backend.candidates=lambda:[]
        backend.rhythm=NoDelay()
        backend.fallback_announced=False
        backend.choice_cache={} if cached is None else {('fa','发'):cached}
        def delete(start,end,expected):
            self.assertEqual(state['text'],expected)
            state['text']=expected[:start]+expected[end:]
            return 'test_rollback'
        backend.delete_range=Mock(side_effect=delete)
        def key(vk):
            state['keys'].append(vk)
            if 0x31<=vk<=0x39:
                state['digits'].append(vk-0x30)
                state['text']+='发' if (second_succeeds or success_on) and len(state['digits'])==(success_on or 2) else '错'
        return backend,state,key

    def test_single_character_stops_after_three_choices_and_rolls_back(self):
        backend,state,key=self.backend()
        with patch.object(win,'send_key',side_effect=key),patch('pinyin_agent.windows.time.sleep'):
            self.assertFalse(backend.choose(Chunk('发',('fa',)),lambda:None,lambda *a,**kw:None))
        self.assertEqual(state['digits'],[1,2,3])
        self.assertEqual(backend.type_pinyin.call_count,3)
        self.assertEqual(state['text'],'前文')
        self.assertNotIn(0x22,state['keys'])

    def test_second_choice_can_succeed(self):
        backend,state,key=self.backend(second_succeeds=True)
        with patch.object(win,'send_key',side_effect=key),patch('pinyin_agent.windows.time.sleep'):
            self.assertTrue(backend.choose(Chunk('发',('fa',)),lambda:None,lambda *a,**kw:None))
        self.assertEqual(state['digits'],[1,2])
        self.assertEqual(state['text'],'前文发')

    def test_third_choice_can_succeed(self):
        backend,state,key=self.backend(success_on=3)
        with patch.object(win,'send_key',side_effect=key),patch('pinyin_agent.windows.time.sleep'):
            self.assertTrue(backend.choose(Chunk('发',('fa',)),lambda:None,lambda *a,**kw:None))
        self.assertEqual(state['digits'],[1,2,3])
        self.assertEqual(state['text'],'前文发')

    def test_cached_choice_counts_toward_same_limit(self):
        backend,state,key=self.backend(cached=(0,7))
        with patch.object(win,'send_key',side_effect=key),patch('pinyin_agent.windows.time.sleep'):
            self.assertFalse(backend.choose(Chunk('发',('fa',)),lambda:None,lambda *a,**kw:None))
        self.assertEqual(state['digits'],[7,1,2])
        self.assertEqual(backend.type_pinyin.call_count,3)

    def test_second_attempt_can_use_a_word_to_select_a_character(self):
        backend,state,_=self.backend()
        backend.retry_chunk=lambda chunk:Chunk('已经',('yi','jing'))
        def key(vk):
            if vk==0x31:
                state['digits'].append(1)
                state['text']+='一' if len(state['digits'])==1 else '已经'
        with patch.object(win,'send_key',side_effect=key),patch('pinyin_agent.windows.time.sleep'):
            self.assertTrue(backend.choose(Chunk('已',('yi',)),lambda:None,lambda *a,**kw:None))
        self.assertEqual(state['digits'],[1,1])
        self.assertEqual(state['text'],'前文已')
        self.assertEqual(backend.type_pinyin.call_args_list[1].args[0],Chunk('已经',('yi','jing')))
        self.assertEqual(backend.delete_range.call_count,2)

    def test_english_recovery_does_not_reset_attempt_budget(self):
        backend=ExternalBackend(SimpleNamespace(hwnd=1,thread=1),1)
        backend.rhythm=NoDelay()
        backend.recover_chinese_mode=Mock(return_value='shift')
        attempts=[]
        def wrong_mode(instance,*args):
            instance._attempt_budget.consume()
            attempts.append(1)
            raise EnglishInputDetected()
        with patch.object(win.WindowsBackend,'_choose',wrong_mode):
            with self.assertRaisesRegex(RuntimeError,'最多尝试 3 次'):
                backend.choose(Chunk('发',('fa',)),lambda:None,lambda *a,**kw:None)
        self.assertEqual(len(attempts),3)
        self.assertEqual(backend.recover_chinese_mode.call_count,2)

    def test_followup_repair_cannot_start_a_fourth_input_attempt(self):
        class WrongEditor(Editor):
            attempt_scope=win.WindowsBackend.attempt_scope
            def choose(self,chunk,checkpoint,emit):
                self._attempt_budget.consume()
                self.actions.append(('pinyin',chunk.text))
                self.write('错')
                return True
        editor=WrongEditor()
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError,'最多尝试 3 次'):
                Agent(editor,run_dir=folder).run('发力',make_plan('发力'))
        self.assertEqual(sum(action=='pinyin' for action,_ in editor.actions),3)


if __name__=='__main__':unittest.main()
