"""Record the real system caret in a self-owned editor during a short IME run."""
import ctypes as C
import argparse
import json
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/".deps")]
from pinyin_agent import windows as win
from pinyin_agent.engine import Agent
from pinyin_agent.planner import make_plan


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--full-ui",action="store_true")
    parser.add_argument("--label",default="simple")
    args=parser.parse_args()
    root=tk.Tk()
    app=None
    if args.full_ui:
        from pinyin_agent.ui import App,build_parser
        app=App(root,build_parser().parse_args(["--offline","--pace","slow","--text","打字"]))
        editor=app.editor
    else:
        root.title("拼音光标位置诊断")
        root.geometry("800x350")
        tk.Label(root,text="正在记录真实光标位置，短句“打字”，请保持本窗口焦点。").pack(pady=15)
        frame=tk.Frame(root)
        frame.pack(fill="both",expand=True,padx=20,pady=20)
        editor=win.NativeEditor(frame)
    calls=queue.Queue()
    done=threading.Event()
    trace=[]
    began=time.perf_counter()
    phase=["setup"]
    result={}
    images=[]
    last_capture=[0.]
    def record(kind,**data):
        trace.append({"t":round(time.perf_counter()-began,5),"kind":kind,"phase":phase[0],**data})
    def ui_call(fn):
        event,value=threading.Event(),[]
        calls.put((fn,event,value))
        if not event.wait(5):raise RuntimeError("UI timeout")
        if isinstance(value[0],Exception):raise value[0]
        return value[0]
    def emit(event):
        if event["kind"]=="typing":phase[0]="composing"
        elif event["kind"]=="selected":phase[0]="committed"
        elif event["kind"]=="complete":phase[0]="complete"
        record("event",event=event)
    original_key=win.send_key
    def key(vk,modifiers=()):
        record("key",vk=vk)
        return original_key(vk,modifiers)
    win.send_key=key
    def pump():
        while not calls.empty():
            fn,event,value=calls.get()
            try:value.append(fn())
            except Exception as error:value.append(error)
            event.set()
        info=win.GUITHREADINFO(cbSize=C.sizeof(win.GUITHREADINFO))
        if win.u.GetGUIThreadInfo(editor.thread,C.byref(info)):
            record("caret",x=info.rcCaret.left,y=info.rcCaret.top,flags=info.flags,
                   focus=info.hwndFocus,caret=info.hwndCaret,tk_focus=str(root.focus_get()))
        now=time.perf_counter()-began
        if app and phase[0] in ("composing","committed") and now-last_capture[0]>.25:
            from PIL import ImageGrab,Image,ImageDraw
            source_rect=(app.source.winfo_rootx(),app.source.winfo_rooty(),app.source.winfo_rootx()+340,app.source.winfo_rooty()+45)
            target_rect=win.W.RECT()
            win.u.GetWindowRect(editor.hwnd,C.byref(target_rect))
            source=ImageGrab.grab(bbox=source_rect)
            target=ImageGrab.grab(bbox=(target_rect.left,target_rect.top,target_rect.left+340,target_rect.top+45))
            row=Image.new("RGB",(800,55),"#e7ecf4")
            ImageDraw.Draw(row).text((2,5),"%.2fs" % now,fill="black")
            row.paste(source,(90,5))
            row.paste(target,(450,5))
            images.append(row)
            last_capture[0]=now
        if done.is_set():
            result["trace"]=trace
            (ROOT/"runs"/("caret-diagnostic-"+args.label+".json")).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
            if images:
                from PIL import Image
                montage=Image.new("RGB",(800,55*len(images)),"white")
                for index,row in enumerate(images):montage.paste(row,(0,index*55))
                montage.save(ROOT/"runs"/("caret-diagnostic-"+args.label+".png"))
            if editor.tsf:editor.tsf.close()
            root.destroy()
        else:root.after(5,pump)
    def work():
        try:
            backend=win.WindowsBackend(editor,ui_call,pace="slow")
            actual=Agent(backend,emit).run("打字",make_plan("打字"))
            result.update(success=actual=="打字",actual=actual)
        except Exception as error:result.update(success=False,error=str(error))
        finally:done.set()
    def start():
        root.attributes("-topmost",True)
        root.lift()
        root.focus_force()
        root.update_idletasks()
        rect=win.W.RECT()
        win.u.GetWindowRect(editor.hwnd,C.byref(rect))
        point=win.W.POINT(rect.left+15,rect.top+15)
        if win.u.GetAncestor(win.u.WindowFromPoint(point),2)!=editor.root:
            result.update(success=False,error="Test window occluded")
            done.set()
            return
        mouse=win.W.POINT()
        win.u.GetCursorPos(C.byref(mouse))
        win.u.SetCursorPos(point.x,point.y)
        inputs=(win.INPUT*2)(win.INPUT(type=0,mi=win.MOUSEINPUT(dwFlags=2)),win.INPUT(type=0,mi=win.MOUSEINPUT(dwFlags=4)))
        win.u.SendInput(2,inputs,C.sizeof(win.INPUT))
        win.u.SetCursorPos(mouse.x,mouse.y)
        if app:
            # Reproduce the normal flow: the source Text widget was last edited.
            app.source.focus_set()
            app.source.mark_set("insert","1.0")
            record("source_focused",tk_focus=str(root.focus_get()))
        threading.Thread(target=work,daemon=True).start()
    root.after(700,start)
    root.after(5,pump)
    root.mainloop()
    print(json.dumps({k:v for k,v in result.items() if k!="trace"},ensure_ascii=True))


if __name__=="__main__":main()
