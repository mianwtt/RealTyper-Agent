"""Global-hotkey test in a self-owned localhost Chrome textarea, no user sites."""
import ctypes as C
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import tkinter as tk

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/".deps")]
from pinyin_agent import windows as win
from pinyin_agent.external import capture_target
from pinyin_agent.quick import QuickSession
from pinyin_agent.hotkeys import GlobalHotkeys
from pinyin_agent.ui import build_parser
from pinyin_agent import __version__

HTML='''<!doctype html><meta charset="utf-8"><title>拼音快捷输入 · 自有浏览器测试</title>
<style>body{font:22px sans-serif;margin:30px}textarea,[contenteditable]{font:26px sans-serif;width:90%;height:280px;border:1px solid #888}</style>
<p>自有浏览器测试，保持本输入框焦点。</p><form><textarea aria-label="拼音快捷测试框" autofocus></textarea><button>测试按钮</button></form>
<script>let sent=0,enters=0,keylog=[];let box=document.querySelector('textarea,[contenteditable]');
function position(){if(!box.isContentEditable)return[box.selectionStart,box.selectionEnd];const s=window.getSelection();if(!s.rangeCount)return[0,0];const r=s.getRangeAt(0),a=r.cloneRange(),b=r.cloneRange();a.selectNodeContents(box);b.selectNodeContents(box);a.setEnd(r.startContainer,r.startOffset);b.setEnd(r.endContainer,r.endOffset);return[a.toString().length,b.toString().length]}
function report(){fetch('/snapshot',{method:'POST',body:JSON.stringify({text:box.isContentEditable?box.textContent:box.value,focused:document.activeElement===box,selection:position(),sent,enters,keylog})})}
document.querySelector('form').onsubmit=e=>{e.preventDefault();sent++;report()};
box.onkeydown=e=>{if(e.key==='Enter'){enters++} keylog.push({key:e.key,ctrl:e.ctrlKey,alt:e.altKey,shift:e.shiftKey,time:performance.now()});keylog=keylog.slice(-300)};box.oninput=report;
window.onload=()=>{const seed=__SEED__;box.focus();if(box.isContentEditable){box.textContent=seed.text;const n=box.firstChild||box.appendChild(document.createTextNode(''));const r=document.createRange();r.setStart(n,seed.position);r.setEnd(n,seed.end??seed.position);const s=window.getSelection();s.removeAllRanges();s.addRange(r)}else{box.value=seed.text;box.setSelectionRange(seed.position,seed.end??seed.position)}report();setInterval(report,200)};
</script>'''


def main():
    insert='--insert' in sys.argv
    use_edge='--edge' in sys.argv
    initial='前文😀|后文' if insert else ''
    prefix='前文😀|' if insert else ''
    seed={'text':initial,'position':len(prefix.encode('utf-16-le'))//2}
    if '--selected' in sys.argv and insert:
        seed['end']=seed['position']
        seed['position']=2
    html=HTML.replace('__SEED__',json.dumps(seed,ensure_ascii=False))
    if '--contenteditable' in sys.argv:
        html=html.replace('<textarea aria-label="拼音快捷测试框" autofocus></textarea>',
                          '<div contenteditable="true" role="textbox" aria-label="拼音快捷测试框" aria-multiline="false"></div>')
    snapshot={}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            data=html.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_POST(self):
            value=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
            snapshot.clear();snapshot.update(value)
            self.send_response(204);self.end_headers()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    root=tk.Tk();root.geometry('380x100');root.update();root.lift();root.focus_force()
    source='发力\n法力' if '--multiline' in sys.argv else ('法力 A😀' if insert else '发力、法力 A😀')
    if '--single-character' in sys.argv:source='已绘出'
    if '--forced-single' in sys.argv:source='师注式'
    if '--reported-sentence' in sys.argv:
        source='三次试验中，最长的这段用时约是最短的多少倍？按图估算，保留1位小数，只输出数值。'
    if '--mixed-regression' in sys.argv:
        source='工程师复核射频电路的阻抗匹配方案，表下注明按式计算，最长用时已绘出。'
    if '--table-regression' in sys.argv:
        source='第3页表1的标题已标明自放电率是计算值，表下注明按式（5）计算，读表即可确定。'
    expected=prefix+source+initial[len(prefix):]
    win.set_clipboard_text(source,win.u.GetAncestor(root.winfo_id(),2))
    events=[]
    session=QuickSession(build_parser().parse_args(['--hybrid'] if '--hybrid' in sys.argv else ['--offline']),win.u.GetAncestor(root.winfo_id(),2),
                         lambda kind,data:events.append((kind,data)))
    process=None
    def hotkey(action,error=None):
        if action=='activate':
            try:
                if process is None or capture_target().pid!=process.pid:return
            except RuntimeError:return
            session.activate()
        elif action=='stop':session.stop()
        elif action=='error':events.append(('error',str(error)))
    hotkeys=GlobalHotkeys(hotkey);hotkeys.start()
    if not hotkeys.ready.wait(3) or hotkeys.error:raise RuntimeError(str(hotkeys.error))
    folder=ROOT/'runs'/('browser-fixture-'+str(int(time.time())))
    chrome=Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' if use_edge else 'C:/Program Files/Google/Chrome/Application/chrome.exe')
    startup=subprocess.STARTUPINFO();startup.dwFlags=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=1
    process=subprocess.Popen([str(chrome),'--user-data-dir='+str(folder),'--no-first-run',
        '--no-default-browser-check','--disable-background-mode','--force-renderer-accessibility',
        '--app=http://127.0.0.1:%d/' % server.server_port],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,startupinfo=startup)
    root.withdraw() # The controller must not keep focus over its browser fixture.
    began=time.monotonic();state={'triggered':False,'target':None};report={}
    attempts=[0]
    def focus_fixture():
        # Find only windows belonging to this test's dedicated Chrome process.
        callback_type=C.WINFUNCTYPE(win.W.BOOL,win.W.HWND,win.W.LPARAM)
        def visit(hwnd,_):
            pid=win.W.DWORD()
            win.u.GetWindowThreadProcessId(hwnd,C.byref(pid))
            if pid.value==process.pid and win.u.IsWindowVisible(hwnd):
                import uiautomation as auto
                with auto.UIAutomationInitializerInThread():
                    control=auto.ControlFromHandle(hwnd).EditControl(Name='拼音快捷测试框',searchDepth=15)
                    if control.Exists(.2,0):
                        win.u.SetForegroundWindow(hwnd)
                        control.SetFocus()
            return True
        callback=callback_type(visit)
        win.signature(win.u,'EnumWindows',win.W.BOOL,[callback_type,win.W.LPARAM])
        win.u.EnumWindows(callback,0)
    def pump():
        if not state['triggered'] and snapshot and time.monotonic()-began>attempts[0]+1 and attempts[0]<10:
            attempts[0]+=1
            focus_fixture()
        if not state['triggered'] and snapshot.get('focused'):
            try:target=capture_target()
            except RuntimeError:target=None
            if target and target.pid==process.pid:
                target.guard()
                state.update(triggered=True,target=target)
                win.send_key(0x56,(0x11,0x12))
        done=state['triggered'] and not session.busy and any(k in ('idle','error') for k,_ in events)
        if done or time.monotonic()-began>max(40,len(source)*4):
            complete=next((d for k,d in events if k=='complete'),None)
            error=next((d for k,d in events if k=='error'),None)
            task_result=next((d for k,d in events if k=='agent' and d['kind']=='complete'),{})
            caret=len((prefix+source).encode('utf-16-le'))//2
            success=(bool(complete) and snapshot.get('text')==expected and snapshot.get('sent')==0 and snapshot.get('enters')==0
                     and snapshot.get('selection')==[caret,caret] and task_result.get('actual')==source
                     and win.clipboard_text()==source)
            if '--multiline' in sys.argv:success=bool(error and '浏览器跨行' in error) and snapshot.get('text')==initial
            report.update(success=success,browser='edge' if use_edge else 'chrome',initial=initial,expected=expected,source=source,snapshot=dict(snapshot),error=error,
                          events=[{'kind':k,'data':d} for k,d in events],log=str(session.agent.path) if session.agent else None)
            filename='external-browser-'+__version__+('-edge' if use_edge else '-chrome')+('-insert' if insert else '')+('-multiline' if '--multiline' in sys.argv else '')+'-result.json'
            if '--single-character' in sys.argv:filename=filename.replace('-result.json','-single-character-result.json')
            if '--forced-single' in sys.argv:filename=filename.replace('-result.json','-forced-single-result.json')
            if '--reported-sentence' in sys.argv:filename=filename.replace('-result.json','-reported-sentence-result.json')
            if '--mixed-regression' in sys.argv:filename=filename.replace('-result.json','-mixed-regression-result.json')
            if '--table-regression' in sys.argv:filename=filename.replace('-result.json','-table-regression-result.json')
            if '--contenteditable' in sys.argv:filename=filename.replace('-result.json','-contenteditable-result.json')
            (ROOT/'runs'/filename).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            session.stop();hotkeys.close()
            if state['target']:
                win.signature(win.u,'PostMessageW',win.W.BOOL,[win.W.HWND,win.W.UINT,win.W.WPARAM,win.W.LPARAM])
                win.u.PostMessageW(state['target'].root,0x10,0,0)
            else:process.terminate()
            root.destroy()
        else:root.after(100,pump)
    root.after(100,pump)
    root.mainloop()
    server.shutdown()
    print(json.dumps({k:v for k,v in report.items() if k!='events'},ensure_ascii=True))
    return 0 if report.get('success') else 1


if __name__=='__main__':raise SystemExit(main())
