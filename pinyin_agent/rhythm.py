"""Bounded, variable playback pacing; presets are heuristics, not human measurements."""
from dataclasses import dataclass
import math
import random
import time


@dataclass(frozen=True)
class Pace:
    label: str
    scale: float


PACES = {
    "slow": Pace("舒缓", 1.45),
    "natural": Pace("自然", 1.0),
    "brisk": Pace("较快", 0.72),
}

# Seconds: minimum, most likely, maximum. All user-action pauses vary.
PAUSES = {
    "word": (.20, .34, .64),
    "selection": (.14, .24, .46),
    "punctuation": (.28, .44, .76),
    "sentence": (.60, .88, 1.45),
    "paragraph": (.90, 1.20, 1.85),
    "retry": (.24, .38, .68),
    "correction": (.12, .20, .36),
    "review": (.45, .70, 1.15),
    "hesitation": (.55, .80, 1.40),
}


class TypingRhythm:
    def __init__(self, pace="natural", rng=None, clock=None, sleep=None):
        if pace not in PACES:
            raise ValueError("未知输入节奏：" + str(pace))
        self.pace = pace
        self.profile = PACES[pace]
        self.rng = rng if rng is not None else random.Random()
        self.clock = clock if clock is not None else time.monotonic
        self.sleep = sleep if sleep is not None else time.sleep
        self.tempo = 1.0
        self.stats = {}

    def describe(self):
        return {"pace": self.pace, "label": self.profile.label,
                "key_mean_ms": round(150 * self.profile.scale),
                "key_bounds_ms": [round(55 * self.profile.scale), round(390 * self.profile.scale)],
                "variable": True}

    def begin_chunk(self):
        # Gradual changes between phrases create short faster/slower stretches.
        self.tempo = .65 * self.tempo + .35 * self.rng.uniform(.80, 1.25)

    def sample(self, kind):
        if kind == "key":
            sigma = .34
            mean = .150 * self.tempo
            delay = self.rng.lognormvariate(math.log(mean) - sigma * sigma / 2, sigma)
            delay = min(.390, max(.055, delay))
        else:
            minimum, mode, maximum = PAUSES[kind]
            delay = self.rng.triangular(minimum, maximum, mode)
        return delay * self.profile.scale

    def wait(self, kind, checkpoint=lambda: None):
        delay = self.sample(kind)
        record = self.stats.setdefault(kind, {"count": 0, "total": 0., "min": delay,
                                              "max": delay, "examples": []})
        record["count"] += 1
        record["total"] += delay
        record["min"] = min(record["min"], delay)
        record["max"] = max(record["max"], delay)
        if len(record["examples"]) < 12:
            record["examples"].append(round(delay * 1000, 2))
        deadline = self.clock() + delay
        while True:
            checkpoint()
            remaining = deadline - self.clock()
            if remaining <= 0:
                break
            # Stop, F12 and focus loss are checked even during long pauses.
            self.sleep(min(.025, remaining))
        return delay

    def after_word(self, checkpoint):
        self.wait("word", checkpoint)
        if self.rng.random() < .05:
            self.wait("hesitation", checkpoint)

    def after_literal(self, text, index, checkpoint):
        char = text[index]
        next_char = text[index + 1:index + 2]
        previous = text[index - 1:index] if index else ""
        if char == "\n":
            self.wait("paragraph", checkpoint)
        elif char in "。！？.!?" and (not next_char or next_char not in "。！？.!?") and not (
                char == "." and previous.isdigit() and next_char.isdigit()):
            self.wait("sentence", checkpoint)
        elif char in "，、；：,;:" and (not next_char or next_char not in "，、；：,;:"):
            self.wait("punctuation", checkpoint)
        elif char == " " and previous and previous != " ":
            self.wait("word", checkpoint)

    def summary(self):
        return {"profile": self.describe(), "delays": {
            kind: {"count": stat["count"], "mean_ms": round(stat["total"] / stat["count"] * 1000, 2),
                   "min_ms": round(stat["min"] * 1000, 2), "max_ms": round(stat["max"] * 1000, 2),
                   "examples_ms": list(stat["examples"])}
            for kind, stat in self.stats.items()}}
