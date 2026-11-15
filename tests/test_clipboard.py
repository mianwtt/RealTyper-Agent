import ctypes as C
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/".deps"))
from pinyin_agent import windows as win


class ClipboardTests(unittest.TestCase):
    def test_copy_reads_actual_editor_without_selecting_or_keyboard_shortcuts(self):
        backend=win.WindowsBackend.__new__(win.WindowsBackend)
        backend.editor=SimpleNamespace(hwnd=123)
        backend.rhythm=SimpleNamespace(wait=lambda kind,checkpoint:checkpoint())
        backend.checkpoint=Mock()
        backend.read=Mock(return_value="实际已输入😀\n第二行")
        backend.select=Mock()
        with patch.object(win,"set_clipboard_text") as copy:
            with patch.object(win,"clipboard_text",return_value="实际已输入😀\n第二行"):
                with patch.object(win,"send_key") as key:
                    self.assertEqual(backend.copy_all(),"实际已输入😀\n第二行")
        copy.assert_called_once_with("实际已输入😀\n第二行",123)
        backend.select.assert_not_called()
        key.assert_not_called()

    def test_copy_rejects_editor_changes_during_export(self):
        backend=win.WindowsBackend.__new__(win.WindowsBackend)
        backend.editor=SimpleNamespace(hwnd=123)
        backend.rhythm=SimpleNamespace(wait=lambda kind,checkpoint:checkpoint())
        backend.checkpoint=Mock()
        backend.read=Mock(side_effect=["原有内容","内容已变化"])
        with patch.object(win,"set_clipboard_text"):
            with patch.object(win,"clipboard_text",return_value="原有内容"):
                with self.assertRaises(RuntimeError):backend.copy_all()

    def check_allocation(self,success):
        text="录入😀\n换行"
        payload="录入😀\r\n换行".encode("utf-16-le")+b"\x00\x00"
        buffer=C.create_string_buffer(len(payload))
        user=SimpleNamespace(OpenClipboard=Mock(return_value=True),EmptyClipboard=Mock(return_value=True),
                             SetClipboardData=Mock(return_value=456 if success else 0),CloseClipboard=Mock())
        kernel=SimpleNamespace(GlobalAlloc=Mock(return_value=456),GlobalLock=Mock(return_value=C.addressof(buffer)),
                               GlobalUnlock=Mock(),GlobalFree=Mock())
        with patch.object(win,"u",user),patch.object(win,"k",kernel):
            if success:win.set_clipboard_text(text,123)
            else:
                with self.assertRaises(RuntimeError):win.set_clipboard_text(text,123)
        self.assertEqual(buffer.raw,payload)
        user.OpenClipboard.assert_called_once_with(123)
        user.CloseClipboard.assert_called_once_with()
        kernel.GlobalAlloc.assert_called_once_with(2,len(payload))
        if success:kernel.GlobalFree.assert_not_called()
        else:kernel.GlobalFree.assert_called_once_with(456)

    def test_unicode_and_crlf_copy_transfers_memory_ownership(self):self.check_allocation(True)
    def test_failed_copy_releases_untransferred_memory(self):self.check_allocation(False)


if __name__=="__main__":unittest.main()
