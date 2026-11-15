import json
import threading
import time
from datetime import datetime
from pathlib import Path
from contextlib import nullcontext
from .domain import Chunk, differences, validate_plan,MAX_ATTEMPTS
from .planner import make_plan


class Stopped(RuntimeError):
    pass


class Agent:
    """Stateful execution against an explicit editor adapter, never a guessed window."""
    def __init__(self, backend, emit=lambda event: None, run_dir=None):
        self.backend, self.emit = backend, emit
        self.stop_event, self.pause_event = threading.Event(), threading.Event()
        self.run_dir = Path(run_dir or Path(__file__).resolve().parent.parent / "runs")
        self.path = None
        self.completed = 0

    def event(self, kind, **data):
        event = {"time": datetime.now().astimezone().isoformat(), "kind": kind, **data}
        if self.path:
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(event, ensure_ascii=False) + "\n")
        self.emit(event)

    def checkpoint(self,allow_pause=True):
        paused=allow_pause and self.pause_event.is_set()
        if paused:self.event("paused")
        while allow_pause and self.pause_event.is_set():
            if self.stop_event.wait(0.1):
                raise Stopped("任务已停止，保留当前文本。")
        if paused and not self.stop_event.is_set():self.event("resumed")
        if self.stop_event.is_set() or self.backend.emergency_stop():
            raise Stopped("任务已停止，保留当前文本。")
        self.backend.guard()

    def key_checkpoint(self):
        self.checkpoint(allow_pause=False)

    def type_chunk(self, chunk):
        self.checkpoint()
        if not chunk.chinese:
            self.backend.literal(chunk.text, self.key_checkpoint)
            return
        self.event("typing", text=chunk.text, pinyin=chunk.keys)
        if self.backend.choose(chunk, self.key_checkpoint, self.event):
            if hasattr(self.backend,"after_chunk"):
                self.backend.after_chunk(chunk,self.key_checkpoint)
            return
        raise RuntimeError("候选最多尝试 %d 次仍未找到“%s”，已停止。" % (MAX_ATTEMPTS,chunk.text))

    def repair(self, source):
        seen = {}
        for cycle in range(64):
            self.checkpoint()
            actual = self.backend.read()
            patches = differences(source, actual)
            if not patches:
                return
            self.event("diff", cycle=cycle+1, patches=[p.__dict__ for p in patches])
            # Apply one patch, then re-read/re-diff; no stale range reuse.
            patch = patches[-1]
            signature = (patch.start,patch.new)
            seen[signature] = seen.get(signature,0)+1
            if seen[signature] > MAX_ATTEMPTS:
                raise RuntimeError("相同位置连续纠错失败，已停止重试。")
            if patch.old:
                if hasattr(self.backend,"delete_range"):
                    self.backend.delete_range(patch.start,patch.end,actual)
                else:
                    self.backend.select(patch.start,patch.end,expected=actual)
                    self.backend.backspace()
            else:
                self.backend.select(patch.start,patch.end,expected=actual)
            for chunk in make_plan(patch.new) if patch.new else []:
                self.type_chunk(chunk)
        if self.backend.read() != source:
            raise RuntimeError("达到纠错轮数上限，文本仍有差异；请检查日志。")

    def run(self, source, chunks, model_metadata=None, repair_only=False):
        validate_plan(source, chunks)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".jsonl")
        self.event("start", source=source, plan=[c.to_dict() for c in chunks],
                   model=model_metadata or {"mode": "offline"}, repair_only=repair_only,
                   rhythm=self.backend.rhythm.describe() if hasattr(self.backend,"rhythm") else {})
        began=time.perf_counter()
        try:
            if hasattr(self.backend,"set_checkpoint"):
                self.backend.set_checkpoint(self.key_checkpoint)
            self.backend.begin()
            if not repair_only:
                if self.backend.read():
                    raise RuntimeError("测试编辑器不是空的，请先新建空白任务。")
                expected = ""
                for number, chunk in enumerate(chunks):
                    scope=self.backend.attempt_scope() if hasattr(self.backend,"attempt_scope") else nullcontext()
                    with scope:
                        self.type_chunk(chunk)
                        expected += chunk.text
                        # Initial input, mode recovery and repair share one budget.
                        self.repair(expected)
                    self.completed = number + 1
                    self.event("progress", completed=self.completed, total=len(chunks))
            self.repair(source)
            self.checkpoint()
            copied = self.backend.copy_all()
            if copied != source or self.backend.read() != source:
                raise RuntimeError("最终复制核验失败，没有标记为完成。")
            elapsed=time.perf_counter()-began
            self.event("complete", exact=True, characters=len(source), actual=copied,
                       elapsed_ms=round(elapsed*1000),characters_per_minute=round(len(source)*60/max(elapsed,.001),1))
            return copied
        except Exception as error:
            self.event("stopped" if isinstance(error, Stopped) else "error", message=str(error))
            raise
        finally:
            if hasattr(self.backend,"rhythm"):
                self.event("timing_summary",**self.backend.rhythm.summary())
            self.backend.end()
