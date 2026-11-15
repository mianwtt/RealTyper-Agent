"""Global hotkeys on their own Windows message-loop thread."""
import ctypes as C
from ctypes import wintypes as W
import threading
from .windows import u,k,signature

signature(u,"RegisterHotKey",W.BOOL,[W.HWND,C.c_int,W.UINT,W.UINT])
signature(u,"UnregisterHotKey",W.BOOL,[W.HWND,C.c_int])
signature(u,"GetMessageW",C.c_int,[C.POINTER(W.MSG),W.HWND,W.UINT,W.UINT])
signature(u,"PeekMessageW",W.BOOL,[C.POINTER(W.MSG),W.HWND,W.UINT,W.UINT,W.UINT])
signature(u,"PostThreadMessageW",W.BOOL,[W.DWORD,W.UINT,W.WPARAM,W.LPARAM])
signature(k,"GetCurrentThreadId",W.DWORD,[])


class GlobalHotkeys:
    def __init__(self,callback):
        self.callback=callback
        self.thread_id=None
        self.ready=threading.Event()
        self.error=None
        self.thread=threading.Thread(target=self.run,daemon=True)

    def start(self):self.thread.start()

    def run(self):
        self.thread_id=k.GetCurrentThreadId()
        message=W.MSG()
        u.PeekMessageW(C.byref(message),None,0,0,0)
        registered=[]
        try:
            for identifier,key in ((1,0x56),(2,0x51)):
                if not u.RegisterHotKey(None,identifier,0x4003,key):
                    raise RuntimeError("快捷键已被占用，或快捷模式已经运行。请先关闭重复实例。")
                registered.append(identifier)
            self.ready.set()
            self.callback("ready")
            while u.GetMessageW(C.byref(message),None,0,0)>0:
                if message.message==0x312:
                    self.callback("activate" if message.wParam==1 else "stop")
        except Exception as error:
            self.error=error
            self.ready.set()
            self.callback("error",error)
        finally:
            for identifier in registered:u.UnregisterHotKey(None,identifier)

    def close(self):
        if self.thread_id:u.PostThreadMessageW(self.thread_id,0x12,0,0)
        if self.thread.is_alive():self.thread.join(timeout=.5)
