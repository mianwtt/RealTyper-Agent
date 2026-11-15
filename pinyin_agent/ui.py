import argparse
import json
import queue
import threading
import time
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from . import APP_NAME, __version__
from .domain import differences
from .planner import make_plan,make_hybrid_plan
from .ollama import Ollama, DEFAULT_URL, DEFAULT_MODEL
from .engine import Agent
from .windows import NativeEditor, WindowsBackend, clipboard_text
from .rhythm import PACES
from .external import NativeBackend

DEMO = "大模型的作用就是为了提高生产力。\n我们需要继续发力，避免感到乏力。"


class App:
    def __init__(self, root, args):
        self.root, self.args = root, args
        self.events, self.calls = queue.Queue(), queue.Queue()
        self.busy, self.plan, self.plan_source = False, [], ""
        self.agent, self.metadata = None, {}
        self.plan_metadata = {}
        self.last_log = None
        self.exit_code = 0
        self.last_elapsed = 0
        root.title("%s v%s · 本地验证台" % (APP_NAME, __version__))
        root.geometry("1180x820")
        root.minsize(960, 700)
        root.configure(bg="#f3f6fc")
        self.status = tk.StringVar(value='就绪 · 本地词典 + Qwen 增强' if args.planner=='hybrid' else '就绪 · '+args.planner)
        self.mode = tk.StringVar(value=args.planner)
        self.pace = tk.StringVar(value=PACES[args.pace].label)
        self.url = tk.StringVar(value=args.url)
        self.model = tk.StringVar(value=args.model)
        self.progress = tk.DoubleVar(value=0)
        self.setup_style()
        self.build()
        self.source.insert("1.0", Path(args.text_file).read_text(encoding="utf-8") if args.text_file else args.text or DEMO)
        root.after(30, self.pump)
        root.protocol("WM_DELETE_WINDOW", self.close)
        if args.smoke:
            root.after(800, self.arm_smoke)

    def arm_smoke(self):
        self.root.attributes('-topmost',True)
        self.root.lift()
        self.root.focus_force()
        self.root.after(400,lambda:self.plan_text(auto_run=True))

    def setup_style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#f3f6fc")
        style.configure("Card.TFrame", background="white")
        style.configure("TLabel", background="#f3f6fc", foreground="#17243b", font=("Microsoft YaHei UI",10))
        style.configure("Card.TLabel", background="white")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI",22,"bold"))
        style.configure("Muted.TLabel", foreground="#66758d")
        style.configure("TButton", font=("Microsoft YaHei UI",10), padding=(12,7))
        style.configure("Primary.TButton", background="#245ade", foreground="white")
        style.map("Primary.TButton", background=[("active","#1847ba"),("disabled","#a8b6d3")])
        style.configure("TEntry", padding=5)
        style.configure("Treeview", rowheight=30, font=("Microsoft YaHei UI",10), borderwidth=0)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI",10,"bold"), padding=8)
        style.configure("Horizontal.TProgressbar", background="#245ade", troughcolor="#e3e9f5", borderwidth=0)

    def build(self):
        outer = ttk.Frame(self.root, padding=22)
        outer.pack(fill="both", expand=True)
        top = ttk.Frame(outer)
        top.pack(fill="x")
        ttk.Label(top,text=APP_NAME,style="Title.TLabel").pack(side="left")
        ttk.Label(top,text="  v%s  /  本地验证台" % __version__,style="Muted.TLabel").pack(side="left",pady=(12,0))
        ttk.Button(top,text="开启快捷模式",command=self.open_quick).pack(side="right")
        ttk.Label(outer,text="规划输入片段，观察系统候选，再核验每一个字。",style="Muted.TLabel").pack(anchor="w",pady=(5,14))
        server = ttk.Frame(outer)
        server.pack(fill="x",pady=(0,14))
        ttk.Label(server,text="可选模型").pack(side="left")
        ttk.Entry(server,textvariable=self.url,width=28).pack(side="left",padx=8)
        ttk.Entry(server,textvariable=self.model,width=19).pack(side="left")
        self.check_button = ttk.Button(server,text="检查连接",command=self.check_server)
        self.check_button.pack(side="left",padx=8)
        self.qwen_radio=ttk.Radiobutton(server,text="词典 + Qwen（默认）",variable=self.mode,value="hybrid")
        self.qwen_radio.pack(side="left",padx=(8,8))
        self.offline_radio=ttk.Radiobutton(server,text="仅本地词典",variable=self.mode,value="offline")
        self.offline_radio.pack(side="left")
        rhythm_row=ttk.Frame(outer)
        rhythm_row.pack(fill="x",pady=(0,12))
        ttk.Label(rhythm_row,text="输入节奏").pack(side="left")
        self.pace_combo=ttk.Combobox(rhythm_row,textvariable=self.pace,
            values=[profile.label for profile in PACES.values()],width=8,state="readonly")
        self.pace_combo.pack(side="left",padx=8)
        ttk.Label(rhythm_row,text="按键间隔随机波动，词间和标点处适当停顿",style="Muted.TLabel").pack(side="left",padx=8)
        panes = ttk.Panedwindow(outer,orient="horizontal")
        panes.pack(fill="both",expand=True)
        left = ttk.Frame(panes,style="Card.TFrame",padding=14)
        right = ttk.Frame(panes,style="Card.TFrame",padding=14)
        panes.add(left,weight=1)
        panes.add(right,weight=1)
        source_title = ttk.Frame(left,style="Card.TFrame")
        source_title.pack(fill="x")
        ttk.Label(source_title,text="01  原始文本",style="Card.TLabel").pack(side="left")
        self.import_button = ttk.Button(source_title,text="读取剪贴板",command=self.import_text)
        self.import_button.pack(side="right")
        self.source = tk.Text(left,height=6,wrap="word",font=("Microsoft YaHei UI",12),
                              bg="#f8faff",fg="#17243b",relief="flat",padx=10,pady=10,undo=True)
        self.source.pack(fill="x",pady=(10,16))
        ttk.Label(left,text="02  输入计划",style="Card.TLabel").pack(anchor="w")
        table_frame = ttk.Frame(left,style="Card.TFrame")
        table_frame.pack(fill="both",expand=True,pady=(10,0))
        self.table = ttk.Treeview(table_frame,columns=("text","pinyin","kind"),show="headings",height=8)
        for name,label,width in (("text","片段",145),("pinyin","全拼",200),("kind","类型",70)):
            self.table.heading(name,text=label)
            self.table.column(name,width=width,minwidth=50,stretch=True)
        scroll = ttk.Scrollbar(table_frame,orient="vertical",command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left",fill="both",expand=True)
        scroll.pack(side="right",fill="y")
        target_title = ttk.Frame(right,style="Card.TFrame")
        target_title.pack(fill="x")
        ttk.Label(target_title,text="03  系统输入法测试编辑器",style="Card.TLabel").pack(side="left")
        self.clear_button = ttk.Button(target_title,text="新建空白",command=self.new_document)
        self.clear_button.pack(side="right")
        editor_frame = ttk.Frame(right,style="Card.TFrame",height=180)
        editor_frame.pack(fill="both",expand=True,pady=(10,12))
        self.editor = NativeEditor(editor_frame)
        ttk.Label(right,text="04  候选与校验记录",style="Card.TLabel").pack(anchor="w")
        self.log = tk.Text(right,height=8,wrap="word",font=("Microsoft YaHei UI",10),
                           bg="#f8faff",fg="#40516d",relief="flat",padx=10,pady=8,state="disabled")
        self.log.pack(fill="x",pady=(10,0))
        bar = ttk.Frame(outer)
        bar.pack(fill="x",pady=(15,10))
        self.plan_button = ttk.Button(bar,text="1  生成计划",command=self.plan_text)
        self.plan_button.pack(side="left")
        self.run_button = ttk.Button(bar,text="2  开始拼音录入",style="Primary.TButton",command=self.run_agent,state="disabled")
        self.run_button.pack(side="left",padx=8)
        self.repair_button = ttk.Button(bar,text="校验并修正",command=lambda:self.run_agent(repair_only=True),state="disabled")
        self.repair_button.pack(side="left")
        self.pause_button = ttk.Button(bar,text="暂停",command=self.pause,state="disabled")
        self.pause_button.pack(side="right")
        self.stop_button = ttk.Button(bar,text="停止 · F12",command=self.stop,state="disabled")
        self.stop_button.pack(side="right",padx=8)
        ttk.Progressbar(outer,variable=self.progress,maximum=100).pack(fill="x",pady=(0,8))
        ttk.Label(outer,textvariable=self.status).pack(anchor="w")
        ttk.Label(outer,text="汉字经系统拼音输入法选词；完成后复制全文比对。请保持目标编辑器焦点。",style="Muted.TLabel").pack(anchor="w",pady=(6,0))

    def append_log(self,text):
        self.log.configure(state="normal")
        self.log.insert("end",time.strftime("%H:%M:%S")+"  "+text+"\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_busy(self,busy,running=False):
        self.busy = busy
        for button in (self.plan_button,self.check_button,self.clear_button,self.import_button):
            button.configure(state="disabled" if busy else "normal")
        self.source.configure(state="disabled" if busy else "normal")
        self.offline_radio.configure(state="disabled" if busy else "normal")
        self.qwen_radio.configure(state="disabled" if busy else "normal")
        self.pace_combo.configure(state="disabled" if busy else "readonly")
        for button in (self.run_button,self.repair_button):
            button.configure(state="normal" if self.plan and not busy else "disabled")
        self.pause_button.configure(state="normal" if running else "disabled")
        self.stop_button.configure(state="normal" if running else "disabled")

    def call_ui(self,callback):
        event, result = threading.Event(), []
        self.calls.put((callback,event,result))
        if not event.wait(5):
            raise RuntimeError("界面没有响应，已停止输入。")
        if isinstance(result[0],Exception):
            raise result[0]
        return result[0]

    def pump(self):
        while not self.calls.empty():
            callback,event,result = self.calls.get()
            try:
                result.append(callback())
            except Exception as error:
                result.append(error)
            event.set()
        while not self.events.empty():
            kind,data = self.events.get()
            if kind == "status":
                self.status.set(data)
            elif kind == "checked":
                self.metadata = data
                self.set_busy(False)
                self.status.set("模型已连接 · "+data["name"])
                self.append_log("模型 digest 已核对："+data.get("digest","")[:16]+"…")
            elif kind == "plan":
                source,chunks,metadata,auto_run = data
                self.plan_source,self.plan,self.metadata = source,chunks,metadata
                self.plan_metadata = dict(metadata)
                self.table.delete(*self.table.get_children())
                for chunk in chunks:
                    self.table.insert("","end",values=(repr(chunk.text)[1:-1],chunk.keys or "—","拼音" if chunk.chinese else "原样"))
                self.set_busy(False)
                self.status.set("计划已验证 · %s 字符 / %s 片段 · %s" % (len(source),len(chunks),metadata.get("name","本地词典")))
                self.append_log("片段拼接与原文逐字一致。")
                if auto_run:
                    self.root.after(300,self.run_agent)
            elif kind == "agent":
                self.on_agent_event(data)
            elif kind == "finished":
                self.set_busy(False)
                self.pause_button.configure(text="暂停")
                self.status.set("完成 · 录入 %.2f 秒 · 全文一致，结果已复制到剪贴板" % self.last_elapsed)
                if self.args.smoke:
                    self.smoke_finish(True)
            elif kind == "error":
                self.set_busy(False)
                self.status.set(str(data))
                self.append_log(str(data))
                if self.args.smoke:
                    self.smoke_finish(False,str(data))
        self.root.after(30,self.pump)

    def on_agent_event(self,event):
        kind=event["kind"]
        if kind == "start":
            self.append_log("输入节奏："+event.get("rhythm",{}).get("label","自然")+" · 随机按键与词间停顿")
        elif kind == "typing":
            self.status.set("正在输入：%s  /  %s" % (event["text"],event["pinyin"]))
        elif kind == "candidates":
            if event["candidates"]:
                self.append_log("候选 %s：%s" % (event["target"]," / ".join(c["text"] for c in event["candidates"])))
        elif kind == "selected":
            self.append_log("选中“%s” · %s" % (event["target"],event["source"]))
        elif kind == "candidate_adapter":
            self.append_log("通过上屏结果核验候选，不使用 OCR。")
        elif kind == "candidate_probe" and not event["exact"]:
            self.append_log("候选 %s 不匹配，回退后继续选择。" % event["key"])
        elif kind == "split":
            self.append_log(event["reason"]+"："+event["text"])
        elif kind == "diff":
            self.append_log("发现 %s 处差异，正在定位修正。" % len(event["patches"]))
        elif kind == "progress":
            self.progress.set(event["completed"] / max(1,event["total"]) * 100)
        elif kind == "complete":
            self.last_elapsed=event.get("elapsed_ms",0)/1000
            self.progress.set(100)
            self.append_log("全文复制核验通过：%s 字符。" % event["characters"])

    def check_server(self):
        if self.busy:return
        client=Ollama(self.url.get(),self.model.get())
        self.set_busy(True)
        self.status.set("检查模型列表与 digest…")
        def work():
            try:self.events.put(("checked",client.check()))
            except Exception as error:self.events.put(("error",error))
        threading.Thread(target=work,daemon=True).start()

    def plan_text(self,auto_run=False):
        if self.busy:return
        source=self.source.get("1.0","end-1c")
        mode=self.mode.get()
        client=Ollama(self.url.get(),self.model.get(),timeout=20 if mode=='hybrid' else 180) if mode in ('qwen','hybrid') else None
        self.plan=[]
        self.set_busy(True)
        self.progress.set(0)
        self.status.set("正在生成输入计划…")
        def work():
            try:
                active=client
                try:metadata=active.check() if active else {}
                except RuntimeError:
                    if mode!='hybrid':raise
                    active,metadata=None,{'mode':'hybrid','fallback':'local'}
                planner=make_hybrid_plan if mode=='hybrid' else make_plan
                chunks=planner(source,active,lambda text:self.events.put(("status",text)))
                self.events.put(("plan",(source,chunks,metadata,auto_run)))
            except Exception as error:self.events.put(("error",error))
        threading.Thread(target=work,daemon=True).start()

    def open_quick(self):
        pace=next(key for key,profile in PACES.items() if profile.label==self.pace.get())
        subprocess.Popen([sys.executable,"-m","realtyper_agent","--quick","--url",self.url.get(),
            "--model",self.model.get(),"--pace",pace,"--"+self.mode.get()],
            cwd=str(Path(__file__).resolve().parents[1]),creationflags=subprocess.CREATE_NO_WINDOW)

    def run_agent(self,repair_only=False):
        if self.busy or not self.plan:return
        if self.source.get("1.0","end-1c")!=self.plan_source:
            self.status.set("原文已修改，请重新生成计划。")
            return
        if self.args.smoke and not repair_only:
            # Automated smoke tests own this newly-created fixture, including
            # resetting any text received during its activation/setup phase.
            self.editor.clear()
        self.set_busy(True,running=True)
        self.editor.focus()
        pace=next(key for key,profile in PACES.items() if profile.label==self.pace.get())
        backend=NativeBackend(self.editor,self.call_ui,pace=pace)
        self.agent=Agent(backend,lambda event:self.events.put(("agent",event)))
        def work():
            try:
                self.agent.run(self.plan_source,self.plan,self.plan_metadata,repair_only)
                self.last_log=self.agent.path
                self.events.put(("finished",None))
            except Exception as error:
                self.last_log=self.agent.path
                self.events.put(("error",error))
        threading.Thread(target=work,daemon=True).start()

    def new_document(self):
        if not self.busy:
            self.editor.clear()
            self.progress.set(0)
            self.status.set("已新建空白测试文档")

    def import_text(self):
        try:
            text=clipboard_text()
            self.source.delete("1.0","end")
            self.source.insert("1.0",text)
            self.plan=[]
            self.set_busy(False)
            self.status.set("已读取原文，请生成计划。")
        except Exception as error:self.status.set(str(error))

    def pause(self):
        if self.agent:
            if self.agent.pause_event.is_set():
                self.editor.focus()
                self.agent.pause_event.clear()
                self.pause_button.configure(text="暂停")
            else:
                self.agent.pause_event.set()
                self.editor.focus()
                self.pause_button.configure(text="继续")
                self.status.set("已请求暂停，点击继续恢复。")

    def stop(self):
        if self.agent:self.agent.stop_event.set()

    def close(self):
        self.stop()
        if self.editor.tsf:
            self.editor.tsf.close()
        self.root.destroy()

    def smoke_finish(self,success,error=""):
        self.exit_code=0 if success else 1
        destination=Path(__file__).resolve().parent.parent/"runs"
        destination.mkdir(exist_ok=True)
        report={"success":success,"error":error,"source":self.plan_source,"actual":self.editor.read(),
                'focus':getattr(self.editor,'focus_diagnostics',{}),
                "log":str(self.last_log),"model":self.plan_metadata,"planner":self.mode.get(),
                "rhythm":self.agent.backend.rhythm.summary() if self.agent else {}}
        (destination/"smoke-result.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        if not self.args.keep_open:
            self.root.after(500,self.close)


def build_parser():
    parser=argparse.ArgumentParser(prog="python -m realtyper_agent",description="RealTyper-Agent · 中文拼音智能录入与本地验证台")
    parser.add_argument("--url",default=DEFAULT_URL)
    parser.add_argument("--model",default=DEFAULT_MODEL)
    planning=parser.add_mutually_exclusive_group()
    planning.add_argument("--offline",dest="planner",action="store_const",const="offline",help="仅使用本地词典分词，不调用模型")
    planning.add_argument("--qwen",dest="planner",action="store_const",const="qwen",help="显式启用本机 Qwen 分词")
    planning.add_argument('--hybrid',dest='planner',action='store_const',const='hybrid',help='本地词典与 Qwen 结合（默认），模型失败时继续本地计划')
    parser.set_defaults(planner="hybrid")
    parser.add_argument("--pace",choices=list(PACES),default="natural",help="输入节奏：slow 舒缓，natural 自然（默认），brisk 较快")
    parser.add_argument("--text")
    parser.add_argument("--text-file",help="从 UTF-8 文件读取原文")
    parser.add_argument("--smoke",action="store_true",help="在自有编辑器中执行一次真实输入验证")
    parser.add_argument("--keep-open",action="store_true")
    parser.add_argument("--quick",action="store_true",help="小窗口常驻，从其他应用当前光标处插入并只核验本次内容")
    return parser


def main():
    args=build_parser().parse_args()
    if args.quick:
        from .quick import main as quick_main
        return quick_main(args)
    root=tk.Tk()
    app=App(root,args)
    root.mainloop()
    return app.exit_code


if __name__=="__main__":main()
