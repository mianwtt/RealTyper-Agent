import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/".deps"))
from pinyin_agent.windows import WindowsBackend


class CursorTests(unittest.TestCase):
    def backend(self,text,selection=None):
        state={"text":text,"selection":selection or (len(text),len(text))}
        backend=WindowsBackend.__new__(WindowsBackend)
        backend.guard=Mock()
        backend.composition=lambda:""
        backend.read=lambda:state["text"]
        backend.selection=lambda:state["selection"]
        def select(start,end,expected):
            self.assertEqual(state["text"],expected)
            state["selection"]=(start,end)
        backend.select=Mock(side_effect=select)
        def backspace():
            start,end=state["selection"]
            if start==end:start-=1
            state["text"]=state["text"][:start]+state["text"][end:]
            state["selection"]=(start,start)
        backend.backspace=Mock(side_effect=backspace)
        return backend,state

    def test_tail_retry_does_not_select_or_reposition_text(self):
        backend,state=self.backend("前文法力")
        mode=backend.delete_range(2,4,"前文法力")
        self.assertEqual(mode,"tail_backspace")
        self.assertEqual(state["text"],"前文")
        self.assertEqual(state["selection"],(2,2))
        backend.select.assert_not_called()
        self.assertEqual(backend.backspace.call_count,2)

    def test_middle_correction_only_selects_the_wrong_range(self):
        backend,state=self.backend("法力后文")
        mode=backend.delete_range(0,2,"法力后文")
        self.assertEqual(mode,"selected_range")
        self.assertEqual(state["text"],"后文")
        backend.select.assert_called_once_with(0,2,"法力后文")

    def test_tail_retry_preserves_emoji_and_linebreak_prefix(self):
        backend,state=self.backend("A😀\n法力")
        backend.delete_range(3,5,"A😀\n法力")
        self.assertEqual(state["text"],"A😀\n")
        backend.select.assert_not_called()

    def test_moved_caret_does_not_backspace_the_preceding_text(self):
        backend,state=self.backend("前文法力",(2,2))
        self.assertEqual(backend.delete_range(2,4,"前文法力"),"selected_range")
        self.assertEqual(state["text"],"前文")

    def test_non_han_tail_uses_a_range_to_preserve_graphemes(self):
        backend,state=self.backend("文字😀")
        self.assertEqual(backend.delete_range(2,3,"文字😀"),"selected_range")
        self.assertEqual(state["text"],"文字")

    def test_stale_text_is_not_deleted(self):
        backend,_=self.backend("前文法力X")
        with self.assertRaises(RuntimeError):backend.delete_range(2,4,"前文法力")
        backend.backspace.assert_not_called()
        backend.select.assert_not_called()


if __name__=="__main__":unittest.main()
