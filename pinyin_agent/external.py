"""Read and guard the focused external input; writing still uses real keystrokes."""
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
import os
import re
import time
from . import windows as win
from .domain import MAX_ATTEMPTS

u,k=win.u,win.k
win.signature(u,"SendMessageTimeoutW",win.LRESULT,[W.HWND,W.UINT,W.WPARAM,W.LPARAM,W.UINT,W.UINT,C.POINTER(C.c_size_t)])
win.signature(u,"GetWindowLongPtrW",C.c_ssize_t,[W.HWND,C.c_int])
win.signature(u,"GetKeyboardLayout",W.HANDLE,[W.DWORD])
win.signature(u,"GetKeyboardLayoutList",C.c_int,[C.c_int,C.POINTER(W.HANDLE)])
win.signature(u,"PostMessageW",W.BOOL,[W.HWND,W.UINT,W.WPARAM,W.LPARAM])


class TargetError(RuntimeError):
    pass


class EnglishInputDetected(TargetError):
    pass


def wait_modifiers(guard):
    deadline=time.monotonic()+5
    while any(u.GetAsyncKeyState(key)&0x8000 for key in (0x10,0x11,0x12,0x5B,0x5C)):
        guard()
        if time.monotonic()>deadline:raise TargetError('请松开快捷键后再继续。')
        time.sleep(.02)


def normalized(text):
    return text.replace("\r\n","\n").replace("\r","\n")


def message(hwnd,code,wparam=0,lparam=0):
    result=C.c_size_t()
    if not u.SendMessageTimeoutW(hwnd,code,wparam,lparam,0x0002,700,C.byref(result)):
        raise TargetError("目标输入框没有响应或权限不同，已停止。")
    return result.value


@dataclass(frozen=True)
class Target:
    root: int
    hwnd: int
    thread: int
    pid: int

    def guard(self):
        if not u.IsWindow(self.hwnd) or u.GetForegroundWindow()!=self.root:
            raise TargetError("目标窗口失去焦点，已停止。")
        info=win.GUITHREADINFO(cbSize=C.sizeof(win.GUITHREADINFO))
        if not u.GetGUIThreadInfo(self.thread,C.byref(info)) or info.hwndFocus!=self.hwnd:
            raise TargetError("输入焦点已经改变，已停止。")


def capture_target():
    root=u.GetForegroundWindow()
    pid=W.DWORD()
    thread=u.GetWindowThreadProcessId(root,C.byref(pid))
    info=win.GUITHREADINFO(cbSize=C.sizeof(win.GUITHREADINFO))
    if not root or not u.GetGUIThreadInfo(thread,C.byref(info)) or not info.hwndFocus:
        raise TargetError("请先点击要输入的文本框。")
    if pid.value==os.getpid():
        raise TargetError("请点击其他应用的输入位置，再按快捷键。")
    return Target(int(root),int(info.hwndFocus),int(thread),int(pid.value))


class Win32Input:
    def __init__(self,target,window_class):
        self.target=target
        self.hwnd,self.root,self.thread=target.hwnd,target.root,target.thread
        self.window_class=window_class
        self.rich=window_class.lower().startswith("richedit")
        style=u.GetWindowLongPtrW(self.hwnd,-16)
        if style & 0x20:raise TargetError("不支持密码输入框。")
        if style & 0x800:raise TargetError("该输入框为只读。")
        self.multiline=bool(style & 4)
        self.browser=False
        self.tsf=None

    def guard(self):self.target.guard()
    def focus(self):self.guard() # Never steal focus back from another application.

    def read(self):
        length=message(self.hwnd,0x0E)
        if length>200000:raise TargetError("目标文本范围过大，已停止。")
        buffer=C.create_unicode_buffer(length+2)
        message(self.hwnd,0x0D,len(buffer),C.addressof(buffer))
        return normalized(buffer.value)

    def selection(self):
        start,end=W.DWORD(),W.DWORD()
        message(self.hwnd,0xB0,C.addressof(start),C.addressof(end))
        text=self.read()
        raw=text if self.rich else text.replace("\n","\r\n")
        def index(offset):return len(normalized(raw.encode("utf-16-le")[:offset*2].decode("utf-16-le")))
        return index(start.value),index(end.value)

    def select(self,start,end,expected):
        self.guard()
        if self.read()!=expected:raise TargetError("目标内容已经变化，已取消修正。")
        def offset(index):
            prefix=expected[:index]
            if not self.rich:prefix=prefix.replace("\n","\r\n")
            return len(prefix.encode("utf-16-le"))//2
        message(self.hwnd,0xB1,offset(start),offset(end))


class UIAInput:
    def __init__(self,target,control,auto):
        self.target,self.control,self.auto=target,control,auto
        self.hwnd,self.root,self.thread=target.hwnd,target.root,target.thread
        if control.IsPassword:raise TargetError("不支持密码输入框。")
        if not control.IsEnabled:raise TargetError("该输入框不可编辑。")
        if control.ControlTypeName!="EditControl":
            raise TargetError("当前控件不是可核验的编辑框，请使用普通文本框。")
        self.value=control.GetPattern(auto.PatternId.ValuePattern)
        self.text=control.GetPattern(auto.PatternId.TextPattern)
        if self.value and self.value.IsReadOnly:raise TargetError("该输入框为只读。")
        if not self.value and not self.text:raise TargetError("无法可靠读取这个输入框，未开始输入。")
        self.identity=tuple(control.GetRuntimeId())
        aria=control.AriaProperties or ""
        self.multiline=bool(re.search(r"(?:^|;)\s*multiline\s*=\s*(?:true|1)(?:;|$)",aria,re.I))
        self.browser=(control.FrameworkId or "").lower() in ("chrome","edge")
        self.tsf=None

    def guard(self):
        self.target.guard()
        focused=self.auto.GetFocusedControl()
        if not focused or tuple(focused.GetRuntimeId())!=self.identity:
            raise TargetError("焦点已离开原来的输入框，已停止。")

    def focus(self):self.guard()

    def read(self):
        value=self.value.Value if self.value else self.text.DocumentRange.GetText(200001)
        if len(value)>200000:raise TargetError("目标文本范围过大，已停止。")
        return normalized(value)

    def selection(self):
        current=self.read()
        if self.text:
            ranges=self.text.GetSelection()
            if len(ranges)==1:
                values=[]
                for endpoint in (0,1):
                    prefix=self.text.DocumentRange.Clone()
                    prefix.MoveEndpointByRange(1,ranges[0],endpoint,waitTime=0)
                    value=normalized(prefix.GetText(200001))
                    if not current.startswith(value):
                        raise TargetError("无法确认光标范围，已停止。")
                    values.append(len(value))
                return tuple(values)
        if not current:return 0,0
        raise TargetError("此输入框无法提供准确光标位置，未继续输入。")

    def select(self,start,end,expected):
        self.guard()
        if self.read()!=expected:raise TargetError("目标内容已经变化，已取消修正。")
        if not 0<=start<=end<=len(expected):
            raise TargetError("修正范围无效，未执行删除。")
        current=self.selection()
        if start==end and current[0]!=current[1] and end==current[1]:
            wait_modifiers(self.guard)
            win.send_key(0x27) # Collapse the existing selection at its end.
            self.wait_selection(start,end,expected)
            return
        if not self.text:
            if start==0 and end==len(expected):
                win.send_key(0x41,(0x11,))
                return
            raise TargetError("此输入框不能精确定位修正范围，已停止。")
        document=self.text.DocumentRange
        # UIA Character units need not match Python code points (emoji, joined
        # glyphs and embedded browser text). Resolve boundaries by exact text.
        def boundary(index):
            point=document.Clone()
            if index==0:
                point.MoveEndpointByRange(1,document,0,waitTime=0)
                return point
            found=document.FindText(expected[:index],False,False)
            if found is None and '\n' in expected[:index]:
                found=document.FindText(expected[:index].replace('\n','\r\n'),False,False)
            if found is None or normalized(found.GetText(200001))!=expected[:index]:
                raise TargetError("无法精确定位本次文字，未执行删除。")
            preceding=document.Clone()
            preceding.MoveEndpointByRange(1,found,0,waitTime=0)
            if normalized(preceding.GetText(200001)):
                raise TargetError("文字定位与输入框起点不一致，未执行删除。")
            point.MoveEndpointByRange(0,found,1,waitTime=0)
            point.MoveEndpointByRange(1,found,1,waitTime=0)
            return point
        left,right=boundary(start),boundary(end)
        selected=document.Clone()
        selected.MoveEndpointByRange(0,left,0,waitTime=0)
        selected.MoveEndpointByRange(1,right,0,waitTime=0)
        prefix=document.Clone()
        prefix.MoveEndpointByRange(1,selected,0,waitTime=0)
        if (normalized(prefix.GetText(200001))!=expected[:start]
                or normalized(selected.GetText(200001))!=expected[start:end]):
            raise TargetError("输入框的字符范围不一致，未执行删除。")
        if self.read()!=expected:raise TargetError("目标内容已经变化，已取消修正。")
        accepted=selected.Select(waitTime=0)
        if accepted is False:
            self.keyboard_select(start,end,expected)
            return
        # Browser providers acknowledge selection asynchronously. Do not send
        # Backspace against the previous selection while that update is queued.
        deadline=time.monotonic()+1.2
        while self.selection()!=(start,end):
            self.guard()
            if self.read()!=expected:raise TargetError("定位期间目标内容变化，未执行删除。")
            if time.monotonic()>deadline:
                self.keyboard_select(start,end,expected)
                return
            time.sleep(.02)

    def wait_selection(self,start,end,expected):
        deadline=time.monotonic()+1.2
        while self.selection()!=(start,end):
            self.guard()
            if self.read()!=expected:raise TargetError("定位期间目标内容变化，未执行删除。")
            if time.monotonic()>deadline:raise TargetError("输入框未确认修正位置，未执行删除。")
            time.sleep(.02)

    def keyboard_select(self,start,end,expected):
        wait_modifiers(self.guard)
        left,right=self.selection()
        segment=expected[min(left,start):max(right,end)]
        if len(segment)>256 or any(ord(c)>0xffff or __import__('unicodedata').combining(c) for c in segment):
            raise TargetError("输入框不支持精确定位此范围，未执行删除。")
        if left!=right:win.send_key(0x27)
        for _ in range(abs(end-right)):
            self.guard()
            win.send_key(0x25 if end<right else 0x27)
        for _ in range(end-start):
            self.guard()
            win.send_key(0x25,(0x10,))
        self.wait_selection(start,end,expected)


def bind_target(target,auto):
    target.guard()
    name=C.create_unicode_buffer(256)
    u.GetClassNameW(target.hwnd,name,256)
    cls=name.value.lower()
    if cls=="edit" or cls.startswith("richedit"):
        return Win32Input(target,name.value)
    focused=auto.GetFocusedControl()
    if not focused:raise TargetError("没有找到当前输入框。")
    return UIAInput(target,focused,auto)


class ExternalBackend(win.WindowsBackend):
    def __init__(self,editor,clipboard_owner,pace="natural"):
        super().__init__(editor,lambda fn:fn(),pace=pace)
        self.clipboard_owner=clipboard_owner
        self.direct_supported=False

    def begin(self):self.guard()
    def end(self):pass # UIA lifetime is owned by the session's worker thread.
    def guard(self):
        self.editor.guard()
        # Hotkeys must be released before the next character, including resume.
        deadline=time.monotonic()+5
        while any(u.GetAsyncKeyState(key)&0x8000 for key in (0x10,0x11,0x12,0x5B,0x5C)):
            if getattr(self,"stop_event",None) and self.stop_event.is_set():
                from .engine import Stopped
                raise Stopped("任务已停止，保留当前文字。")
            if time.monotonic()>deadline:raise TargetError("修饰键持续按下，已停止。请松开快捷键后重试。")
            self.editor.guard()
            time.sleep(.02)
    def chinese_mode(self):pass # Recover only after a verified English commit; do not toggle blindly.
    def composition(self):return ""
    def selection(self):return self.editor.selection()
    def select(self,start,end,expected):self.editor.select(start,end,expected)

    def retry_chunk(self,chunk):
        from .planner import character_hint
        return character_hint(chunk) or chunk

    def recover_chinese_mode(self,shift_used):
        self.checkpoint()
        layout=u.GetKeyboardLayout(self.editor.thread) or 0
        if layout&0xffff!=0x0804:
            count=u.GetKeyboardLayoutList(0,None)
            layouts=(W.HANDLE*max(count,1))()
            if count:u.GetKeyboardLayoutList(count,layouts)
            chinese=[item for item in layouts if item and item&0xffff==0x0804]
            if not chinese:raise TargetError("没有找到已启用的中文输入法，请先选择微软拼音。")
            preferred=next((item for item in chinese if item&0xffff0000==0x08040000),chinese[0])
            if not u.PostMessageW(self.editor.hwnd,0x0050,0,C.c_ssize_t(preferred).value):
                raise TargetError("无法切换目标输入框的输入法，请先选择微软拼音。")
            deadline=time.monotonic()+1
            while (u.GetKeyboardLayout(self.editor.thread) or 0)&0xffff!=0x0804:
                self.checkpoint()
                if time.monotonic()>deadline:raise TargetError("目标窗口没有接受中文输入法切换，请手动选择微软拼音。")
                time.sleep(.03)
            return "layout"
        if shift_used:
            raise TargetError("自动切换中文后仍未出现候选，已停止。请确认微软拼音的中英文切换设置。")
        win.send_key(0x10) # Microsoft Pinyin's default Chinese/English mode toggle.
        time.sleep(.2)
        self.checkpoint()
        return "shift"

    def choose(self,chunk,checkpoint,emit):
        with self.attempt_scope() as budget:
            shift_used=False
            for _ in range(MAX_ATTEMPTS):
                try:return super().choose(chunk,checkpoint,emit)
                except EnglishInputDetected:
                    budget.require()
                    action=self.recover_chinese_mode(shift_used)
                    shift_used=shift_used or action=="shift"
                    emit("ime_recovery",action=action,message="检测到英文输入，已自动切换；剩余 %d 次尝试。" % budget.remaining)
                    self.rhythm.wait("retry",checkpoint)
            raise TargetError("本片段最多尝试 %d 次，已停止。" % MAX_ATTEMPTS)

    def read_commit(self,before,checkpoint,require_change=True):
        deadline=time.monotonic()+1.2
        last=self.read()
        stable=time.monotonic()
        while time.monotonic()<deadline:
            checkpoint()
            value=self.read()
            if value!=last:last,stable=value,time.monotonic()
            if (not require_change or value!=before) and time.monotonic()-stable>=.09:return value
            time.sleep(.02)
        return self.read()

    def probe_choices(self,chunk,before,start,end,checkpoint,emit):
        from .planner import selection_hint
        wanted=before[:start]+chunk.text+before[end:]
        hint=selection_hint(chunk)
        cached=self.choice_cache.get((chunk.keys,chunk.text))
        first=True
        tried_digits=set()
        hint_used=False
        while self._attempt_budget.remaining:
            checkpoint()
            trial=hint if hint and self._attempt_budget.used and not hint_used else chunk
            initial_digit=cached[1] if cached and cached[0]==0 else 1
            if trial!=chunk:
                digit=1
                hint_used=True
            else:
                digit=next(d for d in dict.fromkeys((initial_digit,1,2,3)) if d not in tried_digits)
                tried_digits.add(digit)
            if first and trial!=chunk:
                # The base adapter already typed the target. Cancel only its
                # uncommitted composition before the word-assisted final try.
                win.send_key(0x1B)
            if not first or trial!=chunk:
                self.rhythm.wait('retry',checkpoint)
                self.type_pinyin(trial,checkpoint)
            first=False
            self._attempt_budget.consume()
            self.rhythm.wait('selection',checkpoint)
            win.send_key(ord(str(digit)))
            actual=self.read_commit(before,checkpoint)
            self.validate_commit(trial,before,actual,start,end,digit)
            prefix,suffix=before[:start],before[end:]
            fragment=actual[start:len(actual)-len(suffix) if suffix else len(actual)]
            kept=trial.text.index(chunk.text) if trial!=chunk else 0
            assisted=(trial!=chunk and actual.startswith(prefix) and actual.endswith(suffix)
                      and len(fragment)==len(trial.text) and win.HAN.fullmatch(fragment)
                      and fragment[kept:kept+len(chunk.text)]==chunk.text)
            if assisted:
                if kept+len(chunk.text)<len(trial.text):
                    self.delete_range(start+kept+len(chunk.text),start+len(trial.text),actual)
                    actual=self.read()
                if kept:
                    self.delete_range(start,start+kept,actual)
                    actual=self.read()
                    position=start+len(chunk.text)
                    self.select(position,position,actual)
                actual=self.read()
            exact=actual==wanted
            emit('candidate_probe',target=chunk.text,input_text=trial.text,page=1,key=digit,exact=exact,
                 actual_fragment=actual[start:len(actual)-len(before[end:]) if before[end:] else len(actual)])
            if exact:
                if trial==chunk:self.choice_cache[(chunk.keys,chunk.text)]=(0,digit)
                emit('selected',target=chunk.text,source='词带字 · 精确校验' if trial!=chunk else '上屏精确校验 · 无 OCR')
                return True
            win.send_key(0x1B)
            actual=self.read_commit(actual,checkpoint,require_change=False)
            if not actual.startswith(prefix) or not actual.endswith(suffix) or len(actual)<len(prefix)+len(suffix):
                raise TargetError('候选确认后原有文本发生变化，已停止。')
            inserted_end=len(actual)-len(suffix)
            if inserted_end>start:
                method=self.delete_range(start,inserted_end,actual)
                emit('candidate_rollback',target=chunk.text,method=method)
            if self.read()!=before:raise TargetError('候选尝试后未能恢复本次文字，已停止。')
        return False

    def backspace(self):
        self.rhythm.wait("correction",self.checkpoint)
        start,end=self.selection()
        if start==end==0:
            raise TargetError("已到本次输入范围起点，未删除前面的原有文字。")
        before=self.read()
        win.send_key(8)
        deadline=time.monotonic()+1.2
        while self.read()==before:
            self.checkpoint()
            if time.monotonic()>deadline:raise TargetError('输入框没有确认退格结果，已停止。')
            time.sleep(.02)

    def validate_commit(self,chunk,before,actual,start,end,digit):
        raw=before[:start]+chunk.keys+str(digit)+before[end:]
        if actual==raw:
            self.delete_range(start,start+len(chunk.keys)+1,actual)
            if self.read()!=before:
                raise TargetError("英文试输未能完整回退，已停止。")
            raise EnglishInputDetected("未进入中文候选，已撤回本次误输入的字母。")

    def literal(self,text,checkpoint):
        if self.editor.browser and "\n" in text:
            raise TargetError("浏览器跨行录入尚未适配，已停止。")
        self.guard()
        self.selection() # Confirm the actual caret is still inside this task.
        wanted=None
        if self.editor.browser:
            before=self.read()
            start,end=self.selection()
            wanted=before[:start]+text+before[end:]
        self.rhythm.begin_chunk()
        for index,char in enumerate(text):
            self.rhythm.wait("key",checkpoint)
            win.send_literal(char)
            self.rhythm.after_literal(text,index,checkpoint)
        if wanted is not None:
            # Browser accessibility values update after queued character events.
            # Await acknowledgement before the engine can decide to retype a diff.
            deadline=time.monotonic()+1.5
            while self.read()!=wanted:
                checkpoint()
                if time.monotonic()>deadline:
                    raise TargetError("浏览器没有完整接收字符，已停止，请检查当前文字。")
                time.sleep(.03)

    def copy_all(self):
        self.rhythm.wait("review",self.checkpoint)
        actual=self.read()
        win.set_clipboard_text(actual,self.clipboard_owner)
        value=win.clipboard_text()
        self.checkpoint()
        if self.read()!=actual:raise TargetError("核验时目标内容发生变化，已停止。")
        return value


class NativeBackend(ExternalBackend):
    """Same verified typing path with explicit UI-thread setup for our test UI."""
    def __init__(self,editor,ui_call,pace='natural'):
        target=Target(editor.root,editor.hwnd,editor.thread,os.getpid())
        super().__init__(Win32Input(target,'RichEdit50W'),editor.root,pace)
        self.native_editor=editor
        self.ui_call=ui_call

    def begin(self):
        import uiautomation as auto
        self.automation_context=auto.UIAutomationInitializerInThread()
        self.automation_context.__enter__()
        self.ui_call(self.native_editor.focus)
        time.sleep(.2)
        self.guard()

    def end(self):
        win.WindowsBackend.end(self)
