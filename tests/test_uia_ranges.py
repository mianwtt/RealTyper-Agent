"""Range resolution against providers whose text units differ from Python indices."""
import unittest
from types import SimpleNamespace
from pinyin_agent.external import UIAInput,TargetError,normalized


class Range:
    def __init__(self,document,start=0,end=None):
        self.document=document
        self.start=start
        self.end=len(document.value) if end is None else end
    def Clone(self):return Range(self.document,self.start,self.end)
    def GetText(self,limit):return self.document.value[self.start:self.end][:limit]
    def FindText(self,text,backward,ignore_case):
        position=self.document.value.find(text,self.start,self.end)
        return None if position<0 else Range(self.document,position,position+len(text))
    def MoveEndpointByUnit(self,*args,**kwargs):
        raise AssertionError('Character units cannot be treated as code-point indices')
    def MoveEndpointByRange(self,endpoint,other,other_endpoint,waitTime=0):
        position=other.start if other_endpoint==0 else other.end
        if endpoint==0:
            self.start=position
            self.end=max(self.end,position)
        else:
            self.end=position
            self.start=min(self.start,position)
    def Select(self,waitTime=0):self.document.selected=(self.start,self.end)


class UIARangeTests(unittest.TestCase):
    def editor(self,text):
        document=SimpleNamespace(value=text,selected=None)
        editor=UIAInput.__new__(UIAInput)
        editor.guard=lambda:None
        editor.read=lambda:normalized(document.value)
        editor.text=SimpleNamespace(DocumentRange=Range(document),
                                   GetSelection=lambda:[Range(document,*(document.selected or (0,0)))])
        return editor,document

    def test_emoji_combining_glyphs_and_repeated_words(self):
        text='前😀e\u0301已后已'
        editor,document=self.editor(text)
        editor.select(6,7,text)
        self.assertEqual(document.selected,(6,7))
        editor.select(4,5,text)
        self.assertEqual(document.selected,(4,5))

    def test_original_prefix_and_suffix_around_correction(self):
        text='旧前文😀三次试验中，最张后文'
        editor,document=self.editor(text)
        start=text.index('最张')
        editor.select(start,start+2,text)
        self.assertEqual(document.selected,(start,start+2))

    def test_crlf_provider_maps_normalized_lf_indices(self):
        editor,document=self.editor('前\r\n已后')
        editor.select(2,3,'前\n已后')
        self.assertEqual(document.selected,(3,4))

    def test_collapse_selection_to_exact_end(self):
        editor,document=self.editor('😀前文')
        editor.select(3,3,'😀前文')
        self.assertEqual(document.selected,(3,3))

    def test_changed_text_is_not_selected(self):
        editor,document=self.editor('前文已')
        with self.assertRaises(TargetError):editor.select(2,3,'前文一')
        self.assertIsNone(document.selected)
