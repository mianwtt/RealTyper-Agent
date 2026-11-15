"""Small resident panel and clipboard-to-focused-input hotkey sessions."""
import queue
import threading
import tkinter as tk
from tkinter import ttk
from . import __version__
from .engine import Agent, Stopped
from .external import capture_target, bind_target, ExternalBackend, TargetError
from .hotkeys import GlobalHotkeys
from .ollama import Ollama
from .planner import make_plan,make_hybrid_plan
from .scope import InsertionScope
from .rhythm import PACES
from .windows import clipboard_text, u


class QuickSession:
    def __init__(self, args, clipboard_owner, emit):
        self.args, self.clipboard_owner, self.emit = args, clipboard_owner, emit
        self.lock = threading.Lock()
        self.busy = False
        self.agent = None
        self.thread = None
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()

    def activate(self):
        # Called directly by the hotkey thread: capture focus before any UI work.
        with self.lock:
            if self.busy:
                if self.stop_event.is_set():
                    self.emit("status", "正在停止，请稍候。")
                elif self.pause_event.is_set():
                    self.pause_event.clear()
                    self.emit("status", "继续录入；请保持目标输入框焦点。")
                else:
                    self.pause_event.set()
                    self.emit("status", "已请求暂停，将在当前片段结束后暂停。")
                return
            try:
                target = capture_target()
                source = clipboard_text()
                if not source: raise TargetError("剪贴板没有文字，请先复制原文。")
                if len(source) > 20000: raise TargetError("单次最多录入 20000 字，请分段执行。")
                if any(ord(char) < 32 and char != "\n" for char in source):
                    raise TargetError("原文含有 Tab 或其他控制字符，请先整理为普通文字。")
            except Exception as error:
                self.emit("error", str(error))
                return
            self.stop_event = threading.Event()
            self.pause_event = threading.Event()
            self.agent = None
            self.busy = True
            self.emit("status", "已接收 %d 字，正在检查目标输入框……" % len(source))
            self.thread = threading.Thread(target=self.work, args=(target, source), daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.busy: self.emit("status", "已请求停止，保留当前文字。")

    def checkpoint(self, target):
        while self.pause_event.is_set():
            if self.stop_event.wait(.05): raise Stopped("任务已停止，保留当前文字。")
        if self.stop_event.is_set(): raise Stopped("任务已停止，保留当前文字。")
        target.guard()

    def work(self, target, source):
        try:
            import uiautomation as auto
            with auto.UIAutomationInitializerInThread():
                self.checkpoint(target)
                editor = InsertionScope(bind_target(target, auto))
                if editor.collapsed_selection:
                    self.emit("status","已保留选中文字，将从选区末尾继续输入。")
                if "\n" in source and editor.browser:
                    raise TargetError("浏览器跨行录入尚未适配，请先用单行原文，或使用原生多行编辑框。")
                if "\n" in source and not editor.multiline:
                    raise TargetError("原文包含换行，但不能确认目标支持多行，未开始输入。")
                client, metadata = None, {"mode": "offline"}
                def progress(message):
                    self.checkpoint(target)
                    self.emit("status", message)
                if self.args.planner in ('qwen','hybrid'):
                    try:
                        client=Ollama(self.args.url,self.args.model,timeout=20 if self.args.planner=='hybrid' else 180)
                        metadata=client.check()
                    except RuntimeError:
                        if self.args.planner=='qwen':raise
                        client=None
                        progress('Qwen 不可用，继续使用本地词典。')
                planner=make_hybrid_plan if self.args.planner=='hybrid' else make_plan
                chunks=planner(source,client,progress)
                metadata['mode']=self.args.planner
                self.checkpoint(target)
                editor.ensure_pristine()
                self.emit("status", "开始录入，共 %d 个片段；同键暂停，Ctrl+Alt+Q 停止。" % len(chunks))
                backend = ExternalBackend(editor, self.clipboard_owner, self.args.pace)
                backend.stop_event = self.stop_event
                agent = Agent(backend, lambda event: self.emit("agent", event))
                agent.stop_event, agent.pause_event = self.stop_event, self.pause_event
                self.agent = agent
                agent.run(source, chunks, metadata)
                self.emit("complete", "本次输入一致 · %d 字已录入，仅复制本次内容。" % len(source))
        except Stopped as error:
            self.emit("status", str(error))
        except Exception as error:
            self.emit("error", str(error))
        finally:
            with self.lock: self.busy = False
            self.emit("idle", None)


class QuickPanel:
    def __init__(self, root, args):
        self.root, self.args = root, args
        self.events = queue.Queue()
        self.closed = False
        root.title("拼音快捷输入 v%s" % __version__)
        root.geometry("470x340")
        root.resizable(False, False)
        self.pinned=tk.BooleanVar(master=root,value=True)
        root.attributes("-topmost",True)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TLabel", font=("Microsoft YaHei UI", 10))
        body = ttk.Frame(root, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="复制 → 光标定位 → 按快捷键",
                  font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        ttk.Label(body, text="Ctrl + Alt + V    开始 / 暂停 / 继续\nCtrl + Alt + Q    停止",
                  justify="left").pack(anchor="w", pady=(12, 8))
        mode = {'hybrid':'本地词典 + Qwen 增强','qwen':'Qwen '+args.model,'offline':'本地词典 · 不调用 Qwen'}[args.planner]
        ttk.Label(body, text="%s · %s节奏" % (mode, PACES[args.pace].label)).pack(anchor="w")
        ttk.Label(body, text="英文模式会自动切换重试；运行时保持输入框焦点。",
                  font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(5, 8))
        self.status = tk.StringVar(value="正在注册快捷键……")
        self.status_label = ttk.Label(body, textvariable=self.status, wraplength=430)
        self.status_label.pack(anchor="w", fill="x")
        bar = ttk.Frame(body)
        bar.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Checkbutton(bar,text="置顶",variable=self.pinned,command=self.apply_pin).pack(side="left",padx=(0,8))
        ttk.Button(bar, text="最小化，保持可用", command=root.iconify).pack(side="left")
        ttk.Button(bar, text="退出快捷模式", command=self.close).pack(side="right")
        root.update_idletasks()
        owner = u.GetAncestor(root.winfo_id(), 2)
        self.session = QuickSession(args, owner, self.emit)
        self.hotkeys = GlobalHotkeys(self.hotkey)
        self.hotkeys.start()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(30, self.pump)

    def emit(self, kind, data=None): self.events.put((kind, data))

    def apply_pin(self):
        # Change z-order without repeatedly activating the input controller.
        self.root.attributes("-topmost",bool(self.pinned.get()))

    def hotkey(self, action, error=None):
        if action == "activate": self.session.activate()
        elif action == "stop": self.session.stop()
        elif action == "ready": self.emit("status", "就绪 · 窗口默认置顶，切换应用后仍可查看。")
        elif action == "error": self.emit("error", str(error))

    def pump(self):
        while not self.events.empty():
            kind, data = self.events.get()
            if kind in ("status", "error", "complete"):
                self.status.set(data)
                self.status_label.configure(foreground="#a02a2a" if kind == "error" else "#17355a")
                if kind == "error":
                    self.root.bell()
                    self.root.deiconify()
            elif kind == "agent":
                if data["kind"] == "ime_recovery":
                    self.status.set(data["message"])
                elif data["kind"] == "paused":
                    self.status.set("已暂停 · 回到原输入框按 Ctrl+Alt+V 继续。")
                elif data["kind"] == "progress" and not self.session.pause_event.is_set():
                    self.status.set("录入中：%d / %d 片段 · 同键暂停" % (data["completed"], data["total"]))
        if not self.closed: self.root.after(30, self.pump)

    def close(self):
        self.closed = True
        self.session.stop()
        self.hotkeys.close()
        self.root.destroy()


def main(args):
    root = tk.Tk()
    QuickPanel(root, args)
    root.mainloop()
    return 0
