"""Launch RealTyper-Agent using the existing IME implementation."""
from runpy import run_module

run_module("pinyin_agent", run_name="__main__")
