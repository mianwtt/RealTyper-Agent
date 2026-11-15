from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from pinyin_agent import windows as win
from pinyin_agent.domain import Chunk
from pinyin_agent.external import ExternalBackend,EnglishInputDetected,TargetError,u
from pinyin_agent.scope import InsertionScope
from test_scope import Buffer,NoDelay


class ImeRecoveryTests(unittest.TestCase):
    def backend(self):
        backend=ExternalBackend(SimpleNamespace(hwnd=123,thread=456),123)
        backend.guard=Mock()
        backend.checkpoint=Mock()
        backend.rhythm=NoDelay()
        return backend

    def test_english_commit_is_removed_only_from_owned_range(self):
        buffer=Buffer('前文😀后文',3)
        backend=ExternalBackend(InsertionScope(buffer),123)
        backend.guard=backend.editor.guard
        backend.checkpoint=backend.guard
        backend.rhythm=NoDelay()
        buffer.write("fa'li1")
        with patch.object(win,'send_key',side_effect=buffer.key):
            with self.assertRaises(EnglishInputDetected):
                backend.validate_commit(Chunk('发力',('fa','li')),'',"fa'li1",0,0,1)
        self.assertEqual(buffer.text,'前文😀后文')
        self.assertEqual(buffer.caret,(3,3))
        self.assertTrue(all(start>=3 for start,_,_ in buffer.deletions))

    def test_retries_once_after_detected_english(self):
        backend=self.backend()
        backend.recover_chinese_mode=Mock(return_value='shift')
        events=[]
        with patch.object(win.WindowsBackend,'choose',side_effect=[EnglishInputDetected(),True]) as choose:
            self.assertTrue(backend.choose(Chunk('发力',('fa','li')),lambda:None,lambda kind,**kw:events.append(kind)))
        self.assertEqual(choose.call_count,2)
        backend.recover_chinese_mode.assert_called_once_with(False)
        self.assertIn('ime_recovery',events)

    def test_cannot_toggle_shift_repeatedly(self):
        backend=self.backend()
        with patch.object(win.WindowsBackend,'choose',side_effect=EnglishInputDetected()):
            with patch.object(u,'GetKeyboardLayout',return_value=0x08040804),patch.object(win,'send_key') as key:
                with patch('pinyin_agent.external.time.sleep'):
                    with self.assertRaises(TargetError):
                        backend.choose(Chunk('发力',('fa','li')),lambda:None,lambda *a,**k:None)
        key.assert_called_once_with(0x10)

    def test_layout_switch_targets_the_focused_control(self):
        backend=self.backend()
        def layouts(count,array):
            if count:
                array[0]=0x04090409
                array[1]=0x08040804
            return 2
        with patch.object(u,'GetKeyboardLayout',side_effect=[0x04090409,0x08040804]):
            with patch.object(u,'GetKeyboardLayoutList',side_effect=layouts):
                with patch.object(u,'PostMessageW',return_value=True) as post,patch.object(win,'send_key') as key:
                    self.assertEqual(backend.recover_chinese_mode(False),'layout')
        post.assert_called_once_with(123,0x0050,0,0x08040804)
        key.assert_not_called()

    def test_missing_chinese_layout_stops_without_blind_toggle(self):
        backend=self.backend()
        with patch.object(u,'GetKeyboardLayout',return_value=0x04090409),patch.object(u,'GetKeyboardLayoutList',return_value=0):
            with patch.object(win,'send_key') as key:
                with self.assertRaises(TargetError):backend.recover_chinese_mode(False)
        key.assert_not_called()


if __name__=='__main__':unittest.main()
