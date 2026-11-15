from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
import re
from typing import List, Tuple

HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0003134f]+")

MAX_ATTEMPTS=3


class AttemptBudget:
    def __init__(self):self.used=0

    @property
    def remaining(self):return MAX_ATTEMPTS-self.used

    def require(self):
        if self.remaining<=0:raise RuntimeError("本片段最多尝试 %d 次，已停止。" % MAX_ATTEMPTS)

    def consume(self):
        self.require()
        self.used+=1


@dataclass(frozen=True)
class Chunk:
    text: str
    syllables: Tuple[str, ...] = ()

    @property
    def chinese(self):
        return bool(HAN.fullmatch(self.text))

    @property
    def keys(self):
        # Explicit syllable separators prevent xian / xi'an ambiguity.
        return "'".join(self.syllables)

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Patch:
    start: int
    end: int
    old: str
    new: str


def differences(expected: str, actual: str) -> List[Patch]:
    """Indices address actual text; reverse application avoids offset drift."""
    return [Patch(j1, j2, actual[j1:j2], expected[i1:i2])
            for op, i1, i2, j1, j2 in SequenceMatcher(
                None, expected, actual, autojunk=False).get_opcodes() if op != "equal"]


def utf16_offset(text: str, index: int) -> int:
    return len(text[:index].replace("\n", "\r\n").encode("utf-16-le")) // 2


def validate_plan(source: str, chunks: List[Chunk]):
    if "".join(c.text for c in chunks) != source:
        raise ValueError("切分结果改写或遗漏了原文，已拒绝执行。")
    for c in chunks:
        if not c.text:
            raise ValueError("输入片段不能为空。")
        if c.chinese:
            if len(c.syllables) != len(c.text):
                raise ValueError("汉字与拼音音节数量不一致：" + c.text)
            if not all(re.fullmatch(r"[a-zv]+", s) for s in c.syllables):
                raise ValueError("拼音必须是无声调的小写字母，ü 使用 v。")
        elif c.syllables or HAN.search(c.text):
            raise ValueError("汉字和符号必须分开规划。")
