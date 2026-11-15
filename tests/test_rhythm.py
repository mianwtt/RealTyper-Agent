import math
import queue
import random
import statistics
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/".deps"))

from pinyin_agent.engine import Stopped
from pinyin_agent.rhythm import TypingRhythm
from pinyin_agent.ui import App, build_parser


class FakeClock:
    def __init__(self):
        self.now = 0.
        self.slices = []

    def clock(self):
        return self.now

    def sleep(self,seconds):
        self.slices.append(seconds)
        self.now += seconds


class RhythmTests(unittest.TestCase):
    def rhythm(self,pace="natural"):
        clock = FakeClock()
        return TypingRhythm(pace,rng=random.Random(42),clock=clock.clock,sleep=clock.sleep),clock

    def test_key_intervals_vary_and_are_slower_than_old_25ms(self):
        rhythm,_ = self.rhythm()
        values = []
        for _ in range(100):
            rhythm.begin_chunk()
            values.extend(rhythm.sample("key") for _ in range(8))
        self.assertTrue(all(.055 <= value <= .390 for value in values))
        self.assertGreater(len({round(value,4) for value in values}),500)
        self.assertGreater(statistics.stdev(values),.03)
        self.assertGreater(statistics.mean(values),.12)
        self.assertLess(statistics.mean(values),.18)

    def test_paces_order_both_keys_and_boundary_pauses(self):
        means = []
        pauses = []
        for pace in ("brisk","natural","slow"):
            rhythm,_ = self.rhythm(pace)
            means.append(statistics.mean(rhythm.sample("key") for _ in range(300)))
            pauses.append(statistics.mean(rhythm.sample("sentence") for _ in range(300)))
        self.assertLess(means[0],means[1])
        self.assertLess(means[1],means[2])
        self.assertLess(pauses[0],pauses[1])
        self.assertLess(pauses[1],pauses[2])

    def test_long_pause_can_be_stopped_before_it_finishes(self):
        rhythm,clock = self.rhythm()
        def checkpoint():
            if clock.now >= .075:
                raise Stopped("stop")
        with self.assertRaises(Stopped):
            rhythm.wait("paragraph",checkpoint)
        self.assertLessEqual(clock.now,.1)
        self.assertLessEqual(max(clock.slices),.025)

    def test_end_of_literal_chunk_and_newline_have_pauses(self):
        rhythm,_ = self.rhythm()
        for text in ("。","，","\n"):
            rhythm.after_literal(text,0,lambda:None)
        summary = rhythm.summary()["delays"]
        self.assertEqual(summary["sentence"]["count"],1)
        self.assertEqual(summary["punctuation"]["count"],1)
        self.assertEqual(summary["paragraph"]["count"],1)

    def test_decimal_and_repeated_punctuation_do_not_pause_per_dot(self):
        rhythm,_ = self.rhythm()
        rhythm.after_literal("3.14",1,lambda:None)
        for index in range(3):
            rhythm.after_literal("...",index,lambda:None)
        self.assertEqual(rhythm.summary()["delays"]["sentence"]["count"],1)

    def test_summary_records_variable_durations(self):
        rhythm,clock = self.rhythm()
        for _ in range(12):
            rhythm.wait("key")
        stat = rhythm.summary()["delays"]["key"]
        self.assertEqual(stat["count"],12)
        self.assertEqual(len(stat["examples_ms"]),12)
        self.assertLess(stat["min_ms"],stat["max_ms"])
        self.assertTrue(math.isfinite(clock.now))


class LocalModelTests(unittest.TestCase):
    def test_default_combines_local_dictionary_and_qwen(self):
        args = build_parser().parse_args([])
        self.assertEqual(args.planner,"hybrid")
        self.assertEqual(args.url,"http://127.0.0.1:11434")
        self.assertEqual(args.model,"qwen3.5:9b")
        self.assertEqual(args.pace,"natural")

    def test_rule_only_mode_can_be_explicitly_selected(self):
        self.assertEqual(build_parser().parse_args(["--qwen"]).planner,"qwen")
        self.assertEqual(build_parser().parse_args(["--offline"]).planner,"offline")

    def test_offline_planning_does_not_construct_or_query_model_client(self):
        app = App.__new__(App)
        app.busy = False
        app.source = Mock()
        app.source.get.return_value = "发力"
        app.mode = Mock()
        app.mode.get.return_value = build_parser().parse_args(['--offline']).planner
        app.set_busy = Mock()
        app.progress,app.status = Mock(),Mock()
        app.events = queue.Queue()
        thread = Mock()
        def immediate_thread(**kwargs):
            thread.start.side_effect = kwargs["target"]
            return thread
        with patch("pinyin_agent.ui.Ollama",side_effect=AssertionError("Offline must not use model")) as model:
            with patch("pinyin_agent.ui.threading.Thread",side_effect=immediate_thread):
                app.plan_text()
        kind,data = app.events.get_nowait()
        self.assertEqual(kind,"plan")
        self.assertEqual("".join(chunk.text for chunk in data[1]),"发力")
        self.assertEqual(data[2],{})
        model.assert_not_called()


if __name__=="__main__":unittest.main()
