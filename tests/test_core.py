import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/".deps"))
from pinyin_agent.domain import Chunk, differences, utf16_offset, validate_plan
from pinyin_agent.engine import Agent, Stopped
from pinyin_agent.planner import make_plan


class Editor:
    """Contract test adapter; actual Windows IME checks use --smoke separately."""
    def __init__(self,text="",wrong_once=False,missing_words=False):
        self.text=text
        self.selection=(len(text),len(text))
        self.wrong_once=wrong_once
        self.missing_words=missing_words
        self.actions=[]
        self.focused=True

    def begin(self):pass
    def end(self):pass
    def emergency_stop(self):return False
    def guard(self):
        if not self.focused:raise RuntimeError("focus lost")
    def read(self):return self.text
    def write(self,text):
        a,b=self.selection
        self.text=self.text[:a]+text+self.text[b:]
        self.selection=(a+len(text),a+len(text))
    def choose(self,chunk,checkpoint,emit):
        if self.missing_words and len(chunk.text)>1:return False
        self.actions.append(("pinyin",chunk.text))
        if self.wrong_once:
            self.wrong_once=False
            self.write("法力")
        else:self.write(chunk.text)
        return True
    def literal(self,text,checkpoint):
        self.actions.append(("literal",text))
        self.write(text)
    def select(self,start,end,expected):
        assert expected==self.text
        self.selection=(start,end)
    def backspace(self):
        self.actions.append(("backspace",self.selection))
        self.write("")
    def copy_all(self):
        self.actions.append(("copy",self.text))
        return self.text


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def agent(self,editor):return Agent(editor,run_dir=self.directory.name)

    def test_rejects_model_rewrite_before_any_input(self):
        editor=Editor()
        with self.assertRaises(ValueError):
            self.agent(editor).run("发力",[Chunk("法力",("fa","li"))])
        self.assertEqual(editor.actions,[])

    def test_planning_preserves_spaces_lines_punctuation_and_polyphones(self):
        text="中国银行行长在重庆。\nHello  world! 音乐快乐 😀"
        chunks=make_plan(text)
        self.assertEqual("".join(c.text for c in chunks),text)
        self.assertIn(Chunk("行长",("hang","zhang")),chunks)
        self.assertIn(Chunk("重庆",("chong","qing")),chunks)

    def test_model_invalid_reading_is_corrected_without_changing_words(self):
        class Model:
            def plan(self,text,context):return [{"text":text,"syllables":["xuan","wen"]}]
        notices=[]
        chunks=make_plan("原文",Model(),notices.append)
        self.assertEqual(chunks,[Chunk("原文",("yuan","wen"))])
        self.assertTrue(any("xuan → yuan" in notice for notice in notices))

    def test_model_valid_polyphone_reading_is_preserved(self):
        class Model:
            def plan(self,text,context):return [{"text":text,"syllables":["zhong"]}]
        self.assertEqual(make_plan("重",Model()),[Chunk("重",("zhong",))])

    def test_dictionary_fallback_keeps_context_across_model_word_boundaries(self):
        class Model:
            def plan(self,text,context):return [{"text":"银","syllables":["yin"]},{"text":"行","syllables":["banana"]}]
        chunks=make_plan("银行",Model())
        self.assertEqual(chunks,[Chunk("银",("yin",)),Chunk("行",("hang",))])

    def test_bad_model_reading_does_not_hide_rewritten_text(self):
        class Model:
            def plan(self,text,context):return [{"text":"法力","syllables":["banana","li"]}]
        with self.assertRaises(ValueError):make_plan("发力",Model())

    def test_missing_model_syllable_is_rebuilt_from_dictionary(self):
        class Model:
            def plan(self,text,context):return [{"text":text,"syllables":["yuan"]}]
        self.assertEqual(make_plan("原文",Model()),[Chunk("原文",("yuan","wen"))])

    def test_curve_missing_merged_extra_and_malformed_syllables_are_repaired(self):
        for bad in (["quxian"],["qu","xian","xian"],[],None,"quxian",[None,"xian"],[{},"xian"],["QU","XIAN"],["qu1","xian4"]):
            with self.subTest(syllables=bad):
                class Model:
                    def plan(self,text,context):return [{"text":text,"syllables":bad}]
                self.assertEqual(make_plan("曲线",Model()),[Chunk("曲线",("qu","xian"))])

    def test_missing_pinyin_field_is_supplied_without_changing_text(self):
        class Model:
            def plan(self,text,context):return [{"text":text}]
        self.assertEqual(make_plan("曲线",Model()),[Chunk("曲线",("qu","xian"))])

    def test_bad_syllable_count_does_not_allow_text_rewrite(self):
        class Model:
            def plan(self,text,context):return [{"text":"曲面","syllables":["qu"]}]
        with self.assertRaises(ValueError):make_plan("曲线",Model())

    def test_count_repair_keeps_dictionary_context_across_word_boundaries(self):
        class Model:
            def plan(self,text,context):return [{"text":"银","syllables":["yin"]},{"text":"行","syllables":[]}]
        self.assertEqual(make_plan("银行",Model()),[Chunk("银",("yin",)),Chunk("行",("hang",))])

    def test_model_omission_rejected(self):
        class Model:
            def plan(self,text,context):return [{"text":"发","syllables":["fa"]}]
        with self.assertRaises(ValueError):make_plan("发力",Model())

    def test_wrong_commit_is_deleted_and_retyped(self):
        editor=Editor(wrong_once=True)
        result=self.agent(editor).run("发力",make_plan("发力"))
        self.assertEqual(result,"发力")
        self.assertTrue(any(op=="backspace" for op,_ in editor.actions))
        self.assertEqual(editor.actions[-1],("copy","发力"))

    def test_missing_phrase_stops_without_character_fallback(self):
        editor=Editor(missing_words=True)
        with self.assertRaisesRegex(RuntimeError,"最多尝试 3 次"):
            self.agent(editor).run("发力",make_plan("发力"))
        self.assertEqual(editor.actions,[])

    def test_many_independent_errors_are_repaired_without_stale_offsets(self):
        text="发力A生产力B模型C银行D重庆E音乐F"
        editor=Editor("法力XXA生力B摸型C银航D重亲E音月FF")
        self.agent(editor).run(text,make_plan(text),repair_only=True)
        self.assertEqual(editor.text,text)

    def test_nonempty_editor_is_preserved(self):
        editor=Editor("用户已有内容")
        with self.assertRaises(RuntimeError):self.agent(editor).run("发力",make_plan("发力"))
        self.assertEqual(editor.text,"用户已有内容")
        self.assertEqual(editor.actions,[])

    def test_focus_loss_prevents_any_input(self):
        editor=Editor()
        editor.focused=False
        with self.assertRaises(RuntimeError):self.agent(editor).run("发力",make_plan("发力"))
        self.assertEqual(editor.actions,[])

    def test_stop_keeps_current_text(self):
        editor=Editor("保留")
        agent=self.agent(editor)
        agent.stop_event.set()
        with self.assertRaises(Stopped):agent.run("发力",make_plan("发力"),repair_only=True)
        self.assertEqual(editor.text,"保留")

    def test_utf16_offsets_include_surrogates_and_crlf(self):
        self.assertEqual(utf16_offset("甲😀\n乙",3),5)

    def test_repeated_long_text_diff_does_not_hide_changes(self):
        expected="发力"*220+"生产力"+"发力"*220
        actual="发力"*220+"生力"+"发力"*220
        patches=differences(expected,actual)
        for patch in reversed(patches):actual=actual[:patch.start]+patch.new+actual[patch.end:]
        self.assertEqual(actual,expected)


if __name__=="__main__":unittest.main()
