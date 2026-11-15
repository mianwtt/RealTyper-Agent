import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.deps'))
from pinyin_agent.domain import Chunk
from pinyin_agent.planner import make_plan,character_hint


class SegmentationTests(unittest.TestCase):
    def test_productivity_sentence_uses_real_words(self):
        text='大模型的作用就是为了提高生产力'
        self.assertEqual([chunk.text for chunk in make_plan(text)],
                         ['大模型','的','作用','就是','为了','提高','生产力'])

    def test_rhythm_sentence_preserves_word_boundaries(self):
        text='我会把默认模式改为离线规则，并调整输入节奏，让按键间隔有自然波动，在词组之间加入停顿。'
        chunks=make_plan(text)
        words=[chunk.text for chunk in chunks]
        self.assertEqual(''.join(words),text)
        for word in ('默认','模式','离线','规则','调整','输入','节奏','按键','间隔','自然','波动','词组','之间','加入','停顿'):
            self.assertIn(word,words)
        for fragment in ('把默','认模','式改','为离','线规','并调','整输','入节','让按','键间','隔有'):
            self.assertNotIn(fragment,words)

    def test_local_word_can_cross_old_120_character_request_boundary(self):
        chunks=make_plan('的'*119+'生产力')
        self.assertEqual(chunks[-1].text,'生产力')

    def test_homophones_remain_complete_words(self):
        self.assertEqual([chunk.text for chunk in make_plan('发力法力乏力')],['发力','法力','乏力'])

    def test_dictionary_pinyin_keeps_polyphonic_context(self):
        chunks=make_plan('中国银行行长在重庆听音乐')
        self.assertIn(Chunk('行长',('hang','zhang')),chunks)
        self.assertIn(Chunk('重庆',('chong','qing')),chunks)
        self.assertIn(Chunk('音乐',('yin','yue')),chunks)

    def test_reported_longest_uses_length_reading(self):
        chunks=make_plan('三次试验中，最长的这段用时约是最短的多少倍？')
        self.assertIn(Chunk('最长',('zui','chang')),chunks)
        self.assertIn(Chunk('长大',('zhang','da')),make_plan('长大'))

    def test_single_yi_remains_separate_with_word_hint_available(self):
        chunks=make_plan('已绘出的冷却曲线')
        self.assertEqual([chunk.text for chunk in chunks[:2]],['已','绘出'])
        self.assertEqual(character_hint(chunks[0]),Chunk('已经',('yi','jing')))


if __name__=='__main__':unittest.main()
