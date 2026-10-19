"""Windows desktop location and local archive browsing, without shell commands."""
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import uuid

from .models import ApplicationError


def _windows_desktop():
    # Known Folder API respects redirected/OneDrive and localized Desktop paths.
    from ctypes import wintypes
    identifier = (ctypes.c_ubyte * 16).from_buffer_copy(
        uuid.UUID('b4bfcc3a-db2c-424c-b029-7fe99a87c641').bytes_le)
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    ole = ctypes.WinDLL('ole32', use_last_error=True)
    locate = shell.SHGetKnownFolderPath
    locate.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE,
                       ctypes.POINTER(ctypes.c_void_p)]
    locate.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None
    pointer = ctypes.c_void_p()
    try:
        if locate(ctypes.byref(identifier), 0, None, ctypes.byref(pointer)) != 0 or not pointer.value:
            raise OSError()
        return Path(ctypes.wstring_at(pointer))
    finally:
        if pointer.value:
            ole.CoTaskMemFree(pointer)


def desktop_directory():
    if sys.platform == 'win32':
        try:
            return _windows_desktop()
        except (OSError, ValueError):
            raise ApplicationError('Δεν εντοπίστηκε η Επιφάνεια εργασίας. Επιλέξτε φάκελο από το μενού Περισσότερα.') from None
    return Path.home() / 'Desktop'


def client_archive_folder(root, client):
    name = client.vat if client.environment == 'production' else f'{client.vat}_ΔΟΚΙΜΑΣΤΙΚΟ'
    return Path(root) / name


def open_directory(path):
    path = Path(path).absolute()
    try:
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == 'win32':
            os.startfile(str(path), 'explore')
        else:
            subprocess.run(['open' if sys.platform == 'darwin' else 'xdg-open', str(path)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        raise ApplicationError('Δεν άνοιξε ο φάκελος τιμολογίων. Ελέγξτε ότι είναι διαθέσιμος.') from None
