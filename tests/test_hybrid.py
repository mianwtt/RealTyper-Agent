import unittest
from unittest.mock import Mock
from pinyin_agent.domain import Chunk
from pinyin_agent.engine import Stopped
from pinyin_agent.planner import make_hybrid_plan,make_plan


class HybridTests(unittest.TestCase):
    def test_model_cannot_split_known_engineering_words(self):
        text='工程师复核射频电路的阻抗匹配方案，'
        rows=[c.to_dict() for c in make_plan(text)]
        rows[:1]=[{'text':'工程','syllables':['gong','cheng']},{'text':'师','syllables':['shi']}]
        model=Mock();model.plan.return_value=rows
        chunks=make_hybrid_plan(text,model)
        self.assertIn(Chunk('工程师',('gong','cheng','shi')),chunks)
        self.assertIn('射频电路',[c.text for c in chunks])
        self.assertIn('阻抗匹配',[c.text for c in chunks])

    def test_model_malformed_pinyin_is_repaired(self):
        model=Mock();model.plan.return_value=[{'text':'曲线','syllables':['quxian']}]
        self.assertEqual(make_hybrid_plan('曲线',model),[Chunk('曲线',('qu','xian'))])

    def test_model_failure_or_rewrite_falls_back_to_exact_local_plan(self):
        text='表下注明自放电率是计算值，'
        for reply in (RuntimeError('offline'),[{'text':'改写','syllables':['gai','xie']}],None):
            model=Mock()
            if isinstance(reply,Exception):model.plan.side_effect=reply
            else:model.plan.return_value=reply
            self.assertEqual(make_hybrid_plan(text,model),make_plan(text))

    def test_stopped_session_is_not_swallowed_as_fallback(self):
        model=Mock();model.plan.side_effect=Stopped('stop')
        with self.assertRaises(Stopped):make_hybrid_plan('已绘出',model)

    def test_full_punctuated_document_uses_one_model_request(self):
        text='第3页表1的标题已标明自放电率是计算值，表下注明按式（5）计算。'
        model=Mock();model.plan.return_value=[c.to_dict() for c in make_plan(text)]
        self.assertEqual(''.join(c.text for c in make_hybrid_plan(text,model)),text)
        model.plan.assert_called_once_with(text,text)

    def test_bad_legal_longest_reading_is_overridden(self):
        model=Mock();model.plan.return_value=[{'text':'最长','syllables':['zui','zhang']}]
        self.assertEqual(make_hybrid_plan('最长',model),[Chunk('最长',('zui','chang'))])
