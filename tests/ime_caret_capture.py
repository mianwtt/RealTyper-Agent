"""Compare editor/IME rendering using a separate capture process (no UI-thread grabs)."""
import argparse
import ctypes as C
import json
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/".deps")]
from pinyin_agent import windows as win
from pinyin_agent.domain import Chunk
from pinyin_agent.engine import Agent


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--tsf",choices=("forced","native"),default="native")
    args=parser.parse_args()
    label=args.tsf
    root=tk.Tk()
    root.title("微软拼音光标兼容性对照 · "+label)
    root.geometry("900x300")
    tk.Label(root,text="正在检查多字拼音光标兼容性；只输入测试文本，请保持窗口焦点。").pack(pady=12)
    frame=tk.Frame(root)
    frame.pack(fill="both",expand=True,padx=20,pady=15)
    editor=win.NativeEditor(frame,use_tsf=args.tsf=="forced")
    calls=queue.Queue()
    done=threading.Event()
    result={"mode":label}
    capture=[None]
    events=[]
    def ui_call(fn):
        event,values=threading.Event(),[]
        calls.put((fn,event,values))
        if not event.wait(5):raise RuntimeError("UI timeout")
        if isinstance(values[0],Exception):raise values[0]
        return values[0]
    def emit(event):
        events.append(event)
    def pump():
        while not calls.empty():
            fn,event,values=calls.get()
            try:values.append(fn())
            except Exception as error:values.append(error)
            event.set()
        if done.is_set():
            if capture[0]:
                try:
                    capture[0].stdin.write(b"q\n")
                    capture[0].stdin.flush()
                    capture[0].wait(timeout=8)
                except Exception:capture[0].terminate()
            result["events"]=events
            (ROOT/"runs"/("ime-caret-"+label+".json")).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
            if editor.tsf:editor.tsf.close()
            root.destroy()
        else:root.after(15,pump)
    def work():
        try:
            result["initial_text"]=ui_call(editor.read)
            ui_call(editor.clear)
            chunks=[Chunk("我",("wo",)),Chunk("会",("hui",)),Chunk("把",("ba",)),
                    Chunk("默认",("mo","ren")),Chunk("模式",("mo","shi"))]
            actual=Agent(win.WindowsBackend(editor,ui_call,pace="slow"),emit).run("我会把默认模式",chunks)
            result.update(success=actual=="我会把默认模式",actual=actual)
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
            result.update(success=False,error="Own editor occluded")
            done.set()
            return
        cursor=win.W.POINT()
        win.u.GetCursorPos(C.byref(cursor))
        win.u.SetCursorPos(point.x,point.y)
        clicks=(win.INPUT*2)(win.INPUT(type=0,mi=win.MOUSEINPUT(dwFlags=2)),win.INPUT(type=0,mi=win.MOUSEINPUT(dwFlags=4)))
        win.u.SendInput(2,clicks,C.sizeof(win.INPUT))
        win.u.SetCursorPos(cursor.x,cursor.y)
        target=ROOT/"runs"/("ime-caret-"+label+".mp4")
        capture[0]=subprocess.Popen(["ffmpeg","-hide_banner","-loglevel","error","-y","-f","gdigrab",
            "-draw_mouse","0","-framerate","30","-offset_x",str(rect.left),"-offset_y",str(rect.top),
            "-video_size",str(min(850,rect.right-rect.left))+"x90","-i","desktop",
            "-c:v","libx264","-preset","ultrafast","-crf","16",str(target)],stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        root.after(900,lambda:threading.Thread(target=work,daemon=True).start())
    root.after(1000,start)
    root.after(15,pump)
    root.mainloop()
    print(json.dumps({k:v for k,v in result.items() if k!="events"},ensure_ascii=True))


if __name__=="__main__":main()
