"""Observe this application's TSF candidate UI on its GUI thread.

Interface layout follows Microsoft's msctf.h. This is not a cross-process hook.
"""
import ctypes as C
from ctypes import wintypes as W
from comtypes import IUnknown, GUID, HRESULT, COMMETHOD, COMObject, CoCreateInstance, BSTR

P = C.POINTER


class ITfThreadMgr(IUnknown):
    _iid_ = GUID("{AA80E801-2021-11D2-93E0-0060B067B86E}")
    _methods_ = [COMMETHOD([], HRESULT, "Activate", (["out"], P(W.DWORD), "client")),
                 COMMETHOD([], HRESULT, "Deactivate"),
                 COMMETHOD([], HRESULT, "CreateDocumentMgr"),
                 COMMETHOD([], HRESULT, "EnumDocumentMgrs"),
                 COMMETHOD([], HRESULT, "GetFocus"),
                 COMMETHOD([], HRESULT, "SetFocus"),
                 COMMETHOD([], HRESULT, "AssociateFocus"),
                 COMMETHOD([], HRESULT, "IsThreadFocus"),
                 COMMETHOD([], HRESULT, "GetFunctionProvider"),
                 COMMETHOD([], HRESULT, "EnumFunctionProviders"),
                 COMMETHOD([], HRESULT, "GetGlobalCompartment")]


class ITfThreadMgrEx(ITfThreadMgr):
    _iid_ = GUID("{3e90ade3-7594-4cb0-bb58-69628f5f458c}")
    _methods_ = [COMMETHOD([],HRESULT,"ActivateEx",(["out"],P(W.DWORD),"client"),(["in"],W.DWORD,"flags"))]


class ITfUIElement(IUnknown):
    _iid_ = GUID("{ea1ea137-19df-11d7-a6d2-00065b84435c}")
    _methods_ = [COMMETHOD([],HRESULT,"GetDescription",(["out"],P(BSTR),"description")),
                 COMMETHOD([],HRESULT,"GetGUID",(["out"],P(GUID),"guid")),
                 COMMETHOD([],HRESULT,"Show",(["in"],W.BOOL,"show")),
                 COMMETHOD([],HRESULT,"IsShown",(["out"],P(W.BOOL),"shown"))]


class ITfCandidateListUIElement(ITfUIElement):
    _iid_ = GUID("{ea1ea138-19df-11d7-a6d2-00065b84435c}")
    _methods_ = [
        COMMETHOD([],HRESULT,"GetUpdatedFlags",(["out"],P(W.DWORD),"flags")),
        COMMETHOD([],HRESULT,"GetDocumentMgr",(["out"],P(P(IUnknown)),"manager")),
        COMMETHOD([],HRESULT,"GetCount",(["out"],P(W.UINT),"count")),
        COMMETHOD([],HRESULT,"GetSelection",(["out"],P(W.UINT),"selection")),
        COMMETHOD([],HRESULT,"GetString",(["in"],W.UINT,"index"),(["out"],P(BSTR),"value")),
        # Caller owns this writable array; prevent comtypes allocating a single UINT.
        COMMETHOD([],HRESULT,"GetPageIndex",(["in"],P(W.UINT),"indices"),
                  (["in"],W.UINT,"size"),(["out"],P(W.UINT),"count")),
        COMMETHOD([],HRESULT,"SetPageIndex",(["in"],P(W.UINT),"indices"),(["in"],W.UINT,"count")),
        COMMETHOD([],HRESULT,"GetCurrentPage",(["out"],P(W.UINT),"page"))]


class ITfUIElementMgr(IUnknown):
    _iid_ = GUID("{ea1ea135-19df-11d7-a6d2-00065b84435c}")
    _methods_ = [
        COMMETHOD([],HRESULT,"BeginUIElement",(["in"],P(ITfUIElement),"element"),
                  (["in","out"],P(W.BOOL),"show"),(["out"],P(W.DWORD),"identifier")),
        COMMETHOD([],HRESULT,"UpdateUIElement",(["in"],W.DWORD,"identifier")),
        COMMETHOD([],HRESULT,"EndUIElement",(["in"],W.DWORD,"identifier")),
        COMMETHOD([],HRESULT,"GetUIElement",(["in"],W.DWORD,"identifier"),
                  (["out"],P(P(ITfUIElement)),"element"))]


class ITfSource(IUnknown):
    _iid_ = GUID("{4ea48a35-60ae-446f-8fd6-e6a8d82459f7}")
    _methods_ = [COMMETHOD([],HRESULT,"AdviseSink",(["in"],P(GUID),"iid"),
                          (["in"],P(IUnknown),"sink"),(["out"],P(W.DWORD),"cookie")),
                 COMMETHOD([],HRESULT,"UnadviseSink",(["in"],W.DWORD,"cookie"))]


class ITfUIElementSink(IUnknown):
    _iid_ = GUID("{ea1ea136-19df-11d7-a6d2-00065b84435c}")
    _methods_ = [COMMETHOD([],HRESULT,"BeginUIElement",(["in"],W.DWORD,"identifier"),
                          (["in","out"],P(W.BOOL),"show")),
                 COMMETHOD([],HRESULT,"UpdateUIElement",(["in"],W.DWORD,"identifier")),
                 COMMETHOD([],HRESULT,"EndUIElement",(["in"],W.DWORD,"identifier"))]


class Sink(COMObject):
    _com_interfaces_ = [ITfUIElementSink]

    def __init__(self,owner):
        self.owner=owner

    def BeginUIElement(self,this,identifier,show):
        show[0]=True
        self.owner.active.add(identifier)
        return 0

    def UpdateUIElement(self,this,identifier):
        self.owner.active.add(identifier)
        return 0

    def EndUIElement(self,this,identifier):
        self.owner.active.discard(identifier)
        return 0


class Observer:
    def __init__(self):
        self.active,self.errors=set(),[]
        self.manager=CoCreateInstance(GUID("{529a9e6b-6587-4f23-ab9e-9c7d683e3c50}"),interface=ITfThreadMgrEx,clsctx=1)
        self.client=self.manager.ActivateEx(4)
        self.ui=self.manager.QueryInterface(ITfUIElementMgr)
        self.source=self.manager.QueryInterface(ITfSource)
        self.sink=Sink(self)
        self.cookie=self.source.AdviseSink(C.byref(ITfUIElementSink._iid_),self.sink)

    def snapshot(self):
        rows=[]
        for identifier in tuple(self.active):
            try:
                element=self.ui.GetUIElement(identifier).QueryInterface(ITfCandidateListUIElement)
                count,page=element.GetCount(),element.GetCurrentPage()
                indices=(W.UINT*1024)()
                page_count=element.GetPageIndex(indices,len(indices))
                start=indices[page] if page < page_count else 0
                end=indices[page+1] if page+1 < page_count else count
                for index in range(start,min(end,start+9)):
                    rows.append((element.GetString(index),str(index-start+1)))
            except Exception as error:
                self.errors.append(str(error))
                self.errors=self.errors[-5:]
        return rows

    def close(self):
        self.source.UnadviseSink(self.cookie)
        self.manager.Deactivate()
