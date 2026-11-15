"""Windows-only editor and keyboard adapter. No clipboard-to-editor fallback."""
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
from contextlib import contextmanager
import os
import re
import time
import json
import subprocess
import threading
import queue
from pathlib import Path
from .domain import HAN, utf16_offset, AttemptBudget
from .rhythm import TypingRhythm

u = C.WinDLL("user32", use_last_error=True)
imm = C.WinDLL("imm32", use_last_error=True)
k = C.WinDLL("kernel32", use_last_error=True)
PTR = C.c_size_t
LRESULT = C.c_ssize_t


class KEYBDINPUT(C.Structure):
    _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", PTR)]


class MOUSEINPUT(C.Structure):
    _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD),
                ("dwFlags", W.DWORD), ("time", W.DWORD), ("dwExtraInfo", PTR)]


class INPUTUNION(C.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(C.Structure):
    _anonymous_ = ("payload",)
    _fields_ = [("type", W.DWORD), ("payload", INPUTUNION)]


class GUITHREADINFO(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("flags", W.DWORD), ("hwndActive", W.HWND),
                ("hwndFocus", W.HWND), ("hwndCapture", W.HWND), ("hwndMenuOwner", W.HWND),
                ("hwndMoveSize", W.HWND), ("hwndCaret", W.HWND), ("rcCaret", W.RECT)]


def signature(dll, name, restype, args):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = restype, args
    return fn


signature(u, "CreateWindowExW", W.HWND, [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
    C.c_int, C.c_int, C.c_int, C.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID])
signature(u, "SendMessageW", LRESULT, [W.HWND, W.UINT, W.WPARAM, W.LPARAM])
signature(u, "GetAncestor", W.HWND, [W.HWND, W.UINT])
signature(u, "GetForegroundWindow", W.HWND, [])
signature(u, "SetForegroundWindow", W.BOOL, [W.HWND])
signature(u, "SetFocus", W.HWND, [W.HWND])
signature(u, "AttachThreadInput", W.BOOL, [W.DWORD,W.DWORD,W.BOOL])
signature(u, "BringWindowToTop", W.BOOL, [W.HWND])
signature(u, "IsWindow", W.BOOL, [W.HWND])
signature(u, "IsWindowVisible", W.BOOL, [W.HWND])
signature(u, "GetWindowTextLengthW", C.c_int, [W.HWND])
signature(u, "GetWindowTextW", C.c_int, [W.HWND, W.LPWSTR, C.c_int])
signature(u, "GetClassNameW", C.c_int, [W.HWND, W.LPWSTR, C.c_int])
signature(u, "MoveWindow", W.BOOL, [W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.BOOL])
signature(u, "SendInput", W.UINT, [W.UINT, C.POINTER(INPUT), C.c_int])
signature(u, "GetWindowThreadProcessId", W.DWORD, [W.HWND, C.POINTER(W.DWORD)])
signature(u, "GetGUIThreadInfo", W.BOOL, [W.DWORD, C.POINTER(GUITHREADINFO)])
signature(u, "WindowFromPoint", W.HWND, [W.POINT])
signature(u, "GetWindowRect", W.BOOL, [W.HWND, C.POINTER(W.RECT)])
signature(u, "ClientToScreen", W.BOOL, [W.HWND, C.POINTER(W.POINT)])
signature(u, "GetClipboardData", W.HANDLE, [W.UINT])
signature(u, "OpenClipboard", W.BOOL, [W.HWND])
signature(u, "EmptyClipboard", W.BOOL, [])
signature(u, "SetClipboardData", W.HANDLE, [W.UINT,W.HANDLE])
signature(k, "GlobalAlloc", W.HANDLE, [W.UINT,C.c_size_t])
signature(k, "GlobalFree", W.HANDLE, [W.HANDLE])
signature(k, "GlobalLock", W.LPVOID, [W.HANDLE])
signature(k, "GlobalUnlock", W.BOOL, [W.HANDLE])
signature(imm, "ImmGetContext", W.HANDLE, [W.HWND])
signature(imm, "ImmReleaseContext", W.BOOL, [W.HWND, W.HANDLE])
signature(imm, "ImmSetOpenStatus", W.BOOL, [W.HANDLE, W.BOOL])
signature(imm, "ImmGetOpenStatus", W.BOOL, [W.HANDLE])
signature(imm, "ImmGetConversionStatus", W.BOOL, [W.HANDLE, C.POINTER(W.DWORD), C.POINTER(W.DWORD)])
signature(imm, "ImmSetConversionStatus", W.BOOL, [W.HANDLE, W.DWORD, W.DWORD])
signature(imm, "ImmGetCompositionStringW", W.LONG, [W.HANDLE, W.DWORD, W.LPVOID, W.DWORD])
signature(imm, "ImmGetCandidateListW", W.DWORD, [W.HANDLE, W.DWORD, W.LPVOID, W.DWORD])


def send_key(vk, modifiers=()):
    events = []
    for key in modifiers:
        events.append(INPUT(type=1, ki=KEYBDINPUT(wVk=key)))
    events.extend([INPUT(type=1, ki=KEYBDINPUT(wVk=vk)),
                   INPUT(type=1, ki=KEYBDINPUT(wVk=vk, dwFlags=2))])
    for key in reversed(modifiers):
        events.append(INPUT(type=1, ki=KEYBDINPUT(wVk=key, dwFlags=2)))
    array = (INPUT * len(events))(*events)
    if u.SendInput(len(array), array, C.sizeof(INPUT)) != len(array):
        raise RuntimeError("Windows 拒绝了键盘事件。")


def send_literal(char):
    if HAN.search(char):
        raise RuntimeError("禁止通过字符注入输入汉字。")
    if char == "\n":
        send_key(0x0D)
        return
    for offset in range(0, len(char.encode("utf-16-le")), 2):
        unit = int.from_bytes(char.encode("utf-16-le")[offset:offset+2], "little")
        events = (INPUT * 2)(INPUT(type=1, ki=KEYBDINPUT(wScan=unit, dwFlags=4)),
                              INPUT(type=1, ki=KEYBDINPUT(wScan=unit, dwFlags=6)))
        if u.SendInput(2, events, C.sizeof(INPUT)) != 2:
            raise RuntimeError("Windows 拒绝了符号输入。")


def clipboard_text():
    for attempt in range(15):
        if u.OpenClipboard(None):
            try:
                handle = u.GetClipboardData(13)
                if not handle:
                    raise RuntimeError("剪贴板没有 Unicode 文本。")
                pointer = k.GlobalLock(handle)
                if not pointer:
                    raise RuntimeError("无法读取剪贴板。")
                try:
                    return C.wstring_at(pointer).replace("\r\n", "\n")
                finally:
                    k.GlobalUnlock(handle)
            finally:
                u.CloseClipboard()
        time.sleep(.03)
    raise RuntimeError("剪贴板正被其他程序占用。")


def set_clipboard_text(text,owner):
    """Copy actual editor text without selecting it or moving its caret."""
    payload=(text.replace("\r\n","\n").replace("\n","\r\n")).encode("utf-16-le")+b"\x00\x00"
    for _ in range(15):
        if u.OpenClipboard(owner):
            handle=None
            transferred=False
            try:
                handle=k.GlobalAlloc(0x0002,len(payload)) # GMEM_MOVEABLE
                if not handle:raise RuntimeError("无法为剪贴板分配文本内存。")
                pointer=k.GlobalLock(handle)
                if not pointer:raise RuntimeError("无法写入剪贴板内存。")
                try:C.memmove(pointer,payload,len(payload))
                finally:k.GlobalUnlock(handle)
                if not u.EmptyClipboard():raise RuntimeError("无法更新剪贴板。")
                if not u.SetClipboardData(13,handle):raise RuntimeError("无法复制编辑器全文。")
                transferred=True # Windows now owns the HGLOBAL.
                return
            finally:
                if handle and not transferred:k.GlobalFree(handle)
                u.CloseClipboard()
        time.sleep(.03)
    raise RuntimeError("剪贴板正被其他程序占用。")


class NativeEditor:
    def __init__(self, frame, use_tsf=False):
        self.tsf_error = ""
        self.tsf=None
        # Leave IME/caret ownership to the native control. Forcing our own TSF
        # manager and SES_USECTF produces alternating host/composition carets
        # with this Microsoft Pinyin + RichEdit combination. Explicit opt-in is
        # retained only for the rendering comparison test.
        if use_tsf:
            try:
                from .tsf import Observer
                self.tsf = Observer()
            except Exception as error:
                self.tsf_error = str(error)
        self.rich_library=C.WinDLL("msftedit")
        frame.update_idletasks()
        self.hwnd = u.CreateWindowExW(0x200, "RICHEDIT50W", "", 0x50000000 | 0x00200000 | 0x1004 | 0x40,
                                     0, 0, frame.winfo_width(), frame.winfo_height(), frame.winfo_id(), None, None, None)
        if not self.hwnd:
            raise C.WinError(C.get_last_error())
        self.root = u.GetAncestor(self.hwnd, 2)
        self.thread = u.GetWindowThreadProcessId(self.hwnd, None)
        if use_tsf:u.SendMessageW(self.hwnd,0x400+204,0x10000,0x10000) # SES_USECTF
        u.SendMessageW(self.hwnd,0x400+89,1,0) # TM_PLAINTEXT
        gdi = C.WinDLL("gdi32")
        signature(gdi, "CreateFontW", W.HANDLE, [C.c_int]*5+[W.DWORD]*8+[W.LPCWSTR])
        self.font = gdi.CreateFontW(-21, 0, 0, 0, 400, 0, 0, 0, 134, 0, 0, 5, 0, "Microsoft YaHei UI")
        u.SendMessageW(self.hwnd, 0x30, self.font or 0, 1)
        u.SendMessageW(self.hwnd, 0xC5, 2_000_000, 0)
        frame.bind("<Configure>", lambda e: u.MoveWindow(self.hwnd, 0, 0, max(1,e.width), max(1,e.height), True))

    def read(self):
        buffer = C.create_unicode_buffer(u.GetWindowTextLengthW(self.hwnd) + 1)
        u.GetWindowTextW(self.hwnd, buffer, len(buffer))
        return buffer.value.replace("\r\n", "\n").replace("\r","\n")

    def focus(self):
        self.root=u.GetAncestor(self.hwnd,2)
        focused=u.SetForegroundWindow(self.root)
        attached=False
        foreground_thread=u.GetWindowThreadProcessId(u.GetForegroundWindow(),None)
        try:
            if not focused and foreground_thread and foreground_thread!=self.thread:
                attached=bool(u.AttachThreadInput(self.thread,foreground_thread,True))
                u.BringWindowToTop(self.root)
                focused=u.SetForegroundWindow(self.root)
            u.SetFocus(self.hwnd)
        finally:
            if attached:u.AttachThreadInput(self.thread,foreground_thread,False)
        self.focus_diagnostics={"requested":self.root,"foreground":u.GetForegroundWindow(),"set_foreground":bool(focused),
                                "thread":self.thread,"child":self.hwnd}

    def clear(self):
        # Explicit user-invoked empty document action; never used by Agent.
        buffer=C.create_unicode_buffer("")
        u.SendMessageW(self.hwnd, 0x0C, 0, C.addressof(buffer))


@dataclass
class Candidate:
    text: str
    key: str = ""
    rect: tuple = ()
    source: str = ""


class WindowsBackend:
    def __init__(self, editor, ui_call, page_limit=8, pace="natural"):
        self.editor, self.ui_call = editor, ui_call
        self.page_limit = page_limit
        self.rhythm = TypingRhythm(pace)
        self.checkpoint = self.guard
        self.automation_context = None
        self.auto = None
        self.last_snapshot = []
        self.uia_errors = []
        self.candidate_tree = []
        self.ocr_process = None
        self.ocr_queue = queue.Queue()
        self.ocr_error = ""
        self.ocr_debug = {}
        self.choice_cache = {}
        self.direct_supported = None
        self.fallback_announced = False

    def set_checkpoint(self, checkpoint):
        self.checkpoint = checkpoint

    def type_pinyin(self, chunk, checkpoint):
        self.rhythm.begin_chunk()
        for char in chunk.keys:
            self.rhythm.wait("key", checkpoint)
            # Virtual-key letters and apostrophe pass through the real IME.
            send_key(ord(char.upper()) if char != "'" else 0xDE)

    def after_chunk(self, chunk, checkpoint):
        if chunk.chinese:
            self.rhythm.after_word(checkpoint)

    def begin(self):
        import uiautomation as auto
        self.auto = auto
        self.automation_context = auto.UIAutomationInitializerInThread()
        self.automation_context.__enter__()
        self.ui_call(self.editor.focus)
        time.sleep(.2)
        self.guard()

    def end(self):
        if self.ocr_process:
            try:
                self.ocr_process.stdin.close()
                self.ocr_process.wait(timeout=2)
            except Exception:
                self.ocr_process.terminate()
            self.ocr_process = None
        if self.automation_context:
            self.automation_context.__exit__(None, None, None)
            self.automation_context = None

    def guard(self):
        if not u.IsWindow(self.editor.hwnd) or u.GetForegroundWindow() != self.editor.root:
            raise RuntimeError("目标编辑器失去前台焦点，已停止输入。")
        info = GUITHREADINFO(cbSize=C.sizeof(GUITHREADINFO))
        if not u.GetGUIThreadInfo(self.editor.thread, C.byref(info)) or info.hwndFocus != self.editor.hwnd:
            raise RuntimeError("输入焦点已离开测试编辑器，已停止输入。")

    def emergency_stop(self):
        return bool(u.GetAsyncKeyState(0x7B) & 0x8000)  # F12

    def read(self):
        return self.editor.read()

    def with_context(self, callback):
        context = imm.ImmGetContext(self.editor.hwnd)
        if not context:
            return None
        try:
            return callback(context)
        finally:
            imm.ImmReleaseContext(self.editor.hwnd, context)

    def chinese_mode(self):
        def enable(context):
            conversion, sentence = W.DWORD(), W.DWORD()
            if not imm.ImmGetOpenStatus(context):
                imm.ImmSetOpenStatus(context, True)
            if imm.ImmGetConversionStatus(context, C.byref(conversion), C.byref(sentence)):
                if not conversion.value & 1:
                    imm.ImmSetConversionStatus(context, conversion.value | 1, sentence.value)
        self.ui_call(lambda: self.with_context(enable))

    def composition(self):
        def read(context):
            size = imm.ImmGetCompositionStringW(context, 8, None, 0)
            if size <= 0:
                return ""
            buf = C.create_string_buffer(size + 2)
            imm.ImmGetCompositionStringW(context, 8, buf, size)
            return buf.raw[:size].decode("utf-16-le")
        return self.with_context(read) or ""

    def imm_candidates(self):
        def fetch(context):
            size = imm.ImmGetCandidateListW(context, 0, None, 0)
            if size < 24:
                return []
            buffer = C.create_string_buffer(size)
            imm.ImmGetCandidateListW(context, 0, buffer, size)
            words = C.cast(buffer, C.POINTER(W.DWORD))
            count, start, page_size = words[2], words[4], words[5]
            if count > 10000 or start >= count:
                return []
            result = []
            for number in range(start, min(count, start + page_size)):
                offset = words[6 + number]
                if offset < size:
                    value = C.wstring_at(C.addressof(buffer) + offset)
                    result.append(Candidate(value, str(number-start+1) if number-start < 9 else "", source="IMM"))
            return result
        return self.with_context(fetch) or []

    def uia_candidates(self):
        # Restrict discovery to visible system IME windows, never arbitrary app text.
        windows = []
        self.uia_errors = []
        self.candidate_tree = []
        callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        @callback_type
        def visit(hwnd, unused):
            if u.IsWindowVisible(hwnd):
                name = C.create_unicode_buffer(256)
                u.GetClassNameW(hwnd, name, 256)
                title = C.create_unicode_buffer(256)
                u.GetWindowTextW(hwnd,title,256)
                cls = name.value.lower()
                relevant = ("candidate" in cls or "ime" in cls or
                            ("corewindow" in cls and any(t in title.value.lower() for t in ("输入","input"))))
                if relevant:
                    if hwnd != self.editor.root:
                        windows.append(hwnd)
            return True
        u.EnumWindows(visit, 0)
        rows = []
        for hwnd in windows:
            try:
                root = self.auto.ControlFromHandle(hwnd)
                queue = [(root, 0)]
                count = 0
                while queue and count < 250:
                    control, depth = queue.pop(0)
                    count += 1
                    name = control.Name or ""
                    aid = control.AutomationId or ""
                    self.candidate_tree.append({"name":name,"id":aid,"depth":depth,"class":control.ClassName})
                    rect = control.BoundingRectangle
                    text = name.strip()
                    match = re.fullmatch(r"([1-9])\s*[.、．:]?\s*(.+)", text)
                    key = match.group(1) if match else ""
                    if match:
                        text = match.group(2).strip()
                    if HAN.fullmatch(text) and len(text) <= 40 and rect.width() > 0 and rect.height() > 0:
                        rows.append(Candidate(text, key, (rect.left, rect.top, rect.right, rect.bottom), "UIA:"+aid))
                    if depth < 7:
                        queue.extend((c, depth+1) for c in control.GetChildren())
            except Exception as error:
                self.uia_errors.append(str(error))
                continue
        # Keep smallest exact text bounds, avoid duplicate container/child labels.
        seen, result = set(), []
        for row in sorted(rows, key=lambda r: (r.rect[2]-r.rect[0])*(r.rect[3]-r.rect[1])):
            if row.text not in seen:
                result.append(row)
                seen.add(row.text)
        return result

    def diagnostics(self):
        from pathlib import Path
        records = []
        callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        @callback_type
        def visit(hwnd, unused):
            name, title = C.create_unicode_buffer(256), C.create_unicode_buffer(256)
            u.GetClassNameW(hwnd, name, 256)
            u.GetWindowTextW(hwnd, title, 256)
            if u.IsWindowVisible(hwnd) and any(t in (name.value+title.value).lower() for t in ("ime", "input", "输入", "corewindow", "candidate")):
                records.append({"hwnd": int(hwnd), "class": name.value, "title": title.value,
                                "visible": bool(u.IsWindowVisible(hwnd))})
            return True
        u.EnumWindows(visit, 0)
        screenshot = ""
        try:
            from PIL import ImageGrab
            rect = W.RECT()
            u.GetWindowRect(self.editor.root, C.byref(rect))
            screenshot = str(Path(__file__).resolve().parent.parent/"runs"/"ime-diagnostic.png")
            ImageGrab.grab(bbox=(rect.left,rect.top,rect.right,rect.bottom)).save(screenshot)
        except Exception:
            pass
        return {"composition": self.composition(), "windows": records,
                "uia_errors": self.uia_errors, "tree":self.candidate_tree,"screenshot": screenshot,
                "tsf_error":self.editor.tsf_error,"tsf_active":list(self.editor.tsf.active) if self.editor.tsf else [],
                "tsf_errors":self.editor.tsf.errors if self.editor.tsf else [],
                "ocr_error":self.ocr_error,"ocr_debug":self.ocr_debug}

    def ocr_candidates(self):
        """Read the visible candidate row with Windows' local Chinese OCR."""
        try:
            from PIL import ImageGrab
            if self.ocr_process is None:
                script=Path(__file__).with_name("ocr.ps1")
                executable=os.path.join(os.environ.get("SystemRoot","C:\\Windows"),"System32","WindowsPowerShell","v1.0","powershell.exe")
                self.ocr_process=subprocess.Popen([executable,"-NoProfile","-NonInteractive","-File",str(script)],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                    text=True,encoding="utf-8",creationflags=subprocess.CREATE_NO_WINDOW)
                process=self.ocr_process
                def read_lines():
                    for line in process.stdout:
                        self.ocr_queue.put(line)
                threading.Thread(target=read_lines,daemon=True).start()
            info=GUITHREADINFO(cbSize=C.sizeof(GUITHREADINFO))
            if not u.GetGUIThreadInfo(self.editor.thread,C.byref(info)):
                return []
            point=W.POINT(info.rcCaret.left,info.rcCaret.bottom)
            u.ClientToScreen(info.hwndCaret or self.editor.hwnd,C.byref(point))
            x,y=max(0,point.x-6),max(0,point.y)
            width=min(1200,u.GetSystemMetrics(0)-x)
            height=min(65,u.GetSystemMetrics(1)-y)
            if width<=0 or height<=0:return []
            scale=2
            path=Path(__file__).resolve().parent.parent/"runs"/"candidate-current.png"
            image=ImageGrab.grab(bbox=(x,y,x+width,y+height)).resize((width*scale,height*scale)).convert("RGB")
            # The selected candidate's blue marker can merge with digit 1 in OCR.
            image.putdata([(246,246,246) if b-r>40 and b>g*1.05 else (r,g,b) for r,g,b in image.getdata()])
            image.save(path)
            self.ocr_process.stdin.write(json.dumps({"path":str(path)})+"\n")
            self.ocr_process.stdin.flush()
            result=json.loads(self.ocr_queue.get(timeout=8))
            self.ocr_debug={"result":result,"hosts":[]}
            if "error" in result:
                self.ocr_error=result["error"]
                return []
            words=[word for line in result.get("lines",[]) for word in line.get("words",[])]
            words.sort(key=lambda w:w["x"])
            candidates=[]
            current=[]
            key=""
            host_roots=set()
            callback_type=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
            @callback_type
            def locate_host(hwnd,unused):
                title=C.create_unicode_buffer(256)
                u.GetWindowTextW(hwnd,title,256)
                if any(term in title.value.lower() for term in ("windows 输入体验","microsoft text input","windows input experience")):
                    host_roots.add(u.GetAncestor(hwnd,2))
                return True
            u.EnumWindows(locate_host,0)
            def flush():
                nonlocal current,key
                if current:
                    text="".join(w["text"] for w in current)
                    left=min(w["x"] for w in current)/scale+x
                    top=min(w["y"] for w in current)/scale+y
                    right=max(w["x"]+w["width"] for w in current)/scale+x
                    bottom=max(w["y"]+w["height"] for w in current)/scale+y
                    center=W.POINT(int((left+right)/2),int((top+bottom)/2))
                    hwnd=u.WindowFromPoint(center)
                    root=u.GetAncestor(hwnd,2) if hwnd else None
                    title=C.create_unicode_buffer(256)
                    class_name=C.create_unicode_buffer(256)
                    if root:u.GetWindowTextW(root,title,256)
                    if root:u.GetClassNameW(root,class_name,256)
                    self.ocr_debug["hosts"].append({"text":text,"title":title.value,"class":class_name.value,"hwnd":root})
                    # Only accept text that is visibly inside the system IME host.
                    if root and (root in host_roots or any(t in title.value.lower() for t in ("输入", "input", "ime"))):
                        candidates.append(Candidate(text,key,(int(left),int(top),int(right),int(bottom)),"Windows OCR"))
                current=[]
                key=""
            for word in words:
                value=word["text"].strip()
                if re.fullmatch(r"[1-9][.、．]?",value):
                    flush()
                    key=value[0]
                elif HAN.fullmatch(value):
                    if current and (word["x"]-(current[-1]["x"]+current[-1]["width"])>word["height"]*.7
                                    or abs(word["y"]-current[-1]["y"])>word["height"]*.6):
                        flush()
                    current.append(word)
                else:
                    flush()
            flush()
            return candidates
        except Exception as error:
            self.ocr_error=str(error)
            return []

    def candidates(self):
        if self.direct_supported is False:
            return []
        tsf_rows = self.ui_call(self.editor.tsf.snapshot) if self.editor.tsf else []
        candidates = [Candidate(text,key,source="TSF") for text,key in tsf_rows]
        candidates = candidates or self.imm_candidates() or self.uia_candidates()
        self.direct_supported = bool(candidates)
        self.last_snapshot = candidates
        return candidates

    @contextmanager
    def attempt_scope(self):
        owner=getattr(self,"_attempt_budget",None) is None
        if owner:self._attempt_budget=AttemptBudget()
        try:yield self._attempt_budget
        finally:
            if owner:self._attempt_budget=None

    def choose(self, chunk, checkpoint, emit):
        with self.attempt_scope():
            return self._choose(chunk,checkpoint,emit)

    def _choose(self, chunk, checkpoint, emit):
        self._attempt_budget.require()
        before = self.read()
        start,end=self.selection()
        self.chinese_mode()
        self.type_pinyin(chunk, checkpoint)
        time.sleep(.15)
        previous = None
        for page in range(1):
            checkpoint()
            rows = self.candidates()
            emit("candidates", target=chunk.text, page=page+1,
                 candidates=[{"text": r.text, "key": r.key, "source": r.source} for r in rows])
            target = next((r for r in rows if r.text == chunk.text), None)
            if target:
                self._attempt_budget.consume()
                self.rhythm.wait("selection", checkpoint)
                if target.key:
                    send_key(ord(target.key))
                elif target.rect:
                    # Candidate bounds are from the current system IME snapshot.
                    point = W.POINT()
                    u.GetCursorPos(C.byref(point))
                    x = (target.rect[0]+target.rect[2]) // 2
                    y = (target.rect[1]+target.rect[3]) // 2
                    u.SetCursorPos(x, y)
                    events = (INPUT*2)(INPUT(type=0, mi=MOUSEINPUT(dwFlags=2)), INPUT(type=0, mi=MOUSEINPUT(dwFlags=4)))
                    if u.SendInput(2, events, C.sizeof(INPUT)) != 2:
                        raise RuntimeError("候选词点击失败。")
                    u.SetCursorPos(point.x, point.y)
                else:
                    raise RuntimeError("候选词没有可操作的位置。")
                time.sleep(.15)
                emit("selected", target=chunk.text, source=target.source)
                return True
            signature = tuple(r.text for r in rows)
            if not rows or signature == previous:
                if not rows:
                    if not self.fallback_announced:
                        emit("candidate_adapter",mode="commit-verify",reason="候选接口未暴露文字，使用上屏校验，无 OCR")
                        self.fallback_announced=True
                    return self.probe_choices(chunk,before,start,end,checkpoint,emit)
                break
            break
        send_key(0x1B)
        time.sleep(.08)
        if self.read() != before:
            raise RuntimeError("拼音未进入组合状态或取消后文本发生变化；请确认中文全拼模式。")
        return False

    def selection(self):
        start,end=W.DWORD(),W.DWORD()
        u.SendMessageW(self.editor.hwnd,0xB0,C.addressof(start),C.addressof(end))
        text=self.read()
        def index(offset):
            return len(text.encode("utf-16-le")[:offset*2].decode("utf-16-le"))
        return index(start.value),index(end.value)

    def probe_choices(self,chunk,before,start,end,checkpoint,emit):
        """Bounded real candidate selection, using committed editor text as feedback.

        No candidate order is assumed correct. A rejected commit is removed with
        Backspace before retrying; the source text is never injected directly.
        """
        wanted=before[:start]+chunk.text+before[end:]
        cached=self.choice_cache.get((chunk.keys,chunk.text))
        choices=[cached] if cached and cached[0]==0 else []
        choices.extend((0,digit) for digit in (1,2,3) if (0,digit) not in choices)
        began=time.perf_counter()
        for attempt,(page,digit) in enumerate(choices[:self._attempt_budget.remaining]):
            checkpoint()
            self._attempt_budget.consume()
            trial=chunk
            if attempt:
                self.rhythm.wait("retry", checkpoint)
                if hasattr(self,"retry_chunk"):
                    trial=self.retry_chunk(chunk)
                    if trial!=chunk:page,digit=0,1
                self.chinese_mode()
                self.type_pinyin(trial, checkpoint)
                time.sleep(.06)
            self.rhythm.wait("selection", checkpoint)
            send_key(ord(str(digit)))
            time.sleep(.08)
            actual=self.read()
            if hasattr(self,"validate_commit"):
                self.validate_commit(trial,before,actual,start,end,digit)
            if trial!=chunk and actual==before[:start]+trial.text+before[end:]:
                # Word-assisted character selection still uses pinyin and a
                # real candidate key. Remove only its verified extra suffix.
                self.delete_range(start+len(chunk.text),start+len(trial.text),actual)
                actual=self.read()
            exact=actual==wanted
            emit("candidate_probe",target=chunk.text,input_text=trial.text,page=page+1,key=digit,exact=exact,
                 elapsed_ms=round((time.perf_counter()-began)*1000))
            if exact:
                if trial==chunk:self.choice_cache[(chunk.keys,chunk.text)]=(page,digit)
                emit("selected",target=chunk.text,source="词带字 · 上屏精确校验" if trial!=chunk else "上屏精确校验 · 无 OCR")
                return True
            self.rhythm.wait("correction", checkpoint)
            send_key(0x1B)
            time.sleep(.03)
            actual=self.read()
            prefix,suffix=before[:start],before[end:]
            if not actual.startswith(prefix) or not actual.endswith(suffix) or len(actual)<len(prefix)+len(suffix):
                raise RuntimeError("候选确认后原有文本发生变化，已停止。")
            inserted_end=len(actual)-len(suffix)
            if inserted_end>start:
                method=self.delete_range(start,inserted_end,actual)
                emit("candidate_rollback",target=chunk.text,method=method)
            if self.read()!=before:
                raise RuntimeError("候选尝试后未能恢复原文，已停止。")
        return False

    def literal(self, text, checkpoint):
        self.rhythm.begin_chunk()
        for index, char in enumerate(text):
            self.rhythm.wait("key", checkpoint)
            send_literal(char)
            self.rhythm.after_literal(text,index,checkpoint)

    def select(self, start, end, expected):
        self.guard()
        if self.read() != expected:
            raise RuntimeError("编辑器文本发生外部变化，已取消修正。")
        if self.composition():
            raise RuntimeError("拼音尚未确认，不能移动光标删除正文。")
        u.SendMessageW(self.editor.hwnd, 0xB1, len(expected[:start].encode("utf-16-le"))//2,
                      len(expected[:end].encode("utf-16-le"))//2)

    def delete_range(self,start,end,expected):
        self.guard()
        if self.read()!=expected:
            raise RuntimeError("编辑器文本发生变化，已取消删除。")
        if self.composition():
            raise RuntimeError("拼音尚未确认，不能删除正文。")
        # Normal typing retries happen at the end. Keep the caret there and use
        # actual Backspace keys, without selecting/repositioning the word.
        if (start<end and end==len(expected)
                and re.fullmatch(r"[\u3400-\u9fffA-Za-z0-9']+",expected[start:end])
                and self.selection()==(end,end)):
            prefix=expected[:start]
            for _ in range(end-start):
                current=self.read()
                if current==prefix:
                    break
                if not current.startswith(prefix):
                    raise RuntimeError("末尾退格范围已变化，已停止。")
                self.backspace()
                if self.read()==current or not self.read().startswith(prefix):
                    raise RuntimeError("末尾退格结果不符合预期，已停止。")
            if self.read()!=prefix:
                raise RuntimeError("末尾文字未能完整回退，已停止。")
            return "tail_backspace"
        self.select(start,end,expected)
        self.backspace()
        return "selected_range"

    def backspace(self):
        self.rhythm.wait("correction", self.checkpoint)
        send_key(8)
        time.sleep(.06)

    def copy_all(self):
        self.rhythm.wait("review", self.checkpoint)
        actual=self.read()
        set_clipboard_text(actual,self.editor.hwnd)
        value=clipboard_text()
        self.checkpoint()
        if self.read()!=actual:
            raise RuntimeError("复制期间编辑器文本发生变化，未通过核验。")
        return value
