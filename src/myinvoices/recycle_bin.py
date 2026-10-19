"""Use the modern Windows Recycle Bin API, refusing permanent-delete callbacks."""
import sys
from pathlib import Path


def recycle_folder(path):
    if sys.platform != 'win32':
        from send2trash import send2trash
        return send2trash(str(path))
    import pythoncom
    import win32file
    from win32com.shell import shell, shellcon
    from win32com.server.exception import COMException
    from send2trash.win.IFileOperationProgressSink import FileOperationProgressSink

    # Recycle Bin semantics are required; do not use legacy SHFileOperation or
    # fall back to rmtree for network/removable/unrecyclable locations.
    if win32file.GetDriveType(str(Path(path).anchor)) != 3:  # DRIVE_FIXED
        raise OSError('Recycle Bin requires a local fixed drive')

    class RecycleOnlySink(FileOperationProgressSink):
        def PreDeleteItem(self, flags, item):
            if not flags & shellcon.TSF_DELETE_RECYCLE_IF_POSSIBLE:
                # A returned HRESULT is not sufficient in the Python COM sink.
                raise COMException(desc='Recycle Bin required', scode=-2147467260)  # E_ABORT
            return 0

    pythoncom.CoInitialize()
    try:
        operation=pythoncom.CoCreateInstance(shell.CLSID_FileOperation,None,
                                           pythoncom.CLSCTX_ALL,shell.IID_IFileOperation)
        flags=(shellcon.FOF_NOCONFIRMATION | shellcon.FOF_NOERRORUI | shellcon.FOF_SILENT |
               shellcon.FOFX_EARLYFAILURE | 0x20000000 | 0x00080000)
        # FOFX_ADDUNDORECORD | FOFX_RECYCLEONDELETE on Windows 11.
        operation.SetOperationFlags(flags)
        sink=pythoncom.WrapObject(RecycleOnlySink(),shell.IID_IFileOperationProgressSink)
        item=shell.SHCreateItemFromParsingName(str(path),None,shell.IID_IShellItem)
        operation.DeleteItem(item,sink)
        result=operation.PerformOperations()
        if result or operation.GetAnyOperationsAborted():
            raise OSError('Recycle Bin operation did not complete')
    finally:
        pythoncom.CoUninitialize()
