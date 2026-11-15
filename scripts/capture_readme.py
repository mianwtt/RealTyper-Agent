"""Export the app's own UI for documentation; no control of other applications."""
import ctypes as C
import json
from pathlib import Path
import sys
import time
import tkinter as tk

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]
from PIL import ImageGrab
from pinyin_agent.quick import QuickPanel
from pinyin_agent.ui import App,build_parser
from pinyin_agent import windows as win

ASSETS=ROOT/'docs'/'assets'
ASSETS.mkdir(parents=True,exist_ok=True)
report={'screenshots':[],'success':True}

def export(root,filename):
    root.update_idletasks()
    hwnd=win.u.GetAncestor(root.winfo_id(),2)
    rect=win.W.RECT()
    win.u.GetWindowRect(hwnd,C.byref(rect))
    # DWM's visible frame excludes the transparent resize margins and background.
    dwm=C.WinDLL('dwmapi')
    win.signature(dwm,'DwmGetWindowAttribute',C.c_long,[win.W.HWND,win.W.DWORD,C.c_void_p,win.W.DWORD])
    visible=win.W.RECT()
    if dwm.DwmGetWindowAttribute(hwnd,9,C.byref(visible),C.sizeof(visible))==0:
        rect=visible
    if rect.left<0 or rect.top<0 or rect.right>win.u.GetSystemMetrics(0) or rect.bottom>win.u.GetSystemMetrics(1):
        raise RuntimeError('Documentation window exceeds the display; capture cancelled')
    for x,y in ((rect.left+24,rect.top+45),(rect.right-24,rect.bottom-24)):
        point=win.W.POINT(x,y)
        if win.u.GetAncestor(win.u.WindowFromPoint(point),2)!=hwnd:
            raise RuntimeError('Own documentation window is occluded; capture cancelled')
    ImageGrab.grab(bbox=(rect.left,rect.top,rect.right,rect.bottom)).save(ASSETS/filename)
    report['screenshots'].append({'file':filename,'width':rect.right-rect.left,'height':rect.bottom-rect.top})

def capture_quick():
    root=tk.Tk()
    panel=QuickPanel(root,build_parser().parse_args([]))
    # Documentation fixtures never start typing into another application.
    panel.session.activate=lambda:None
    root.attributes('-topmost',True)
    root.lift()
    root.focus_force()
    began=time.monotonic()
    def poll():
        if panel.hotkeys.error:
            report.update(success=False,quick_error=str(panel.hotkeys.error))
            panel.close()
        elif panel.hotkeys.ready.is_set() and time.monotonic()-began>.8:
            try:
                export(root,'quick-mode.png')
                report['quick_status']=panel.status.get()
            except Exception as error:report.update(success=False,quick_error=str(error))
            panel.close()
        elif time.monotonic()-began>8:
            report.update(success=False,quick_error='Hotkey registration timed out')
            panel.close()
        else:root.after(100,poll)
    root.after(100,poll)
    root.mainloop()

def capture_planner():
    root=tk.Tk()
    args=build_parser().parse_args(['--text','我会把默认模式改为离线规则。'])
    app=App(root,args)
    # Fit this display while keeping every action and the status line visible.
    root.geometry('1180x820+20+20')
    app.source.configure(height=4)
    app.log.configure(height=6)
    app.table.configure(height=7)
    root.attributes('-topmost',True)
    root.lift()
    root.focus_force()
    began=time.monotonic()
    ready_at=[None]
    root.after(400,app.plan_text)
    def poll():
        if app.plan and not app.busy:
            if ready_at[0] is None:ready_at[0]=time.monotonic()
            if time.monotonic()-ready_at[0]>.6:
                try:
                    export(root,'planner.png')
                    report.update(plan=[c.to_dict() for c in app.plan],model=app.plan_metadata)
                except Exception as error:report.update(success=False,planner_error=str(error))
                app.close()
                return
        if time.monotonic()-began>60 or (time.monotonic()-began>2 and not app.busy and not app.plan):
            report.update(success=False,planner_error=app.status.get())
            app.close()
        else:root.after(100,poll)
    root.after(100,poll)
    root.mainloop()

capture_quick()
if '--quick-only' not in sys.argv:capture_planner()
report_name='readme-quick-capture.json' if '--quick-only' in sys.argv else 'readme-capture.json'
(ROOT/'runs'/report_name).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=True))
raise SystemExit(0 if report['success'] else 1)
