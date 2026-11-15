"""Real global-hotkey integration against a separate, self-owned target process."""
import argparse
import ctypes as C
import json
from pathlib import Path
import subprocess
import sys
import time
import tkinter as tk

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/".deps")]
from pinyin_agent import windows as win
from pinyin_agent.quick import QuickSession
from pinyin_agent.hotkeys import GlobalHotkeys
from pinyin_agent.ui import build_parser
from pinyin_agent.external import capture_target
from pinyin_agent import __version__


def fixture(args):
    root=tk.Tk()
    root.title("快捷输入 · 自有跨进程测试框")
    root.geometry("820x420")
    tk.Label(root,text="自有测试：请保持本窗口焦点，测试结束会自动关闭。",font=("Microsoft YaHei UI",12)).pack(pady=12)
    frame=tk.Frame(root)
    frame.pack(fill="both",expand=True,padx=20,pady=12)
    editor=win.NativeEditor(frame)
    other=tk.Entry(root)
    other.pack(fill="x",padx=20,pady=8)
    source="发力、法力、乏力。\nHello 😀 123" if args.case=="native" else "默认模式"
    if args.case in ("focus","pause-stop","pause-resume"):source="发力法力乏力"
    initial,position,selection_end='',0,0
    if args.case in ('nonempty','append'):
        initial='已有内容😀'
        position=selection_end=len(initial)
        source='发力'
    elif args.case=='insert-middle':
        initial='前文😀\n后文法力'
        position=selection_end=len('前文😀\n')
        source='法力'
    elif args.case=='insert-head':
        initial='原有后文😀'
        source='发力'
    elif args.case=='selected':
        initial='前文后文'
        position,selection_end=0,2
        source='发力'
    elif args.case=='english-mode':
        initial='前文😀后文'
        position=selection_end=len('前文😀')
        source='发力'
    expected=initial[:selection_end]+source+initial[selection_end:]
    native=win.WindowsBackend(editor,lambda fn:fn())
    began=time.monotonic()
    ready=False
    armed_selection=[]
    initial_conversion=[]
    english_probe={'verified':False}
    focus_history=[]
    def snapshot():
        foreground=win.u.GetForegroundWindow()
        owner=win.W.DWORD()
        win.u.GetWindowThreadProcessId(foreground,C.byref(owner))
        name=C.create_unicode_buffer(160)
        win.u.GetClassNameW(foreground,name,len(name))
        if not focus_history or focus_history[-1]['hwnd']!=foreground:
            focus_history.append({'hwnd':foreground,'pid':owner.value,'class':name.value,
                                  'ms':round((time.monotonic()-began)*1000)})
        data={"text":editor.read(),"other":other.get(),"source":source,"ready":ready,
              'fixture_root':editor.root,'focus_history':focus_history[-20:],
              'initial':initial,'expected':expected,'insertion_index':selection_end,
              'selection':list(native.selection()),'armed_selection':armed_selection,
              'english_keystroke_verified':english_probe['verified'],
              'initial_ime_conversion':initial_conversion[0] if initial_conversion else None}
        (args.folder/"fixture.json").write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")
    def trigger():
        nonlocal ready
        root.attributes("-topmost",True)
        root.lift()
        root.focus_force()
        editor.focus()
        rect=win.W.RECT()
        win.u.GetWindowRect(editor.hwnd,C.byref(rect))
        point=win.W.POINT(rect.left+25,rect.top+25)
        if win.u.WindowFromPoint(point)!=editor.hwnd:raise RuntimeError("Fixture is occluded")
        # Native SetFocus above is verified below. A queued synthetic click here
        # could arrive after fixture setup and silently move a nonempty caret.
        if initial:
            buffer=C.create_unicode_buffer(initial)
            win.u.SendMessageW(editor.hwnd,0xC,0,C.addressof(buffer))
        native.chinese_mode()
        if initial:native.select(position,selection_end,expected=initial)
        win.set_clipboard_text(source,editor.hwnd)
        ready=True
        def launch():
            try:
                info=win.GUITHREADINFO(cbSize=C.sizeof(win.GUITHREADINFO))
                win.u.GetGUIThreadInfo(editor.thread,C.byref(info))
                if (win.u.GetForegroundWindow()!=editor.root or info.hwndFocus!=editor.hwnd):
                    raise RuntimeError("Fixture lost focus before hotkey; no input sent")
                if args.case=='english-mode':
                    def force_english(context):
                        mode,sentence=win.W.DWORD(),win.W.DWORD()
                        if not win.imm.ImmGetConversionStatus(context,C.byref(mode),C.byref(sentence)):
                            raise RuntimeError('Fixture IME state is unavailable')
                        if not win.imm.ImmSetConversionStatus(context,mode.value&~1,sentence.value):
                            raise RuntimeError('Fixture cannot set English mode')
                        win.imm.ImmGetConversionStatus(context,C.byref(mode),C.byref(sentence))
                        initial_conversion[:]=[mode.value]
                        if mode.value&1:raise RuntimeError('Fixture is not in English mode')
                    native.with_context(force_english)
                    # IMM's conversion bit can lag behind TSF state. Verify an
                    # actual ASCII keystroke before claiming an English setup.
                    def fail_setup(error):
                        (args.folder/'setup-error.txt').write_text(str(error),encoding='utf-8')
                        root.after(100,root.destroy)
                    def check_focus():
                        current=win.GUITHREADINFO(cbSize=C.sizeof(win.GUITHREADINFO))
                        win.u.GetGUIThreadInfo(editor.thread,C.byref(current))
                        if win.u.GetForegroundWindow()!=editor.root or current.hwndFocus!=editor.hwnd:
                            raise RuntimeError('English fixture lost focus; no input sent')
                    def activate_after_probe():
                        try:
                            check_focus()
                            if editor.read()!=initial:raise RuntimeError('English probe did not restore fixture')
                            armed_selection[:]=native.selection()
                            if tuple(armed_selection)!=(position,selection_end):raise RuntimeError('English probe changed caret')
                            win.send_key(0x56,(0x11,0x12))
                        except Exception as error:fail_setup(error)
                    def verify_probe(retry):
                        try:
                            check_focus()
                            actual=editor.read()
                            if actual in (initial[:position]+c+initial[position:] for c in ('x','X')):
                                english_probe['verified']=True
                                win.send_key(8)
                                root.after(150,activate_after_probe)
                            elif actual==initial and retry==0:
                                win.send_key(0x1B)
                                win.send_key(0x10)
                                root.after(150,lambda:probe_english(1))
                            else:raise RuntimeError('Fixture could not verify actual English input')
                        except Exception as error:fail_setup(error)
                    def probe_english(retry=0):
                        try:
                            check_focus()
                            win.send_key(ord('X'))
                            root.after(150,lambda:verify_probe(retry))
                        except Exception as error:fail_setup(error)
                    probe_english()
                    return
                if initial:native.select(position,selection_end,expected=initial)
                armed_selection[:]=native.selection()
                if initial and tuple(armed_selection)!=(position,selection_end):
                    raise RuntimeError('Fixture caret changed before hotkey; no input sent')
                win.send_key(0x56,(0x11,0x12))
            except Exception as error:
                (args.folder/'setup-error.txt').write_text(str(error),encoding='utf-8')
                root.after(100,root.destroy)
        root.after(200,launch)
    def pump():
        if (args.folder/"focus").exists():
            (args.folder/"focus").unlink()
            other.focus_force()
        for name,key in (("pause",0x56),("stop",0x51)):
            signal=args.folder/name
            if signal.exists():
                signal.unlink()
                # Only the fixture itself sends its hotkeys while it is foreground.
                info=win.GUITHREADINFO(cbSize=C.sizeof(win.GUITHREADINFO))
                win.u.GetGUIThreadInfo(editor.thread,C.byref(info))
                if win.u.GetForegroundWindow()==editor.root and info.hwndFocus==editor.hwnd:
                    win.send_key(key,(0x11,0x12))
        snapshot()
        if (args.folder/"close").exists() or time.monotonic()-began>110:
            root.destroy()
        else:root.after(80,pump)
    root.after(1000,trigger)
    root.after(150,pump)
    root.mainloop()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--fixture",action="store_true")
    parser.add_argument("--case",choices=("native","model","nonempty","append","insert-middle","insert-head","selected","english-mode","focus","pause-stop","pause-resume"),default="native")
    parser.add_argument("--folder",type=Path)
    args=parser.parse_args()
    if args.fixture:
        fixture(args)
        return 0
    args.folder=ROOT/"runs"/("external-acceptance-"+__version__+'-'+args.case)
    args.folder.mkdir(exist_ok=True)
    for name in ("close","pause","stop","focus","fixture.json","setup-error.txt"):
        path=args.folder/name
        if path.exists():path.unlink()
    root=tk.Tk()
    root.title("快捷输入 · 测试控制器")
    root.geometry("400x100")
    root.update_idletasks()
    events=[]
    started=[False]
    state={"pause_sent":False,"stop_sent":False,"resume_sent":False,"focus_sent":False,"paused_at":None}
    configured=build_parser().parse_args([] if args.case=="model" else ["--offline"])
    session=QuickSession(configured,win.u.GetAncestor(root.winfo_id(),2),lambda kind,data:events.append((kind,data,time.monotonic())))
    process=None
    def hotkey(action,error=None):
        if action=="activate":
            try:
                if process is None or capture_target().pid!=process.pid:return
            except RuntimeError:return
            started[0]=True
            session.activate()
        elif action=="stop":session.stop()
        elif action=="error":events.append(("error",str(error),time.monotonic()))
    hotkeys=GlobalHotkeys(hotkey)
    hotkeys.start()
    if not hotkeys.ready.wait(3) or hotkeys.error:raise RuntimeError(str(hotkeys.error))
    process=subprocess.Popen([sys.executable,__file__,"--fixture","--case",args.case,"--folder",str(args.folder)],cwd=str(ROOT))
    began=time.monotonic()
    report={}
    def pump():
        typing=any(k=="agent" and d["kind"]=="typing" for k,d,_ in events)
        progress=any(k=="agent" and d["kind"]=="progress" for k,d,_ in events)
        if args.case=="focus" and typing and not state["focus_sent"]:
            (args.folder/"focus").touch()
            state["focus_sent"]=True
        if args.case in ("pause-stop","pause-resume") and progress and not state["pause_sent"]:
            (args.folder/"pause").touch()
            state["pause_sent"]=True
        if args.case in ("pause-stop","pause-resume") and session.pause_event.is_set():
            paused=next((t for k,d,t in events if k=="agent" and d["kind"]=="paused"),None)
            if state["paused_at"] is None and paused:state["paused_at"]=paused
            elif state["paused_at"] and time.monotonic()-state["paused_at"]>2:
                if args.case=="pause-stop" and not state["stop_sent"]:
                    (args.folder/"stop").touch()
                    state["stop_sent"]=True
                elif args.case=="pause-resume" and not state["resume_sent"]:
                    (args.folder/"pause").touch()
                    state["resume_sent"]=True
        done=(started[0] and not session.busy and any(k in ("idle","error") for k,_,_ in events)) or process.poll() is not None
        if done or time.monotonic()-began>100:
            snapshot={}
            path=args.folder/"fixture.json"
            if path.exists():snapshot=json.loads(path.read_text(encoding="utf-8"))
            complete=next((d for k,d,_ in events if k=="complete"),None)
            error=next((d for k,d,_ in events if k=="error"),None)
            if (args.folder/'setup-error.txt').exists():error=(args.folder/'setup-error.txt').read_text(encoding='utf-8')
            task_result=next((d for k,d,_ in events if k=='agent' and d['kind']=='complete'),{})
            success=(bool(complete) and snapshot.get('expected')==snapshot.get('text')
                     and task_result.get('actual')==snapshot.get('source')
                     and snapshot.get('selection')==[snapshot.get('insertion_index',0)+len(snapshot.get('source',''))]*2
                     and win.clipboard_text()==snapshot.get('source'))
            if args.case=="focus":success=bool(error and "焦点" in error) and snapshot.get("other")==""
            if args.case=="pause-stop":success=state["stop_sent"] and not complete and not error
            if args.case=="pause-resume":success=success and state["resume_sent"]
            if args.case=='english-mode':
                success=success and snapshot.get('english_keystroke_verified') is True
                success=success and any(k=='agent' and d['kind']=='ime_recovery' for k,d,_ in events)
            report.update(success=success,case=args.case,fixture=snapshot,error=error,state=state,
                          events=[{"kind":k,"data":d} for k,d,_ in events],
                          elapsed_ms=round((time.monotonic()-began)*1000),log=str(session.agent.path) if session.agent else None)
            (args.folder/"result.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
            session.stop()
            (args.folder/"close").touch()
            hotkeys.close()
            root.destroy()
        else:root.after(100,pump)
    root.after(100,pump)
    root.mainloop()
    process.wait(timeout=5)
    print(json.dumps({k:v for k,v in report.items() if k!="events"},ensure_ascii=True))
    return 0 if report.get("success") else 1


if __name__=="__main__":raise SystemExit(main())
