import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.deps'))
from pinyin_agent import windows as win
from pinyin_agent.engine import Agent
from pinyin_agent.external import ExternalBackend,TargetError,UIAInput
from pinyin_agent.planner import make_plan
from pinyin_agent.scope import InsertionScope,ScopeError


class Buffer:
    hwnd=123
    browser=False
    tsf=None
    def __init__(self,text,position,selection_end=None):
        self.text=text
        self.caret=(position,position if selection_end is None else selection_end)
        self.selections=[]
        self.deletions=[]
    def guard(self):pass
    def read(self):return self.text
    def selection(self):return self.caret
    def select(self,start,end,expected):
        assert self.text==expected
        self.selections.append((start,end))
        self.caret=(start,end)
    def write(self,text):
        start,end=self.caret
        self.text=self.text[:start]+text+self.text[end:]
        self.caret=(start+len(text),start+len(text))
    def key(self,key):
        assert key==8
        start,end=self.caret
        if start==end:start-=1
        self.deletions.append((start,end,self.text[start:end]))
        self.caret=(start,end)
        self.write('')


class NoDelay:
    def begin_chunk(self):pass
    def wait(self,kind,checkpoint):checkpoint()
    def after_literal(self,text,index,checkpoint):checkpoint()
    def describe(self):return {}
    def summary(self):return {}


class ScopedBackend(ExternalBackend):
    def __init__(self,editor,wrong_once=False):
        super().__init__(InsertionScope(editor),123)
        self.buffer=editor
        self.wrong_once=wrong_once
        self.rhythm=NoDelay()
    def guard(self):self.editor.guard()
    def after_chunk(self,chunk,checkpoint):pass
    def choose(self,chunk,checkpoint,emit):
        checkpoint()
        self.selection()
        text=chunk.text
        if self.wrong_once:
            self.wrong_once=False
            text='法力'
        self.buffer.write(text)
        return True


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def run_task(self,buffer,source,wrong_once=False):
        backend=ScopedBackend(buffer,wrong_once)
        copied=[]
        def copy(text,owner):copied.append(text)
        with patch.object(win,'send_key',side_effect=buffer.key),patch.object(win,'send_literal',side_effect=buffer.write):
            with patch.object(win,'set_clipboard_text',side_effect=copy),patch.object(win,'clipboard_text',side_effect=lambda:copied[-1]):
                result=Agent(backend,run_dir=self.directory.name).run(source,make_plan(source))
        return backend,result,copied

    def test_append_to_existing_text_only_copies_new_text(self):
        buffer=Buffer('已有文字',4)
        _,result,copied=self.run_task(buffer,'发力')
        self.assertEqual(buffer.text,'已有文字发力')
        self.assertEqual(result,'发力')
        self.assertEqual(copied,['发力'])
        self.assertEqual(buffer.caret,(6,6))

    def test_insert_in_middle_keeps_prefix_and_suffix(self):
        buffer=Buffer('前文后文',2)
        self.run_task(buffer,'发力')
        self.assertEqual(buffer.text,'前文发力后文')
        self.assertEqual(buffer.caret,(4,4))
        self.assertEqual(buffer.selections,[])

    def test_insert_at_beginning_preserves_original_suffix(self):
        buffer=Buffer('原有文字',0)
        self.run_task(buffer,'发力')
        self.assertEqual(buffer.text,'发力原有文字')
        self.assertEqual(buffer.caret,(2,2))

    def test_repair_is_confined_to_new_text_among_identical_old_words(self):
        original='旧法力😀\n后文法力'
        position=len('旧法力😀\n')
        buffer=Buffer(original,position)
        _,result,copied=self.run_task(buffer,'发力',wrong_once=True)
        self.assertEqual(buffer.text,original[:position]+'发力'+original[position:])
        self.assertEqual(buffer.deletions,[(position,position+1,'法')])
        self.assertEqual(result,'发力')
        self.assertEqual(copied,['发力'])

    def test_selected_original_text_is_preserved(self):
        buffer=Buffer('ABCD',1,3)
        self.run_task(buffer,'X')
        self.assertEqual(buffer.text,'ABCXD')
        self.assertEqual(buffer.caret,(4,4))

    def test_repeated_tasks_do_not_recheck_previous_task(self):
        buffer=Buffer('旧文字',3)
        self.run_task(buffer,'发力')
        _,result,copied=self.run_task(buffer,'A😀')
        self.assertEqual(buffer.text,'旧文字发力A😀')
        self.assertEqual(result,'A😀')
        self.assertEqual(copied,['A😀'])

    def test_scope_cannot_select_original_text(self):
        buffer=Buffer('前文后文',2)
        scope=InsertionScope(buffer)
        buffer.write('新增')
        for start,end in ((-1,0),(0,3)):
            with self.assertRaises(ScopeError):scope.select(start,end,'新增')
        self.assertEqual(buffer.selections,[])
        self.assertEqual(buffer.text,'前文新增后文')

    def test_backspace_at_scope_start_never_deletes_original_prefix(self):
        buffer=Buffer('前文后文',2)
        backend=ScopedBackend(buffer)
        with patch.object(win,'send_key') as key:
            with self.assertRaises(TargetError):backend.backspace()
        key.assert_not_called()
        self.assertEqual(buffer.text,'前文后文')

    def test_caret_move_during_planning_is_detected(self):
        buffer=Buffer('ABC',1)
        scope=InsertionScope(buffer)
        buffer.caret=(2,2)
        with self.assertRaises(ScopeError):scope.ensure_pristine()
        self.assertEqual(buffer.text,'ABC')

    def test_original_user_edit_is_not_undone(self):
        buffer=Buffer('ABC',1)
        scope=InsertionScope(buffer)
        buffer.text='AXC'
        with self.assertRaises(ScopeError):scope.read()
        self.assertEqual(buffer.text,'AXC')
        self.assertEqual(buffer.selections,[])

    def test_uia_nonempty_value_without_caret_pattern_is_not_guessed(self):
        editor=UIAInput.__new__(UIAInput)
        editor.text=None
        editor.read=lambda:'已有文字'
        with self.assertRaises(TargetError):editor.selection()


if __name__=='__main__':unittest.main()
