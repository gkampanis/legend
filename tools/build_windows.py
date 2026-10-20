"""Build a self-contained Windows application directory, including Chromium."""
import os
from pathlib import Path
import subprocess
import sys


def main():
    if sys.platform != 'win32':
        print('Windows build requires Windows Python / a Windows CI runner.')
        return 2
    root=Path(__file__).resolve().parents[1]
    environment=dict(os.environ,PLAYWRIGHT_BROWSERS_PATH='0')
    subprocess.run([sys.executable,'-m','playwright','install','chromium'],cwd=root,env=environment,check=True)
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed',
             '--name','myInvoices','--paths',str(root/'src'),
             '--collect-all','playwright','--collect-all','pypdfium2','--collect-all','pypdfium2_raw',
             '--collect-all','send2trash','--hidden-import','win32com.shell.shell',
             '--hidden-import','pythoncom',str(root/'tools'/'windows_entry.py')]
    subprocess.run(command,cwd=root,env=environment,check=True)
    print('Application: dist/myInvoices/myInvoices.exe. Distribute the entire myInvoices directory.')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
