from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from myinvoices.recycle_bin import recycle_folder


class NativeRecycleBoundaryTests(unittest.TestCase):
    def run_backend(self,recycle=True,drive=3):
        path=Path('/selected/client')
        class NativeAbort(Exception):
            def __init__(self,**kwargs):
                self.code=kwargs['scode']
        operation=Mock()
        operation.PerformOperations.return_value=0
        operation.GetAnyOperationsAborted.return_value=False
        def delete(item,sink):
            sink.PreDeleteItem(128 if recycle else 0,item)
        operation.DeleteItem.side_effect=delete
        com=SimpleNamespace(CoInitialize=Mock(),CoUninitialize=Mock(),CLSCTX_ALL=1,
                            CoCreateInstance=Mock(return_value=operation),WrapObject=lambda obj,iid:obj)
        shell=SimpleNamespace(CLSID_FileOperation='operation',IID_IFileOperation='interface',
                              IID_IFileOperationProgressSink='sink',IID_IShellItem='item',
                              SHCreateItemFromParsingName=Mock(return_value='selected folder'))
        flags=SimpleNamespace(TSF_DELETE_RECYCLE_IF_POSSIBLE=128,FOF_NOCONFIRMATION=16,
                              FOF_NOERRORUI=1024,FOF_SILENT=4,FOFX_EARLYFAILURE=0x100000)
        modules={'pythoncom':com,'win32file':SimpleNamespace(GetDriveType=Mock(return_value=drive)),
                 'win32com.shell':SimpleNamespace(shell=shell,shellcon=flags),
                 'win32com.server.exception':SimpleNamespace(COMException=NativeAbort),
                 'send2trash.win.IFileOperationProgressSink':SimpleNamespace(FileOperationProgressSink=object)}
        with patch.dict('sys.modules',modules),patch('myinvoices.recycle_bin.sys.platform','win32'):
            if not recycle:
                with self.assertRaises(NativeAbort) as caught:
                    recycle_folder(path)
                self.assertEqual(caught.exception.code,-2147467260)
            elif drive!=3:
                with self.assertRaises(OSError):
                    recycle_folder(path)
            else:
                recycle_folder(path)
                shell.SHCreateItemFromParsingName.assert_called_once_with(str(path),None,'item')
                self.assertTrue(operation.SetOperationFlags.call_args.args[0] & 0x80000)
        if drive==3:
            com.CoUninitialize.assert_called_once()
        else:
            operation.DeleteItem.assert_not_called()

    def test_modern_native_operation_recycles_only_selected_folder(self):
        self.run_backend()

    def test_permanent_delete_callback_aborts_the_operation(self):
        self.run_backend(recycle=False)

    def test_network_or_removable_drive_is_refused(self):
        self.run_backend(drive=4)
