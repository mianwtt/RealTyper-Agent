"""Real Windows-only integration checks against a self-owned RichEdit window.

Run explicitly: python tests/windows_acceptance.py
These checks send genuine IME keystrokes. Do not switch windows while running.
"""
import sys
import argparse
import json
import queue
import threading
import tkinter as tk
import ctypes as C
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/".deps")]
from pinyin_agent.windows import NativeEditor,WindowsBackend,u,W,INPUT,MOUSEINPUT
from pinyin_agent.engine import Agent
from pinyin_agent.planner import make_plan
from pinyin_agent import __version__
from pinyin_agent.ollama import Ollama
from pinyin_agent.rhythm import PACES


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--case",choices=("all","homophones","mixed","repair","local-model","model-reading-fix","local-dictionary"),default="all")
    parser.add_argument("--pace",choices=list(PACES),default="natural")
    parser.add_argument("--keep-open",action="store_true")
    parser.add_argument("--start-delay",type=float,default=1.6)
    args=parser.parse_args()
    prepared=None
    if args.case=="local-model":
        client=Ollama()
        metadata=client.check()
        source="我会把默认模式改为离线规则"
        prepared=(source,make_plan(source,client),metadata)
    if args.case=="local-dictionary":
        source='默认模式'
        prepared=(source,make_plan(source),{'mode':'local-dictionary','qwen_calls':0})
    if args.case=="model-reading-fix":
        class IncorrectReadingModel:
            def plan(self,text,context):
                return [{"text":text,"syllables":["xuan","wen"]}]
        source="原文"
        notices=[]
        chunks=make_plan(source,IncorrectReadingModel(),notices.append)
        prepared=(source,chunks,{"mode":"invalid-reading-regression-fixture","notices":notices})
    root=tk.Tk()
    root.title("拼音 Agent v%s · 光标演示（%s）" % (__version__,PACES[args.pace].label))
    root.geometry("900x500")
    status=tk.StringVar(value="输入节奏：%s。请观察输入末尾的光标，保持本窗口焦点。F12 可停止。" % PACES[args.pace].label)
    tk.Label(root,textvariable=status,font=("Microsoft YaHei UI",12)).pack(pady=16)
    frame=tk.Frame(root)
    frame.pack(fill="both",expand=True,padx=20,pady=20)
    editor=NativeEditor(frame)
    calls=queue.Queue()
    results=[]
    finished=threading.Event()
    def ui_call(callback):
        event,result=threading.Event(),[]
        calls.put((callback,event,result))
        if not event.wait(5):raise RuntimeError("UI timeout")
        if isinstance(result[0],Exception):raise result[0]
        return result[0]
    def pump():
        while not calls.empty():
            callback,event,result=calls.get()
            try:result.append(callback())
            except Exception as error:result.append(error)
            event.set()
        if finished.is_set():
            if args.keep_open:
                status.set("演示已结束，结果保留在下方。可以查看光标位置，然后关闭此窗口。")
            else:
                close()
        else:root.after(15,pump)
    def run_case(name,expected,initial=None,chunks=None,metadata=None):
        ui_call(editor.clear)
        if initial is not None:
            # The faulty fixture itself is typed through the real system IME.
            Agent(WindowsBackend(editor,ui_call,pace=args.pace)).run(initial,make_plan(initial))
        events=[]
        backend=WindowsBackend(editor,ui_call,pace=args.pace)
        agent=Agent(backend,events.append)
        actual=agent.run(expected,chunks if chunks is not None else make_plan(expected),metadata,
                         repair_only=initial is not None)
        complete=next(e for e in reversed(events) if e["kind"]=="complete")
        timing=backend.rhythm.summary()
        key_stats=timing["delays"].get("key",{})
        variable=key_stats.get("count",0)>1 and key_stats["min_ms"]<key_stats["max_ms"]
        results.append({"name":name,"version":__version__,"success":actual==expected and variable,"actual":actual,
                        "backspace_correction":any(e["kind"]=="diff" for e in events),
                        "elapsed_ms":complete["elapsed_ms"],"rhythm":timing,"model":metadata or {"mode":"offline"},
                        "plan":[chunk.to_dict() for chunk in chunks] if chunks is not None else [],"log":str(agent.path)})
    def work():
        try:
            if args.case in ("all","homophones"):
                run_case("homophones","发力、法力、乏力")
            if args.case in ("all","mixed"):
                run_case("mixed_multiline","银行行长在重庆。\nHello  world! 123 😀")
            if args.case in ("all","repair"):
                run_case("repair_with_surrogate_and_linebreak","A😀\n继续发力，提高生产力。",
                         initial="A😀\n继续法力，提高生力。")
            if args.case in ("local-model","model-reading-fix","local-dictionary"):
                source,chunks,metadata=prepared
                name='local_dictionary_plan' if args.case=='local-dictionary' else ('model_reading_correction' if args.case=='model-reading-fix' else 'local_model_semantic_plan')
                run_case(name,source,chunks=chunks,metadata=metadata)
        except Exception as error:
            results.append({"name":"integration_error","success":False,"error":str(error),
                            "focus":getattr(editor,"focus_diagnostics",{}),"text":editor.read()})
        finally:
            suffix="" if args.case=="all" else "-"+args.case
            if args.pace!="natural":suffix+="-"+args.pace
            (ROOT/"runs"/("windows-acceptance-"+__version__+suffix+".json")).write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
            finished.set()
    def start():
        root.attributes("-topmost",True)
        root.lift()
        root.focus_force()
        root.update_idletasks()
        rect=W.RECT()
        u.GetWindowRect(editor.hwnd,C.byref(rect))
        point=W.POINT(rect.left+20,rect.top+20)
        # Click only if this exact coordinate currently belongs to our test editor.
        if u.GetAncestor(u.WindowFromPoint(point),2)!=u.GetAncestor(editor.hwnd,2):
            raise RuntimeError("Test editor is occluded; no input sent")
        original=W.POINT()
        u.GetCursorPos(C.byref(original))
        u.SetCursorPos(point.x,point.y)
        clicks=(INPUT*2)(INPUT(type=0,mi=MOUSEINPUT(dwFlags=2)),INPUT(type=0,mi=MOUSEINPUT(dwFlags=4)))
        u.SendInput(2,clicks,C.sizeof(INPUT))
        u.SetCursorPos(original.x,original.y)
        if args.case not in ("model-reading-fix","local-dictionary"):
            root.after(300,lambda:root.attributes("-topmost",False))
        threading.Thread(target=work,daemon=True).start()
    def close():
        if editor.tsf:editor.tsf.close()
        root.destroy()
    root.protocol("WM_DELETE_WINDOW",close)
    remaining=[max(0,int(args.start_delay))]
    def countdown():
        if remaining[0]>0:
            status.set("%s 秒后开始慢速打字，请准备录屏；开始后保持此窗口焦点。" % remaining[0])
            remaining[0]-=1
            root.after(1000,countdown)
        else:
            status.set("正在慢速打字，请录屏。F12 可停止。")
            start()
    root.after(200,countdown)
    root.after(15,pump)
    root.mainloop()
    print(json.dumps(results,ensure_ascii=True))
    return 0 if results and all(r["success"] for r in results) else 1


if __name__=="__main__":raise SystemExit(main())
